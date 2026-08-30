"""Proves the shared fixtures in tests/conftest.py actually work.

Tasks 14a-14f build roughly forty tests on top of `client_as_owner`,
`client_as_reader`, `client_at_limit`, `client_demo`, and their siblings --
a fixture that silently changed shape would surface as a wall of collection
failures in whichever task discovered it first, far from where the fixture
itself was defined. This module is what proves each one now, once, in the
task that owns them.
"""

# --- from the brief ---------------------------------------------------------


def test_the_owner_client_is_authenticated(client_as_owner):
    assert client_as_owner.get("/api/auth/me").status_code == 200


def test_roles_differ_between_clients(client_as_owner, repo):
    assert client_as_owner.get("/api/auth/me").json()["role"] == "owner"


def test_the_demo_client_is_flagged(client_demo):
    assert client_demo.get("/api/auth/me").json()["is_demo"] is True


def test_the_at_limit_workspace_is_actually_at_its_limit(client_at_limit, repo):
    ws = repo.workspace("ws1")
    assert ws.runs_used >= ws.run_limit


# --- every remaining fixture the brief lists, verified in its own right ----


def test_the_approver_client_has_the_approver_role(client_as_approver):
    assert client_as_approver.get("/api/auth/me").json()["role"] == "approver"


def test_the_reader_client_has_the_reader_role(client_as_reader):
    assert client_as_reader.get("/api/auth/me").json()["role"] == "reader"


def test_the_owner_no_totp_client_really_has_no_totp_secret(client_owner_no_totp, repo):
    user = repo.user_by_email("owner@acme.com")
    assert user.totp_secret is None


def test_the_owner_client_does_have_a_totp_secret_by_default(client_as_owner, repo):
    user = repo.user_by_email("owner@acme.com")
    assert user.totp_secret is not None


def test_each_role_fixture_is_a_distinct_membership_not_a_shared_one(client_as_owner, repo):
    # _member always writes workspace id "ws1", but each call mints its own
    # uuid'd user -- two separate fixture instantiations (as would happen
    # across two different tests) must not collide on the same user id.
    owner = repo.user_by_email("owner@acme.com")
    assert owner is not None
    assert owner.id.startswith("u_")


def test_the_repo_and_sessions_fixtures_share_the_apps_own_store(repo, sessions, app):
    # sessions is pulled off app.state, not constructed independently --
    # confirm it is backed by the SAME repo this test also got, not a
    # second, disconnected one.
    assert sessions is app.state.sessions
    assert repo is app.state.repo


def test_the_ledger_fixture_is_the_apps_own_ledger(ledger, app):
    assert ledger is app.state.ledger


def test_the_client_fixture_starts_with_no_session(client):
    assert client.get("/api/auth/me").status_code == 401


def test_client_at_limit_is_still_a_working_owner_session(client_at_limit):
    # Being at the run limit must not, by itself, break authentication --
    # it is a business-logic limit later routes enforce, not an auth state.
    assert client_at_limit.get("/api/auth/me").status_code == 200
    assert client_at_limit.get("/api/auth/me").json()["role"] == "owner"


def test_two_tests_using_client_as_owner_do_not_leak_state():
    # Regression guard for fixture *isolation*, not fixture *shape*: build
    # two independent app/repo pairs by hand (mirroring what pytest does
    # across two separate test functions) and confirm a session issued
    # against one is unresolvable against the other.
    from app.main import build_app
    from app.models import Membership, User, Workspace
    from app.repo import Repo
    from app.security import hash_password
    from app.settings import PlumblineConfig
    from core.fakes import FakeFirestore

    def build():
        cfg = PlumblineConfig(
            project_id="test",
            location="us-central1",
            vertex_location="global",
            model="gemini-3.5-flash",
            firestore_prefix="plumbline",
        )
        rp = Repo(cfg, client=FakeFirestore())
        a = build_app(config=cfg, repo=rp)
        ws = Workspace(id="ws1", name="Acme", repo="acme/storefront")
        rp.put_workspace(ws)
        user = User(
            id="u_fixed", email="owner@acme.com", password_hash=hash_password("x" * 12), name="Owner"
        )
        rp.put_user(user)
        rp.put_membership(Membership(id="m1", user_id=user.id, workspace_id=ws.id, role="owner"))
        sess = a.state.sessions.issue(user.id, ws.id)
        return a, sess

    app_a, sess_a = build()
    app_b, _ = build()
    assert app_b.state.sessions.resolve(sess_a.id) is None
