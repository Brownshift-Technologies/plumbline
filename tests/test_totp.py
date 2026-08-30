"""TOTP enrolment, verification, removal, and the RFC 6238 replay defense
that must survive horizontal scaling -- see app/account_routes.py and
app/repo.py's Repo.consume_totp_step for the implementation this exercises.
"""

import pyotp

from app.models import User
from app.security import new_totp_secret, totp_step_for

# --- from the brief: enrol / verify / remove --------------------------------


def test_enrol_returns_an_otpauth_uri(client_owner_no_totp):
    r = client_owner_no_totp.post("/api/auth/totp/enrol")
    assert r.status_code == 200
    body = r.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["secret"]


def test_verify_with_a_current_code_confirms_the_secret(client_owner_no_totp, repo):
    secret = client_owner_no_totp.post("/api/auth/totp/enrol").json()["secret"]
    code = pyotp.TOTP(secret).now()

    r = client_owner_no_totp.post("/api/auth/totp/verify", json={"code": code})
    assert r.status_code == 200

    user = repo.user_by_email("owner@acme.com")
    assert user.totp_secret == secret
    assert user.totp_pending_secret is None


def test_removing_totp_requires_a_current_code(client_as_owner, repo):
    uid = client_as_owner.get("/api/auth/me").json()["id"]
    user = repo.user(uid)

    # Wrong code: refused, and the secret survives.
    r = client_as_owner.request("DELETE", "/api/auth/totp", json={"code": "000000"})
    assert r.status_code == 400
    assert repo.user(uid).totp_secret == user.totp_secret

    code = pyotp.TOTP(user.totp_secret).now()
    r = client_as_owner.request("DELETE", "/api/auth/totp", json={"code": code})
    assert r.status_code == 200
    assert repo.user(uid).totp_secret is None


def test_deleting_totp_from_a_stolen_session_without_a_current_code_is_refused(client_as_owner, repo):
    # Attack: a stolen session cookie alone, with no knowledge of the
    # victim's authenticator app, must not be able to strip their 2FA --
    # that would turn "steal a cookie" into "permanently downgrade the
    # account's security", which is worse than the session theft alone.
    uid = client_as_owner.get("/api/auth/me").json()["id"]
    r = client_as_owner.request("DELETE", "/api/auth/totp", json={"code": ""})
    assert r.status_code in (400, 422)
    assert repo.user(uid).totp_secret is not None


# --- the approval-gate guarantee --------------------------------------------


def test_totp_is_required_before_an_approver_can_approve(client_owner_no_totp, repo):
    # Starting (but not completing) enrolment must not, by itself, satisfy
    # whatever reads User.totp_secret to gate an approval (Task 14b). Only
    # the CONFIRMED field means anything to that gate.
    client_owner_no_totp.post("/api/auth/totp/enrol")
    uid = client_owner_no_totp.get("/api/auth/me").json()["id"]
    user = repo.user(uid)
    assert user.totp_secret is None
    assert user.totp_pending_secret is not None  # enrolment did start


def test_an_unconfirmed_secret_does_not_satisfy_an_approval_gate(client_as_owner, repo):
    # Attack: enrolling a second time (e.g. from a stolen session, or a
    # user switching phones mid-flow) while a CONFIRMED secret already
    # exists must not disturb that confirmed secret at all -- it must stay
    # gate-satisfying until a fresh code actually verifies the new one.
    uid = client_as_owner.get("/api/auth/me").json()["id"]
    original_secret = repo.user(uid).totp_secret
    assert original_secret is not None

    client_as_owner.post("/api/auth/totp/enrol")

    user = repo.user(uid)
    assert user.totp_secret == original_secret  # untouched, still gate-satisfying
    assert user.totp_pending_secret is not None
    assert user.totp_pending_secret != original_secret


def test_verify_rejects_a_code_for_the_wrong_secret(client_owner_no_totp, repo):
    client_owner_no_totp.post("/api/auth/totp/enrol")
    wrong_code = pyotp.TOTP(new_totp_secret()).now()
    r = client_owner_no_totp.post("/api/auth/totp/verify", json={"code": wrong_code})
    assert r.status_code == 400
    uid = client_owner_no_totp.get("/api/auth/me").json()["id"]
    assert repo.user(uid).totp_secret is None


def test_verify_with_no_pending_enrolment_is_rejected(client_owner_no_totp):
    r = client_owner_no_totp.post("/api/auth/totp/verify", json={"code": "123456"})
    assert r.status_code == 400


# --- migrated from tests/test_security.py (fix round 1): same guarantees,
# now tested against the mechanism that actually enforces them. Task 6/7
# built these against `verify_totp`'s in-process replay dict; that
# mechanism was removed, not renamed -- see app/security.py's module
# docstring. `Repo.consume_totp_step` is what closes the replay window
# now, exercised here via `totp_step_for`'s code-to-step lookup, using the
# `repo`/`_seeded_user` this file's other RFC 6238 tests already use.


def test_a_totp_code_does_not_verify_twice(repo):
    # Replay: a code captured once (over a shoulder, in a log, on a
    # compromised link) must not be usable a second time inside its
    # validity window -- the standard TOTP replay attack.
    secret = new_totp_secret()
    user = _seeded_user(repo, secret)
    code = pyotp.TOTP(secret).now()

    step = totp_step_for(secret, code)
    assert repo.consume_totp_step(user.id, step) is True
    assert repo.consume_totp_step(user.id, step) is False


def test_a_totp_replay_is_rejected_even_from_the_adjacent_window(repo):
    # The same captured code must not become valid again just because time
    # moved into the following window and the code is still inside the
    # +/-1 step tolerance -- it was already consumed.
    secret = new_totp_secret()
    user = _seeded_user(repo, secret)
    code = pyotp.TOTP(secret).now()

    step = totp_step_for(secret, code)
    assert repo.consume_totp_step(user.id, step) is True
    # Still within the +/-1 step tolerance (totp_step_for resolves the same
    # step again), but the counter is already spent -> rejected.
    assert totp_step_for(secret, code) == step
    assert repo.consume_totp_step(user.id, step) is False


def test_totp_replay_tracking_is_per_secret(repo):
    # Consuming a code for one user's secret must not affect another
    # user's independently-generated code, even if by coincidence they
    # collide -- Repo.consume_totp_step is keyed by user_id, not shared
    # global state.
    s1, s2 = new_totp_secret(), new_totp_secret()
    u1 = User(id="u_totp_1", email="one@acme.com", password_hash="x", name="One", totp_secret=s1)
    u2 = User(id="u_totp_2", email="two@acme.com", password_hash="x", name="Two", totp_secret=s2)
    repo.put_user(u1)
    repo.put_user(u2)

    code1 = pyotp.TOTP(s1).now()
    step1 = totp_step_for(s1, code1)
    assert repo.consume_totp_step(u1.id, step1) is True
    assert repo.consume_totp_step(u1.id, step1) is False

    code2 = pyotp.TOTP(s2).now()
    step2 = totp_step_for(s2, code2)
    assert repo.consume_totp_step(u2.id, step2) is True


# --- RFC 6238 replay, straight from the brief --------------------------------


def _seeded_user(repo, secret):
    user = User(id="u_totp", email="totp@acme.com", password_hash="x", name="Totp", totp_secret=secret)
    repo.put_user(user)
    return user


def test_a_code_cannot_be_replayed_on_a_second_instance(repo):
    """Two verifiers over the SAME repo stand in for two Cloud Run instances.
    The second must reject a code the first consumed."""
    secret = new_totp_secret()
    user = _seeded_user(repo, secret)
    code = pyotp.TOTP(secret).now()
    step = totp_step_for(secret, code)

    # "Instance A" accepts it.
    assert repo.consume_totp_step(user.id, step) is True
    # "Instance B" -- a fresh call against the same repo, with no in-process
    # memory of instance A's dict, because there is no dict any more --
    # must reject the identical replayed step.
    assert repo.consume_totp_step(user.id, step) is False


def test_an_older_step_is_rejected_after_a_newer_one_is_used(repo):
    """Monotonicity is the whole mitigation: once step N is redeemed, N-1 is
    dead even though its own window may still be open."""
    user = _seeded_user(repo, new_totp_secret())
    assert repo.consume_totp_step(user.id, 100) is True
    # Step 99 was never itself consumed, but it is older than 100, and a
    # legitimate user never needs to redeem an older step than one already
    # used -- so it is refused purely on being stale.
    assert repo.consume_totp_step(user.id, 99) is False
    # A genuinely newer step still works.
    assert repo.consume_totp_step(user.id, 101) is True


def test_two_concurrent_verifications_of_one_code_admit_exactly_one(repo):
    # Mirrors tests/test_repo.py's
    # test_two_real_concurrent_claims_for_the_same_email_only_one_wins:
    # force a real interleaving (verifier B runs to completion in the
    # middle of verifier A's transaction, right after A has read the "not
    # yet used" snapshot but before A commits) rather than two sequential
    # calls, which would never exercise the abort-and-retry path at all.
    from core import fakes

    user = _seeded_user(repo, new_totp_secret())
    step = 50

    original_get = fakes.FakeDoc.get
    interleaved = []
    b_result = []

    def get_then_let_the_other_verifier_in(self, transaction=None):
        snapshot = original_get(self, transaction=transaction)
        if self._path == f"plumbline_users/{user.id}" and not interleaved:
            interleaved.append(True)
            b_result.append(repo.consume_totp_step(user.id, step))
        return snapshot

    fakes.FakeDoc.get = get_then_let_the_other_verifier_in
    try:
        a_result = repo.consume_totp_step(user.id, step)
    finally:
        fakes.FakeDoc.get = original_get

    assert interleaved == [True], "the interleaving never happened"
    results = {a_result, b_result[0]}
    assert results == {True, False}, f"exactly one of two concurrent verifications must win, got {(a_result, b_result[0])}"


def test_the_step_is_persisted_so_a_restart_does_not_forget_it(repo):
    # "Restart" here is stood in for by building a brand-new Repo over the
    # same underlying FakeFirestore client -- nothing about the mitigation
    # can live in a Repo instance's own memory, or this would fail exactly
    # the way the module-level dict it replaces does.
    from app.repo import Repo
    from app.settings import PlumblineConfig

    fake = repo.store._client_override
    user = _seeded_user(repo, new_totp_secret())
    assert repo.consume_totp_step(user.id, 7) is True

    fresh_repo = Repo(
        PlumblineConfig(project_id="test", location="x", vertex_location="x", model="x", firestore_prefix="plumbline"),
        client=fake,
    )
    assert fresh_repo.user(user.id).last_used_totp_step == 7
    assert fresh_repo.consume_totp_step(user.id, 7) is False
    assert fresh_repo.consume_totp_step(user.id, 8) is True
