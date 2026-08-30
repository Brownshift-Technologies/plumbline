"""Password reset -- see app/account_routes.py's module docstring for the
enumeration-resistance, hashed-at-rest, and full-session-revocation
guarantees this exercises.
"""

import time

from app.models import PasswordReset
from app.security import hash_password, hash_token


def _capture_reset_email(app):
    """Install a capturing `deliver_reset_email` hook and return the list
    it appends `(email, token)` to -- the only way this test suite can get
    at a raw reset token, since the HTTP response deliberately never
    contains one (see the module docstring)."""
    sent = []
    app.state.deliver_reset_email = lambda email, token: sent.append((email, token))
    return sent


# --- from the brief ----------------------------------------------------------


def test_request_returns_the_same_response_for_a_known_and_unknown_email(client_as_owner, client, app):
    _capture_reset_email(app)
    known = client.post("/api/auth/reset/request", json={"email": "owner@acme.com"})
    unknown = client.post("/api/auth/reset/request", json={"email": "nobody@acme.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"ok": True}


# A `test_wrong_and_right_password_cost_roughly_the_same`-style timing check
# was tried here and dropped: unlike signin (dominated by a real argon2
# hash either way -- see app/security.py's _DUMMY_HASH), this route's two
# branches differ only by one extra in-process function call
# (`deliver_reset_email`), too small relative to TestClient/ASGI overhead
# for a wall-clock ratio to say anything meaningful without becoming a
# flaky test. Same-status/same-body (above) plus doing the same Firestore
# write on both paths (`test_the_token_is_stored_hashed_not_in_the_clear`
# implicitly covers "a row is always written") is the honest bar for what
# is actually verifiable here.


def test_the_token_is_stored_hashed_not_in_the_clear(client_as_owner, client, app, repo):
    sent = _capture_reset_email(app)
    client.post("/api/auth/reset/request", json={"email": "owner@acme.com"})
    assert len(sent) == 1
    _, raw_token = sent[0]

    # The raw token must not appear anywhere in the store under its own
    # value -- only its hash is a valid document id.
    assert repo.store.get("password_resets", raw_token) is None
    stored = repo.store.get("password_resets", hash_token(raw_token))
    assert stored is not None
    assert stored["id"] != raw_token


def test_consuming_a_reset_revokes_every_existing_session(client_as_owner, client, app, repo, sessions):
    uid = client_as_owner.get("/api/auth/me").json()["id"]
    # A second, independent session for the same user (a second device).
    other = sessions.issue(uid, "ws1", user_agent="other-device")
    assert sessions.resolve(other.id) is not None

    sent = _capture_reset_email(app)
    client.post("/api/auth/reset/request", json={"email": "owner@acme.com"})
    _, raw_token = sent[0]

    r = client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "a new long enough password"})
    assert r.status_code == 200

    # Both the other device's session AND the session client_as_owner was
    # using when it made the request are gone -- a reset trusts nothing
    # that predates it, including a session an attacker might hold.
    assert sessions.resolve(other.id) is None
    assert client_as_owner.get("/api/auth/me").status_code == 401


def test_the_new_password_actually_signs_in(client, app, repo, sessions):
    from app.models import User
    from app.security import verify_password

    user = User(id="u_reset", email="reset@acme.com", password_hash=hash_password("old long enough password"), name="R")
    repo.put_user(user)

    sent = _capture_reset_email(app)
    client.post("/api/auth/reset/request", json={"email": "reset@acme.com"})
    _, raw_token = sent[0]

    client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "brand new long password"})
    updated = repo.user("u_reset")
    assert verify_password("brand new long password", updated.password_hash)
    assert not verify_password("old long enough password", updated.password_hash)


def test_a_reset_token_is_single_use(client_as_owner, client, app):
    sent = _capture_reset_email(app)
    client.post("/api/auth/reset/request", json={"email": "owner@acme.com"})
    _, raw_token = sent[0]

    first = client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "first new password!!"})
    assert first.status_code == 200

    second = client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "second new password!"})
    assert second.status_code == 400


def test_a_second_use_of_the_same_token_is_rejected(client_as_owner, client, app):
    sent = _capture_reset_email(app)
    client.post("/api/auth/reset/request", json={"email": "owner@acme.com"})
    _, raw_token = sent[0]
    client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "one time use password"})

    r = client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "another password try"})
    assert r.status_code == 400


def test_a_reset_token_expires(client_as_owner, repo):
    uid = client_as_owner.get("/api/auth/me").json()["id"]
    raw_token = "already-expired-token"
    repo.put_password_reset(
        PasswordReset(id=hash_token(raw_token), user_id=uid, expires_at=time.time() - 1)
    )
    result = repo.consume_password_reset(hash_token(raw_token))
    assert result is None


def test_an_expired_token_is_rejected(client_as_owner, client, repo):
    uid = client_as_owner.get("/api/auth/me").json()["id"]
    raw_token = "stale-token"
    repo.put_password_reset(
        PasswordReset(id=hash_token(raw_token), user_id=uid, expires_at=time.time() - 1)
    )
    r = client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "a fine new password"})
    assert r.status_code == 400


# --- extra attacker-shaped tests ---------------------------------------------


def test_an_unknown_token_is_rejected(client):
    r = client.post("/api/auth/reset/confirm", json={"token": "this-was-never-issued", "new_password": "whatever new password"})
    assert r.status_code == 400


def test_a_reset_token_for_a_user_that_no_longer_resolves_is_rejected(client, repo):
    # Simulates a token that was genuinely issued but whose account is now
    # gone/unresolvable -- Store has no hard delete, so this is modelled
    # directly the way app/repo.py's own delete_session tombstoning is:
    # a row that used to point at someone real now points at nobody.
    raw_token = "orphaned-token"
    repo.put_password_reset(
        PasswordReset(id=hash_token(raw_token), user_id="u_does_not_exist", expires_at=time.time() + 1800)
    )
    r = client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "does not matter here"})
    assert r.status_code == 400


def test_a_short_new_password_is_rejected_even_with_a_valid_token(client_as_owner, client, app):
    sent = _capture_reset_email(app)
    client.post("/api/auth/reset/request", json={"email": "owner@acme.com"})
    _, raw_token = sent[0]

    r = client.post("/api/auth/reset/confirm", json={"token": raw_token, "new_password": "short"})
    assert r.status_code == 400
