"""Task 14c: the surface map -- `GET /api/surface`, `POST /api/surface/remap`.

A remap is not a bespoke "run Cartographer only" primitive -- it is an
ordinary `Run`, with `trigger="remap"`, going through the exact same
`enqueue_run` helper `app/run_routes.py`'s `POST /api/runs` uses (same
transactional numbering, same 402-at-limit check, same
enqueue-not-execute contract). `job/orchestrator.py`'s own sequence
always runs Cartographer first regardless of what triggered the run, so a
second, parallel "just crawl" code path would duplicate `enqueue_run`'s
logic for no behavioural difference -- and every place logic like that is
duplicated is a place the two copies can quietly drift (see
`app/run_routes.py`'s own docstring on why the 402 check lives in exactly
one function).
"""

from fastapi import APIRouter, Depends, Request

from app.deps import current_session, require_write_role
from app.run_routes import enqueue_run, simulate_run

router = APIRouter(prefix="/api/surface")


def _route_json(r) -> dict:
    return {
        "id": r.id, "workspace_id": r.workspace_id, "path": r.path,
        "coverage_pct": r.coverage_pct, "last_mapped": r.last_mapped,
    }


@router.get("")
def get_surface(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    routes = repo.routes_for_workspace(sess.workspace_id)  # coverage ascending
    uncovered = sum(1 for r in routes if r.coverage_pct == 0)
    return {
        "routes": [_route_json(r) for r in routes],
        "total": len(routes),
        "uncovered": uncovered,
    }


@router.post("/remap")
def remap_surface(
    request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    # A demo session's remap is a simulated run in its own sandbox -- see
    # app/run_routes.py's simulate_run docstring for why "remap" (a run
    # whose trigger says why it started, per this module's own docstring)
    # gets the identical demo/real split `POST /api/runs` does.
    run = simulate_run(request, sess, trigger="remap") if sess.is_demo else enqueue_run(request, sess, trigger="remap")
    return {"run_id": run.id, "number": run.number, "state": run.state}
