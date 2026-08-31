"""`GET /api/mcp/info` -- the session-authenticated view of the MCP server.

The MCP server is authenticated with an API key, not a session cookie, so the
dashboard cannot call it directly and show the result. This is the read beside
it, and it exists because the MCP work had no presence in the product at all:
eight tools, a role filter and a manifest scanner, discoverable only by reading
the source.
"""

from app.mcp_tools import TOOLS


def test_it_lists_every_tool_with_the_roles_that_may_call_it(client_as_owner):
    body = client_as_owner.get("/api/mcp/info").json()
    assert len(body["tools"]) == len(TOOLS) == 8

    by_name = {t["name"]: t for t in body["tools"]}
    assert by_name["plumbline_approve_patch"]["roles"] == ["owner"]
    assert "reader" in by_name["plumbline_get_run"]["roles"]


def test_roles_are_reported_not_filtered_away(client_as_owner):
    """A tool the caller cannot use is still listed, marked not allowed.

    Silently omitting it would read as a bug rather than as a permission
    boundary: "your key cannot approve a patch" is information, an approve
    tool missing from a list is a mystery.
    """
    body = client_as_owner.get("/api/mcp/info").json()
    names = [t["name"] for t in body["tools"]]
    assert "plumbline_approve_patch" in names
    assert all("allowed" in t for t in body["tools"])


def test_an_owner_may_approve_and_a_demo_visitor_may_not(client_as_owner, client):
    owner = {t["name"]: t for t in client_as_owner.get("/api/mcp/info").json()["tools"]}
    assert owner["plumbline_approve_patch"]["allowed"] is True

    client.post("/api/auth/demo")
    demo = client.get("/api/mcp/info").json()
    assert demo["your_role"] == "reader"
    by_name = {t["name"]: t for t in demo["tools"]}
    assert by_name["plumbline_approve_patch"]["allowed"] is False
    assert by_name["plumbline_get_run"]["allowed"] is True


def test_the_endpoint_it_advertises_is_the_one_that_serves_mcp(client_as_owner):
    body = client_as_owner.get("/api/mcp/info").json()
    assert body["endpoint"].endswith("/mcp")
    # And that path really is mounted, rather than being a string in a docstring.
    assert client_as_owner.post("/mcp", json={}).status_code != 404


def test_summaries_are_one_sentence_not_the_whole_model_prompt(client_as_owner):
    """The full descriptions are written for a model choosing a tool. They run
    to several sentences and would be unreadable in a settings table."""
    for t in client_as_owner.get("/api/mcp/info").json()["tools"]:
        assert t["summary"].endswith(".")
        assert len(t["summary"]) < 200, t["name"]


def test_an_anonymous_caller_gets_401(client):
    assert client.get("/api/mcp/info").status_code == 401
