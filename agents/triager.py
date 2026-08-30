"""Task 12b: Triager -- turns a reproducible failure into a root-caused
`Finding`, and turns an unreproducible one into an honest "we don't know
yet" instead.

The one rule that matters more than the rest of this file: a flake must
never reach Surgeon. `run()` re-runs every known, currently-failing spec
`attempts` times under the same seed and the same fault, and a spec whose
`attempts` outcomes are not all identical -- ANY mix of pass and fail, not
only "mostly fails" -- is a flake, full stop. A flaky spec still gets a
`Finding` (so it is visible and not silently dropped), but with
`status="needs_repro"` and no `finding_id` ever returned in `data` -- that
missing id is what Surgeon (a later task) reads as "there is nothing here
to attempt a patch against yet." Only a spec that failed identically all
`attempts` times earns a root-cause call and a `status="triaged"` Finding.
See `test_a_mixed_result_is_a_flake` and
`test_a_flake_is_not_handed_to_the_surgeon` in `tests/test_triager.py`.

Classification is Runner's, imported and reused (`agents.runner._classify`),
never re-derived here -- the same discipline `agents/healer.py` already
follows and explains at length in its own module docstring: two independent
copies of "what kind of failure is this" is how a fleet's classification
quietly forks over time. Triager uses it for a narrower purpose than
Healer's, though: a failure classified `"selector"` is left alone
ENTIRELY -- not triaged, not given a Finding at all -- because that is
Healer's job, and a spec that is merely a stale locator is not the real bug
this agent exists to root-cause. Only `"assertion"` and `"timeout"`
failures become candidates.

Three gateway calls at most per run, none of them per item:

1. `trace.read` (reproduce) -- for every known spec, re-runs it `attempts`
   times via `ctx.browser.run_spec` and, for each currently-failing,
   non-selector spec, reads back whatever trace/HAR artefacts a prior
   Runner run already captured for it (`ctx.repo.artefacts_for_spec`). ONE
   call for every spec in the batch, not one per spec -- the same shape
   Cartographer's crawl and Runner's own suite run already take, for the
   same reason (see either module's docstring). Because this tool's name
   ends in `.read`, the Gateway redacts its return value
   (`core.guards.redact_deep`) BEFORE this method ever holds it -- which is
   the entire mechanism behind `test_pii_in_a_har_does_not_reach_the_
   finding`: a card number embedded in a HAR artefact is stripped to
   `"[CARD]"` on the way out of THIS call, so it is not the raw value that
   ever reaches a model prompt in call 2, and it is not this class's job to
   redact anything itself.
2. `trace.read` (root cause) -- made only when at least one candidate
   reproduced deterministically. `payload` carries every deterministic
   candidate's (already-redacted) error/trace/HAR text through
   `check_input` before the model is ever called -- the same fleet-wide
   rule Author and Healer already apply to site-derived text (see either
   module's docstring): a trace or HAR is exactly the kind of artefact the
   SITE UNDER TEST controls the contents of, and an injection-shaped string
   living inside one is no less dangerous once it reaches a model prompt
   than a poisoned `aria-label` is. See
   `test_it_refuses_to_determine_a_root_cause_when_the_trace_contains_an_
   injection_attempt`.
3. `repo.write:findings` -- persists one `Finding` per distinct candidate
   (flaky or deterministic alike), in one call, keyed on `(workspace_id,
   spec_path)` (`_finding_id`) so re-running Triager against an unchanged
   failure overwrites its own prior Finding rather than accumulating a
   duplicate (`test_re_running_does_not_duplicate_the_finding`). Two
   DIFFERENT specs that happen to share one underlying bug still get two
   separate Findings -- this module has no way to know two specs share a
   root cause short of comparing the model's own prose (unreliable, and not
   part of the contract), and the contract itself says "keyed on spec
   path", not "keyed on root cause" -- see the task report's point-7
   discussion.

`data`'s five required fields describe exactly one candidate -- the
"primary" one (the first deterministic candidate found, in spec-path order;
falling back to the first flaky one if there is no deterministic candidate
at all; empty defaults if there was nothing to triage). Every OTHER
candidate in the same run still gets its own ledgered Finding via call 3
above; only the single-candidate summary in `data` is necessarily narrower
than "everything this run touched", matching the interface Task 12b's brief
specifies (`root_cause: str`, not `root_causes: dict`).

Root cause is asked for from the trace, the HAR, and (when one is
available) the diff of the pull request under test -- never the error
string alone (`test_the_root_cause_uses_the_trace_not_just_the_error_
string`). This codebase has no data source for "the diff of the PR under
test" yet as of this task (that is Surgeon/Task 13 territory -- see the
task report) -- `_root_cause_prompt` still names that evidence slot
explicitly, with a "(no diff available)" placeholder, so the prompt's shape
does not have to change the day that data source exists.
"""

from agents.base import AgentResult
from agents.runner import _classify
from app.models import Artefact, Finding

DEFAULT_ATTEMPTS = 5


def _finding_id(workspace_id: str, spec_path: str) -> str:
    """Deterministic, not random -- see `agents/runner.py`'s own
    `_artefact_id` for the identical reasoning: re-triaging the SAME
    failing spec must overwrite its own prior Finding, not accumulate a
    second row for what is still, evidentially, the same failure."""
    return f"fnd_{workspace_id}:{spec_path}"


def _seed_for(workspace_id: str, spec_path: str) -> str:
    """The reproduction seed carried onto every Finding this run writes.
    Deterministic (workspace + spec path), not random, for the same reason
    `_finding_id` is: a caller re-reading a Finding later needs the exact
    seed this triage ran under, and a random seed recorded after the fact
    would be a seed nothing could actually be re-run against."""
    return f"seed:{workspace_id}:{spec_path}"


def _artefact_text(artefacts: list[Artefact], kind: str) -> str:
    for a in artefacts:
        if a.kind == kind:
            return a.content
    return ""


def _root_cause_prompt(candidate: dict, attempts: int) -> str:
    return (
        f"Spec {candidate['spec_path']!r} failed identically on all "
        f"{candidate['repro_count']} of {attempts} reproduction attempts "
        f"under the same seed and fault. Determine the most likely root "
        f"cause using the evidence below -- do not rely on the error "
        f"string alone.\n"
        f"Error: {candidate['error_text']}\n"
        f"Trace: {candidate['trace_text'] or '(no trace artefact captured)'}\n"
        f"HAR: {candidate['har_text'] or '(no HAR artefact captured)'}\n"
        f"Diff of the pull request under test: "
        f"{candidate.get('diff_text') or '(no diff available)'}\n"
        "Reply with one or two sentences naming the likely root cause."
    )


class Triager:
    name = "triager"

    def __init__(self, attempts: int = DEFAULT_ATTEMPTS, only_specs: list[str] | None = None):
        self.attempts = max(1, attempts)
        # Task 13's own carried ruling: the orchestrator hands Triager
        # Runner's ACTUAL failure list rather than leaving this agent to
        # re-run every spec in the workspace `attempts` times, which is
        # both wasteful (a workspace with 40 passing specs and 1 failure
        # cost 40 * attempts reproduction runs it had no reason to make)
        # and slower than it needs to be by the same factor -- the fleet's
        # existing scale problem this field exists to close. `None` (the
        # default) preserves the original, pre-Task-13 behaviour byte for
        # byte: every caller that built a `Triager` before this field
        # existed -- every test in this file included -- gets exactly the
        # same "every spec in the workspace" scan it always did. A caller
        # that DOES pass `only_specs` gets the scan narrowed to exactly
        # those paths; `run()` still applies every one of this agent's own
        # rules (selector-kind failures skipped, a spec that now holds is
        # dropped, flake-vs-deterministic classification) to whatever this
        # narrower set turns out to be -- narrowing the INPUT list changes
        # nothing about how each spec in it is judged.
        self.only_specs = only_specs

    def run(self, ctx) -> AgentResult:
        specs = sorted(ctx.repo.specs_for_workspace(ctx.workspace_id).items())
        if self.only_specs is not None:
            only = set(self.only_specs)
            specs = [(path, content) for path, content in specs if path in only]

        def reproduce():
            candidates = []
            for spec_path, _content in specs:
                results = [ctx.browser.run_spec(spec_path) for _ in range(self.attempts)]
                passed_flags = [bool(r.get("passed")) for r in results]
                failing = [r for r in results if not r.get("passed")]
                if not failing:
                    continue  # held every attempt -- nothing to triage
                kind = _classify(failing[0])
                if kind == "selector":
                    continue  # Healer's job, not Triager's -- see the module docstring

                artefacts = ctx.repo.artefacts_for_spec(ctx.workspace_id, spec_path)
                candidates.append({
                    "spec_path": spec_path,
                    "kind": kind,
                    "repro_count": len(failing),
                    "is_flake": len(set(passed_flags)) > 1,
                    "error_text": failing[0].get("error", ""),
                    "trace_text": _artefact_text(artefacts, "trace"),
                    "har_text": _artefact_text(artefacts, "har"),
                })
            return candidates

        # ONE gateway call for the whole reproduction batch -- see the
        # module docstring's point 1.
        candidates = ctx.gateway.call(
            ctx.workspace_id, self.name, "trace.read",
            target=f"{len(specs)} spec(s)", fn=reproduce,
        )

        flaky = [c for c in candidates if c["is_flake"]]
        deterministic = [c for c in candidates if not c["is_flake"]]

        root_causes: dict[str, str] = {}
        if deterministic:
            def determine_root_causes():
                return {
                    c["spec_path"]: ctx.model.generate(_root_cause_prompt(c, self.attempts))
                    for c in deterministic
                }

            # Fix-round-1 fleet rule (see the module docstring's point 2):
            # site-derived evidence text is screened through `payload`
            # before the model is ever called.
            payload = {
                "error_text": " ".join(c["error_text"] for c in deterministic),
                "trace_text": " ".join(c["trace_text"] for c in deterministic),
                "har_text": " ".join(c["har_text"] for c in deterministic),
            }
            root_causes = ctx.gateway.call(
                ctx.workspace_id, self.name, "trace.read",
                target=f"{len(deterministic)} deterministic failure(s)",
                payload=payload, fn=determine_root_causes,
            )

        if flaky or deterministic:
            def persist_findings():
                for c in flaky:
                    ctx.repo.put_finding(Finding(
                        id=_finding_id(ctx.workspace_id, c["spec_path"]),
                        workspace_id=ctx.workspace_id,
                        title=f"Flaky: {c['spec_path']} "
                              f"({c['repro_count']} of {self.attempts} attempts failed)",
                        route="", found_by=self.name, status="needs_repro",
                        severity="medium", seed=_seed_for(ctx.workspace_id, c["spec_path"]),
                        repro_count=c["repro_count"],
                    ))
                for c in deterministic:
                    ctx.repo.put_finding(Finding(
                        id=_finding_id(ctx.workspace_id, c["spec_path"]),
                        workspace_id=ctx.workspace_id,
                        title=root_causes[c["spec_path"]][:200],
                        route="", found_by=self.name, status="triaged",
                        severity="high", seed=_seed_for(ctx.workspace_id, c["spec_path"]),
                        repro_count=c["repro_count"],
                    ))
                return len(flaky) + len(deterministic)

            ctx.gateway.call(
                ctx.workspace_id, self.name, "repo.write:findings",
                target=f"{len(flaky) + len(deterministic)} finding(s)", fn=persist_findings,
            )

        primary = deterministic[0] if deterministic else (flaky[0] if flaky else None)
        if primary is None:
            return AgentResult(
                summary="No failure to triage",
                detail="Every known spec held, or the only failures were selector "
                       "drift (Healer's job, not Triager's).",
                outcome="ok",
                data={"root_cause": "", "repro_count": 0, "is_flake": False,
                      "seed": "", "finding_id": None},
            )

        is_flake = primary["is_flake"]
        finding_id = None if is_flake else _finding_id(ctx.workspace_id, primary["spec_path"])
        root_cause = "" if is_flake else root_causes.get(primary["spec_path"], "")

        detail = (
            f"{primary['spec_path']}: reproduced {primary['repro_count']} of "
            f"{self.attempts} attempts -- "
            + ("a mixed result, recorded as needs_repro rather than handed to Surgeon."
               if is_flake else "deterministic, handed to Surgeon.")
        )
        if len(candidates) > 1:
            detail += (f" ({len(candidates)} failing spec(s) triaged in total: "
                       f"{len(deterministic)} deterministic, {len(flaky)} flaky.)")

        return AgentResult(
            summary=f"Triaged {len(candidates)} failing spec(s)",
            detail=detail,
            outcome="flaky" if is_flake else "triaged",
            data={
                "root_cause": root_cause, "repro_count": primary["repro_count"],
                "is_flake": is_flake, "seed": _seed_for(ctx.workspace_id, primary["spec_path"]),
                "finding_id": finding_id,
            },
        )
