"""POST /api/auth/{signup,signin,signout,password}, GET /api/auth/me,
GET/DELETE /api/auth/sessions -- everything except OAuth/TOTP/reset
(Task 8b) and demo entry (tests/test_demo_mode.py)."""

import time

# --- from the brief ---------------------------------------------------------


def test_signup_then_signin_sets_a_session_cookie(client):
    client.post(
        "/api/auth/signup",
        json={"email": "r@acme.com", "password": "correct horse battery", "name": "Roger K."},
    )
    r = client.post("/api/auth/signin", json={"email": "r@acme.com", "password": "correct horse battery"})
    assert r.status_code == 200 and "pl_session" in r.cookies


def test_signin_with_a_wrong_password_is_401(client):
    client.post(
        "/api/auth/signup",
        json={"email": "r@acme.com", "password": "correct horse battery", "name": "Roger K."},
    )
    assert client.post("/api/auth/signin", json={"email": "r@acme.com", "password": "nope"}).status_code == 401


def test_me_is_401_without_a_session(client):
    assert client.get("/api/auth/me").status_code == 401


def test_changing_the_password_signs_out_other_devices(client, repo, sessions):
    client.post(
        "/api/auth/signup",
        json={"email": "r@acme.com", "password": "correct horse battery", "name": "Roger K."},
    )
    client.post("/api/auth/signin", json={"email": "r@acme.com", "password": "correct horse battery"})
    user = repo.user_by_email("r@acme.com")
    stale = sessions.issue(user.id, "ws1")
    client.post("/api/auth/password", json={"current": "correct horse battery", "new": "a longer passphrase"})
    assert sessions.resolve(stale.id) is None


# --- Step 0a: sign-in must not leak which emails exist ----------------------


def test_a_missing_email_and_a_wrong_password_return_identical_responses(client):
    client.post(
        "/api/auth/signup",
        json={"email": "real@acme.com", "password": "correct horse battery", "name": "Real"},
    )
    missing = client.post("/api/auth/signin", json={"email": "nosuchuser@acme.com", "password": "whatever12345"})
    wrong = client.post("/api/auth/signin", json={"email": "real@acme.com", "password": "wrongpassword12"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


def test_a_missing_email_and_a_wrong_password_cost_roughly_the_same(client):
    # Coarse smoke check, matching the threshold/iteration shape
    # tests/test_security.py already uses for the same class of claim --
    # not a precision timing measurement, which would be too flaky here.
    client.post(
        "/api/auth/signup",
        json={"email": "real2@acme.com", "password": "correct horse battery", "name": "Real"},
    )
    iterations = 5

    start = time.perf_counter()
    for _ in range(iterations):
        client.post("/api/auth/signin", json={"email": "nosuchuser2@acme.com", "password": "whatever12345"})
    missing_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(iterations):
        client.post("/api/auth/signin", json={"email": "real2@acme.com", "password": "wrongpassword12"})
    wrong_elapsed = time.perf_counter() - start

    ratio = max(missing_elapsed, wrong_elapsed) / max(min(missing_elapsed, wrong_elapsed), 1e-9)
    assert ratio < 3.0, f"missing-email vs wrong-password signin timing diverged too much: ratio={ratio:.2f}"


# --- Step 3a: normalise email at the choke point -----------------------------


def test_a_mixed_case_signup_can_sign_in_lowercased(client):
    client.post(
        "/api/auth/signup",
        json={"email": "Roger@Acme.com", "password": "correct horse battery", "name": "Roger K."},
    )
    r = client.post("/api/auth/signin", json={"email": "roger@acme.com", "password": "correct horse battery"})
    assert r.status_code == 200


def test_signup_rejects_an_email_already_taken_in_another_case(client):
    body = {"email": "Roger@Acme.com", "password": "correct horse battery", "name": "R"}
    client.post("/api/auth/signup", json=body)
    again = client.post("/api/auth/signup", json={**body, "email": "roger@acme.com"})
    assert again.status_code == 409


# --- attacker-shaped tests beyond the brief ----------------------------------


def test_signup_with_an_email_differing_only_by_case_from_an_existing_one_is_rejected(client):
    # Same attack as the brief's own case-collision test, phrased the other
    # direction (existing account is uppercase, new attempt is lowercase)
    # to confirm the check is symmetric rather than only catching one
    # direction of the comparison.
    client.post(
        "/api/auth/signup",
        json={"email": "existing@acme.com", "password": "correct horse battery", "name": "E"},
    )
    again = client.post(
        "/api/auth/signup",
        json={"email": "EXISTING@ACME.COM", "password": "correct horse battery", "name": "E2"},
    )
    assert again.status_code == 409


def test_signup_password_boundary_eleven_chars_rejected_twelve_accepted(client):
    # The minimum is 12; 11 must fail closed and 12 must be the first
    # length that succeeds -- an off-by-one here is a real weakening of
    # the minimum, not a cosmetic issue.
    eleven = client.post(
        "/api/auth/signup",
        json={"email": "short@acme.com", "password": "a" * 11, "name": "Short"},
    )
    assert eleven.status_code == 400

    twelve = client.post(
        "/api/auth/signup",
        json={"email": "exact@acme.com", "password": "a" * 12, "name": "Exact"},
    )
    assert twelve.status_code == 200


def test_password_change_boundary_eleven_chars_rejected_twelve_accepted(client):
    client.post(
        "/api/auth/signup",
        json={"email": "changer@acme.com", "password": "correct horse battery", "name": "C"},
    )
    client.post("/api/auth/signin", json={"email": "changer@acme.com", "password": "correct horse battery"})

    too_short = client.post(
        "/api/auth/password", json={"current": "correct horse battery", "new": "a" * 11}
    )
    assert too_short.status_code == 400

    long_enough = client.post(
        "/api/auth/password", json={"current": "correct horse battery", "new": "a" * 12}
    )
    assert long_enough.status_code == 200


def test_a_session_cookie_from_a_different_workspace_is_still_a_valid_session(client, repo, sessions):
    # current_session only checks that the *session* resolves -- it says
    # nothing about which workspace's data a route then lets that session
    # touch. That authorisation boundary is require_role's job (and each
    # route's, when it reads/writes workspace-scoped data), not
    # current_session's -- so a session issued for one workspace must
    # still authenticate normally; it must not be treated as invalid
    # merely for naming a workspace this app instance happens to have no
    # other record of.
    client.post(
        "/api/auth/signup",
        json={"email": "cross@acme.com", "password": "correct horse battery", "name": "Cross"},
    )
    user = repo.user_by_email("cross@acme.com")
    other_ws_session = sessions.issue(user.id, "ws_some_other_tenant_entirely")
    client.cookies.set("pl_session", other_ws_session.id)
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["workspace_id"] == "ws_some_other_tenant_entirely"


def test_a_cookie_for_a_revoked_session_is_401(client, sessions, repo):
    client.post(
        "/api/auth/signup",
        json={"email": "revoked@acme.com", "password": "correct horse battery", "name": "Rev"},
    )
    client.post("/api/auth/signin", json={"email": "revoked@acme.com", "password": "correct horse battery"})
    sid = client.cookies.get("pl_session")
    sessions.revoke(sid)
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_password_change_with_correct_current_but_a_too_short_new_password_is_rejected_and_does_not_revoke_sessions(
    client, repo, sessions
):
    # A rejected password change must be a true no-op: it must not sign out
    # other devices just because the *current* password check passed --
    # only an actually-applied change should do that (see
    # test_changing_the_password_signs_out_other_devices).
    client.post(
        "/api/auth/signup",
        json={"email": "guarded@acme.com", "password": "correct horse battery", "name": "G"},
    )
    client.post("/api/auth/signin", json={"email": "guarded@acme.com", "password": "correct horse battery"})
    user = repo.user_by_email("guarded@acme.com")
    other = sessions.issue(user.id, "ws1")

    r = client.post("/api/auth/password", json={"current": "correct horse battery", "new": "short"})
    assert r.status_code == 400
    assert sessions.resolve(other.id) is not None


def test_concurrent_signups_on_the_same_email_only_one_wins(client, repo):
    # Simulated concurrency: two requests both pass repo.user_by_email's
    # cheap pre-check by racing repo.claim_email directly (the transactional
    # layer signup relies on to close the actual window an HTTP-level race
    # cannot deterministically reproduce against a single in-process
    # TestClient). Exactly one of two racing claims for the same email may
    # succeed, and exactly one User document must exist afterwards.
    import uuid

    email = "racer@acme.com"
    winner = repo.claim_email(email, "u_first")
    loser = repo.claim_email(email, f"u_second_{uuid.uuid4().hex[:6]}")
    assert winner is True
    assert loser is False


def test_a_second_signup_after_a_lost_claim_race_gets_a_clean_409(client):
    # End-to-end version of the same guarantee through the real HTTP route:
    # claim the email out from under signup before it runs, and confirm
    # signup reports the same 409 a normal duplicate-email attempt gets,
    # rather than creating a second, ambiguous account.
    repo_ref = client.app.state.repo
    repo_ref.claim_email("raced@acme.com", "u_already_here")
    r = client.post(
        "/api/auth/signup",
        json={"email": "raced@acme.com", "password": "correct horse battery", "name": "Loser"},
    )
    assert r.status_code == 409
    assert repo_ref.user_by_email("raced@acme.com") is None  # claim won, but no User row was ever made


def test_a_signed_in_user_cannot_revoke_another_users_session_by_guessing_its_id(client, repo, sessions):
    # DELETE /api/auth/sessions/{sid} must be scoped to the caller's own
    # sessions -- otherwise any signed-in account could revoke a session
    # belonging to a completely different account just by knowing (or
    # guessing) its id, a horizontal privilege escalation.
    client.post(
        "/api/auth/signup",
        json={"email": "victim@acme.com", "password": "correct horse battery", "name": "Victim"},
    )
    victim = repo.user_by_email("victim@acme.com")
    victim_session = sessions.issue(victim.id, "ws1")

    client.post(
        "/api/auth/signup",
        json={"email": "attacker@acme.com", "password": "correct horse battery", "name": "Attacker"},
    )
    client.post(
        "/api/auth/signin", json={"email": "attacker@acme.com", "password": "correct horse battery"}
    )

    r = client.delete(f"/api/auth/sessions/{victim_session.id}")
    assert r.status_code == 404
    assert sessions.resolve(victim_session.id) is not None


def test_a_user_can_revoke_their_own_other_session(client, repo, sessions):
    client.post(
        "/api/auth/signup",
        json={"email": "self@acme.com", "password": "correct horse battery", "name": "Self"},
    )
    client.post("/api/auth/signin", json={"email": "self@acme.com", "password": "correct horse battery"})
    user = repo.user_by_email("self@acme.com")
    other = sessions.issue(user.id, "ws1")

    r = client.delete(f"/api/auth/sessions/{other.id}")
    assert r.status_code == 200
    assert sessions.resolve(other.id) is None
