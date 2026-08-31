"""`GET /api/mcp/info` -- what the Settings pane needs to describe the MCP server.

The MCP server itself (`app/mcp_server.py`) is authenticated with an API key,
not a session cookie, so the dashboard cannot simply call it and show the
result. This is the session-authenticated read beside it: the endpoint URL, the
tool manifest, and which tools each role may call.

It exists because the MCP work had no presence in the product at all. Eight
tools, a role filter and a manifest scanner were shipped and tested, and a
customer's only way to discover any of it was to read the source. A capability
nobody can find is close to one that does not exist.

`roles` is returned per tool rather than pre-filtered, so the pane can show
what the caller can do AND what they cannot, which is the more useful thing:
"your key cannot approve a patch" is information, whereas an approve tool
silently missing from a list looks like a bug.
"""

from fastapi import APIRouter, Depends, Request

from app.deps import current_session
from app.mcp_tools import TOOLS

router = APIRouter(prefix="/api/mcp")


@router.get("/info")
def mcp_info(request: Request, sess=Depends(current_session)):
    base = str(request.base_url).rstrip("/")
    repo = request.app.state.repo
    role = "reader" if sess.is_demo else (repo.role_of(sess.user_id, sess.workspace_id) or "reader")
    return {
        "endpoint": f"{base}/mcp",
        "transport": ["POST (JSON-RPC)", "GET (SSE)"],
        "auth": "Bearer <api key>",
        "your_role": role,
        "tools": [
            {
                "name": t.name,
                # First sentence only. The full descriptions are written for a
                # model deciding whether to call the tool, not for someone
                # skimming a settings page.
                "summary": t.description.split(". ")[0].rstrip(".") + ".",
                "roles": sorted(t.roles),
                "allowed": role in t.roles,
            }
            for t in TOOLS
        ],
    }
