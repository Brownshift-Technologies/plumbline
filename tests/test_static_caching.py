"""Cache headers on the built dashboard.

Written after a real failure mode: fixes were deployed and the user kept
hitting the bug they fixed. `index.html` was served with no `Cache-Control`,
no `ETag` and no `Last-Modified`, which lets a browser apply *heuristic*
freshness and hold it for as long as it likes. Because index.html is the
only file that names the current fingerprinted bundle, a stale copy pins
the browser to a superseded `index-<hash>.js` -- and redeploying does not
help, because the browser never asks for the new index.

So the two rules are opposites, and both matter:
  - /assets/*  is content-addressed and may be cached forever.
  - index.html names those addresses and must never be cached.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def production_client(monkeypatch):
    monkeypatch.setenv("PLUMBLINE_ENV", "test")
    import importlib

    import app.production as production

    importlib.reload(production)
    if not (production.DIST_DIR / "index.html").exists():
        pytest.skip("web/dist is not built -- run `cd web && npm run build`")
    return TestClient(production.app)


def test_index_html_is_never_cached(production_client):
    r = production_client.get("/")
    assert r.status_code == 200
    cache = r.headers.get("cache-control", "")
    assert "no-store" in cache, (
        f"index.html was served with Cache-Control={cache!r}. It names the "
        "current asset hashes; a cached copy strands the browser on an old "
        "bundle and no redeploy can reach it."
    )


def test_a_deep_spa_route_is_also_never_cached(production_client):
    """The catch-all serves index.html for every client-side route, so the
    header has to be on that path too, not only on `/`."""
    r = production_client.get("/settings")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "")


def test_fingerprinted_assets_are_cached_immutably(production_client):
    import re

    html = production_client.get("/").text
    asset = re.search(r"/assets/[A-Za-z0-9_.-]+\.js", html)
    assert asset, "no fingerprinted script in index.html"

    r = production_client.get(asset.group(0))
    assert r.status_code == 200
    cache = r.headers.get("cache-control", "")
    assert "immutable" in cache and "max-age=31536000" in cache, (
        f"asset served with Cache-Control={cache!r} -- fingerprinted files "
        "should be cached for a year"
    )
