"""OAuth start/callback against `FakeProvider` -- offline, no network. See
app/oauth_routes.py's module docstring for the CSRF-state design and the
account-linking decision this exercises.

A separate opt-in suite (tests/test_oauth_live.py) is where a real
Google/GitHub/Okta endpoint would be exercised; nothing in this file ever
leaves the process.
"""

from itsdangerous import URLSafeTimedSerializer

from app.providers import FakeProvider


def _install_fake(app, **kwargs):
    provider = FakeProvider(**kwargs)
    app.state.oauth_providers["fake"] = provider
    return provider


def _start(client):
    return client.get("/api/auth/oauth/fake/start", follow_redirects=False)


# --- from the brief ----------------------------------------------------------


def test_start_redirects_to_the_provider(client, app):
    _install_fake(app, email="new@acme.com")
    r = _start(client)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://fake-provider.test/authorize?state=")
    assert client.cookies.get("pl_oauth_state")


def test_callback_rejects_a_missing_state(client, app):
    _install_fake(app, email="new@acme.com")
    _start(client)
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "abc"})
    assert r.status_code == 400


def test_callback_rejects_a_forged_state(client, app):
    _install_fake(app, email="new@acme.com")
    _start(client)
    forged = URLSafeTimedSerializer("a-completely-different-key", salt="plumbline-oauth-state").dumps(
        {"n": "x", "p": "fake"}
    )
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "abc", "state": forged})
    assert r.status_code == 400


def test_callback_links_to_an_existing_user_by_email(client_as_owner, client, app, repo):
    original_id = repo.user_by_email("owner@acme.com").id
    _install_fake(app, email="owner@acme.com", email_verified=True)

    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "code-1", "state": signed_state})

    assert r.status_code == 200
    assert r.json()["id"] == original_id
    # No second account was created for this email.
    assert repo.user_by_email("owner@acme.com").id == original_id


def test_callback_creates_a_user_when_the_email_is_new(client, app, repo):
    _install_fake(app, email="brand-new@acme.com", name="Brand New")

    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "code-1", "state": signed_state})

    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Brand New"
    user = repo.user_by_email("brand-new@acme.com")
    assert user is not None
    assert user.id == body["id"]
    # A real, working workspace membership, not a half-created account.
    assert repo.role_of(user.id, body["workspace_id"]) == "owner"


def test_callback_issues_a_session_cookie(client, app):
    _install_fake(app, email="cookie-check@acme.com")
    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "code-1", "state": signed_state})
    assert r.status_code == 200
    assert "pl_session" in client.cookies
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "cookie-check@acme.com"


# --- account-linking decision, both directions -------------------------------


def test_callback_refuses_to_link_an_unverified_email_to_an_existing_account(client_as_owner, client, app, repo):
    # The attack the brief names: someone who merely REGISTERED an OAuth
    # account using another person's email address (no proof of mailbox
    # control) must not thereby be handed that person's real, existing
    # Plumbline account.
    original_id = repo.user_by_email("owner@acme.com").id
    _install_fake(app, email="owner@acme.com", email_verified=False)

    # A fresh, anonymous browser -- not signed in as the victim -- attempts
    # the OAuth flow. This is the attacker's own browser in the real
    # attack: they never had the victim's session cookie to begin with.
    client.cookies.clear()
    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "code-1", "state": signed_state})

    assert r.status_code == 409
    # No session was handed out for the real owner's account.
    assert "pl_session" not in client.cookies
    assert repo.user_by_email("owner@acme.com").id == original_id


def test_callback_creates_a_new_account_for_an_unverified_email_that_is_not_already_registered(client, app, repo):
    # An unverified email is only refused when it would LINK to an
    # existing account -- a brand new email has nothing to take over.
    _install_fake(app, email="unverified-but-new@acme.com", email_verified=False)
    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "code-1", "state": signed_state})
    assert r.status_code == 200
    assert repo.user_by_email("unverified-but-new@acme.com") is not None


# --- extra attacker-shaped tests ---------------------------------------------


def test_callback_rejects_a_state_that_belongs_to_a_different_browser(client, app):
    # The classic OAuth login-CSRF: an attacker completes their OWN OAuth
    # flow to obtain a validly-signed state+code pair, then gets a victim
    # to open a callback URL carrying the attacker's state as a query
    # param. The victim's browser never received the attacker's state
    # cookie, so the query-vs-cookie equality check must fail even though
    # the state itself verifies cleanly against this app's signing key.
    _install_fake(app, email="victim@acme.com")
    start = _start(client)
    attackers_signed_state = start.headers["location"].split("state=")[1]

    # Stand in for "the victim's browser", which never received the
    # attacker's state cookie: clear it before presenting the attacker's
    # captured state+code pair.
    client.cookies.clear()
    r = client.get(
        "/api/auth/oauth/fake/callback", params={"code": "attacker-code", "state": attackers_signed_state}
    )
    assert r.status_code == 400


def test_callback_rejects_an_expired_state(client, app, monkeypatch):
    _install_fake(app, email="slow@acme.com")
    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]

    # Shrink the acceptance window to 0 rather than sleeping in a test:
    # `callback` reads this module global at call time, so any elapsed
    # time at all between the signing above and the request below now
    # counts as expired.
    import app.oauth_routes as oauth_routes

    monkeypatch.setattr(oauth_routes, "_STATE_MAX_AGE", -1)
    r = client.get("/api/auth/oauth/fake/callback", params={"code": "abc", "state": signed_state})
    assert r.status_code == 400


def test_a_callback_replayed_with_the_same_code_is_rejected(client, app):
    _install_fake(app, email="replay@acme.com")
    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]
    params = {"code": "reused-code", "state": signed_state}

    first = client.get("/api/auth/oauth/fake/callback", params=params)
    assert first.status_code == 200

    # The state cookie was consumed on the first, successful callback; a
    # second request replaying the exact same URL has nothing left to
    # match it against.
    second = client.get("/api/auth/oauth/fake/callback", params=params)
    assert second.status_code == 400


def test_a_replayed_code_is_rejected_even_with_a_fresh_valid_state(client, app):
    # Defence in depth: even if an attacker somehow obtained a second,
    # independently-valid state for the same browser/provider, the
    # PROVIDER itself (real OAuth servers, and FakeProvider mirroring that)
    # refuses to exchange an authorization code a second time.
    provider = _install_fake(app, email="replay2@acme.com")
    start1 = _start(client)
    state1 = start1.headers["location"].split("state=")[1]
    first = client.get("/api/auth/oauth/fake/callback", params={"code": "one-time-code", "state": state1})
    assert first.status_code == 200

    start2 = _start(client)
    state2 = start2.headers["location"].split("state=")[1]
    second = client.get("/api/auth/oauth/fake/callback", params={"code": "one-time-code", "state": state2})
    assert second.status_code == 400
    assert "one-time-code" in provider._redeemed_codes


def test_unknown_provider_is_404(client):
    r = client.get("/api/auth/oauth/not-a-real-provider/start", follow_redirects=False)
    assert r.status_code == 404


def test_callback_rejects_a_provider_mismatch_between_state_and_path(client, app):
    # A state signed for "fake" presented on a different provider's
    # callback path must not be honoured just because the signature and
    # cookie otherwise match.
    _install_fake(app, email="mismatch@acme.com")
    _install_fake(app, email="mismatch2@acme.com")  # ensure "fake" exists; only one provider registered in tests
    start = _start(client)
    signed_state = start.headers["location"].split("state=")[1]
    app.state.oauth_providers["fake2"] = app.state.oauth_providers["fake"]
    r = client.get("/api/auth/oauth/fake2/callback", params={"code": "abc", "state": signed_state})
    assert r.status_code == 400
