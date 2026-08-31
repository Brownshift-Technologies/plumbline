"""Task 14g: `app/github_routes.py` -- connect/disconnect role gates, the
inbound webhook's signature verification, and revoking a cached token on
disconnect."""

import hashlib
import hmac
import json

from agents.repo_source import FakeGitHub
from app.models import Workspace


def _connect_body():
    return {"repo_full_name": "acme/storefront", "default_branch": "main"}


def test_a_reader_cannot_connect_a_repository(client_as_reader):
    resp = client_as_reader.post("/api/workspaces/ws1/repo", json=_connect_body())
    assert resp.status_code == 403


def test_a_demo_session_cannot_connect_a_repository(client_demo):
    ws_id = client_demo.get("/api/auth/me").json()["workspace_id"]
    resp = client_demo.post(f"/api/workspaces/{ws_id}/repo", json=_connect_body())
    assert resp.status_code == 200
    body = resp.json()
    # Real GitHub connections still stay refused in a demo session's own
    # sandbox -- see this task's report's "must stay refused" list -- but
    # with a reason explaining why, not the old bare "nothing was saved".
    assert body["demo"] is True and body["persisted"] is False
    assert "repository" in body["reason"].lower()


def test_an_owner_can_connect_after_an_installation_exists(client_as_owner):
    repo = client_as_owner.app.state.repo
    ws = repo.workspace("ws1")
    repo.put_workspace(type(ws)(**{**ws.__dict__, "installation_id": "inst_1"}))
    client_as_owner.app.state.github_app = FakeGitHub()

    resp = client_as_owner.post("/api/workspaces/ws1/repo", json=_connect_body())
    assert resp.status_code == 200
    assert resp.json()["repo_full_name"] == "acme/storefront"
    assert repo.workspace("ws1").repo_full_name == "acme/storefront"


def test_connecting_without_an_installation_first_is_rejected(client_as_owner):
    resp = client_as_owner.post("/api/workspaces/ws1/repo", json=_connect_body())
    assert resp.status_code == 400


def test_disconnecting_a_repo_revokes_the_cached_token(client_as_owner):
    repo = client_as_owner.app.state.repo
    ws = repo.workspace("ws1")
    repo.put_workspace(type(ws)(**{**ws.__dict__, "installation_id": "inst_1"}))
    fake = FakeGitHub()
    client_as_owner.app.state.github_app = fake

    client_as_owner.post("/api/workspaces/ws1/repo", json=_connect_body())
    # Prime the cache the way GitHubApp's own installation_token() would.
    fake._tokens = {"inst_1": ("ghs_cached", 9999999999.0)}

    resp = client_as_owner.delete("/api/workspaces/ws1/repo")
    assert resp.status_code == 200
    assert "inst_1" not in fake._tokens
    assert repo.workspace("ws1").repo_full_name == ""


def test_a_webhook_without_a_signature_is_401(client):
    resp = client.post("/api/github/webhook", content=b'{"action":"opened"}',
                        headers={"X-GitHub-Event": "pull_request"})
    assert resp.status_code == 401


def test_a_webhook_with_a_wrong_signature_is_401(client):
    resp = client.post(
        "/api/github/webhook", content=b'{"action":"opened"}',
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert resp.status_code == 401


def test_a_webhook_with_a_correct_signature_is_accepted(client):
    client.app.state.github_webhook_secret = "whsec_test"
    body = json.dumps({"action": "opened", "installation": {"id": 1}}).encode()
    signature = "sha256=" + hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/api/github/webhook", content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 204
