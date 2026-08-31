"""Task 14d: `pk_live_` API keys -- generation, hashing, role ceiling,
expiry, revocation, and per-key rate limiting."""

import time

from app.api_keys import create_api_key, hash_key
from app.models import Workspace


def _ws(repo, **overrides):
    ws = Workspace(id="ws1", name="Acme", repo="acme/storefront", **overrides)
    repo.put_workspace(ws)
    return ws


def _make_key(client, role, **kw):
    """Build a workspace (if needed) and a real API key directly through
    `app.api_keys.create_api_key`, bypassing the session-authenticated
    `POST /api/keys` route -- for tests (`client`, no pre-authenticated
    session) that only need a working key of a given role, not a
    round-trip through the owner-only creation endpoint (already covered
    by `test_a_key_is_shown_once_and_stored_hashed` below)."""
    repo = client.app.state.repo
    if repo.workspace("ws1") is None:
        _ws(repo)
    _, raw = create_api_key(repo.store, "ws1", "test key", role, "u_test", **kw)
    return raw


def _auth(raw_key: str) -> dict:
    return {"Authorization": f"Bearer {raw_key}"}


def test_a_key_is_shown_once_and_stored_hashed(client_as_owner, repo):
    resp = client_as_owner.post("/api/keys", json={"name": "CI key", "role": "owner"})
    assert resp.status_code == 200
    body = resp.json()
    raw_key = body["key"]
    assert raw_key.startswith("pk_live_")

    stored = repo.store.get("api_keys", body["id"])
    assert stored["key_hash"] == hash_key(raw_key)
    # The raw value never sits in the stored document under any field.
    assert raw_key not in stored.values()
    assert all(raw_key != v for v in stored.values() if isinstance(v, str))

    # And it is never shown again on a later list.
    listed = client_as_owner.get("/api/keys").json()["keys"]
    assert all("key" not in k for k in listed)


def test_a_leaked_hash_cannot_be_used_to_authenticate(client, repo):
    raw_key = _make_key(client, "owner")
    stored = repo.store.get("api_keys", repo.store.query("api_keys", "workspace_id", "==", "ws1")[0]["id"])
    leaked_hash = stored["key_hash"]

    # The raw key works.
    ok = client.get("/v1/surface", headers=_auth(raw_key))
    assert ok.status_code == 200

    # The hash itself -- exactly what a leaked Firestore export would
    # contain -- does not.
    denied = client.get("/v1/surface", headers=_auth(leaked_hash))
    assert denied.status_code == 401


def test_a_reader_key_cannot_approve_a_patch(client):
    raw_key = _make_key(client, "reader")
    resp = client.post("/v1/findings/fnd_1/approve", headers=_auth(raw_key))
    assert resp.status_code == 403


def test_a_key_cannot_exceed_the_role_it_was_issued_with(client):
    raw_key = _make_key(client, "reader")
    # A reader key can read...
    assert client.get("/v1/surface", headers=_auth(raw_key)).status_code == 200
    # ...but cannot write, no matter what it asks for.
    resp = client.post("/v1/runs", json={"trigger": "ci"}, headers=_auth(raw_key))
    assert resp.status_code == 403


def test_an_expired_key_is_rejected(client):
    raw_key = _make_key(client, "owner", expires_in_days=-1)  # already expired
    resp = client.get("/v1/surface", headers=_auth(raw_key))
    assert resp.status_code == 401


def test_a_revoked_key_stops_working_immediately(client_as_owner):
    created = client_as_owner.post("/api/keys", json={"name": "k", "role": "owner"}).json()
    raw_key = created["key"]
    assert client_as_owner.get("/v1/surface", headers=_auth(raw_key)).status_code == 200

    revoke = client_as_owner.delete(f"/api/keys/{created['id']}")
    assert revoke.status_code == 200

    assert client_as_owner.get("/v1/surface", headers=_auth(raw_key)).status_code == 401


def test_rate_limiting_is_per_key_not_per_ip(client):
    repo = client.app.state.repo
    _ws(repo, api_rate_limit_per_minute=2)
    key_a, _ = create_api_key(repo.store, "ws1", "a", "owner", "u1")
    key_b, _ = create_api_key(repo.store, "ws1", "b", "owner", "u1")
    from app.api_keys import check_rate_limit

    # Exhaust key A's tiny bucket entirely -- every request comes from the
    # SAME test client / same "IP" as key B's requests below.
    result1 = check_rate_limit(repo.store, key_a, 2)
    result2 = check_rate_limit(repo.store, key_a, 2)
    result3 = check_rate_limit(repo.store, key_a, 2)
    assert result1.allowed and result2.allowed
    assert not result3.allowed

    # Key B, from the exact same client/IP, is untouched -- its own bucket
    # has never been spent.
    result_b = check_rate_limit(repo.store, key_b, 2)
    assert result_b.allowed


def test_a_429_carries_retry_after(client):
    repo = client.app.state.repo
    _ws(repo, api_rate_limit_per_minute=1)
    _, raw = create_api_key(repo.store, "ws1", "k", "owner", "u1")

    first = client.get("/v1/surface", headers=_auth(raw))
    assert first.status_code == 200
    second = client.get("/v1/surface", headers=_auth(raw))
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0


def test_a_demo_session_cannot_create_a_key(client_demo):
    resp = client_demo.post("/api/keys", json={"name": "x", "role": "owner"})
    assert resp.status_code == 200
    assert resp.json() == {"demo": True, "persisted": False}
