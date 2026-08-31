"""Task 14c: behaviour routes -- `GET/POST /api/behaviours`,
`PATCH/DELETE /api/behaviours/{id}`.

**Deletion is a soft delete.** `core.store.Store` has no delete primitive
at all (see that module's docstring -- every collection in this codebase
is append/replace-only), so `DELETE /api/behaviours/{id}` writes
`status="deleted"` through the same `put_behaviour` every other write
here uses, rather than removing anything. `list_behaviours` filters
`status="deleted"` rows out of its default view (unless a caller
explicitly asks for `?status=deleted`) so a "deleted" behaviour reads as
gone everywhere a caller does not go looking for it, while the underlying
document -- and everything that ever referenced its id -- stays intact.
This is the same tombstone discipline `Repo.delete_session` already uses
for a collection with no delete of its own.

**Demo sessions write for real.** A demo session's `sess.workspace_id` is
its own per-session sandbox (`app/auth_routes.py`'s `demo()`), so every
route below runs exactly the same write path a real session gets -- no
`sess.is_demo` short-circuit. Nothing here reaches outside that sandbox,
so nothing here needs to refuse.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.deps import current_session, require_write_role
from app.models import Behaviour

router = APIRouter(prefix="/api/behaviours")

_WRITE_ROLES = ("owner", "approver")


def _behaviour_json(b: Behaviour) -> dict:
    return {
        "id": b.id, "workspace_id": b.workspace_id, "text": b.text, "route": b.route,
        "spec_path": b.spec_path, "tags": list(b.tags), "owner": b.owner,
        "status": b.status, "source": b.source,
    }


def _get_or_404(repo, behaviour_id: str, workspace_id: str) -> Behaviour:
    row = next(
        (b for b in repo.behaviours_for_workspace(workspace_id) if b.id == behaviour_id), None
    )
    if row is None:
        raise HTTPException(404, "no such behaviour")
    return row


class CreateBehaviour(BaseModel):
    text: str = ""
    route: str = ""
    tags: tuple[str, ...] = ()
    owner: str = ""


class UpdateBehaviour(BaseModel):
    text: str | None = None
    route: str | None = None
    tags: tuple[str, ...] | None = None
    owner: str | None = None
    status: str | None = None


@router.get("")
def list_behaviours(
    request: Request, tag: str | None = None, owner: str | None = None,
    route: str | None = None, status: str | None = None, sess=Depends(current_session),
):
    rows = request.app.state.repo.behaviours_for_workspace(sess.workspace_id)
    if status:
        rows = [b for b in rows if b.status == status]
    else:
        rows = [b for b in rows if b.status != "deleted"]
    if tag:
        rows = [b for b in rows if tag in b.tags]
    if owner:
        rows = [b for b in rows if b.owner == owner]
    if route:
        rows = [b for b in rows if b.route == route]
    return {"behaviours": [_behaviour_json(b) for b in rows], "total": len(rows)}


@router.post("")
def create_behaviour(
    body: CreateBehaviour, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role(*_WRITE_ROLES)),
):
    if not body.text.strip() or not body.route.strip():
        raise HTTPException(400, "a behaviour needs both text and a route")

    repo = request.app.state.repo
    behaviour = Behaviour(
        id=f"beh_{uuid.uuid4().hex[:12]}", workspace_id=sess.workspace_id, text=body.text.strip(),
        route=body.route.strip(), tags=tuple(body.tags), owner=body.owner, source="human",
    )
    repo.put_behaviour(behaviour)
    return _behaviour_json(behaviour)


@router.patch("/{behaviour_id}")
def update_behaviour(
    behaviour_id: str, body: UpdateBehaviour, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role(*_WRITE_ROLES)),
):
    repo = request.app.state.repo
    row = _get_or_404(repo, behaviour_id, sess.workspace_id)
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "tags" in updates:
        updates["tags"] = tuple(updates["tags"])
    updated = type(row)(**{**row.__dict__, **updates})
    repo.put_behaviour(updated)
    return _behaviour_json(updated)


@router.delete("/{behaviour_id}")
def delete_behaviour(
    behaviour_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    repo = request.app.state.repo
    row = _get_or_404(repo, behaviour_id, sess.workspace_id)
    repo.put_behaviour(type(row)(**{**row.__dict__, "status": "deleted"}))
    return {"ok": True}
