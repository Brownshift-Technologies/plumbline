"""Task 11b: Healer -- repairs a selector that has drifted, and nothing else.

The one rule that matters more than the rest of this file's contract: an
assertion failure is a real bug, not selector drift, and repairing it
would hide exactly what a customer is paying this platform to find. Every
decision below exists to keep that line bright:

- Failure classification is `agents.runner._classify`, imported and reused
  rather than re-implemented here (fix round 1). Runner (Task 12a, landed
  after this one) already encodes exactly the right priority order:
  Playwright's own STRUCTURED fields (`status == "timedOut"`, and
  `matcher` -- present only on an `expect(...)` failure) as the primary
  signal, falling back to a regex over `error` only when a driver supplies
  neither, and defaulting an unrecognisable failure to `"assertion"`, never
  `"selector"` -- the fail-safe direction, since Healer only ever acts on
  `"selector"`. The earlier version of this file regexed `error` alone,
  which misreads a failing web-first assertion as drift: Playwright renders
  THAT failure as "Timeout ... waiting for locator" too, the same wording
  a genuinely broken locator produces. Two independent copies of "what
  counts as drift" is how a fleet's classification quietly forks over
  time; importing Runner's keeps there being exactly one. See
  `test_a_timeout_shaped_assertion_failure_is_not_misread_as_drift` below
  for the overlap this fixes.
- `_replace_locator_line` will not touch a line inside an `expect(...)`
  call, even if that line also happens to construct a locator (an
  assertion routinely does: `await expect(page.getByText('Total: $50')).
  toBeVisible()`). Classification above already keeps Healer off a spec
  that FAILED on an assertion; this is the second, independent guard
  against the narrower case of a spec that failed on drift in ONE place
  while an assertion elsewhere in the same file also happens to construct
  a locator -- that assertion line must never be the one edited.
- A repair is provisional until `ctx.browser.run_spec` says the spec now
  passes. If it does not, the draft is discarded outright -- nothing is
  written for that spec -- and it is reported in `abandoned`, not silently
  dropped. `agents/browser.py`'s `FakeBrowser.run_spec` (extended this
  task) is what lets a test express "this spec fails, then a repair makes
  it pass" or "fails, and a repair still doesn't help" as two different
  seeded outcomes for the same path.

Three gateway calls per run at most, none of them per-item:

1. `trace.read` (discover) -- runs every known spec once, classifies each
   non-passing result, and for a drift candidate navigates to its route
   and captures the current accessibility tree. One call for the whole
   batch, not one per spec.
2. `trace.read` (repair) -- drafts and verifies a fix for every candidate
   discovery found. Split from (1) into its own call specifically so its
   `payload` can carry the SITE-DERIVED text (fix round 1: every
   candidate's error string and captured element names/roles) through
   `check_input` before any of it reaches the model -- that text is not
   known until discovery has already run, so it cannot be screened inside
   call (1)'s own payload. Skipped entirely (no call made) when discovery
   found nothing to repair. The mental model fix here is fleet-wide: the
   attacker is not our customer, who types nothing Healer ever reads --
   it is the SITE UNDER TEST, which controls every accessible name and
   every error string this agent interpolates into a prompt.
3. `repo.write:specs` -- persists only the repairs that survived
   verification, in one call. Skipped when there are none.
"""

import re
from pathlib import PurePosixPath

from agents.base import AgentResult
from agents.runner import _classify

# Any line that constructs a Playwright locator -- a brittle CSS
# `page.locator(...)` or a `getBy*` call that has simply gone stale (a
# control's role changed, its name did not). Deliberately broad: Healer
# does not need to know in advance what KIND of locator broke, only where
# one lives in the file.
_LOCATOR_LINE = re.compile(r"page\.(?:locator|getBy\w+)\(")


def _spec_id(path: str) -> str:
    """"specs/checkout.submit.spec.ts" -> "checkout.submit" -- the bare
    identifier `abandoned` reports, with neither the directory nor the
    Playwright-file extension a caller already knows is implied."""
    return PurePosixPath(path).name.removesuffix(".spec.ts")


def _replace_locator_line(content: str, new_line: str) -> str | None:
    """Rewrite exactly the first line that builds a locator and is NOT
    part of an `expect(...)` assertion. Returns the whole file with that
    one line replaced, or None if no eligible line was found at all (the
    caller treats that as an abandoned repair, not a crash)."""
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if _LOCATOR_LINE.search(line) and "expect(" not in line:
            indent = line[: len(line) - len(line.lstrip())]
            ending = "\n" if line.endswith("\n") else ""
            lines[i] = f"{indent}{new_line.strip()}{ending}"
            return "".join(lines)
    return None


def _elements_text(elements: list[dict]) -> str:
    return ", ".join(
        f"{e.get('role', '')} {e.get('name', '')!r}" for e in elements if e.get("role")
    ) or "(no elements found on the current page)"


def _prompt(spec_path: str, error: str, content: str, elements: list[dict]) -> str:
    return (
        f"The Playwright spec {spec_path!r} has a locator that no longer "
        f"resolves ({error}).\n"
        f"Spec:\n{content}\n"
        f"Elements currently on the page: {_elements_text(elements)}\n"
        "Reply with exactly one replacement line using getByRole, "
        "getByLabel, or getByTestId -- nothing else, no explanation."
    )


class Healer:
    name = "healer"

    def run(self, ctx) -> AgentResult:
        specs = [
            (b.spec_path, b.route)
            for b in ctx.repo.behaviours_for_workspace(ctx.workspace_id)
            if b.spec_path
        ]

        def discover():
            candidates = []
            for spec_path, route in specs:
                result = ctx.browser.run_spec(spec_path)
                if result.get("passed") or _classify(result) != "selector":
                    # Passing, or failing for a reason that is not selector
                    # drift (an assertion, a genuine timeout) -- left alone
                    # entirely, per the module docstring's one rule.
                    continue

                content = ctx.repo.spec(ctx.workspace_id, spec_path)
                if content is None:
                    continue

                ctx.browser.goto(route)
                candidates.append({
                    "spec_path": spec_path, "error": result.get("error", ""),
                    "content": content, "elements": ctx.browser.a11y(),
                })
            return candidates

        candidates = ctx.gateway.call(
            ctx.workspace_id, self.name, "trace.read",
            target=f"{len(specs)} specs", fn=discover,
        )

        def repair():
            repaired, abandoned = [], []
            for c in candidates:
                new_line = ctx.model.generate(
                    _prompt(c["spec_path"], c["error"], c["content"], c["elements"])
                )
                new_content = _replace_locator_line(c["content"], new_line)
                if new_content is None:
                    abandoned.append(_spec_id(c["spec_path"]))
                    continue

                verify = ctx.browser.run_spec(c["spec_path"])
                if verify.get("passed"):
                    repaired.append((c["spec_path"], new_content))
                else:
                    # Reverted: nothing is written for this spec, and it
                    # is reported rather than silently dropped.
                    abandoned.append(_spec_id(c["spec_path"]))
            return repaired, abandoned

        repaired_specs, abandoned = [], []
        if candidates:
            # Fix round 1: site-derived text (every candidate's error
            # string and the accessible names/roles just captured off the
            # live page) is screened before any of it reaches a model call
            # -- see the module docstring's point 2.
            payload = {
                "error_text": " ".join(c["error"] for c in candidates),
                "elements_text": " ".join(_elements_text(c["elements"]) for c in candidates),
            }
            repaired_specs, abandoned = ctx.gateway.call(
                ctx.workspace_id, self.name, "trace.read",
                target=f"{len(candidates)} candidates", payload=payload, fn=repair,
            )

        def persist():
            for spec_path, new_content in repaired_specs:
                ctx.repo.put_spec(ctx.workspace_id, spec_path, new_content)
            return len(repaired_specs)

        repaired = 0
        if repaired_specs:
            repaired = ctx.gateway.call(
                ctx.workspace_id, self.name, "repo.write:specs",
                target=f"{len(repaired_specs)} repairs", fn=persist,
            )

        detail = f"{repaired} selector(s) repaired." if repaired else "No selectors repaired."
        if abandoned:
            detail += f" Abandoned: {', '.join(abandoned)}."

        return AgentResult(
            summary=f"Healed {repaired} selector(s)",
            detail=detail,
            data={"repaired": repaired, "abandoned": abandoned},
        )
