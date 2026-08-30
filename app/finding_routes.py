"""Task 14b: finding and patch routes, with the approval gate.

**The gate.** Approving a patch that touches a human-gated path (payments,
billing -- `gateway/policy.py`'s `DEFAULT_RULES`, or a workspace's own
`gate_rules`) requires role `owner` **and** a CONFIRMED TOTP secret
(`User.totp_secret`, never `totp_pending_secret` -- see `app/models.py`'s
docstring for why an unconfirmed secret must never satisfy this). Rather
than hand-roll a second "is this a payments path" regex here, "does this
patch need the gate" is answered by calling the exact same `decide()`
this fleet already uses to decide whether Surgeon may merge it --
`_patch_needs_owner_totp` below. That is not a style preference: it is
what keeps this route's opinion of "gated" and the Gateway's opinion of
"gated" from being able to drift apart the moment someone edits a
workspace's `gate_rules` and only remembers to update one of the two.

**Idempotent approval, no second PR.** Surgeon (`agents/surgeon.py`)
already opened the pull request before the run ever stopped at this gate
-- `Patch.pr_url` is set by the time a human ever sees it. Approving here
never calls `pr.open` again; it only flips `Patch.gate_state` to
`"merged"`. The idempotency check (`gate_state == "merged"` already ->
`{"already_approved": true}`, 200, no further write) is what makes
"approving twice" safe without a second gateway call of any kind, and it
runs AFTER the permission gate, not before -- a caller who never had
permission to approve a patch does not get to learn its approval state by
retrying.

**Human actions get the human's id in the ledger, not an agent name.**
Every approve/reject/change-request writes to the ledger through
`ledger.append(workspace_id, actor=<user id>, ...)` directly -- NOT
through `Gateway.call`, which exists for agent tool calls scoped by
`gateway/policy.py`'s `SCOPES` (keyed by agent name: `"surgeon"`,
`"triager"`, ...). A human clicking "approve" is not any agent in that
table, and forcing this through `Gateway.call` would either need a fake
agent identity that lies about who acted, or would fail scope-checking
outright. Direct `ledger.append` is what makes the ledger say "user u_...
approved this," which is the whole point (contract point 4: "the ledger
is the record of who signed").

Judgement calls -- see task-14a-15-report.md for the fuller writeup:
- **A patch whose finding was already resolved by another path.**
  Approval acts on the `Patch`/gate state, not `Finding.status` -- a
  finding independently marked accepted/resolved elsewhere does not
  strand a still-open patch in `awaiting_approval` forever. Approving it
  is still meaningful (the PR still exists and someone still has to
  decide whether it merges); rejecting or approving does not touch
  `Finding.status` either, for the same reason.
- **Reject/changes note length.** The contract only names 10 characters
  for `reject`; `changes` (a real, actionable substitute for "no") gets
  the identical floor for the identical reason -- a one-word "no" is not
  something Surgeon's next attempt (or a future human) can act on.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.deps import current_session, require_write_role
from app.models import Finding
from gateway.policy import decide

router = APIRouter(prefix="/api/findings")

_MIN_NOTE_LEN = 10
_WRITE_ROLES = ("owner", "approver")


def _finding_json(f: Finding) -> dict:
    return {
        "id": f.id, "workspace_id": f.workspace_id, "title": f.title, "route": f.route,
        "found_by": f.found_by, "status": f.status, "severity": f.severity,
        "seed": f.seed, "repro_count": f.repro_count, "at": f.at,
    }


def _patch_json(p) -> dict:
    return {
        "id": p.id, "finding_id": p.finding_id, "diff": p.diff, "files": list(p.files),
        "added": p.added, "removed": p.removed, "verified": p.verified,
        "pr_url": p.pr_url, "gate_state": p.gate_state,
    }


def _get_finding_or_404(repo, finding_id: str, workspace_id: str) -> Finding:
    finding = next(
        (f for f in repo.findings_for_workspace(workspace_id) if f.id == finding_id), None
    )
    if finding is None:
        raise HTTPException(404, "no such finding")
    return finding


def _get_patch_or_404(repo, finding_id: str):
    patch = repo.patch_for_finding(finding_id)
    if patch is None:
        raise HTTPException(404, "this finding has no patch")
    return patch


def _patch_needs_owner_totp(workspace, patch) -> bool:
    """Whether approving `patch` needs role `owner` + confirmed TOTP --
    true when ANY file it touches would need a human at `pr.merge` under
    this workspace's own gate rules. See the module docstring for why
    this reuses `decide()` rather than a bespoke path check."""
    rules = list(workspace.gate_rules) if workspace and workspace.gate_rules else None
    targets = list(patch.files) or [patch.finding_id]
    return any(decide("surgeon", "pr.merge", target=t, rules=rules).needs_human for t in targets)


def _check_approve_permission(repo, sess, workspace, patch) -> None:
    role = repo.role_of(sess.user_id, sess.workspace_id)
    if role not in _WRITE_ROLES:
        raise HTTPException(403, f"approving a patch needs one of {_WRITE_ROLES}")
    if not _patch_needs_owner_totp(workspace, patch):
        return
    if role != "owner":
        raise HTTPException(403, "approving this patch needs role 'owner'")
    user = repo.user(sess.user_id)
    if not user or not user.totp_secret:
        raise HTTPException(403, "approving this patch needs a confirmed TOTP device")


class RejectBody(BaseModel):
    note: str = Field(min_length=1)


class ChangesBody(BaseModel):
    note: str = Field(min_length=1)


@router.get("")
def list_findings(
    request: Request, status: str | None = None, route: str | None = None,
    found_by: str | None = None, sess=Depends(current_session),
):
    rows = request.app.state.repo.findings_for_workspace(sess.workspace_id)
    if status:
        rows = [f for f in rows if f.status == status]
    if route:
        rows = [f for f in rows if f.route == route]
    if found_by:
        rows = [f for f in rows if f.found_by == found_by]
    return {"findings": [_finding_json(f) for f in rows], "total": len(rows)}


@router.get("/{finding_id}")
def get_finding(finding_id: str, request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    finding = _get_finding_or_404(repo, finding_id, sess.workspace_id)
    return _finding_json(finding)


@router.get("/{finding_id}/patch")
def get_patch(finding_id: str, request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    finding = _get_finding_or_404(repo, finding_id, sess.workspace_id)
    patch = _get_patch_or_404(repo, finding.id)
    return _patch_json(patch)


@router.post("/{finding_id}/patch/approve")
def approve_patch(
    finding_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner", "approver")),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    repo, ledger = request.app.state.repo, request.app.state.ledger
    finding = _get_finding_or_404(repo, finding_id, sess.workspace_id)
    patch = _get_patch_or_404(repo, finding.id)
    workspace = repo.workspace(sess.workspace_id)

    _check_approve_permission(repo, sess, workspace, patch)

    if patch.gate_state == "merged":
        return {"already_approved": True, "ok": True, "pr_url": patch.pr_url}

    repo.put_patch(type(patch)(**{**patch.__dict__, "gate_state": "merged"}))
    ledger.append(
        sess.workspace_id, sess.user_id, "patch.approve",
        {"finding_id": finding.id, "patch_id": patch.id, "pr_url": patch.pr_url},
    )
    return {"already_approved": False, "ok": True, "pr_url": patch.pr_url}


@router.post("/{finding_id}/patch/reject")
def reject_patch(
    finding_id: str, body: RejectBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role(*_WRITE_ROLES)),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    note = body.note.strip()
    if len(note) < _MIN_NOTE_LEN:
        raise HTTPException(400, f"a rejection note needs at least {_MIN_NOTE_LEN} characters")

    repo, ledger = request.app.state.repo, request.app.state.ledger
    finding = _get_finding_or_404(repo, finding_id, sess.workspace_id)
    patch = _get_patch_or_404(repo, finding.id)

    repo.put_patch(type(patch)(**{**patch.__dict__, "gate_state": "rejected"}))
    # Back to "triaged" -- the status Surgeon's own findings_for_workspace
    # filter (agents/surgeon.py) looks for -- so the next fleet run tries
    # again, with the note as the ledger's record of why the first
    # attempt didn't stand.
    repo.put_finding(type(finding)(**{**finding.__dict__, "status": "triaged"}))
    ledger.append(
        sess.workspace_id, sess.user_id, "patch.reject",
        {"finding_id": finding.id, "patch_id": patch.id, "note": note},
    )
    return {"ok": True}


@router.post("/{finding_id}/patch/changes")
def request_changes(
    finding_id: str, body: ChangesBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role(*_WRITE_ROLES)),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    note = body.note.strip()
    if len(note) < _MIN_NOTE_LEN:
        raise HTTPException(400, f"a change request needs at least {_MIN_NOTE_LEN} characters")

    repo, ledger = request.app.state.repo, request.app.state.ledger
    finding = _get_finding_or_404(repo, finding_id, sess.workspace_id)
    patch = _get_patch_or_404(repo, finding.id)

    repo.put_patch(type(patch)(**{**patch.__dict__, "gate_state": "changes_requested"}))
    repo.put_finding(type(finding)(**{**finding.__dict__, "status": "triaged"}))
    ledger.append(
        sess.workspace_id, sess.user_id, "patch.request_changes",
        {"finding_id": finding.id, "patch_id": patch.id, "note": note},
    )
    return {"ok": True}


@router.post("/{finding_id}/accept")
def accept_finding(
    finding_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role(*_WRITE_ROLES)),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    repo = request.app.state.repo
    finding = _get_finding_or_404(repo, finding_id, sess.workspace_id)
    repo.put_finding(type(finding)(**{**finding.__dict__, "status": "accepted"}))
    return {"ok": True}


@router.post("/{finding_id}/snooze")
def snooze_finding(
    finding_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role(*_WRITE_ROLES)),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    repo = request.app.state.repo
    finding = _get_finding_or_404(repo, finding_id, sess.workspace_id)
    repo.put_finding(type(finding)(**{**finding.__dict__, "status": "snoozed"}))
    return {"ok": True}
