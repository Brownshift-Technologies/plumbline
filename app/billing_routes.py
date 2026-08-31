"""Task 14c: `GET /api/billing`, `POST /api/billing/plan`.

**Fix round 1.** `GET /api/billing` originally returned a nested
`{"meters": {"runs": {...}, "seats": {...}}}` shape of its own invention,
never checked against the one thing that actually matters: what the
frontend this codebase already ships reads. `web/src/lib/types.ts`'s
`BillingInfo` and `BillingPane.tsx` (`data.interval`, `data.renews_at`,
`data.seats_used`, `data.seat_limit`, `data.payment_method`, ...) expect a
FLAT object -- not just missing `renews_at`/`interval` as two extra
fields bolted onto the old shape, but a different shape entirely. This
route now returns exactly the fields `BillingInfo` declares, in the flat
form the frontend already renders.

`Workspace` (app/models.py) carries `plan`/`seats`/`run_limit`/
`runs_used` but no price, billing interval, renewal date, or payment
method -- none of those are workspace *state* the way a role or a run
count is; they are product/business decisions (or a genuinely unwired
payment provider) with nowhere else in this codebase to live yet.
`_PLAN_CATALOGUE` is the one place pricing/interval live; `_renews_at`
computes an honest "first of next calendar month" the same way
`app/run_routes.py`'s `_next_reset_date` computes a workspace's run-limit
reset -- every current plan renews monthly, so there is no per-workspace
billing-cycle-start field to read this from yet, and a computed answer
close to reality beats a fixed placeholder date that drifts stale.
`payment_method` is `""` when nothing is on file -- the same "log it,
don't fake it" honesty `app/account_routes.py`'s `_deliver_reset_email_default`
already commits to for a provider this codebase has not wired up, rather
than fabricating a card number nobody actually charged.

Changing plan updates `seats`/`run_limit` to the new plan's own limits (a
downgrade genuinely shrinks what a workspace is allowed) but leaves
`runs_used` untouched -- switching plans mid-cycle does not erase what a
workspace has already run this period.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.deps import current_session, require_write_role

router = APIRouter(prefix="/api/billing")

_BILLING_INTERVAL = "monthly"

_PLAN_CATALOGUE = {
    "starter": {"price": 0, "seats": 2, "run_limit": 50},
    "team": {"price": 490, "seats": 5, "run_limit": 500},
    "scale": {"price": 1490, "seats": 20, "run_limit": 2500},
}


class ChangePlan(BaseModel):
    plan: str


def _renews_at() -> float:
    """The first of next calendar month, UTC, as an epoch timestamp --
    `BillingPane.tsx` does `new Date(data.renews_at * 1000)`, so this is
    seconds, not an ISO date string the way `_next_reset_date` in
    `app/run_routes.py` returns one for a different consumer."""
    now = datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc).timestamp()


@router.get("")
def get_billing(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    plan_info = _PLAN_CATALOGUE.get(workspace.plan, {"price": None})
    return {
        "plan": workspace.plan,
        "price": plan_info.get("price"),
        "interval": _BILLING_INTERVAL,
        "renews_at": _renews_at(),
        "runs_used": workspace.runs_used,
        "run_limit": workspace.run_limit,
        "seats_used": len(repo.members_of(sess.workspace_id)),
        "seat_limit": workspace.seats,
        "payment_method": "",
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
