"""Task 14e: Plumbline as an MCP server -- subtractive per-key tool
gating, the human-gate refusal for `plumbline_approve_patch`, and the
ledger record of every call."""

from app.api_keys import create_api_key
from app.mcp_tools import TOOLS
from app.models import Finding, Patch, Workspace


def _ws(repo):
    if repo.workspace("ws1") is None:
        repo.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/storefront"))


def _key(client, role, **kw):
    repo = client.app.state.repo
    _ws(repo)
    _, raw = create_api_key(repo.store, "ws1", "k", role, "u1", **kw)
    return {"Authorization": f"Bearer {raw}"}


def _rpc(client, headers, method, params=None, rpc_id=1):
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def test_tools_list_requires_a_valid_key(client):
    resp = _rpc(client, {}, "tools/list")
    assert resp.status_code == 401

    resp = _rpc(client, {"Authorization": "Bearer pk_live_not_a_real_key"}, "tools/list")
    assert resp.status_code == 401


def test_a_reader_key_does_not_see_the_write_tools_at_all(client):
    headers = _key(client, "reader")
    resp = _rpc(client, headers, "tools/list")
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert "plumbline_run_tests" not in names
    assert "plumbline_write_behaviour" not in names
    assert "plumbline_approve_patch" not in names
    assert "plumbline_get_run" in names  # reads are still offered

    # And calling one directly (not just listing) is refused too.
    call = _rpc(client, headers, "tools/call", {"name": "plumbline_run_tests", "arguments": {}})
    assert "error" in call.json()


def test_an_owner_key_sees_every_tool(client):
    headers = _key(client, "owner")
    resp = _rpc(client, headers, "tools/list")
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {t.name for t in TOOLS}


def test_approving_a_gated_patch_is_refused_with_the_gate_named(client):
    repo = client.app.state.repo
    _ws(repo)
    repo.put_finding(Finding(id="f1", workspace_id="ws1", title="x", route="/checkout", found_by="triager"))
    repo.put_patch(Patch(id="p1", finding_id="f1", diff="", files=("src/checkout/payment-client.ts",)))
    headers = _key(client, "owner")

    resp = _rpc(client, headers, "tools/call", {"name": "plumbline_approve_patch", "arguments": {"finding_id": "f1"}})
    result = resp.json()["result"]
    content = __import__("json").loads(result["content"][0]["text"])
    assert content["approved"] is False
    assert content["refused_reason"] == "human_gate"
    assert "payment" in content["gate"] or "checkout" in content["gate"]

    # And the patch was NOT actually merged.
    assert repo.patch_for_finding("f1").gate_state != "merged"


def test_approving_an_ungated_patch_succeeds_for_an_owner_key(client):
    repo = client.app.state.repo
    _ws(repo)
    repo.put_finding(Finding(id="f2", workspace_id="ws1", title="x", route="/catalog", found_by="triager"))
    repo.put_patch(Patch(id="p2", finding_id="f2", diff="", files=("src/catalog/list.ts",)))
    headers = _key(client, "owner")

    resp = _rpc(client, headers, "tools/call", {"name": "plumbline_approve_patch", "arguments": {"finding_id": "f2"}})
    content = __import__("json").loads(resp.json()["result"]["content"][0]["text"])
    assert content["approved"] is True
    assert repo.patch_for_finding("f2").gate_state == "merged"

    # An approver key never even gets to try -- not offered the tool, and
    # a direct call is refused outright.
    approver_headers = _key(client, "approver")
    denied = _rpc(client, approver_headers, "tools/call", {"name": "plumbline_approve_patch", "arguments": {"finding_id": "f2"}})
    assert "error" in denied.json()


def test_every_mcp_call_is_recorded_in_the_ledger_with_the_key_id(client, ledger):
    repo = client.app.state.repo
    _ws(repo)
    from app.api_keys import create_api_key as _create
    key, raw = _create(repo.store, "ws1", "k", "owner", "u1")
    headers = {"Authorization": f"Bearer {raw}"}

    _rpc(client, headers, "tools/call", {"name": "plumbline_get_coverage", "arguments": {}})

    entries = [e for e in ledger.entries("ws1") if e["action"] == "mcp.call"]
    assert len(entries) == 1
    assert entries[0]["actor"] == f"apikey:{key.id}"
    assert entries[0]["detail"]["tool"] == "plumbline_get_coverage"


def test_run_tests_returns_a_run_id_without_blocking(client):
    client.app.state.enqueue_job = lambda job_name, args: "op/fake"
    headers = _key(client, "owner")
    resp = _rpc(client, headers, "tools/call", {"name": "plumbline_run_tests", "arguments": {"trigger": "mcp"}})
    content = __import__("json").loads(resp.json()["result"]["content"][0]["text"])
    assert content["state"] == "queued"
    assert content["run_id"]


def test_every_tool_carries_a_description_written_for_a_model(client):
    for tool in TOOLS:
        assert len(tool.description) > 40, f"{tool.name} description too thin to be useful to a model"
        assert "refus" in tool.description.lower() or tool.name.startswith("plumbline_get") or tool.name.startswith("plumbline_list") or tool.name.startswith("plumbline_verify"), (
            f"{tool.name} (a write/approve tool) should describe what it refuses"
        )


def test_mcp_shares_the_rest_rate_limit_per_key(client):
    repo = client.app.state.repo
    _ws(repo)
    from app.api_keys import create_api_key as _create
    _, raw = _create(repo.store, "ws1", "k", "owner", "u1")
    repo.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/storefront", api_rate_limit_per_minute=1))
    headers = {"Authorization": f"Bearer {raw}"}

    first = _rpc(client, headers, "tools/list")
    assert first.status_code == 200
    # The REST endpoint and the MCP endpoint drain the SAME bucket.
    second = client.get("/v1/surface", headers=headers)
    assert second.status_code == 429
    assert "Retry-After" in second.headers
