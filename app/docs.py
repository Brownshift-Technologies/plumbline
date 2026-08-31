"""Task 14d: `GET /docs` and `GET /openapi.json` -- documentation for the
PUBLIC surface only.

`core/web.py`'s `create_app` disables FastAPI's generated docs routes
outright, and says exactly why in its own docstring: this service is
deployed `--allow-unauthenticated`, so a docs page would otherwise publish
every internal dashboard route (`/api/auth/signin`, `/api/billing/plan`,
...) to anyone who asks. That reasoning does not change just because
`/v1/...` (Task 14d's whole point) now needs a public docs page -- it means
the docs page this module serves must show `/v1/...` ONLY, never the
internal routes `core.web`'s own docstring was protecting in the first
place.

`build_public_schema` builds the full schema via `FastAPI.openapi()` (so
every route's real, already-declared `summary`/`description`/`responses`
-- see `app/public_routes.py` -- is picked up automatically, with nothing
re-typed here) and then filters `paths` down to whatever
`app.state.docs_prefix` (default `"/v1"`) starts with. Recomputed on
every request rather than cached: `FastAPI.openapi()` itself is a plain
dict-builder with no network or Firestore call in it, cheap enough not to
need caching, and NOT caching means a route added or edited later shows
up immediately with no stale-schema class of bug to worry about.

**"Every public route has a description and an example" is a promise
this module's job is to make USEFUL, not merely present.** An OpenAPI
page whose every route field is an empty string still satisfies "the
route showed up in the schema" -- it is worse than no page at all,
because it *looks* like documentation. `tests/test_openapi.py`'s
`test_every_v1_route_has_a_description_and_an_example` reads the SAME
filtered schema this module serves and checks it directly, so drift here
fails the suite.
"""

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

_DEFAULT_PREFIX = "/v1"


def build_public_schema(app: FastAPI, prefix: str = _DEFAULT_PREFIX) -> dict:
    full = app.openapi()
    public_paths = {path: item for path, item in full.get("paths", {}).items() if path.startswith(prefix)}
    return {**full, "paths": public_paths}


def register_docs(app: FastAPI, prefix: str = _DEFAULT_PREFIX) -> None:
    """Mounts `GET /openapi.json` and `GET /docs` on `app`. Plain routes,
    not `FastAPI(docs_url=...)`, so the internal-routes suppression in
    `core.web.create_app` is untouched -- see the module docstring."""

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_json(request: Request) -> JSONResponse:
        return JSONResponse(build_public_schema(request.app, prefix))

    @app.get("/docs", include_in_schema=False)
    def swagger_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} -- public API")
