"""The Plumbline FastAPI app object.

Nothing before this module builds the actual app -- Tasks 1-7 shipped the
collaborators (`Repo`, `SessionService`, `Ledger`, `Gateway`, `security`)
without ever wiring them together behind HTTP. This module is that wiring:
`build_app` constructs one of each collaborator, mounts every router
Plumbline exposes, and stores the collaborators on `app.state` so a
dependency (`app/deps.py`) or a route module never has to reach for a
module-level singleton to find them.

`build_app(config=None, repo=None)` rather than a bare module-level `app`
exists specifically for tests: `tests/conftest.py`'s `app` fixture calls it
with a `FakeFirestore`-backed `Repo` so each test gets an app wired to its
own in-memory store, isolated from every other test's. Production (`main:app`
under uvicorn) still gets a working module-level `app` below, built with the
real `load_settings()`/`Repo` defaults -- but that instance is never shared
with a test.

`core.web.create_app(on_event, "plumbline-api")` is the base: it already
gives Plumbline the Pub/Sub `/events` receiver and the fail-closed-to-204
error handling that library's own tests cover (see `core/web.py`). This
module does not touch `/events` or `/healthz` at all -- it only adds to what
`create_app` already returns.
"""

import uuid

from fastapi import FastAPI

from app.auth_routes import router as auth_router
from app.models import User, Workspace
from app.repo import Repo
from app.sessions import SessionService
from app.settings import PlumblineConfig, load_settings
from core.telemetry import log_event
from core.web import create_app
from gateway.gateway import Gateway
from gateway.ledger import Ledger


def _on_event(payload: dict) -> None:
    """Pub/Sub handler for Plumbline's own topics.

    No Plumbline code publishes to this service's subscription yet --
    Task 14a+ wires `run.requested`/`run.step`/`run.finished` through
    `core.events.enqueue_job` and the agent fleet that consumes them.
    Until that lands, this is deliberately a no-op beyond a log line: it
    makes a message's arrival visible in Cloud Logging instead of it
    disappearing silently, without inventing handling for events nothing
    publishes yet. `core.web.create_app`'s `/events` endpoint acks 204
    regardless of what this function does or raises (see that module's
    docstring for why), so there is no correctness reason to do more here
    before there is something real to do.
    """
    keys = sorted(payload.keys()) if isinstance(payload, dict) else None
    log_event("event.plumbline_received", severity="INFO", keys=keys)


def _bootstrap_workspace(user: User) -> Workspace:
    """Build a fresh `Workspace` for a brand-new user, but do not persist
    it -- `signup` (in `app/auth_routes.py`) is the caller, and it also
    needs to create the owning `Membership` in the same breath, so it owns
    the single `repo.put_workspace` write rather than this function racing
    it with one of its own.

    `repo` is left unconnected (`repo=""`) on purpose: nothing about
    signing up implies a specific GitHub repository, and no task before
    this one defines a "connect your repository" flow for `build_app` to
    call into. An empty string is a valid, honest starting state -- every
    read of `Workspace.repo` downstream must already tolerate a workspace
    that has not been pointed at a repo yet, exactly as it must tolerate
    one that has.
    """
    return Workspace(
        id=f"ws_{uuid.uuid4().hex[:12]}",
        name=f"{user.name}'s Workspace",
        repo="",
    )


def _seed_demo_if_missing_factory(config: PlumblineConfig, repo: Repo):
    """Build the `seed_demo_if_missing` closure stored on `app.state`.

    `seed/demo.py` (Task 15) does not exist yet -- this is a forward
    dependency, not a bug: this task owns the demo *entry point*
    (`POST /api/auth/demo`), Task 15 owns the demo *data*. Rather than
    have `app/auth_routes.py` import `seed.demo` directly (which would
    make every test importing `app.auth_routes` fail at collection until
    Task 15 lands -- the worst outcome for a module ~40 downstream tests
    depend on), the import is attempted lazily, inside the closure, only
    when a demo session is actually requested. Until Task 15 ships this
    is a no-op: `POST /api/auth/demo` still issues a real, working
    session (`SessionService.issue` only needs `config.demo_workspace_id`,
    a plain string -- it does not require the workspace to already have a
    Firestore row), it is simply against a workspace with nothing seeded
    in it yet. Once `seed.demo.seed_demo(repo, config) -> Workspace`
    exists, this starts calling it on every demo entry, and `seed_demo`
    is documented (Task 15's own brief) to be idempotent, so calling it
    once per demo session is the intended usage, not wasted work.
    """

    def seed_demo_if_missing() -> None:
        try:
            from seed.demo import seed_demo
        except ImportError:
            log_event(
                "demo.seed_not_available",
                severity="INFO",
                detail="seed.demo is a Task 15 forward dependency; demo entry proceeds unseeded",
            )
            return
        seed_demo(repo, config)

    return seed_demo_if_missing


def build_app(config: PlumblineConfig | None = None, repo: Repo | None = None) -> FastAPI:
    """Construct one fully-wired Plumbline `FastAPI` app.

    `config`/`repo` are injectable so a caller -- chiefly
    `tests/conftest.py`'s `app` fixture -- can hand this a
    `FakeFirestore`-backed `Repo` instead of a real Firestore client. Every
    other collaborator (`SessionService`, `Ledger`, `Gateway`) is built
    fresh from whichever `repo`/`config` this call ends up with, so a test
    app and the production app share no state at all -- not even
    incidentally through a module-level default.
    """
    cfg = config or load_settings()
    rp = repo or Repo(cfg)

    app = create_app(_on_event, "plumbline-api")

    app.state.config = cfg
    app.state.repo = rp
    app.state.sessions = SessionService(rp, cfg)
    app.state.ledger = Ledger(rp)
    app.state.gateway = Gateway(rp, app.state.ledger)
    app.state.bootstrap_workspace = _bootstrap_workspace
    app.state.seed_demo_if_missing = _seed_demo_if_missing_factory(cfg, rp)

    app.include_router(auth_router)

    @app.get("/_health")
    def _health():
        # The path deploy scripts, uptime probes, and Task 18's tests use.
        # `core.web.create_app` already registers `/healthz`, but that is
        # the one path Google's Cloud Run frontend intercepts and answers
        # itself before the request ever reaches this container -- a bare
        # `GET /healthz` never arrives in production, so it cannot be
        # relied on to prove the app (as opposed to the platform) is up.
        # `/_health` is a normal application route and reports live config
        # values, not a hardcoded string, so a probe against it also
        # catches a deploy that silently picked up the wrong model or
        # location.
        return {
            "ok": True,
            "model": cfg.model,
            "gemini_location": cfg.vertex_location,
            "service": "plumbline-api",
        }

    return app


app = build_app()
