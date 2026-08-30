"""Task 12a: Runner -- executes the suite, and nothing that touches a model.

That last clause is the load-bearing one. Triager (a later task) re-runs a
failure five times under one seed and reports "reproduced 5 of 5" -- a claim
that is only true if the same input produces the same run every time. A
model call anywhere in this file's execution loop would make that false:
`ctx.model` is never read here, by construction, not by discipline
(`test_it_uses_no_model_so_runs_are_reproducible` below pins `ctx.model =
None` and asserts `run()` still works, exactly so a future edit that reaches
for `ctx.model` "just this once" fails loudly instead of quietly).

Two things past "run every spec and count" are worth calling out, because a
real suite is not the clean fixture list Task 12a's own tests model:

1. **Concurrency is an implementation detail, not an output.** Specs run up
   to `workers` at a time (a `ThreadPoolExecutor`), but `data["failures"]`
   is sorted by spec path before it is ever returned. Two runs of the same
   suite -- with completion order shuffled by real thread scheduling, not
   by anything Runner controls -- must produce byte-identical output, or
   the diff between two runs of an unchanged suite becomes noise nobody can
   read. See `test_failures_come_back_in_a_deterministic_order`.
2. **A spec can hang. The run must not.** `_run_all` bounds the whole batch
   to `spec_timeout_s` via `concurrent.futures.wait(..., timeout=...)`
   rather than waiting on each future in turn -- any future still
   outstanding when that budget expires is reported as `kind="timeout"`
   and the run returns anyway. Python cannot forcibly kill a thread, so the
   stuck call keeps running in the background after `run()` has already
   returned (`pool.shutdown(wait=False)` deliberately does not join it) --
   a real production driver should isolate spec execution in something
   killable (a subprocess), which this task does not have. See the task
   report's point-8 discussion for why this is an accepted, documented
   limitation rather than a solved problem.

Failure classification (`_classify`) is what routes a failure to Healer:
`selector` and nothing else does. `assertion` must never reach Healer --
repairing an assertion hides the bug the customer is paying this platform
to find (this is `agents/healer.py`'s own rule, restated here because
Runner is the thing that decides which bucket a failure lands in before
Healer ever sees it). Classification prefers STRUCTURED fields on the
result (`status`, `matcher`) over string-matching `error`, and falls back
to a regex over `error` (the same vocabulary `agents/healer.py`'s
`_is_selector_drift` already trusts) only when a driver hands back neither
field. See `_classify`'s own docstring for exactly why, and the task
report for exactly how brittle that fallback is.
"""

import concurrent.futures as cf
import re
import time

from agents.base import AgentResult
from app.models import Artefact

# The four artefact kinds captured for every failing (non-crash) spec, per
# the task brief. A tuple, not a set: `_write_artefacts` writes them in
# this fixed order, so the `artefacts` list on a failure entry is itself
# deterministic run to run, not just the outer `failures` list.
_ARTEFACT_KINDS = ("video", "trace", "har", "console")

DEFAULT_WORKERS = 4
# A whole-batch watchdog, not a per-spec one -- see the module docstring's
# point 2. 30s is generous for a fake or a real Playwright spec alike; a
# caller running a genuinely slow real suite passes its own
# `spec_timeout_s` rather than living with this default.
DEFAULT_SPEC_TIMEOUT_S = 30.0

# Runner's own minimal "did this even parse" check, before a spec ever
# reaches the browser at all -- point 5 of the brief: a spec that fails to
# load is a `crash`, not a `failed` assertion. Deliberately the SAME two
# checks `agents/author.py`'s `_is_valid` already uses to accept a spec in
# the first place (`"test(" in text` and `"await" in text`) -- every spec
# THIS platform's own Author writes already satisfies them by construction,
# so this never false-positives on a spec this platform generated itself.
# It is still a heuristic, not a parser: a legitimately-shaped but
# differently-structured hand-written spec (no bare `test(...)` block, an
# unusual `await`-free async pattern) could read as "won't load" when a
# real Playwright collector would happily run it. See the task report.
def _looks_loadable(content: str) -> bool:
    return bool(content and content.strip()) and "test(" in content and "await" in content


# Fallback-only vocabulary, used exclusively when a driver's result carries
# neither `status` nor `matcher` (see `_classify`) -- the exact same three
# patterns `agents/healer.py`'s `_is_selector_drift` trusts, so the two
# agents never disagree about what a bare error STRING means when that is
# all either of them has to go on.
_SELECTOR_ERROR = re.compile(
    r"strict mode violation|Timeout .* waiting for locator|no element matches", re.I
)
_TIMEOUT_ERROR = re.compile(r"Timeout .* exceeded", re.I)


def _classify(result: dict) -> str:
    """`selector` | `assertion` | `timeout` for a spec that ran to
    completion and did not pass. (`crash` is never returned from here --
    see `_run_one`, which decides that case before this function is ever
    called, from a load check or a raised exception, neither of which
    leaves a `result` dict to classify.)

    Primary signal is STRUCTURED, matching what a real Playwright JSON
    reporter already hands back without any string parsing:

    - `status == "timedOut"` is Playwright's own distinct status for a test
      that exceeded ITS OWN configured timeout -- not a status a wording
      change to any error message could ever affect.
    - `matcher` is True only when the failure came from an `expect(...)`
      call (Playwright's JSON reporter attaches a `matcherResult` object to
      exactly those failures) and False when it did not -- a raw locator
      resolution failure outside of `expect(...)` has no matcher at all.
      This is what keeps `selector` and `assertion` apart on WHAT KIND OF
      CALL failed, not on how the resulting message happened to be phrased.

    Fallback: when a driver supplies neither field (the current
    `FakeBrowser`, or a not-yet-upgraded real driver), regex over `error`.
    An unrecognised error under this fallback defaults to `assertion`, not
    `selector` -- deliberately the fail-safe direction. Healer only ever
    acts on `selector`, so an ambiguous failure defaulting to `selector`
    risks auto-"repairing" a real bug into silence; defaulting the same
    ambiguity to `assertion` risks nothing worse than Healer leaving a
    spec alone that it might, with better information, have fixed. Between
    "occasionally too cautious" and "occasionally hides a bug", this
    module always takes the former.
    """
    if result.get("status") == "timedOut":
        return "timeout"
    matcher = result.get("matcher")
    if matcher is True:
        return "assertion"
    if matcher is False:
        return "selector"

    error = result.get("error", "") or ""
    if _TIMEOUT_ERROR.search(error) and "waiting for locator" not in error.lower():
        return "timeout"
    if _SELECTOR_ERROR.search(error):
        return "selector"
    return "assertion"


def _artefact_id(run_id: str, spec_path: str, kind: str) -> str:
    """Deterministic, not random -- see `Artefact`'s own docstring in
    app/models.py for why two failing specs writing the same KIND of
    artefact in the same run must not collide, and why re-running the same
    failing spec in the same run should overwrite its own prior artefact
    rather than accumulate a duplicate."""
    return f"af_{run_id}:{spec_path}:{kind}"


class Runner:
    name = "runner"

    def __init__(self, workers: int = DEFAULT_WORKERS, spec_timeout_s: float = DEFAULT_SPEC_TIMEOUT_S):
        self.workers = max(1, workers)
        self.spec_timeout_s = spec_timeout_s

    def run(self, ctx) -> AgentResult:
        start = time.monotonic()
        specs = sorted(ctx.repo.specs_for_workspace(ctx.workspace_id).items())

        # ONE gateway call for the whole batch, not one per spec -- see the
        # module docstring and Cartographer's own write_all for why a
        # per-item gateway call is the wrong shape (it would mean N
        # serialised, contending ledger transactions for a logically single
        # act: "we ran the suite").
        outcomes = ctx.gateway.call(
            ctx.workspace_id, self.name, "browser.drive",
            target=f"{len(specs)} specs",
            fn=lambda: self._run_all(ctx, specs),
        )

        # Concurrency (real thread scheduling inside _run_all above) must
        # not leak into the result -- sort by spec path here, unconditionally,
        # regardless of what order threads actually finished in.
        outcomes.sort(key=lambda o: o["spec"])

        held = sum(1 for o in outcomes if o["passed"])
        failing = [o for o in outcomes if not o["passed"]]
        non_crash_failing = [o for o in failing if o["kind"] != "crash"]
        crashed = len(failing) - len(non_crash_failing)

        if non_crash_failing:
            ctx.gateway.call(
                ctx.workspace_id, self.name, "artefact.write",
                target=f"{len(non_crash_failing)} specs",
                fn=lambda: self._write_artefacts(ctx, non_crash_failing),
            )
        for o in failing:
            o.setdefault("artefacts", [])

        duration_ms = int((time.monotonic() - start) * 1000)
        outcome = "failed" if (non_crash_failing or crashed) else "ok"
        detail = f"{held} held, {len(non_crash_failing)} failed"
        if crashed:
            detail += f", {crashed} crashed (did not load)"

        return AgentResult(
            summary=f"Ran {len(specs)} spec(s)",
            detail=detail,
            outcome=outcome,
            data={
                "held": held,
                "failed": len(non_crash_failing),
                "failures": [
                    {"spec": o["spec"], "error": o["error"], "kind": o["kind"], "artefacts": o["artefacts"]}
                    for o in failing
                ],
                "duration_ms": duration_ms,
            },
        )

    def _run_all(self, ctx, specs: list[tuple[str, str]]) -> list[dict]:
        """Run every (path, content) pair up to `self.workers` at a time,
        bounded to `self.spec_timeout_s` for the WHOLE batch -- see the
        module docstring's point 2 for why a single stuck spec must not
        hang this call forever, and why that bound is per-batch rather than
        reset per spec.
        """
        if not specs:
            return []

        max_workers = max(1, min(self.workers, len(specs)))
        pool = cf.ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = {pool.submit(self._run_one, ctx, path, content): path for path, content in specs}
            done, not_done = cf.wait(futures, timeout=self.spec_timeout_s)
            outcomes = [f.result() for f in done]
            for f in not_done:
                path = futures[f]
                outcomes.append({
                    "spec": path, "passed": False, "kind": "timeout",
                    "error": f"did not complete within the {self.spec_timeout_s}s runner timeout",
                })
        finally:
            # wait=False: joining here would defeat the whole point of the
            # watchdog above by blocking this call on the very thread it
            # just gave up waiting on. The thread, if still running, keeps
            # running in the background after this method returns -- see
            # the module docstring's point 2.
            pool.shutdown(wait=False)
        return outcomes

    def _run_one(self, ctx, path: str, content: str) -> dict:
        if not _looks_loadable(content):
            return {
                "spec": path, "passed": False, "kind": "crash",
                "error": "spec failed to load (no test(...)/await found)",
            }
        try:
            result = ctx.browser.run_spec(path)
        except Exception as exc:  # the driver itself blew up mid-run
            return {"spec": path, "passed": False, "kind": "crash", "error": str(exc)}

        if result.get("passed"):
            return {"spec": path, "passed": True, "kind": "", "error": ""}
        return {
            "spec": path, "passed": False,
            "kind": _classify(result), "error": result.get("error", ""),
        }

    def _write_artefacts(self, ctx, failing: list[dict]) -> int:
        """Video, trace, HAR, and console log for every failing (non-crash)
        spec -- and nothing for a passing one. Runs inside the single
        `artefact.write` gateway call `run()` makes, so N failing specs
        cost one ledger entry, not N."""
        written = 0
        for o in failing:
            artefacts = []
            for kind in _ARTEFACT_KINDS:
                artefact_id = _artefact_id(ctx.run_id, o["spec"], kind)
                ctx.repo.put_artefact(Artefact(
                    id=artefact_id, workspace_id=ctx.workspace_id, run_id=ctx.run_id,
                    spec_path=o["spec"], kind=kind,
                    content=f"{kind} for {o['spec']}: {o['error']}",
                ))
                artefacts.append(artefact_id)
                written += 1
            o["artefacts"] = artefacts
        return written
