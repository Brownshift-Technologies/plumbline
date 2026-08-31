"""Tier 2 (2026-08-30 contract, item 1): where a workspace's own general
settings -- starting with `target_url`, the application under test -- get
read and written. No file with this name existed before this task: every
other workspace-scoped setting already had its own owning route file by
the time it needed one (`fleet_paused`/`gate_rules` in
`app/agent_routes.py`, `repo_full_name` in `app/github_routes.py`), and
this is where a genuinely general "this workspace's own configuration"
setting belongs from here on, rather than getting bolted onto either of
those for a reason unrelated to what they already own.

**Validated at write time, not at run time.**
`agents.cartographer.validate_target_url` -- not a second parser here --
decides well-formedness; see that function's own docstring for why it
reuses `agents.cartographer._internal_href`'s own normalisation rather
than inventing a second one. A malformed URL a customer tries to save
gets a field-specific 400 immediately; the alternative (accept anything,
let the next real run's `job/orchestrator.py` discover the problem deep
inside a Cloud Run Job) is exactly the "a malformed target costs a whole
run" shape the Tier 2 contract calls out by name -- "discovered at save
time it costs a form error" instead.

**Clearing `target_url` back to `""` is not a validation failure.** An
empty string is `Workspace.target_url`'s own documented "unconfigured"
default (`app/models.py`), a legitimate state a customer can deliberately
return to (disconnecting a target the same way `DELETE /api/workspaces/
{id}/repo` disconnects a repo) -- so this route only ever runs
`validate_target_url` against a NON-empty value being set, never against
the act of clearing one.

**Owner-only, like every other workspace-identity write in this
codebase.** `repo_full_name` (`app/github_routes.py`) and
`gate_rules`/`fleet_paused` (`app/agent_routes.py`) are both
`require_write_role("owner")`; the application a run actually points at
belongs in the same tier -- an `approver` can approve a patch, not decide
what site the fleet tests in the first place. Like `fleet_paused` and
`gate_rules`, a demo session's own write here is a REAL write to that
session's own sandbox workspace (see `app/agent_routes.py`'s
`_set_paused` for the reference reasoning) -- there is nothing outside
the sandbox this reaches, unlike `POST /api/workspaces/{id}/repo`'s real
GitHub App bind, so there is no `demo_refusal` branch here.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agents.cartographer import validate_target_url
from app.deps import current_session, require_write_role

router = APIRouter(prefix="/api/workspace")


@router.get("")
def get_workspace(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    return {"target_url": workspace.target_url}


class TargetUrlBody(BaseModel):
    target_url: str


@router.put("/target-url")
def put_target_url(
    body: TargetUrlBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    value = body.target_url.strip()
    if value:
        error = validate_target_url(value)
        if error:
            raise HTTPException(400, f"target_url {error}")

    repo, ledger = request.app.state.repo, request.app.state.ledger
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")

    updated = type(workspace)(**{**workspace.__dict__, "target_url": value})
    repo.put_workspace(updated)
    ledger.append(sess.workspace_id, sess.user_id, "workspace.target_url_updated",
                  {"target_url": value})
    return {"target_url": value}
