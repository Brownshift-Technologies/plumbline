"""Task 14e: the eight tools Plumbline exposes over MCP, and the
subtractive, per-key gating that decides which of them a given key ever
sees.

**Subtractive, not just refusing.** `visible_tools(role)` is what
`app/mcp_server.py`'s `tools/list` calls to build the list it hands back
-- a `reader` key's list literally does not contain
`plumbline_run_tests`/`plumbline_write_behaviour`/`plumbline_approve_patch`
at all, the same way `gateway/policy.py`'s `SCOPES` never lets a rule
GRANT a tool an agent's scope doesn't already hold (see that module's own
docstring: "rules subtract, they never add"). `call_tool` re-checks the
identical role gate before executing -- a caller that already knows a
tool's name (from documentation, a cached list, a guess) and skips
straight to `tools/call` gets exactly the same 403-shaped refusal a
`tools/list` omission implies, never a bypass.

**`plumbline_approve_patch` never lets a machine past a human gate.**
Reuses `app/public_routes.py`'s `approve_patch_as_key`/`GateRefusal` --
the SAME function `POST /v1/findings/{id}/approve` calls -- so the two
entry points can never quietly disagree about what counts as gated. A
refusal comes back as a normal, successful tool RESULT
(`{"approved": false, "refused_reason": "human_gate", "gate": "..."}`),
not a protocol-level error: an MCP error is the wrong shape for "this
needs a human", because it reads to a calling agent as "retry me" or "I
malfunctioned" rather than "go get your human". A structured result the
agent can read and relay is the whole point (Task 14e's brief, verbatim).

**Every tool description is written for a model, not a human reading
docs.** Each says what it does, when to reach for it, what it returns,
and what it refuses -- `test_every_tool_carries_a_description_written_for_a_model`
checks for exactly that shape (a refusal clause), not merely "some text
is present".

**A discovered tool is not this module's problem -- a customer-run MCP
server's tools are (Task 14f).** These eight are Plumbline's OWN, defined
in code, never untrusted third-party input; `agents/mcp_client.py`'s
`check_input` scan is what stands between an outside server's tool
manifest and a model prompt. Nothing here needs that scan.
"""

import uuid
from dataclasses import dataclass
from typing import Callable

from app.models import Behaviour
from app.public_routes import GateRefusal, _v1_finding, _v1_run, approve_patch_as_key
from app.run_routes import enqueue_run

_READ_ROLES = frozenset({"owner", "approver", "reader"})
_WRITE_ROLES = frozenset({"owner", "approver"})
_APPROVE_ROLES = frozenset({"owner"})


class ToolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict
    roles: frozenset[str]
    handler: Callable[[object, object, dict], dict]


def _fake_session(key):
    class _Sess:
        workspace_id = key.workspace_id
        user_id = f"apikey:{key.id}"
        is_demo = False

    return _Sess()


def _handle_run_tests(request, key, arguments: dict) -> dict:
    sess = _fake_session(key)
    run = enqueue_run(request, sess, arguments.get("trigger", "mcp"), arguments.get("commit", ""))
    return {"run_id": run.id, "number": run.number, "state": run.state}


def _handle_get_run(request, key, arguments: dict) -> dict:
    repo = request.app.state.repo
    run_id = arguments.get("run_id", "")
    run = repo.run(run_id)
    if run is None or run.workspace_id != key.workspace_id:
        raise ToolError(-32602, f"no such run: {run_id!r}")
    return _v1_run(run).model_dump()


def _handle_list_findings(request, key, arguments: dict) -> dict:
    repo = request.app.state.repo
    rows = repo.findings_for_workspace(key.workspace_id)
    status = arguments.get("status")
    if status:
        rows = [f for f in rows if f.status == status]
    return {"findings": [_v1_finding(f).model_dump() for f in rows]}


def _handle_get_finding(request, key, arguments: dict) -> dict:
    repo = request.app.state.repo
    finding_id = arguments.get("finding_id", "")
    finding = next((f for f in repo.findings_for_workspace(key.workspace_id) if f.id == finding_id), None)
    if finding is None:
        raise ToolError(-32602, f"no such finding: {finding_id!r}")
    return _v1_finding(finding).model_dump()


def _handle_get_coverage(request, key, arguments: dict) -> dict:
    repo = request.app.state.repo
    routes = repo.routes_for_workspace(key.workspace_id)
    uncovered = sum(1 for r in routes if r.coverage_pct == 0)
    return {
        "routes": [{"path": r.path, "coverage_pct": r.coverage_pct} for r in routes],
        "total": len(routes), "uncovered": uncovered,
    }


def _handle_write_behaviour(request, key, arguments: dict) -> dict:
    repo = request.app.state.repo
    text, route = arguments.get("text", "").strip(), arguments.get("route", "").strip()
    if not text or not route:
        raise ToolError(-32602, "both 'text' and 'route' are required")
    behaviour = Behaviour(
        id=f"beh_{uuid.uuid4().hex[:12]}", workspace_id=key.workspace_id, text=text, route=route,
        tags=tuple(arguments.get("tags", [])), owner=arguments.get("owner", ""), source="mcp",
    )
    repo.put_behaviour(behaviour)
    return {"id": behaviour.id, "text": behaviour.text, "route": behaviour.route}


def _handle_verify_ledger(request, key, arguments: dict) -> dict:
    ledger = request.app.state.ledger
    intact = ledger.verify(key.workspace_id)
    return {"intact": intact, "checked": len(ledger.entries(key.workspace_id))}


def _handle_approve_patch(request, key, arguments: dict) -> dict:
    repo, ledger = request.app.state.repo, request.app.state.ledger
    finding_id = arguments.get("finding_id", "")
    try:
        result = approve_patch_as_key(repo, ledger, key, finding_id)
    except GateRefusal as exc:
        return {
            "approved": False,
            "refused_reason": "human_gate",
            "gate": exc.gate,
            "message": (
                f"this patch is human-gated ({exc.gate}) and cannot be approved by an "
                "API key -- ask a workspace owner to approve it from the Plumbline dashboard, "
                "where a confirmed TOTP device is required."
            ),
        }
    return {"approved": True, **result}


TOOLS: tuple[McpTool, ...] = (
    McpTool(
        name="plumbline_run_tests",
        description=(
            "Start a Plumbline test run against the connected repository. Reach for this right "
            "after your own agent has pushed or staged a code change and you want to know whether "
            "it broke anything real -- not a lint pass, an actual fleet of browser-driven agents. "
            "Returns immediately with a run id and state='queued'; the run executes asynchronously, "
            "so poll plumbline_get_run with the returned run_id (or subscribe to the run.finished "
            "webhook) rather than waiting here. Refuses for a 'reader' key: it is not offered to you "
            "at all unless your key carries 'owner' or 'approver'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "trigger": {"type": "string", "description": "Free-text label for why this run started."},
                "commit": {"type": "string", "description": "The commit SHA under test."},
            },
        },
        roles=_WRITE_ROLES, handler=_handle_run_tests,
    ),
    McpTool(
        name="plumbline_get_run",
        description=(
            "Look up one test run by id -- its state, timing, and how many findings it held, "
            "failed, or repaired. Use this to poll a run started with plumbline_run_tests until "
            "state is 'finished' or 'failed'. Refuses if the run does not exist or belongs to a "
            "different workspace than your key."
        ),
        input_schema={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
        roles=_READ_ROLES, handler=_handle_get_run,
    ),
    McpTool(
        name="plumbline_list_findings",
        description=(
            "List every finding Plumbline has recorded for your workspace, optionally filtered by "
            "status (triaged | accepted | snoozed). Use this to see what is currently broken before "
            "deciding what to fix or what to ask a human about. Read-only; never refuses on role."
        ),
        input_schema={"type": "object", "properties": {"status": {"type": "string"}}},
        roles=_READ_ROLES, handler=_handle_list_findings,
    ),
    McpTool(
        name="plumbline_get_finding",
        description=(
            "Fetch one finding's full detail by id -- title, route, severity, who found it. Use "
            "this after plumbline_list_findings to inspect a specific finding before deciding "
            "whether to approve its patch. Refuses if the finding does not exist in your workspace."
        ),
        input_schema={"type": "object", "properties": {"finding_id": {"type": "string"}}, "required": ["finding_id"]},
        roles=_READ_ROLES, handler=_handle_get_finding,
    ),
    McpTool(
        name="plumbline_get_coverage",
        description=(
            "Get the mapped surface (every known route) and its test coverage percentage, plus "
            "how many routes have zero coverage. Use this to answer 'what parts of the app have "
            "no tests at all' before deciding where to point Author next. Read-only; never refuses "
            "on role."
        ),
        input_schema={"type": "object", "properties": {}},
        roles=_READ_ROLES, handler=_handle_get_coverage,
    ),
    McpTool(
        name="plumbline_write_behaviour",
        description=(
            "Record a new expected behaviour (a plain-English spec of what a route should do) for "
            "Author to turn into a real Playwright spec on the next run. Use this when you (the "
            "calling agent) have just learned about a requirement Plumbline does not know about "
            "yet -- a new feature, an edge case a human described. Requires 'text' and 'route'. "
            "Refuses entirely (not offered) for a 'reader' key."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Plain-English description of the expected behaviour."},
                "route": {"type": "string", "description": "The route this behaviour applies to, e.g. /checkout."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "owner": {"type": "string"},
            },
            "required": ["text", "route"],
        },
        roles=_WRITE_ROLES, handler=_handle_write_behaviour,
    ),
    McpTool(
        name="plumbline_verify_ledger",
        description=(
            "Recompute the audit ledger's hash chain end to end and report whether it verifies "
            "intact. Use this to answer 'has anything in this workspace's audit trail been "
            "tampered with' -- e.g. before trusting a historical approval record. Read-only; "
            "never refuses on role."
        ),
        input_schema={"type": "object", "properties": {}},
        roles=_READ_ROLES, handler=_handle_verify_ledger,
    ),
    McpTool(
        name="plumbline_approve_patch",
        description=(
            "Approve the patch attached to a finding so it may merge. Requires an 'owner' key -- "
            "not offered at all to 'approver' or 'reader' keys. Even with an 'owner' key, THIS "
            "TOOL REFUSES to approve any patch that touches a human-gated path (payments, billing) "
            "-- it returns a structured result with refused_reason='human_gate' and the gate's own "
            "reason, instead of an error, so you can relay exactly what happened to your human "
            "rather than retrying. There is no argument or workaround that approves a gated patch "
            "from this tool; only a human, signed in with a confirmed second factor, can do that "
            "from the dashboard."
        ),
        input_schema={"type": "object", "properties": {"finding_id": {"type": "string"}}, "required": ["finding_id"]},
        roles=_APPROVE_ROLES, handler=_handle_approve_patch,
    ),
)

_BY_NAME = {t.name: t for t in TOOLS}


def visible_tools(role: str) -> list[McpTool]:
    return [t for t in TOOLS if role in t.roles]


def call_tool(name: str, role: str, request, key, arguments: dict) -> dict:
    tool = _BY_NAME.get(name)
    if tool is None:
        raise ToolError(-32601, f"no such tool: {name!r}")
    if role not in tool.roles:
        raise ToolError(-32603, f"this key's role ({role!r}) may not call {name!r}")
    return tool.handler(request, key, arguments)
