"""Task 14e: Plumbline as an MCP server -- `POST /mcp` (streamable HTTP,
one JSON-RPC request in, one JSON-RPC response out) and `GET /mcp` (the
SSE transport's initial handshake).

**Authenticated exactly like the public REST API, on purpose.** Both
routes depend on `app.api_keys.current_api_key` -- the identical
dependency `app/public_routes.py`'s `/v1/...` router uses. That is not
an implementation shortcut, it is the whole point of Task 14e's own
contract: "every MCP tool call is an API-key call and inherits its
role", and "rate limiting is shared with the REST API, per key" --
sharing one dependency function is what makes those true by
CONSTRUCTION rather than by two independently-written rate limiters
happening to agree.

**No `mcp` SDK dependency.** `pyproject.toml` does not vendor the
`mcp` Python package, and this module does not need it: the MCP
transport contract this task actually needs (`initialize`, `tools/list`,
`tools/call`, over JSON-RPC 2.0) is small enough to implement directly
against FastAPI, the way every other transport in this codebase
(Firestore, Pub/Sub, OAuth) is hand-rolled against the underlying
protocol rather than pulled in as a heavy client library. A customer's
MCP-aware agent (Claude Code, Cursor, or any client that speaks
streamable-HTTP MCP) needs only `POST /mcp` with a JSON-RPC body and a
bearer key; this module is exactly that surface, no more.

**Every call is one gateway-shaped record in the ledger.** `_dispatch`
below writes ONE `ledger.append(..., actor=f"apikey:{key.id}", ...)` per
`tools/call` -- success or `ToolError` -- with the tool name and a
(redacted, via `Ledger.append`'s own `_redact` barrier) copy of the
arguments. `initialize`/`tools/list` are not tool calls and do not
themselves change anything the audit trail cares about, so they are not
separately ledgered -- consistent with `gateway/gateway.py`'s own
`Gateway.call`, which records a decision per ACT, not per introspection.
"""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api_keys import current_api_key
from app.mcp_tools import ToolError, call_tool, visible_tools
from app.models import ApiKey

router = APIRouter()

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "plumbline", "version": "1.0"}


def _tool_json(tool) -> dict:
    return {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}


def _rpc_result(rpc_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _dispatch(request: Request, key: ApiKey, body: dict) -> dict:
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return _rpc_result(rpc_id, {
            "protocolVersion": _PROTOCOL_VERSION, "serverInfo": _SERVER_INFO,
            "capabilities": {"tools": {}},
        })

    if method == "tools/list":
        tools = visible_tools(key.role)
        return _rpc_result(rpc_id, {"tools": [_tool_json(t) for t in tools]})

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(name, key.role, request, key, arguments)
        except ToolError as exc:
            request.app.state.ledger.append(
                key.workspace_id, f"apikey:{key.id}", "mcp.call",
                {"tool": name, "arguments": arguments, "ok": False, "error": exc.message},
            )
            return _rpc_error(rpc_id, exc.code, exc.message)
        request.app.state.ledger.append(
            key.workspace_id, f"apikey:{key.id}", "mcp.call",
            {"tool": name, "arguments": arguments, "ok": True},
        )
        # MCP's own content-block shape: a single JSON text block carrying
        # the structured result, so any MCP client (which only guarantees
        # it can render `content`) can read it, not only one that also
        # understands a bespoke `structuredContent` field.
        return _rpc_result(rpc_id, {"content": [{"type": "text", "text": json.dumps(result)}]})

    return _rpc_error(rpc_id, -32601, f"unknown method: {method!r}")


@router.post("/mcp")
async def mcp_post(request: Request, key: ApiKey = Depends(current_api_key)):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 -- a malformed body is a client error, not a 500
        return JSONResponse(_rpc_error(None, -32700, "invalid JSON"), status_code=400)
    return JSONResponse(_dispatch(request, key, body))


@router.get("/mcp")
async def mcp_sse(request: Request, key: ApiKey = Depends(current_api_key)):
    """The SSE transport's handshake: on connect, a server announces the
    URI a client should POST JSON-RPC messages to (here, this same
    `/mcp`) via a single `event: endpoint` message -- the streamable-HTTP
    server this module already runs as `POST /mcp`. A real, bidirectional
    SSE session (server-pushed tool results delivered async over the open
    stream) is out of scope for what this task's own tests exercise;
    `tools/call` already answers synchronously over `POST /mcp`, so this
    handshake is what lets an SSE-only MCP client discover that endpoint
    and switch to it, not a second, parallel execution path of its own.
    """

    async def events():
        yield f"event: endpoint\ndata: /mcp\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
