"""Task 14d: `GET /docs`, `GET /openapi.json`, and the promise that every
`/v1/...` route is actually documented, not merely present in the schema."""


def test_openapi_json_contains_only_v1_routes(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["paths"], "expected at least one documented path"
    for path in schema["paths"]:
        assert path.startswith("/v1"), f"{path!r} is not a public /v1 route and should not be documented"
    # And the internal, session-authenticated surface is NOT published --
    # core/web.py's own module docstring is explicit about why (this
    # service is deployed --allow-unauthenticated).
    assert "/api/auth/signin" not in schema["paths"]
    assert "/api/billing" not in schema["paths"]


def test_docs_page_serves_interactive_docs(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower()


def test_every_v1_route_has_a_description_and_an_example(client):
    schema = client.get("/openapi.json").json()
    checked = 0
    for path, methods in schema["paths"].items():
        for verb, operation in methods.items():
            if verb not in ("get", "post", "put", "delete", "patch"):
                continue
            checked += 1
            assert operation.get("summary"), f"{verb.upper()} {path} has no summary"
            assert operation.get("description"), f"{verb.upper()} {path} has no description"

            responses = operation.get("responses", {})
            success = next((r for code, r in responses.items() if code.startswith("2")), None)
            assert success is not None, f"{verb.upper()} {path} has no documented success response"
            content = success.get("content", {}).get("application/json", {})
            has_example = "example" in content or "examples" in content
            assert has_example, f"{verb.upper()} {path} has no example response"
    assert checked >= 6, "expected every /v1 route from the task-14d brief to be documented"
