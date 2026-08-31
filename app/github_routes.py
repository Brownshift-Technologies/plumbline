"""Task 14g: connecting a GitHub repository -- App install, repo
selection, read-only spec import, and the inbound webhook.

**Connecting a repo is an `owner`-only write.** `POST`/`DELETE
/api/workspaces/{id}/repo` both use `require_write_role("owner")` --
narrower than the `("owner", "approver")` pair most other write routes in
this codebase use (`app/behaviour_routes.py`, `app/run_routes.py`), on
purpose: pointing Plumbline at a different GitHub repository, or
disconnecting the one it has, changes where every future Surgeon PR
lands and what every future import reads from. That is an ownership-tier
decision, not a routine day-to-day one an `approver` should be able to
make alone.

**Import never writes to the repo.** `POST /api/github/import` calls
`agents.repo_source.import_specs`, which itself calls only
`GitHubApp.list_specs`/`read_file` (both read-only) -- see that module's
own docstring for why this is load-bearing, not incidental. The only
write this route makes is `repo.put_behaviour`, to PLUMBLINE's own
store.

**The webhook is verified before it is parsed.** `POST
/api/github/webhook` reads the raw body FIRST, verifies
`X-Hub-Signature-256` against it with `app.github.verify_webhook_signature`
(constant-time), and only THEN calls `request.json()`. An unverified
webhook is an unauthenticated write path into whatever this route does
next -- see that function's own docstring for the constant-time
comparison itself.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from agents.repo_source import import_specs
from app.deps import current_session, require_write_role
from app.github import verify_webhook_signature

router = APIRouter()

_STATE_SALT = "plumbline-github-install-state"
_STATE_MAX_AGE_SECONDS = 600


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.oauth_state_secret, salt=_STATE_SALT)


@router.get("/api/github/install")
def github_install(request: Request, sess=Depends(current_session)):
    state = _serializer(request).dumps({"workspace_id": sess.workspace_id})
    slug = request.app.state.github_app_slug
    url = f"https://github.com/apps/{slug}/installations/new?{urlencode({'state': state})}"
    return RedirectResponse(url, status_code=302)


@router.get("/api/github/callback")
def github_callback(installation_id: str, state: str, request: Request, setup_action: str = ""):
    try:
        data = _serializer(request).loads(state, max_age=_STATE_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(400, "invalid or expired install state") from exc

    repo = request.app.state.repo
    workspace = repo.workspace(data.get("workspace_id", ""))
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    repo.put_workspace(type(workspace)(**{**workspace.__dict__, "installation_id": installation_id}))
    return RedirectResponse("/settings?github=connected", status_code=302)


@router.get("/api/github/repos")
def github_repos(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None or not workspace.installation_id:
        raise HTTPException(400, "no GitHub App installation connected yet")
    repos = request.app.state.github_app.list_installation_repos(workspace.installation_id)
    return {"repos": repos}


class ConnectRepoBody(BaseModel):
    repo_full_name: str
    default_branch: str = "main"


@router.post("/api/workspaces/{workspace_id}/repo")
def connect_repo(
    workspace_id: str, body: ConnectRepoBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    if workspace_id != sess.workspace_id:
        raise HTTPException(404, "no such workspace")
    repo = request.app.state.repo
    workspace = repo.workspace(workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    if not workspace.installation_id:
        raise HTTPException(400, "connect a GitHub App installation first")

    updated = type(workspace)(**{
        **workspace.__dict__, "repo_full_name": body.repo_full_name, "default_branch": body.default_branch,
    })
    repo.put_workspace(updated)
    request.app.state.github_app.bind(body.repo_full_name, workspace.installation_id)
    request.app.state.ledger.append(
        sess.workspace_id, sess.user_id, "github.repo_connect",
        {"repo_full_name": body.repo_full_name, "default_branch": body.default_branch},
    )
    return {"repo_full_name": updated.repo_full_name, "default_branch": updated.default_branch}


@router.delete("/api/workspaces/{workspace_id}/repo")
def disconnect_repo(
    workspace_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    if workspace_id != sess.workspace_id:
        raise HTTPException(404, "no such workspace")
    repo = request.app.state.repo
    workspace = repo.workspace(workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")

    if workspace.installation_id:
        request.app.state.github_app.revoke(workspace.installation_id)
    updated = type(workspace)(**{**workspace.__dict__, "repo_full_name": "", "installation_id": ""})
    repo.put_workspace(updated)
    request.app.state.ledger.append(sess.workspace_id, sess.user_id, "github.repo_disconnect", {})
    return {"ok": True}


@router.post("/api/github/import")
def import_repo_specs(
    request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner", "approver")),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None or not workspace.repo_full_name:
        raise HTTPException(400, "connect a repository first")

    github_app = request.app.state.github_app
    github_app.bind(workspace.repo_full_name, workspace.installation_id)
    behaviours = import_specs(
        github_app, workspace.repo_full_name, workspace.default_branch, sess.workspace_id,
    )
    for behaviour in behaviours:
        repo.put_behaviour(behaviour)
    return {"imported": len(behaviours)}


@router.post("/api/github/webhook", status_code=204)
async def github_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    secret = request.app.state.github_webhook_secret
    if not verify_webhook_signature(secret, raw_body, signature):
        raise HTTPException(401, "missing or invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 -- a malformed-but-signed body must not 500
        payload = {}
    event = request.headers.get("X-GitHub-Event", "")
    installation_id = str((payload.get("installation") or {}).get("id", "")) if isinstance(payload, dict) else ""
    if installation_id:
        # Attributed to the workspace THIS installation belongs to, not a
        # synthetic id -- `Store.query` (not a typed `Repo` method; this
        # task's own file list does not include `app/repo.py`, owned this
        # session by a concurrently-running implementer) is enough for a
        # one-field lookup with no tenancy ambiguity: `installation_id` is
        # unique per GitHub App installation.
        rows = request.app.state.repo.store.query("workspaces", "installation_id", "==", installation_id)
        if rows:
            request.app.state.ledger.append(rows[0]["id"], "github-webhook", "github.webhook_received", {
                "event": event, "installation_id": installation_id,
            })
