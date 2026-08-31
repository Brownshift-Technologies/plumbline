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


# --- fix round 1: the CSV export must actually READ in bounded pages, ------
# not just stream a response built from one big list underneath -----------


def test_the_csv_export_never_calls_the_full_chain_read(client_as_owner, ledger, monkeypatch):
    """Test with teeth for the fix-round finding: `ledger_csv` used to
    stream the HTTP response while still reading the whole chain via
    `Ledger.entries` underneath. Monkeypatching `entries` to explode is
    what actually fails if that regression comes back -- asserting on the
    CSV body's content (which passes either way) would not."""
    import gateway.ledger as ledger_module

    for i in range(3):
        ledger.append("ws1", "surgeon", f"tool.{i}", {"decision": "allowed"})

    def explode(self, workspace_id):
        raise AssertionError("ledger_csv must read via entries_page, not entries()")

    monkeypatch.setattr(ledger_module.Ledger, "entries", explode)
    r = client_as_owner.get("/api/ledger.csv")
    assert r.status_code == 200
    assert len(r.text.strip().splitlines()) == 4  # header + 3 entries


def test_the_csv_export_walks_multiple_pages_correctly(client_as_owner, ledger):
    import app.ledger_routes as ledger_routes

    original_page_size = ledger_routes._CSV_PAGE_SIZE
    ledger_routes._CSV_PAGE_SIZE = 2  # force >1 page for a handful of entries
    try:
        for i in range(5):
            ledger.append("ws1", "surgeon", f"tool.{i}", {"decision": "allowed"})
        r = client_as_owner.get("/api/ledger.csv")
    finally:
        ledger_routes._CSV_PAGE_SIZE = original_page_size

    lines = r.text.strip().splitlines()
    assert len(lines) == 6  # header + 5 entries, no duplicates, no gaps
    seqs = [int(line.split(",")[0]) for line in lines[1:]]
    assert seqs == [0, 1, 2, 3, 4]
