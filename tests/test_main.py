"""app/main.py: build_app assembly, /_health, and the inherited /healthz."""

import pytest

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


def test_seed_demo_workspace_is_a_noop_when_seed_module_is_absent(client, monkeypatch):
    # seed/demo.py exists in this codebase now, but `_seed_demo_workspace_
    # factory`'s deferred `from seed.demo import seed_demo` is still
    # written to degrade to a logged no-op rather than a crash if that
    # import ever fails -- exercised here the same way any "module not
    # found" case is: setting the module to `None` in `sys.modules` makes
    # a subsequent `import` raise `ImportError`, exactly like a genuinely
    # missing module would.
    import sys

    monkeypatch.setitem(sys.modules, "seed.demo", None)
    client.app.state.seed_demo_workspace("ws_demo_test")  # must not raise


# --- fix round 1: OAUTH_STATE_SECRET must be enforced, not merely documented -


def test_build_app_raises_when_the_oauth_secret_is_unset_in_production(monkeypatch):
    # The real-world path: no config override, no OAUTH_STATE_SECRET, and a
    # deploy tier that is not explicitly dev/test -- exactly what an
    # under-configured Cloud Run service looks like. `build_app()` must
    # refuse to start rather than silently sign CSRF state with a fixed,
    # source-visible string.
    monkeypatch.setenv("PLUMBLINE_ENV", "production")
    monkeypatch.delenv("OAUTH_STATE_SECRET", raising=False)
    from app.main import build_app

    with pytest.raises(RuntimeError, match="OAUTH_STATE_SECRET"):
        build_app()


def test_build_app_starts_in_test_mode_without_the_secret(config, repo):
    # No monkeypatch here on purpose: this proves the AMBIENT pytest
    # environment (PLUMBLINE_ENV=test, set once in tests/conftest.py) is
    # already sufficient -- no individual test, this one included, has to
    # set anything for the suite to keep working.
    assert not config.oauth_state_secret
    from app.main import build_app

    app = build_app(config=config, repo=repo)
    assert app.state.oauth_state_secret  # falls back, but starts


def test_the_insecure_fallback_is_never_used_outside_dev_or_test(monkeypatch):
    from app.main import _INSECURE_DEV_OAUTH_SECRET, build_app

    # A real, configured secret always wins over the fallback, regardless
    # of deploy tier -- production with a real secret must not raise.
    monkeypatch.setenv("PLUMBLINE_ENV", "production")
    monkeypatch.setenv("OAUTH_STATE_SECRET", "a-real-secret-from-secret-manager")
    app = build_app()
    assert app.state.oauth_state_secret == "a-real-secret-from-secret-manager"
    assert app.state.oauth_state_secret != _INSECURE_DEV_OAUTH_SECRET

    # Any deploy tier other than the two explicitly allow-listed ones (not
    # just the literal string "production") is refused when the secret is
    # unset -- the guard is an allow-list, not a "production"-string check.
    monkeypatch.setenv("PLUMBLINE_ENV", "staging")
    monkeypatch.delenv("OAUTH_STATE_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        build_app()


def test_dev_mode_also_accepts_the_fallback(monkeypatch):
    # "test" is not the only allow-listed tier -- a real dev box running
    # `PLUMBLINE_ENV=dev uvicorn app.main:app` with no secret configured
    # yet must still be able to boot.
    monkeypatch.setenv("PLUMBLINE_ENV", "dev")
    monkeypatch.delenv("OAUTH_STATE_SECRET", raising=False)
    from app.main import build_app

    app = build_app()
    assert app.state.oauth_state_secret
