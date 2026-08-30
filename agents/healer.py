"""Task 11b: Healer -- repairs a selector that has drifted, and nothing else.

The one rule that matters more than the rest of this file's contract: an
assertion failure is a real bug, not selector drift, and repairing it
would hide exactly what a customer is paying this platform to find. Every
decision below exists to keep that line bright:

- `_is_selector_drift` is an allow-list of three specific error shapes
  (`strict mode violation`, `Timeout .* waiting for locator`, `no element
  matches`) -- Playwright's own vocabulary for "the DOM no longer matches
  what this locator describes". Anything else -- an `expect(...)` mismatch,
  a bare test timeout with no "waiting for locator" in it, a network
  error -- is left completely alone: not attempted, not counted as
  repaired, not counted as abandoned either. It was never Healer's to
  touch.
- `_replace_locator_line` will not touch a line inside an `expect(...)`
  call, even if that line also happens to construct a locator (an
  assertion routinely does: `await expect(page.getByText('Total: $50')).
  toBeVisible()`). The FAILURE classification above already keeps Healer
  off a spec that failed on an assertion; this is the second, independent
  guard against the narrower case of a spec that failed on drift in ONE
  place while an assertion elsewhere in the same file also happens to
  construct a locator -- that assertion line must never be the one edited.
- A repair is provisional until `ctx.browser.run_spec` says the spec now
  passes. If it does not, the draft is discarded outright -- nothing is
  written for that spec -- and it is reported in `abandoned`, not silently
  dropped. `agents/browser.py`'s `FakeBrowser.run_spec` (extended this
  task) is what lets a test express "this spec fails, then a repair makes
  it pass" or "fails, and a repair still doesn't help" as two different
  seeded outcomes for the same path.

Two gateway calls per run, matching Author's shape (Task 11a) exactly:
`trace.read` wraps the whole discover-draft-verify loop over every spec
this workspace has (one call, not one per candidate -- see Cartographer's
own module docstring for why a per-item gateway call is the wrong shape),
and `repo.write:specs` persists only the repairs that survived
verification, in one call.
"""

import re
from pathlib import PurePosixPath

from agents.base import AgentResult

_DRIFT_PATTERNS = (
    re.compile(r"strict mode violation", re.I),
    re.compile(r"Timeout .* waiting for locator", re.I),
    re.compile(r"no element matches", re.I),
)

# Any line that constructs a Playwright locator -- a brittle CSS
# `page.locator(...)` or a `getBy*` call that has simply gone stale (a
# control's role changed, its name did not). Deliberately broad: Healer
# does not need to know in advance what KIND of locator broke, only where
# one lives in the file.
_LOCATOR_LINE = re.compile(r"page\.(?:locator|getBy\w+)\(")


def _is_selector_drift(error: str) -> bool:
    return any(p.search(error or "") for p in _DRIFT_PATTERNS)


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


def _prompt(spec_path: str, error: str, content: str, elements: list[dict]) -> str:
    described = ", ".join(
        f"{e.get('role', '')} {e.get('name', '')!r}" for e in elements if e.get("role")
    ) or "(no elements found on the current page)"
    return (
        f"The Playwright spec {spec_path!r} has a locator that no longer "
        f"resolves ({error}).\n"
        f"Spec:\n{content}\n"
        f"Elements currently on the page: {described}\n"
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

        def discover_and_repair():
            repaired, abandoned = [], []
            for spec_path, route in specs:
                result = ctx.browser.run_spec(spec_path)
                if result.get("passed") or not _is_selector_drift(result.get("error", "")):
                    # Passing, or failing for a reason that is not selector
                    # drift (an assertion, an unrelated timeout) -- left
                    # alone entirely, per the module docstring's one rule.
                    continue

                content = ctx.repo.spec(ctx.workspace_id, spec_path)
                if content is None:
                    continue

                ctx.browser.goto(route)
                new_line = ctx.model.generate(
                    _prompt(spec_path, result.get("error", ""), content, ctx.browser.a11y())
                )
                new_content = _replace_locator_line(content, new_line)
                if new_content is None:
                    abandoned.append(_spec_id(spec_path))
                    continue

                verify = ctx.browser.run_spec(spec_path)
                if verify.get("passed"):
                    repaired.append((spec_path, new_content))
                else:
                    # Reverted: nothing is written for this spec, and it
                    # is reported rather than silently dropped.
                    abandoned.append(_spec_id(spec_path))
            return repaired, abandoned

        repaired_specs, abandoned = ctx.gateway.call(
            ctx.workspace_id, self.name, "trace.read",
            target=f"{len(specs)} specs", fn=discover_and_repair,
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
