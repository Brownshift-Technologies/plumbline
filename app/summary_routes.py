"""`GET /api/summary` -- the counts the sidebar shows next to Runs and Findings.

The sidebar used to hardcode `count: 18` and `count: 7` (and a `"7"` badge on
Agents) straight from the design prototype. They were the prototype's numbers,
not the workspace's, so every visitor saw 18 runs and 7 findings no matter what
was actually in their sandbox -- including a brand-new one with nothing in it.

One endpoint rather than three fetches from the sidebar: it renders on every
screen, so `/runs` + `/findings` + `/agents` would be three round trips before
anything else on the page could start. This is one, and it returns counts
only -- no rows -- so it stays cheap as a workspace grows.
"""

from fastapi import APIRouter, Depends, Request

from app.deps import current_session
from gateway.policy import SCOPES

router = APIRouter(prefix="/api/summary")


@router.get("")
def summary(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    ws = sess.workspace_id
    return {
        "runs": len(repo.runs_for_workspace(ws)),
        "findings": len([f for f in repo.findings_for_workspace(ws) if f.status != "accepted"]),
        "behaviours": len(repo.behaviours_for_workspace(ws)),
        # The fleet is static in code -- gateway/policy.py's SCOPES is the
        # same source app/agent_routes.py counts from, so the sidebar and
        # the Agents screen can never disagree.
        "agents": len(SCOPES),
    }
