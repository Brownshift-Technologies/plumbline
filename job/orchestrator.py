"""Task 13: the Orchestrator -- where all eleven agents finally become a run.

Every agent task from 10 through 12g built one piece of the fleet in
isolation, against `tests/agent_fixtures.py`'s `make_ctx`. Nothing before
this module ever sequenced them, wrote a `Step` for one, or decided what
"the run failed" versus "the run needs a human" versus "the run is just
fine, Cartographer only found nothing new" actually means at the level of
a WHOLE run rather than one agent's own `AgentResult`. That is this
module's entire job.

Sequence, and why four agents this task's own original brief never named
now sit where they do
--------------------------------------------------------------------------
The brief this task was written against (Task 13's own brief file)
predates Sentinel, Auditor, Oracle, and Economist -- it only ever knew
about Cartographer, Author, Healer, Chaos, Runner, Triager, and Surgeon.
Those seven keep the exact relative order the brief specifies. The four
newer agents are placed here, deliberately, as follows:

- **Sentinel runs first, before Cartographer, unconditionally, every
  run.** A production incident should shape what gets mapped and tested,
  not arrive after the crawl has already decided what "the site" looks
  like. Fix round 1 reversed this module's own original design here: an
  earlier version gated Sentinel on an `_has_open_incidents` precondition
  check and skipped it SILENTLY (no `Step` at all) when no `Incident` was
  open, reasoning that "no incidents today" is the routine, unremarkable
  case -- the same category as Triager/Surgeon being skipped below when
  Runner found nothing to triage. That reasoning does not survive contact
  with debuggability: unlike Runner-found-nothing (which WRITES a real
  step with an explanatory detail before anything downstream goes quiet),
  a bug in `_has_open_incidents` itself -- a wrong status string, a wrong
  `workspace_id`, a query fault -- is indistinguishable from a genuine
  "no incidents" state; both produce zero rows and zero steps, and a
  customer asking "why didn't my incident produce a behaviour?" has
  nothing in the run's own record to tell the two apart. Sentinel's own
  `run()` already returns a cheap, honest "No open incidents" `AgentResult`
  (one `telemetry.read` gateway call, no model call) when there is nothing
  to do -- there was never a real need to suppress that step at all.
  The general principle this reversal established for the whole fleet:
  **every agent either runs or explains why it did not** -- Oracle's
  explicit `outcome="skipped"` step (below) is the one place true
  silence is still earned, because Oracle's precondition (does the
  workspace have two environments configured) is read directly off
  `Workspace.environments`, not derived from a query that could itself be
  wrong the same way `_has_open_incidents` was.
- **Auditor runs immediately after Cartographer.** Its whole SCOPES-level
  job (`browser.read`, `graph.read`) is auditing the routes Cartographer
  just mapped; running it any later would mean running it against a graph
  that may have already been mutated by Author/Healer/Chaos/Runner for
  reasons that have nothing to do with accessibility or security. Unlike
  Sentinel, Auditor ALWAYS runs (once Cartographer has found at least one
  route) -- it has no meaningful "nothing to audit" precondition of its
  own; a site with zero findings still gets a real, informative step
  (`"0 accessibility finding(s), 0 security finding(s)"`).
- **Oracle runs right after Auditor -- but only when the workspace has a
  second environment configured.** `Oracle.run()` reads `ctx.browsers[
  baseline_env]`/`ctx.browsers[candidate_env]` directly and raises
  `KeyError` if either is missing (see `agents/oracle.py`) -- there is no
  graceful "nothing to diff" path inside Oracle itself the way Sentinel
  has one for "no incidents". That structural fact is what makes Oracle's
  skip different from Sentinel's: this module never even constructs an
  `Oracle` instance, let alone calls `run()` on it, when
  `Workspace.environments` names fewer than two environments -- but it
  DOES still write a `Step` explaining why, because "this workspace could
  turn Oracle on and hasn't" is a configuration gap worth a customer
  noticing once, not a routine, expected state the way "no incidents
  today" is. See `_oracle_step`.
- **Economist runs last, unconditionally, and never blocks anything.**
  It holds no write scope at all (`gateway/policy.py`'s SCOPES has no
  write tool for `"economist"`), so there is nothing about running it
  that could gate or fail the run out from under an otherwise-successful
  pass -- it is pure advice about the SUITE's own long-term cost, informed
  by everything the run up to this point just did. Running it last, after
  Runner/Triager/Surgeon, is what lets a future edit to those agents that
  starts tagging `Behaviour.tags` with `duration_ms`/`repairs`/
  `green_streak` (the real gap `agents/economist.py`'s own module
  docstring names) feed Economist's recommendations for the SAME run that
  produced them, not only the next one.

Three carried rulings from the fix-round review, and how each is met
--------------------------------------------------------------------------
1. **The orchestrator calls `Repo.claim_run`, which calls `Repo.put_run`.**
   `agents/chaos.py`'s own module docstring names the defect directly:
   nothing before this task ever called `put_run` in production, so
   Chaos's observed-p99 branch never executed outside a hand-seeded test.
   `execute()` below calls `claim_run` (which sets `state="running"` via a
   real `put_run`) before a single agent runs, and `_finish` calls
   `put_run` again at the end to record the terminal state -- every real
   run this module drives now leaves a real `Run` row with a real
   `duration_ms` a LATER run's Chaos step can read a p99 out of. See
   `_finish`'s own note on `duration_ms`.
2. **Triager is handed Runner's actual failure list.** `_execute_sequence`
   reads `runner_result.data["failures"]` and constructs
   `Triager(only_specs=[...])` with exactly those spec paths -- not the
   default (every spec in the workspace, re-run `attempts` times
   regardless of whether it just passed). See `agents/triager.py`'s own
   `only_specs` docstring for the other half of this.
3. **Runner's watchdog thread leak is fixed by this module's OWN
   lifetime, not by editing `agents/runner.py`.** That file's `_run_all`
   deliberately calls `pool.shutdown(wait=False)` on a batch timeout,
   leaving a stuck spec's thread running in the background -- see its own
   module docstring for why: Python cannot forcibly kill a thread, and
   joining here would defeat the whole point of the watchdog. That leak is
   harmless in a process that exits right after the one run it was started
   for (a Cloud Run Job's container), and genuinely wrong in a long-lived
   one (a stray thread accumulating forever inside a warm server process).
   `job/worker.py` -- THIS task's own file, not `agents/runner.py` -- is
   what fixes the distinction: it is a short-lived, one-run-per-process
   entrypoint by construction (see that module's own docstring), which is
   exactly the assumption that makes `Runner`'s existing behaviour safe
   without touching a single line of it.

Three distinct outcomes, and how each is told apart
--------------------------------------------------------------------------
`_step` (below) is the one place every one of the eleven agents' calls
goes through, and it is where these three shapes are told apart -- get
this wrong and either a gate silently becomes a crash, or a policy block
silently stops the whole run:

- `GatewayError(needs_human=True)` -- a GATE. `_step` writes the `Step`
  with `outcome="gated"` and raises the internal `_HaltRun("finished")`
  control-flow signal: a gate is a SUCCESSFUL run that needs a signature,
  not a failure. `agents/surgeon.py` also has its own internal convention
  for the identical case -- it catches its own `pr.merge` `GatewayError`
  and returns an `AgentResult(outcome="gated", ...)` normally rather than
  letting the exception propagate (see that module's docstring). `_step`
  treats a NORMALLY-returned `AgentResult` whose own `outcome=="gated"`
  exactly the same way -- see the `isinstance` check below -- so Surgeon's
  self-caught gate and a raised `GatewayError` gate from any OTHER agent
  are indistinguishable from this module's point of view, on purpose.
- `GatewayError` without `needs_human` -- a BLOCK. `_step` writes the
  `Step` with `outcome="blocked"` and returns `None` -- the sequence
  CONTINUES. A blocked Chaos (its `env.write` denied outright for a
  target that is not staging) must not stop the Runner from ever running;
  see `test_a_blocked_chaos_does_not_stop_the_runner`.
- Any other exception -- an ERROR. `_step` records the step with
  `outcome="error"` and `detail=type(exc).__name__` -- the exception's
  TYPE, deliberately never `str(exc)` and never a traceback: either can
  carry PII (a site-derived string interpolated into an error message,
  the way `core/web.py`'s own `_describe` worries about for exactly this
  reason). `_step` then raises `_HaltRun("failed")`: an unexpected error
  stops the run outright, but every `Step` already written up to that
  point survives untouched in Firestore -- nothing here rewinds or
  deletes them.

Judgement calls this module makes about what a real worker meets
--------------------------------------------------------------------------
- **An agent that hangs.** Nothing in this module adds a per-agent
  watchdog. `agents/runner.py`'s own module docstring already explains why
  Python cannot forcibly kill a thread; the same limitation applies to
  every other agent's `run()` call here, and a general per-call timeout
  (a second thread racing the agent's own) would need to solve the exact
  un-killable-thread problem Runner's own docstring documents as
  unsolved, for every one of the other ten agents, not just Runner's own
  spec loop. The right layer for this is the Cloud Run Job's own
  configured execution timeout (an infrastructure setting, not something
  `Orchestrator.execute` can enforce cheaply from inside one Python
  process) -- flagged here explicitly rather than half-solved with a
  thread this module has no better way to kill than Runner's own already
  does.
- **An agent that returns `None`** (or anything that is not an
  `AgentResult`). `_step` checks `isinstance(result, AgentResult)` right
  after a successful (non-raising) call and treats a violation of that
  contract exactly like an unexpected exception: `outcome="error"`,
  `detail` naming the wrong type returned, the run stops `"failed"`. A
  malformed agent is not silently treated as "did nothing".
- **Two workers started for the same run id.** `Repo.claim_run` is the
  actual fix -- see its own docstring -- but `execute()` also short-circuits
  BEFORE ever calling it: a plain, non-transactional read of `run.state`
  that is not `"queued"` returns the run as-is, unchanged, with no attempt
  to claim or re-run it. That first check is not itself race-safe (two
  workers could both pass it at once) -- `claim_run`'s own transaction is
  what actually closes the race in that case, returning `None` to
  whichever caller loses. The plain check above it exists only so the
  overwhelmingly common case (a worker retried well after the original
  execution already finished) does not pay for a transaction it cannot
  possibly need.
- **A run whose workspace was deleted mid-flight.** `execute()` checks
  `self._repo.workspace(run.workspace_id) is None` before ever calling
  `claim_run` (which would itself refuse for the same reason, inside its
  own transaction) and marks the run `"failed"` outright -- there is
  nothing left to bill or scope agents to, and leaving the run `"queued"`
  forever (with no workspace left that could ever re-trigger it) would
  mean Task 14a's stream hangs on a run that can never move again.
- **`PLUMBLINE_RUN_ID` unset, or naming a run that does not exist.**
  `execute()` raises `ValueError` for a missing run id outright -- this is
  `job/worker.py`'s problem to catch and report as a startup failure (see
  that module's own docstring), not a state this module tries to represent
  as a `Run` row, since there is no workspace to attribute a `Step` to and
  no id to write one against.
"""

import time
import uuid

from agents.auditor import Auditor
from agents.author import Author
from agents.base import AgentContext, AgentResult
from agents.cartographer import Cartographer
from agents.chaos import Chaos
from agents.economist import Economist
from agents.healer import Healer
from agents.oracle import Oracle
from agents.runner import Runner
from agents.sentinel import Sentinel
from agents.surgeon import Surgeon
from agents.triager import Triager
from app.models import Run, Step
from gateway.gateway import GatewayError

DEFAULT_CHAOS_TARGET_ENV = "staging"


class _HaltRun(Exception):
    """Internal control-flow only -- never escapes `execute()`. Carries the
    terminal `Run.state` the halt should end in (`"finished"` for a gate,
    `"failed"` for an error), so `_step` (which is what DECIDES a halt is
    needed) and `execute()` (which is the only place that actually writes
    the terminal state) do not have to duplicate that decision."""

    def __init__(self, state: str):
        self.state = state


class Orchestrator:
    def __init__(self, repo, gateway, model_factory, browser_factory,
                 chaos_target_env: str = DEFAULT_CHAOS_TARGET_ENV):
        self._repo = repo
        self._gateway = gateway
        self._model_factory = model_factory
        self._browser_factory = browser_factory
        self._chaos_target_env = chaos_target_env
        # A per-`Step` hook, called immediately after each Step is written
        # to `repo` -- `None` by default (every real caller). Tests use it
        # to observe write ORDER directly (see
        # `test_every_step_is_written_as_it_happens_not_at_the_end`) rather
        # than only being able to inspect the final, already-complete
        # list `steps_for_run` returns after `execute` has returned.
        self._on_step = None

    def execute(self, run_id: str) -> Run:
        run = self._repo.run(run_id)
        if run is None:
            raise ValueError(f"no such run: {run_id!r}")

        if run.state != "queued":
            # Already claimed/running elsewhere, already terminal, or
            # cancelled before any worker reached it -- never re-run or
            # re-bill for a run id this call does not own. See the module
            # docstring's "two workers" judgement call.
            return run

        workspace = self._repo.workspace(run.workspace_id)
        if workspace is None:
            # Deleted between enqueue and pickup -- nothing left to bill or
            # scope agents to. See the module docstring.
            return self._finish(run, "failed")

        if workspace.fleet_paused:
            # Task 14c: `POST /api/agents/pause` -- "takes effect on the
            # next run, not mid-run" (that route's own contract). Checked
            # here, before `claim_run` is ever called, so a paused
            # workspace's queued run is left exactly as it is: still
            # `queued`, `runs_used` untouched, unbilled, ready to run the
            # moment `POST /api/agents/resume` clears the flag and some
            # worker (a retry of this same execution, or a fresh one)
            # reaches it again. Never reached for a run this process has
            # already claimed -- pausing mid-sequence cannot interrupt it.
            return run

        claimed = self._repo.claim_run(run_id)
        if claimed is None:
            # Lost a genuine race to another worker between the checks
            # above and this call. `claim_run`'s own transaction is the
            # actual guarantee; report whatever the winner left behind.
            return self._repo.run(run_id)
        run = claimed  # state == "running"; runs_used already incremented

        ctx = self._build_context(run)
        try:
            self._execute_sequence(ctx, run)
        except _HaltRun as halt:
            return self._finish(run, halt.state)
        except Exception:
            # A genuinely unexpected failure escaping the sequence itself
            # (not one raised out of any single agent -- every one of
            # those is already caught inside `_step`) still must not leave
            # the run stuck in "running" forever.
            return self._finish(run, "failed")
        return self._finish(run, "finished")

    # -- context ------------------------------------------------------

    def _build_context(self, run: Run) -> AgentContext:
        return AgentContext(
            workspace_id=run.workspace_id, run_id=run.id,
            gateway=self._gateway, model=self._model_factory(),
            browser=self._browser_factory(), repo=self._repo,
        )

    # -- the sequence itself --------------------------------------------

    def _execute_sequence(self, ctx: AgentContext, run: Run) -> None:
        # Fix round 1: Sentinel now runs unconditionally, every time --
        # see the module docstring's own updated note. It used to be
        # gated on `_has_open_incidents`, silently absent otherwise; that
        # made a genuine bug in the precondition check (a wrong status
        # string, a wrong workspace_id, a query fault) indistinguishable
        # from the ordinary "no incidents today" case -- both produce zero
        # rows and zero steps. `Sentinel.run()` already returns a cheap,
        # honest "No open incidents" AgentResult when there is nothing to
        # do (see agents/sentinel.py), so there was never a real need to
        # suppress its step at all.
        self._step(Sentinel(), ctx, run)

        cartographer_result = self._step(Cartographer(), ctx, run)
        if cartographer_result is not None and not cartographer_result.data.get("routes"):
            # Nothing to map means nothing downstream has anything to do
            # against -- stop here, successfully, rather than running ten
            # more agents against an empty graph. Cartographer's own
            # `detail` already says why (new routes vs. unreachable ones --
            # see agents/cartographer.py), so this module adds nothing of
            # its own to it.
            raise _HaltRun("finished")

        self._step(Auditor(), ctx, run)
        self._oracle_step(ctx, run)

        self._step(Author(), ctx, run)
        self._step(Healer(), ctx, run)
        self._step(Chaos(target_env=self._chaos_target_env), ctx, run)

        runner_result = self._step(Runner(), ctx, run)
        failures = runner_result.data.get("failures") if runner_result is not None else None
        if failures:
            only_specs = [f["spec"] for f in failures]
            self._step(Triager(only_specs=only_specs), ctx, run)
            self._step(Surgeon(), ctx, run)
        # A blocked or clean Runner (no failures, or blocked outright with
        # nothing to hand off) skips Triager/Surgeon identically -- there
        # is nothing to triage either way. See the module docstring's
        # "any other exception"/GatewayError discussion for why a blocked
        # call returns `None` from `_step` rather than an `AgentResult`.

        self._step(Economist(), ctx, run)

    def _oracle_step(self, ctx: AgentContext, run: Run) -> None:
        workspace = self._repo.workspace(ctx.workspace_id)
        envs = list(workspace.environments) if workspace is not None else []
        if len(envs) < 2:
            self._write_step(
                run, "oracle", "Oracle skipped",
                "needs a second environment configured on the workspace "
                f"(found {len(envs)}); set Workspace.environments to at "
                "least two names to enable differential testing.",
                "skipped", time.monotonic(),
            )
            return
        baseline_env, candidate_env = envs[0], envs[1]
        ctx.browsers[baseline_env] = self._browser_factory(baseline_env)
        ctx.browsers[candidate_env] = self._browser_factory(candidate_env)
        self._step(Oracle(baseline_env=baseline_env, candidate_env=candidate_env), ctx, run)

    # -- per-agent execution, Step-writing, and outcome classification --

    def _step(self, agent, ctx: AgentContext, run: Run) -> AgentResult | None:
        """Run one agent, write exactly one `Step` for it IMMEDIATELY
        (before this method returns, and therefore before the next agent
        in `_execute_sequence` ever starts) -- Task 14a's SSE stream tails
        Firestore on a 1s poll, so a batched write at the very end of
        `execute()` would mean nothing appears there until the whole run
        is already over. See the module docstring for how each of the
        three outcome shapes below is told apart.

        Returns the agent's own `AgentResult` when it completed normally
        and was not a gate -- the caller reads `.data` off that for the
        Cartographer/Runner short-circuits. Returns `None` for a blocked
        call (nothing to read `.data` off of; `fn()` never ran) or for a
        halt (though a halt is a raised `_HaltRun`, not a return -- callers
        never actually see `None` from that path, since it never reaches
        a `return` at all).
        """
        started = time.monotonic()
        try:
            result = agent.run(ctx)
        except GatewayError as exc:
            outcome = "gated" if exc.needs_human else "blocked"
            self._write_step(
                run, agent.name, f"{agent.name} was denied: {exc.reason}",
                exc.reason, outcome, started,
            )
            if exc.needs_human:
                raise _HaltRun("finished") from None
            return None
        except Exception as exc:
            self._write_step(
                run, agent.name, f"{agent.name} raised an unexpected error",
                type(exc).__name__, "error", started,
            )
            raise _HaltRun("failed") from exc

        if not isinstance(result, AgentResult):
            self._write_step(
                run, agent.name, f"{agent.name} returned no result",
                f"expected an AgentResult, got {type(result).__name__}",
                "error", started,
            )
            raise _HaltRun("failed")

        self._write_step(run, agent.name, result.summary, result.detail, result.outcome, started)
        if result.outcome == "gated":
            # Surgeon's own self-caught pr.merge gate (agents/surgeon.py)
            # takes this path -- a normally-RETURNED AgentResult, not a
            # raised GatewayError -- and is treated identically to one.
            raise _HaltRun("finished")
        return result

    def _write_step(self, run: Run, agent_name: str, summary: str, detail: str,
                     outcome: str, started: float) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        step = Step(
            id=f"st_{uuid.uuid4().hex[:12]}", run_id=run.id, agent=agent_name,
            summary=summary, detail=detail, outcome=outcome, duration_ms=duration_ms,
        )
        self._repo.append_step(step)
        if self._on_step is not None:
            self._on_step(step)

    # -- terminal state -----------------------------------------------

    def _finish(self, run: Run, state: str) -> Run:
        # Re-read rather than trust the caller's `run` object: `_step`
        # never mutates `Run` itself (only ever writes `Step` rows), so the
        # only field that could have changed underneath it since
        # `claim_run` is one nothing here has touched -- re-reading is
        # cheap defence against a future edit assuming otherwise, not a
        # sign anything currently races here.
        #
        # `duration_ms` is stamped here, once, at the run's own natural end
        # -- this IS the `put_run` call `agents/chaos.py`'s module
        # docstring names as missing in production (see this module's own
        # docstring, ruling 1): every run this method finishes leaves a
        # real duration a LATER run's Chaos step can read a p99 out of.
        current = self._repo.run(run.id) or run
        duration_ms = int((time.time() - current.started_at) * 1000)
        finished = type(current)(**{**current.__dict__, "state": state, "duration_ms": max(0, duration_ms)})
        self._repo.put_run(finished)
        return finished
