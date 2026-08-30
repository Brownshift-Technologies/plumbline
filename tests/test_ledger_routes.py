"""Task 14c: `GET /api/ledger`, `GET /api/ledger/verify`, `GET /api/ledger.csv`."""


def test_verify_reports_an_intact_chain(client_as_owner, ledger):
    ledger.append("ws1", "surgeon", "pr.open", {"decision": "allowed"})
    ledger.append("ws1", "surgeon", "pr.merge", {"decision": "allowed"})
    r = client_as_owner.get("/api/ledger/verify")
    body = r.json()
    assert body == {"intact": True, "checked": 2}


def test_verify_reports_a_tampered_chain(client_as_owner, ledger, repo):
    ledger.append("ws1", "surgeon", "pr.open", {"decision": "allowed"})
    entry_id = ledger.entries("ws1")[0]["id"]
    tampered = {**repo.store.get("ledger", entry_id), "action": "pr.merge"}
    repo.store.put("ledger", entry_id, tampered)
    r = client_as_owner.get("/api/ledger/verify")
    body = r.json()
    assert body["intact"] is False


def test_the_ledger_paginates(client_as_owner, ledger):
    for i in range(5):
        ledger.append("ws1", "surgeon", f"tool.{i}", {"decision": "allowed"})
    first = client_as_owner.get("/api/ledger", params={"limit": 2}).json()
    assert len(first["entries"]) == 2
    assert first["next_cursor"] is not None
    second = client_as_owner.get(
        "/api/ledger", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len(second["entries"]) == 2
    assert {e["id"] for e in first["entries"]}.isdisjoint({e["id"] for e in second["entries"]})


def test_an_unknown_ledger_cursor_falls_back_to_the_first_page(client_as_owner, ledger):
    ledger.append("ws1", "surgeon", "tool.a", {"decision": "allowed"})
    r = client_as_owner.get("/api/ledger", params={"cursor": "not-a-real-entry"})
    assert r.status_code == 200
    assert len(r.json()["entries"]) == 1


def test_the_csv_export_streams(client_as_owner, ledger):
    ledger.append("ws1", "surgeon", "pr.merge", {"decision": "allowed", "target": "src/x.ts"})
    r = client_as_owner.get("/api/ledger.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].split(",")[:2] == ["seq", "at"]
    assert len(lines) == 2  # header + one entry


def test_ledger_reads_do_not_see_another_workspaces_entries(client_as_owner, ledger):
    ledger.append("ws-other", "surgeon", "pr.merge", {"decision": "allowed"})
    r = client_as_owner.get("/api/ledger").json()
    assert r["total"] == 0
