"""Tier 2 (2026-08-30 contract, item 1): `app/workspace_routes.py` --
`GET /api/workspace` and `PUT /api/workspace/target-url`.

`_member` (tests/conftest.py) seeds `ws1` with no `target_url` -- the
`Workspace` default, `""` -- so every test below that cares about the
CURRENT value starts from "unconfigured" unless it sets one first.
"""

from app.models import Workspace

_VALID = "https://app.example.com"


def test_getting_the_workspace_reports_an_unset_target_url(client_as_owner):
    r = client_as_owner.get("/api/workspace")
    assert r.status_code == 200
    assert r.json()["target_url"] == ""


def test_an_owner_can_set_a_valid_target_url(client_as_owner, repo):
    r = client_as_owner.put("/api/workspace/target-url", json={"target_url": _VALID})
    assert r.status_code == 200
    assert r.json()["target_url"] == _VALID
    assert repo.workspace("ws1").target_url == _VALID


def test_an_owner_can_clear_the_target_url_back_to_unconfigured(client_as_owner, repo):
    repo.put_workspace(Workspace(**{**repo.workspace("ws1").__dict__, "target_url": _VALID}))
    r = client_as_owner.put("/api/workspace/target-url", json={"target_url": ""})
    assert r.status_code == 200
    assert r.json()["target_url"] == ""
    assert repo.workspace("ws1").target_url == ""


def test_a_javascript_url_is_rejected_at_save_time(client_as_owner, repo):
    r = client_as_owner.put("/api/workspace/target-url", json={"target_url": "javascript:alert(1)"})
    assert r.status_code == 400
    assert "target_url" in r.json()["detail"]
    # Never persisted -- a rejected write must not clobber whatever was
    # already configured (here, still the unset default).
    assert repo.workspace("ws1").target_url == ""


def test_a_file_url_is_rejected_at_save_time(client_as_owner):
    r = client_as_owner.put("/api/workspace/target-url", json={"target_url": "file:///etc/passwd"})
    assert r.status_code == 400


def test_a_target_url_without_a_host_is_rejected(client_as_owner, repo):
    r = client_as_owner.put("/api/workspace/target-url", json={"target_url": "http://"})
    assert r.status_code == 400
    assert repo.workspace("ws1").target_url == ""


def test_a_target_url_missing_a_scheme_is_rejected(client_as_owner):
    r = client_as_owner.put("/api/workspace/target-url", json={"target_url": "app.example.com"})
    assert r.status_code == 400


def test_a_protocol_relative_target_url_is_rejected(client_as_owner):
    # Reuses `_internal_href`'s own normalisation: a leading `//` (here,
    # hidden behind a folded backslash -- WHATWG treats `\` as `/` for
    # http/https) has no scheme of its own to accept -- there is no
    # "current page" for a target URL to inherit one from the way a
    # discovered href would.
    r = client_as_owner.put("/api/workspace/target-url", json={"target_url": "/\\evil.com"})
    assert r.status_code == 400


def test_a_reader_cannot_set_the_target_url(client_as_reader, repo):
    r = client_as_reader.put("/api/workspace/target-url", json={"target_url": _VALID})
    assert r.status_code == 403
    assert repo.workspace("ws1").target_url == ""


def test_an_approver_cannot_set_the_target_url(client_as_approver, repo):
    # Same tier as `repo_full_name` (app/github_routes.py) and `gate_rules`
    # (app/agent_routes.py) -- owner-only, not the wider ("owner",
    # "approver") pair most other write routes use.
    r = client_as_approver.put("/api/workspace/target-url", json={"target_url": _VALID})
    assert r.status_code == 403
    assert repo.workspace("ws1").target_url == ""


def test_setting_the_target_url_appends_a_ledger_entry(client_as_owner, ledger):
    client_as_owner.put("/api/workspace/target-url", json={"target_url": _VALID})
    entries = [e for e in ledger.entries("ws1") if e["action"] == "workspace.target_url_updated"]
    assert len(entries) == 1
    assert entries[0]["detail"]["target_url"] == _VALID
