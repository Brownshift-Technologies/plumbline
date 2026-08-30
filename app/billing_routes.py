"""Task 14c: `GET /api/billing`, `POST /api/billing/plan`.

`Workspace` (app/models.py) carries `plan`/`seats`/`run_limit`/
`runs_used` but no price -- pricing is a product/business decision, not
workspace state, so `_PLAN_CATALOGUE` below is the one place it lives.
Changing plan updates `seats`/`run_limit` to the new plan's own limits (a
downgrade genuinely shrinks what a workspace is allowed) but leaves
`runs_used` untouched -- switching plans mid-cycle does not erase what a
workspace has already run this period.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.deps import current_session, require_write_role

router = APIRouter(prefix="/api/billing")

_PLAN_CATALOGUE = {
    "starter": {"price": 0, "seats": 2, "run_limit": 50},
    "team": {"price": 490, "seats": 5, "run_limit": 500},
    "scale": {"price": 1490, "seats": 20, "run_limit": 2500},
}


class ChangePlan(BaseModel):
    plan: str


@router.get("")
def get_billing(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    plan_info = _PLAN_CATALOGUE.get(workspace.plan, {"price": None})
    seats_used = len(repo.members_of(sess.workspace_id))
    return {
        "plan": workspace.plan,
        "price": plan_info.get("price"),
        "meters": {
            "runs": {"used": workspace.runs_used, "limit": workspace.run_limit},
            "seats": {"used": seats_used, "limit": workspace.seats},
        },
    }


@router.post("/plan")
def change_plan(
    body: ChangePlan, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    if body.plan not in _PLAN_CATALOGUE:
        raise HTTPException(400, f"no such plan {body.plan!r} -- choose one of {sorted(_PLAN_CATALOGUE)}")

    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")

    plan_info = _PLAN_CATALOGUE[body.plan]
    updated = type(workspace)(**{
        **workspace.__dict__, "plan": body.plan,
        "seats": plan_info["seats"], "run_limit": plan_info["run_limit"],
    })
    repo.put_workspace(updated)
    return {"plan": updated.plan, "seats": updated.seats, "run_limit": updated.run_limit}
