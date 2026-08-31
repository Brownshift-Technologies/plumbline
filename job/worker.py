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


def _resolve_navigation_target(env: str | None) -> str:
    """Tier 2 (2026-08-30 contract, item 4): the URL `_browser_factory`
    should point a fresh driver at before the fleet starts, or `""` when
    there is nothing to navigate to yet.

    `""` is not this function's failure to report -- Cartographer's own
    explicit `Workspace.target_url` check (`agents/cartographer.py`) is
    what turns a genuinely unset target into the run's loud, explanatory
    failure step; a driver that never got navigated anywhere simply stays
    on `about:blank` until Cartographer's first real `goto` call, exactly
    as before this task, for every run that DOES have a `target_url` (the
    ones this function's `""` branch used to mean "we don't have this
    wiring yet" now means "Cartographer already knows and will say so").

    Reads `PLUMBLINE_RUN_ID` from the environment again, and builds its
    own short-lived `Repo`, rather than threading `run`/`workspace`
    through `job/worker.py`'s `browser_factory=` argument -- unlike
    `_checkout_factory` above (which Orchestrator calls with `workspace`
    directly, a Tier 2 addition to ITS OWN call site), `browser_factory`'s
    shape is the fixed Tier 2 contract's own: a bare
    `(env: str | None = None) -> Driver` callable Agent B's Playwright
    work and the Oracle path (`job/orchestrator.py`'s `_oracle_step`) both
    already build against. Resolving lazily, at CALL time, keeps that
    signature untouched rather than widening it out from under two other
    files being edited concurrently against the same contract.

    `env`, when Oracle names one (`workspace.environments[0]`/`[1]`), is
    accepted but does not change the URL returned: `Workspace` records
    ordered environment NAMES only (see that field's own docstring in
    `app/models.py`), never a URL per name -- there is exactly one URL on
    a workspace today, `target_url`, so every named environment resolves
    to it. This is the one place a future per-environment URL map would
    plug in; falling back to `target_url` is not a special case today,
    it is the only value there is.
    """
    run_id = os.environ.get("PLUMBLINE_RUN_ID", "").strip()
    if not run_id:
        return ""
    repo = Repo(load_settings())
    run = repo.run(run_id)
    if run is None:
        return ""
    workspace = repo.workspace(run.workspace_id)
    if workspace is None:
        return ""
    return workspace.target_url


def _browser_factory(env: str | None = None):
    """The real driver, one per call -- `job/orchestrator.py` calls this
    once for the run's primary `ctx.browser` (`env=None`) and, only when
    Oracle is actually going to run, once more per named environment. A
    fresh `PlaywrightDriver` per call rather than one shared instance
    across the whole run: `agents/runner.py` drives specs concurrently
    (`ThreadPoolExecutor`), and a single Playwright page is not something
    this codebase's own driver claims is safe to share across threads (see
    `agents/browser.py`'s own module docstring on how far
    `PlaywrightDriver` is actually exercised today).

    Tier 2, item 4: navigates the fresh driver to `target_url` (or the
    named `env`'s resolved URL -- see `_resolve_navigation_target`)
    BEFORE handing it back, so `agents/cartographer.py`'s crawl -- and
    every other agent driving `ctx.browser` after it -- lands on the
    workspace's own site from the very first call, not `about:blank`.
    """
    from agents.browser import PlaywrightDriver

    driver = PlaywrightDriver()
    driver.start()
    target = _resolve_navigation_target(env)
    if target:
        driver.goto(target)
    return driver


def _checkout_factory(workspace):
    """Tier 2: one real `job.checkout.RepoCheckout` per run, or `None`.

    `None` for a demo sandbox (`is_demo`, seeded by `seed/demo.py` -- "a
    demo run stays simulated" is this task's own non-negotiable) and for
    any workspace that has not connected a GitHub repo yet
    (`installation_id`/`repo_full_name` both start empty until
    `app/github_routes.py`'s install/connect flow runs). `Orchestrator`
    passes whatever this returns straight onto `ctx.checkout`, so `None`
    here is exactly what makes Author/Healer/Surgeon skip with an
    explanatory step rather than crash reaching for a repo that was never
    connected.

    Builds its own `GitHubApp` from the same two env vars
    `app/main.py`'s FastAPI process reads (`GITHUB_APP_ID`,
    `GITHUB_APP_PRIVATE_KEY`) -- this Job process never imports or shares
    that FastAPI app's `state`, so it mints its own App identity the same
    way, not a second implementation of one (see `app/github.py`'s own
    module docstring on why there is exactly one client class for this).
    """
    if workspace is None or workspace.is_demo:
        return None
    if not workspace.installation_id or not workspace.repo_full_name:
        return None

    from app.github import GitHubApp
    from job.checkout import RepoCheckout

    github_app = GitHubApp(
        os.environ.get("GITHUB_APP_ID", ""),
        os.environ.get("GITHUB_APP_PRIVATE_KEY", "").encode(),
    )
    github_app.bind(workspace.repo_full_name, workspace.installation_id)
    token = github_app.installation_token(workspace.installation_id)
    default_branch = workspace.default_branch or "main"

    checkout = RepoCheckout.clone(workspace.repo_full_name, token, ref=default_branch)
    # Set after `clone()` returns, deliberately -- `clone()`'s own
    # signature is the Tier 2 contract's fixed one, unchanged; these are
    # the extra attributes `agents/surgeon.py` reads to open a real pull
    # request (see `job/checkout.py`'s own module docstring).
    checkout.github = github_app
    checkout.repo_full_name = workspace.repo_full_name
    checkout.default_branch = default_branch
    return checkout


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
        checkout_factory=_checkout_factory,
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
