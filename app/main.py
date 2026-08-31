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

import os
import uuid

from fastapi import FastAPI

from app.account_routes import router as account_router
from app.agent_routes import router as agent_router
from app.api_keys import router as api_keys_router
from app.auth_routes import router as auth_router
from app.behaviour_routes import router as behaviour_router
from app.billing_routes import router as billing_router
from app.docs import register_docs
from app.finding_routes import router as finding_router
from app.ledger_routes import router as ledger_router
from app.models import User, Workspace
from app.oauth_routes import router as oauth_router
from app.providers import GitHubProvider, GoogleProvider, OktaProvider
from app.public_routes import router as public_router
from app.repo import Repo
from app.run_routes import router as run_router
from app.sessions import SessionService
from app.surface_routes import router as surface_router
from app.settings import PlumblineConfig, load_settings
from app.webhooks import dispatch_run_finished
from app.webhooks import router as webhooks_router
from core.events import enqueue_job
from core.telemetry import log_event
from core.web import create_app
from gateway.gateway import Gateway
from gateway.ledger import Ledger

# Insecure by design, loud about it, and used only when OAUTH_STATE_SECRET
# is unset -- see app/settings.py's PlumblineConfig.oauth_state_secret
# docstring for why this is a fixed fallback rather than a randomly
# generated one: a random-per-process secret would make `start` and
# `callback` disagree the moment they land on two different Cloud Run
# instances, which is exactly the class of bug Task 8b exists to fix
# (Task 6's per-process TOTP replay dict) rather than reintroduce elsewhere.
#
# Fix round 1: a fixed fallback that nothing ever *requires* setting a real
# secret to override is not a control, it is a comment -- this exact string
# lives in the source tree, and the source tree is on GitHub. `build_app`
# below refuses to start on it (raises) unless PLUMBLINE_ENV explicitly
# says this is not production. PLUMBLINE_ENV, not the incidental "is
# pytest running" signal `sys.modules` could give: `core/config.py` has no
# existing env-tier signal to reuse (its GCP_* variables are all
# project/location values, not a deploy-tier flag), so this is a new,
# narrowly-scoped one, read directly from the process environment rather
# than threaded through `PlumblineConfig` -- every test file in this repo
# that builds its own `PlumblineConfig` by hand (there are several) then
# gets the same guard behaviour for free from one env var
# `tests/conftest.py` sets once, at import time, rather than needing every
# one of those call sites updated individually.
_INSECURE_DEV_OAUTH_SECRET = "plumbline-dev-oauth-secret-DO-NOT-USE-IN-PRODUCTION"
# Deploy tiers that may run with no real OAUTH_STATE_SECRET configured.
# "production" (PLUMBLINE_ENV unset defaults here) is deliberately NOT a
# member -- an unset PLUMBLINE_ENV on a real Cloud Run deploy must fail
# closed, not fail open into "looked like dev".
_OAUTH_SECRET_OPTIONAL_ENVS = frozenset({"test", "dev"})


def _on_event_factory(repo: Repo, ledger: Ledger):
    """Build `build_app`'s `_on_event` Pub/Sub handler, closed over the SAME
    `repo`/`ledger` this app's routes use -- a factory (the same pattern
    `_seed_demo_if_missing_factory` below already uses) rather than a
    bare module-level function, because `core.web.create_app(_on_event,
    ...)` is called before `app.state.repo`/`app.state.ledger` exist (see
    `build_app`), so this closure is what lets the handler reach them at
    all without a second, divergent `Repo`/`Ledger` pair.

    `core.events.publish_event` embeds the event name as payload["type"]
    (see that function's own docstring: `{"type": event_type, **payload}`)
    -- `job/worker.py` is this codebase's only publisher today, firing
    `"run.finished"` in both the success and failure path with `run_id`/
    `workspace_id`/`state`. Task 14d's webhook mechanism
    (`app/webhooks.py`) hooks in exactly here: a `run.finished` push is
    the one webhook event this codebase has a real trigger for end to end
    (see `app/webhooks.py`'s own module docstring for why
    `finding.created`/`patch.ready`/`patch.approved` do not, yet).
    `core.web.create_app`'s `/events` endpoint acks 204 regardless of what
    this handler does or raises, so a malformed or unrecognised payload
    is simply logged and ignored, never a reason to fail the ack.
    """

    def _on_event(payload: dict) -> None:
        keys = sorted(payload.keys()) if isinstance(payload, dict) else None
        log_event("event.plumbline_received", severity="INFO", keys=keys)
        if not isinstance(payload, dict) or payload.get("type") != "run.finished":
            return
        workspace_id = payload.get("workspace_id")
        if not workspace_id:
            return
        dispatch_run_finished(repo, ledger, workspace_id, payload)

    return _on_event


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


def _deliver_reset_email_default(email: str, token: str) -> None:
    """Default `app.state.deliver_reset_email` -- see app/account_routes.py's
    module docstring for the whole hook, and why one is needed at all: this
    codebase has no email/SMS provider wired up yet (out of scope for this
    task), so the only thing a real deployment can do today is make the
    token's issuance observable in Cloud Logging for an operator, exactly
    the same "log it, don't fake it" stance `_seed_demo_if_missing_factory`
    takes toward its own Task 15 forward dependency above. The raw token
    itself is deliberately NOT included in the log line -- logging it would
    make Cloud Logging (durable, exportable, read by more people than the
    one intended recipient) an alternate way to obtain a working reset
    link, defeating the entire point of hashing it at rest.
    """
    log_event("auth.password_reset_issued", severity="INFO", email_domain=email.rsplit("@", 1)[-1])


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

    # Checked before anything else is built -- fail fast, with no partial
    # app object and no collaborators constructed, rather than raising
    # partway through wiring. See the module-level comment by
    # `_INSECURE_DEV_OAUTH_SECRET` for the full reasoning; this is the
    # enforcement of it.
    deploy_env = os.getenv("PLUMBLINE_ENV", "production")
    if not cfg.oauth_state_secret and deploy_env not in _OAUTH_SECRET_OPTIONAL_ENVS:
        # Fail hard, not soft: a signed CSRF state token whose signing key
        # is a fixed string in the source tree is not a CSRF defence at
        # all -- anyone who can read this repository (public or not, once
        # it is ever cloned, forked, or leaked) can forge a validly-signed
        # `state` and walk straight through app/oauth_routes.py's callback
        # checks. Refusing to boot is the only thing that reliably stops
        # this from reaching production merely because OAUTH_STATE_SECRET
        # was left unset in a Cloud Run env var/Secret Manager binding --
        # a warning log line is easy to miss; a container that never comes
        # up is not.
        raise RuntimeError(
            "OAUTH_STATE_SECRET is not set and PLUMBLINE_ENV="
            f"{deploy_env!r} is not one of {sorted(_OAUTH_SECRET_OPTIONAL_ENVS)} -- "
            "refusing to start with the fixed, source-visible OAuth state "
            "signing key in what looks like a production environment. Set "
            "OAUTH_STATE_SECRET (Secret Manager -- never bake it into the "
            "image), or set PLUMBLINE_ENV=dev for a non-production run "
            "that accepts the insecure fallback."
        )

    rp = repo or Repo(cfg)
    # Built before `create_app` (below), not after -- `_on_event_factory`
    # needs a real `Ledger` to dispatch webhooks with, and `create_app`
    # is what wires `_on_event` in as the `/events` handler. Reused as-is
    # on `app.state.ledger` just below, rather than constructed twice.
    ledger = Ledger(rp)

    app = create_app(_on_event_factory(rp, ledger), "plumbline-api")

    app.state.config = cfg
    app.state.repo = rp
    app.state.sessions = SessionService(rp, cfg)
    app.state.ledger = ledger
    app.state.gateway = Gateway(rp, app.state.ledger)
    app.state.bootstrap_workspace = _bootstrap_workspace
    app.state.seed_demo_if_missing = _seed_demo_if_missing_factory(cfg, rp)
    app.state.oauth_state_secret = cfg.oauth_state_secret or _INSECURE_DEV_OAUTH_SECRET
    # dict, not a fixed tuple of providers, so tests can add a "fake" entry
    # (`app.state.oauth_providers["fake"] = FakeProvider(...)`) without
    # this module ever importing the test double it has no business
    # knowing about.
    app.state.oauth_providers = {
        "google": GoogleProvider(cfg),
        "github": GitHubProvider(cfg),
        "okta": OktaProvider(cfg),
    }
    app.state.deliver_reset_email = _deliver_reset_email_default
    # `POST /api/runs` (Task 14a) enqueues a Cloud Run Job execution rather
    # than running the fleet in-process -- see app/run_routes.py's module
    # docstring. Injectable on `app.state`, the same pattern as
    # `seed_demo_if_missing`/`deliver_reset_email` just above, so tests can
    # swap in a stub that never resolves real GCP credentials.
    app.state.enqueue_job = lambda job_name, args: enqueue_job(cfg, job_name, args)

    app.include_router(auth_router)
    app.include_router(oauth_router)
    app.include_router(account_router)
    app.include_router(run_router)
    app.include_router(finding_router)
    app.include_router(surface_router)
    app.include_router(behaviour_router)
    app.include_router(agent_router)
    app.include_router(ledger_router)
    app.include_router(billing_router)
    app.include_router(api_keys_router)
    app.include_router(webhooks_router)
    app.include_router(public_router)
    register_docs(app)

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
