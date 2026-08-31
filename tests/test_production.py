"""app/production.py: the API plus `web/dist`, served from one origin.

`web/dist` is expected to exist in this checkout (`web/e2e/server.py`
already depends on the same build), so these tests exercise the real
mount rather than skipping it. Like `app/main.py`'s own module-level
`app = build_app()`, importing `app.production` must not require live GCP
credentials -- `core/store.py`'s `Store._client` defers building a real
Firestore client until first query.
"""

from fastapi.testclient import TestClient


def _client():
    from app.production import app

    return TestClient(app, base_url="https://testserver")


def test_importing_production_does_not_require_gcp_credentials():
    from app.production import app

    assert app.state.config is not None
    assert app.state.repo is not None


def test_health_is_still_reachable_alongside_the_dashboard():
    c = _client()
    body = c.get("/_health").json()
    assert body["ok"] is True
    assert body["model"] == "gemini-3.5-flash"
    assert body["gemini_location"] == "global"


def test_spa_fallback_serves_index_html_for_an_unknown_frontend_route():
    c = _client()
    r = c.get("/runs/whatever-does-not-exist")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_assets_are_mounted():
    import pathlib

    dist_assets = pathlib.Path(__file__).parents[1] / "web" / "dist" / "assets"
    some_asset = next(dist_assets.iterdir())
    c = _client()
    r = c.get(f"/assets/{some_asset.name}")
    assert r.status_code == 200


def test_api_routes_are_not_shadowed_by_the_spa_catch_all():
    # The catch-all is registered last, after every app.include_router()
    # call inside build_app() -- an unauthenticated request to a real API
    # route must still reach that route (and get its own real response,
    # e.g. a 401 for a protected endpoint), not the dashboard's index.html.
    c = _client()
    r = c.get("/api/auth/me")
    assert "text/html" not in r.headers["content-type"]
