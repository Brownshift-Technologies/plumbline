import os
import uuid

import pytest

# Explicit, not incidental: `app/main.py`'s `build_app` refuses to start
# with its fixed, source-visible OAuth state-signing fallback (see that
# module's `_INSECURE_DEV_OAUTH_SECRET`) unless PLUMBLINE_ENV says this is
# not production -- and that includes the module-level `app = build_app()`
# at the bottom of app/main.py, which runs as an import side effect the
# instant `from app.main import build_app` below executes, well before any
# fixture (even the autouse `restore_environ` one) has a chance to run.
# `setdefault`, not a plain assignment: a caller who deliberately runs this
# suite under a different PLUMBLINE_ENV (CI verifying the guard itself,
# say) is not overridden. Setting this once, here, is what lets every
# other test file that builds its own `PlumblineConfig` by hand (there are
# several -- test_gateway.py, test_ledger.py, test_repo.py, test_sessions.py,
# test_conftest_fixtures.py) call `build_app` without individually knowing
# this guard exists at all.
os.environ.setdefault("PLUMBLINE_ENV", "test")

from fastapi.testclient import TestClient

from app.main import build_app
from app.models import Membership, User, Workspace
from app.repo import Repo
from app.security import hash_password, new_totp_secret
from app.settings import PlumblineConfig
from core.fakes import FakeFirestore


@pytest.fixture(autouse=True)
def restore_environ():
    """Snapshot and restore ``os.environ`` around every test.

    ``load_config`` mutates the process environment (GOOGLE_GENAI_USE_VERTEXAI,
    GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION) by design. Without this
    fixture those writes escape the test that made them and persist for the
    rest of the pytest session, so a test run in isolation and the same test
    run inside the full suite see different starting environments — and any
    later test that boots the app in-process observes whichever value the
    first ``load_config`` caller happened to write. This lives in conftest so
    every test module in the core inherits it.
    """
    snapshot = os.environ.copy()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


# --- shared app-level fixtures ----------------------------------------------
#
# Everything below is the deliverable Task 8a exists to hand to Tasks
# 14a-14f (roughly forty tests across those tasks depend on these exact
# fixture names and shapes): one fully-wired `FastAPI` app per test, backed
# by that same test's `FakeFirestore`, plus a signed-in `TestClient` for
# each of the roles a route needs to check `require_role` against.
# `tests/test_conftest_fixtures.py` exercises every one of these directly,
# rather than leaving their correctness to be discovered later as a
# collection failure in a task that merely assumes they work.


@pytest.fixture
def config():
    return PlumblineConfig(
        project_id="test",
        location="us-central1",
        vertex_location="global",
        model="gemini-3.5-flash",
        firestore_prefix="plumbline",
    )


@pytest.fixture
def repo(config):
    return Repo(config, client=FakeFirestore())


@pytest.fixture
def app(config, repo):
    """One app per test, sharing that test's repo so fixtures and requests
    see the same store."""
    return build_app(config=config, repo=repo)


@pytest.fixture
def sessions(app):
    return app.state.sessions


@pytest.fixture
def ledger(app):
    return app.state.ledger


@pytest.fixture
def client(app):
    # base_url is "https://" rather than TestClient's plain-http default.
    # `app/auth_routes.py`'s `_set_cookie` sets `secure=True` on
    # `pl_session` -- correct and required in production (Cloud Run
    # terminates TLS in front of the app) -- but a `Secure` cookie is one
    # `http.cookiejar` (which both httpx and httpx2 delegate cookie
    # storage/matching to) refuses to ever re-attach to a plain-http
    # request: `DefaultCookiePolicy.return_ok_secure` checks
    # `request.type in ("https", "wss")` and silently drops the cookie
    # otherwise. Against the default `http://testserver` base_url, that
    # made every signup/signin-then-authenticated-request test in this
    # task fail with 401 -- not because auth was broken, but because the
    # *test* was silently stripping its own cookie before the second
    # request went out. `https://testserver` is exactly the fix
    # Starlette's own TestClient docs recommend for testing secure
    # cookies, and it costs nothing else: ASGI transport never touches a
    # real socket or a real TLS handshake either way.
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _member(repo, sessions, client, role, *, totp=True, runs_used=0, run_limit=500):
    ws = Workspace(
        id="ws1", name="Acme", repo="acme/storefront", runs_used=runs_used, run_limit=run_limit
    )
    repo.put_workspace(ws)
    user = User(
        id=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{role}@acme.com",
        password_hash=hash_password("correct horse battery"),
        name=role.title(),
        totp_secret=new_totp_secret() if totp else None,
    )
    repo.put_user(user)
    repo.put_membership(
        Membership(id=f"m_{uuid.uuid4().hex[:8]}", user_id=user.id, workspace_id=ws.id, role=role)
    )
    sess = sessions.issue(user.id, ws.id)
    client.cookies.set("pl_session", sess.id)
    return client


@pytest.fixture
def client_as_owner(repo, sessions, client):
    return _member(repo, sessions, client, "owner")


@pytest.fixture
def client_owner_no_totp(repo, sessions, client):
    return _member(repo, sessions, client, "owner", totp=False)


@pytest.fixture
def client_as_approver(repo, sessions, client):
    return _member(repo, sessions, client, "approver")


@pytest.fixture
def client_as_reader(repo, sessions, client):
    return _member(repo, sessions, client, "reader")


@pytest.fixture
def client_at_limit(repo, sessions, client):
    return _member(repo, sessions, client, "owner", runs_used=500, run_limit=500)


@pytest.fixture
def client_demo(client):
    client.post("/api/auth/demo")
    return client
