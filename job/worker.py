"""Task 13: the Cloud Run Job entrypoint. `main()` is what the container's
`ENTRYPOINT` actually runs -- read `PLUMBLINE_RUN_ID`, build one real
`Orchestrator`, execute exactly one run, report the outcome, exit.

Deliberately a short-lived, one-run-per-process script, not a server.
That is not a style choice this module makes casually: `job/orchestrator.py`'s
own module docstring leans on it directly to explain why
`agents/runner.py`'s `pool.shutdown(wait=False)` watchdog-thread leak needs
no fix in that file -- a stray thread left running when THIS process exits
right after (the container tears down, the leaked thread dies with it) is
categorically different from the same leak inside a long-lived server that
would accumulate one more stuck thread every time a spec timed out. Turning
this into a warm, reusable server process later would silently reopen that
exact leak; if that ever happens, `agents/runner.py`'s own watchdog needs
revisiting at the same time, not after.

`sys.exit(1)` on a failed run (not merely a non-zero return, but an actual
process exit) is what makes Cloud Run Jobs report the execution as failed --
a Job's own retry/alerting is keyed off the container's exit code, not off
anything this module could otherwise signal.

`publish_event(..., "run.finished", ...)` fires in BOTH the success and
the failure path -- "either way" is the brief's own word for it, and the
one thing this module refuses to let determine whether a subscriber hears
about a run ending is whether that ending was the good kind. It fires
AFTER `orchestrator.execute` has already returned and the run's terminal
`Run` row has already been written (`Orchestrator._finish`, called from
inside `execute`) -- so a subscriber that reacts to this event by reading
the run back from Firestore never observes a "finished" event for a run
that Firestore itself still says is "running".

Three PLUMBLINE_RUN_ID/run-lookup failure shapes, each handled explicitly
rather than left to an unhandled traceback:
- Unset entirely -- `main()` fails fast with a clear message before ever
  touching `Repo`/`Gateway`/the fleet at all.
- Set, but naming a run `Orchestrator.execute` cannot find --
  `execute()` raises `ValueError` for exactly this (see its own
  docstring); `main()` lets that surface as a loud, non-zero exit with the
  run id in the message, rather than silently doing nothing.
- Set, and the run exists but is not `"queued"` (already claimed by
  another worker, already finished, cancelled) -- `execute()` returns the
  run AS-IS without re-running the fleet or re-billing the workspace (see
  its own docstring's "two workers" discussion). `main()` treats that as a
  quiet success: nothing this process did caused a `run.finished` -- that
  event was either already published by whoever DID finish it, or never
  applies (a cancelled run) -- so this process does not publish a second,
  redundant one, and exits 0 rather than looking like a failure to Cloud
  Run Jobs' own retry logic for a run some other execution already owns.
"""

import os
import sys

from app.repo import Repo
from app.settings import load_settings
from core.events import publish_event
from core.gemini import GeminiModel
from core.telemetry import log_event
from gateway.gateway import Gateway
from gateway.ledger import Ledger
from job.orchestrator import Orchestrator


def _browser_factory(env: str | None = None):
    """The real driver, one per call -- `job/orchestrator.py` calls this
    once for the run's primary `ctx.browser` (`env=None`) and, only when
    Oracle is actually going to run, once more per named environment. A
    fresh `PlaywrightDriver` per call rather than one shared instance
    across the whole run: `agents/runner.py` drives specs concurrently
    (`ThreadPoolExecutor`), and a single Playwright page is not something
    this codebase's own driver claims is safe to share across threads (see
    `agents/browser.py`'s own module docstring on how far
    `PlaywrightDriver` is actually exercised today). `env` is accepted and
    currently unused beyond that -- pointing a driver at a NAMED
    environment's own base URL (rather than the workspace's single default
    target) is real, live-deployment wiring this task does not have enough
    of a contract for yet (no field anywhere records what URL "staging"
    or "production" actually resolves to for a given workspace); flagged
    here rather than guessed at.
    """
    from agents.browser import PlaywrightDriver

    driver = PlaywrightDriver()
    driver.start()
    return driver


def main() -> None:
    run_id = os.environ.get("PLUMBLINE_RUN_ID", "").strip()
    if not run_id:
        log_event("worker.missing_run_id", severity="ERROR")
        print("PLUMBLINE_RUN_ID is not set", file=sys.stderr)
        sys.exit(1)

    config = load_settings()
    repo = Repo(config)
    gateway = Gateway(repo, Ledger(repo))
    orchestrator = Orchestrator(
        repo=repo, gateway=gateway,
        model_factory=lambda: GeminiModel(config),
        browser_factory=_browser_factory,
    )

    try:
        run = orchestrator.execute(run_id)
    except ValueError as exc:
        log_event("worker.run_not_found", severity="ERROR", run_id=run_id, detail=str(exc))
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if run.state == "finished":
        publish_event(config, "run.finished", {"run_id": run.id, "workspace_id": run.workspace_id,
                                                 "state": run.state})
        log_event("worker.run_finished", run_id=run.id, state=run.state)
        sys.exit(0)
    if run.state == "failed":
        publish_event(config, "run.finished", {"run_id": run.id, "workspace_id": run.workspace_id,
                                                 "state": run.state})
        log_event("worker.run_finished", severity="ERROR", run_id=run.id, state=run.state)
        sys.exit(1)

    # Any other state means `execute()` declined to (re-)run this id at all
    # -- already claimed/finished by another execution, or cancelled
    # before a worker ever reached it. See the module docstring's third
    # failure shape: this process caused nothing, so it publishes nothing,
    # and exits 0 rather than reading as a failure to Cloud Run Jobs.
    log_event("worker.run_not_claimed", run_id=run.id, state=run.state)
    sys.exit(0)
