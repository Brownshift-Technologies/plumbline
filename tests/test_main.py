"""app/main.py: build_app assembly, /_health, and the inherited /healthz."""

# --- from the brief ---------------------------------------------------------


def test_health_is_reachable_at_underscore_health(client):
    assert client.get("/_health").status_code == 200


def test_health_reports_the_gated_model_and_location(client):
    body = client.get("/_health").json()
    assert body["model"] == "gemini-3.5-flash"
    assert body["gemini_location"] == "global"


def test_healthz_still_exists_for_the_inherited_library_route(client):
    assert client.get("/healthz").status_code == 200


def test_app_state_carries_every_collaborator(client):
    for name in ("config", "repo", "sessions", "ledger", "gateway"):
        assert getattr(client.app.state, name, None) is not None


# --- beyond the brief ---------------------------------------------------------


def test_health_reports_the_service_name(client):
    assert client.get("/_health").json()["service"] == "plumbline-api"


def test_health_ok_flag_is_true(client):
    assert client.get("/_health").json()["ok"] is True


def test_healthz_is_the_bare_status_body_from_core_web(client):
    # core.web.create_app's own contract for /healthz, unchanged by this
    # module -- this task must not have touched it.
    assert client.get("/healthz").json() == {"status": "ok"}


def test_build_app_with_no_arguments_still_returns_an_app():
    # The module-level `app = build_app()` at the bottom of app/main.py
    # exercises exactly this path (needed for `uvicorn app.main:app`), and
    # it must not require live GCP credentials merely to construct --
    # constructing a Store with no client override used to reach for a
    # real firestore.Client() eagerly, which raises DefaultCredentialsError
    # in any environment without ADC configured (this sandbox included).
    # See core/store.py's `_client` property for the fix.
    from app.main import build_app

    app = build_app()
    assert app.state.config is not None
    assert app.state.repo is not None


def test_build_app_injected_repo_is_the_one_app_state_uses(config, repo):
    from app.main import build_app

    app = build_app(config=config, repo=repo)
    assert app.state.repo is repo


def test_two_build_app_calls_do_not_share_collaborators(config):
    # Each call must construct its own SessionService/Ledger/Gateway, not
    # reach for a module-level singleton -- otherwise one test's app could
    # observe another test's sessions.
    from app.main import build_app
    from app.repo import Repo
    from core.fakes import FakeFirestore

    app_a = build_app(config=config, repo=Repo(config, client=FakeFirestore()))
    app_b = build_app(config=config, repo=Repo(config, client=FakeFirestore()))
    assert app_a.state.repo is not app_b.state.repo
    assert app_a.state.sessions is not app_b.state.sessions


def test_events_endpoint_is_still_inherited_and_acks(client):
    # core.web.create_app's /events receiver must survive being built on
    # top of -- this task mounts routers and adds /_health, nothing more.
    import base64
    import json

    body = {
        "message": {"data": base64.b64encode(json.dumps({"hello": "world"}).encode()).decode()}
    }
    r = client.post("/events", json=body)
    assert r.status_code == 204


def test_seed_demo_if_missing_is_a_noop_when_seed_module_is_absent(client):
    # Task 15 (seed/demo.py) has not landed yet in this codebase. Calling
    # the hook directly must not raise -- it is documented as a no-op with
    # a clear TODO in that case, not a crash waiting to happen the moment
    # POST /api/auth/demo is hit.
    client.app.state.seed_demo_if_missing()  # must not raise
