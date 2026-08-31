"""Production entrypoint: the real Plumbline API plus the built dashboard,
served from ONE FastAPI app on ONE origin.

`Dockerfile`'s `CMD` runs `uvicorn app.production:app`, not `app.main:app`
directly -- mounting the dashboard here, rather than inside
`app/main.py`'s `build_app`, is deliberate: `build_app` is what
`tests/conftest.py`'s `app`/`client` fixtures call for ~every one of this
repo's ~920 tests, and none of those tests want a `web/dist` directory to
exist, let alone a `/{full_path:path}` catch-all route competing with
whatever 404 behaviour a test expects for an unmatched path. Keeping the
mount in this separate, thin module means `app/main.py` and its test
fixtures are untouched by Task 18 -- this module is additive, imported by
nothing the test suite loads.

Why serve the dashboard from the API's own origin at all, rather than a
second Cloud Run service (or a static host) for `web/`: `web/src/lib/api.ts`
already defaults `VITE_API_BASE` to `/api`, a *relative* path -- see
`web/e2e/server.py`'s own module docstring, which this module mirrors
exactly (same `/assets` mount, same `favicon.svg`/`icons.svg` routes, same
SPA catch-all). A relative base only resolves correctly when the dashboard
and the API share an origin. Two Cloud Run services would need either a
third proxy in front of both (one more moving, billable piece) or a CORS
policy plus an absolute `VITE_API_BASE` baked into the frontend build (a
second thing to keep in sync with whichever URL Cloud Run happens to
assign the API on a given deploy). One service, one URL, no CORS, is the
simplest option that is still correct -- `deploy.sh` prints exactly that
one URL at the end for this reason.

This module builds the REAL app -- `app.main.build_app()` with no
overrides, so `load_settings()`'s real `Repo(cfg)` (a real Firestore
client, credentials resolved from Cloud Run's attached service account)
and real `PlumblineConfig` -- unlike `web/e2e/server.py`'s
`build_e2e_app()`, which wires a `FakeFirestore`-backed app for a
credential-free local Playwright run. `build_app()` itself still enforces
its own guard (see `app/main.py`'s module docstring on
`_INSECURE_DEV_OAUTH_SECRET`): this process refuses to start at all if
`OAUTH_STATE_SECRET` is unset and `PLUMBLINE_ENV` is not `test`/`dev` --
`Dockerfile` sets `PLUMBLINE_ENV=production` and `deploy.sh` binds
`OAUTH_STATE_SECRET` from Secret Manager, so a deploy that forgot the
secret fails loudly at container boot instead of serving with the fixed,
source-visible fallback key.

If `web/dist` was never built (a bare `docker build` skipping `cd web &&
npm run build` first, or a local `uvicorn app.production:app` run from a
fresh clone), this module still serves the full API unmodified -- it just
skips mounting the dashboard, rather than raising on import. `deploy.sh`
itself checks for `web/dist/index.html` before building the API image and
fails loudly there instead; a missing build should never look like a
working deploy that silently has no UI, but it also should not make this
module impossible to import for a backend-only use (e.g. hitting `/_health`
or the JSON API directly against a checkout that never ran `npm run
build`).
"""

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.main import build_app

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "web" / "dist"

app = build_app()

# Vite fingerprints every file under /assets (index-<hash>.js), so an
# asset URL's content can never change -- cache it for a year. index.html
# is the opposite: it is the ONLY file that names the current hashes, so a
# browser holding a stale copy keeps loading a superseded bundle and keeps
# hitting bugs that were fixed and deployed hours ago. It was served with
# no Cache-Control, no ETag and no Last-Modified at all, which lets a
# browser apply heuristic freshness and cache it for as long as it likes.
_IMMUTABLE = "public, max-age=31536000, immutable"
_NEVER = "no-store, no-cache, must-revalidate"


class _ImmutableAssets(StaticFiles):
    """StaticFiles that marks fingerprinted assets immutable."""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = _IMMUTABLE
        return response


if (DIST_DIR / "index.html").exists():
    app.mount("/assets", _ImmutableAssets(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/favicon.svg")
    def _favicon():
        return FileResponse(str(DIST_DIR / "favicon.svg"), headers={"Cache-Control": _IMMUTABLE})

    @app.get("/icons.svg")
    def _icons():
        return FileResponse(str(DIST_DIR / "icons.svg"), headers={"Cache-Control": _IMMUTABLE})

    _index_path = DIST_DIR / "index.html"

    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str):
        # Never reached for "/api/*", "/_health", or "/healthz" -- those
        # routes were all registered inside `build_app()` above, before this
        # catch-all was ever added to `app.router.routes`, and Starlette
        # resolves a request against routes in the order they were
        # registered. Same ordering guarantee `web/e2e/server.py`'s own
        # identical fallback documents and relies on.
        #
        # `no-store` because this file names the current asset hashes. Serve
        # it stale and the browser fetches a superseded bundle -- the user
        # keeps hitting a bug that was fixed and redeployed, and no amount
        # of redeploying reaches them until they hard-refresh.
        return FileResponse(str(_index_path), headers={"Cache-Control": _NEVER})
