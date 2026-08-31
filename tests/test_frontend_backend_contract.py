"""Every API path the frontend calls must exist on the backend.

This test exists because of a crash that took the whole app down:
`BillingPane.tsx` fetched `/api/billing/invoices`, a route that had never
been written. `app/production.py` serves the SPA as a catch-all, so instead
of a 404 the request got `index.html` back with a **200**, and the frontend
dutifully handed the raw markup to the component that had asked for an
array. It died on `.map is not a function`, three layers away from the
actual cause, with a stack trace naming a minified React internal.

Nothing else could have caught it. The backend tests all passed -- the
route they were testing did not exist, so there was nothing to fail. The
frontend tests all passed -- they mock `api.get`, so the mock returned the
array the component wanted. Only the two halves *together* are wrong, and
only the assembled, deployed product shows it.

So this test reads the real frontend source, extracts every path it calls,
and resolves each one against the real FastAPI route table.
"""

import re
from pathlib import Path

import pytest



_WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"
_API_BASE = "/api"

# api.get<T>("/x") / api.post<T>(`/x/${id}`) / api.del<T>("/x"), including
# the no-type-argument forms.
_CALL = re.compile(
    r"""api\.(?:get|post|put|patch|del|delete)\s*(?:<[^>]*>)?\s*\(\s*(['"`])(/[^'"`]*)\1""",
    re.VERBOSE,
)


def _frontend_paths() -> set[str]:
    found: set[str] = set()
    for f in _WEB_SRC.rglob("*.ts*"):
        if ".test." in f.name:
            continue
        for _, raw in _CALL.findall(f.read_text()):
            # `${runId}` is a path parameter; normalise it to {x}.
            path = re.sub(r"\$\{\s*[A-Za-z_$][\w.]*\s*\}", "{x}", raw)
            # Anything more complex -- `${qs ? `?${qs}` : ""}` -- is a
            # query string being appended, not more path. Cut there.
            if "${" in path:
                path = path[: path.index("${")]
            path = path.split("?", 1)[0].rstrip("/")
            if path:
                found.add(path)
    return found


def _route_matchers(app) -> list[re.Pattern]:
    # Read the OpenAPI schema, not `app.routes`. This FastAPI version wraps
    # each `include_router` call in an `_IncludedRouter` whose own `.path`
    # is None and whose real routes are nested inside it, so walking
    # `app.routes` finds six paths and none of the API. The schema is the
    # canonical, flattened list and is what the docs serve.
    out = []
    for tmpl in app.openapi()["paths"]:
        if not tmpl.startswith(_API_BASE):
            continue
        # Any {param} on either side matches any single non-slash segment.
        pattern = "".join(
            r"[^/]+" if seg.startswith("{") else re.escape(seg)
            for seg in re.split(r"(\{[^}]*\})", tmpl)
        )
        out.append(re.compile(f"^{pattern}$"))
    return out


def test_every_api_path_the_frontend_calls_exists_on_the_backend(app):
    matchers = _route_matchers(app)
    paths = _frontend_paths()

    assert paths, "extracted no API calls from web/src -- the regex has drifted"

    missing = sorted(
        p for p in paths
        if not any(m.match(f"{_API_BASE}{p}") for m in matchers)
    )
    assert not missing, (
        "the frontend calls API paths that have no backend route: "
        f"{missing}. Because app/production.py serves the SPA as a "
        "catch-all, these return index.html with a 200 rather than a 404, "
        "so they surface as render crashes, not as failed requests."
    )


@pytest.mark.parametrize("path", ["/billing/invoices", "/billing/portal"])
def test_the_two_routes_that_were_actually_missing_are_covered(path):
    """Guards the guard.

    If the extractor above ever silently stops finding calls -- a refactor
    to a different client, a template literal it cannot read -- the main
    test would pass vacuously against an empty set. These are the two paths
    whose absence caused the crash; they must be among what it extracts.
    """
    assert path in _frontend_paths()
