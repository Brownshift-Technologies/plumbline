import time

from app.repo import Repo
from app.security import new_token
from app.sessions import DEMO_TTL_SECONDS, SessionService
from app.settings import PlumblineConfig
from core.fakes import FakeFirestore


def _config(**overrides):
    return PlumblineConfig(
        project_id="t",
        location="us-central1",
        vertex_location="global",
        model="gemini-3.5-flash",
        firestore_prefix="plumbline",
        **overrides,
    )


def _svc(**overrides):
    cfg = _config(**overrides)
    return SessionService(Repo(cfg, client=FakeFirestore()), cfg)


# --- from the brief ---------------------------------------------------------


def test_an_issued_session_resolves():
    s = _svc()
    sess = s.issue("u1", "ws1")
    assert s.resolve(sess.id).user_id == "u1"


def test_an_expired_session_does_not_resolve():
    s = _svc()
    sess = s.issue("u1", "ws1")
    s._repo.put_session(type(sess)(**{**sess.__dict__, "expires_at": time.time() - 1}))
    assert s.resolve(sess.id) is None


def test_a_revoked_session_does_not_resolve():
    s = _svc()
    sess = s.issue("u1", "ws1")
    s.revoke(sess.id)
    assert s.resolve(sess.id) is None


def test_revoke_all_except_keeps_only_the_current_one():
    s = _svc()
    keep = s.issue("u1", "ws1")
    other = s.issue("u1", "ws1")
    s.revoke_all_except("u1", keep.id)
    assert s.resolve(keep.id) is not None
    assert s.resolve(other.id) is None


def test_a_demo_session_is_flagged_and_long_lived():
    # A demo sandbox is meant to be come-back-to-able, like an account:
    # the cookie is the only handle on it, so the session lifetime IS how
    # long the sandbox survives. It used to be two hours, which meant a
    # visitor who returned the next day found their behaviours, runs and
    # approvals gone -- indistinguishable from data loss.
    s = _svc()
    sess = s.issue("demo", "ws_demo", is_demo=True)
    assert sess.is_demo is True
    assert sess.expires_at - time.time() > 300 * 86400


# --- attacker-shaped tests beyond the brief ---------------------------------


def test_a_revoked_session_does_not_resolve_via_truthiness_of_the_tombstone():
    # Repo.session() on a tombstoned id returns a *truthy* Session
    # dataclass (frozen dataclasses have no __bool__), with user_id=""
    # and expires_at=0.0. resolve() must reject it on those field values,
    # not on `bool(session)` -- the exact bug a prior review flagged.
    s = _svc()
    sess = s.issue("u1", "ws1")
    s.revoke(sess.id)
    tombstone = s._repo.session(sess.id)
    assert tombstone is not None  # still a truthy object post-revoke
    assert tombstone.user_id == ""
    assert tombstone.expires_at == 0.0
    assert s.resolve(sess.id) is None


def test_a_demo_session_ignores_the_configured_ttl_in_both_directions():
    # Demo TTL ignores session_ttl_days entirely -- the two branches are
    # independent, not min()/max(). Asserted with the config set BELOW the
    # demo lifetime, which is the direction that would silently shorten a
    # sandbox if the branches were ever merged.
    s = _svc(session_ttl_days=1)
    sess = s.issue("demo", "ws_demo", is_demo=True)
    assert abs(sess.expires_at - (time.time() + DEMO_TTL_SECONDS)) < 5


def test_an_ordinary_session_tracks_the_configured_ttl_not_the_demo_one():
    # The other side of the same behaviour: a non-demo session tracks
    # config exactly. Compared against the configured value itself rather
    # than against DEMO_TTL_SECONDS -- the two were both 365 days for a
    # while, and a comparison between them proved nothing.
    s = _svc(session_ttl_days=30)
    sess = s.issue("u1", "ws1", is_demo=False)
    assert abs(sess.expires_at - (time.time() + 30 * 86400)) < 5


def test_revoke_all_except_a_nonexistent_keep_id_revokes_everything():
    # A stale, forged, or mistyped keep_sid must fail *closed*: with no
    # session matching it, every real session for the user gets revoked
    # rather than silently leaving one alive because "not equal to keep_sid"
    # was true for all of them. Over-revocation (forced re-login) is the
    # safe failure mode; under-revocation (a live session survives) is not.
    s = _svc()
    a = s.issue("u1", "ws1")
    b = s.issue("u1", "ws1")
    s.revoke_all_except("u1", "sid-that-was-never-issued")
    assert s.resolve(a.id) is None
    assert s.resolve(b.id) is None


def test_session_ids_come_from_secrets_and_are_long():
    # issue() must mint ids via new_token() (secrets.token_urlsafe), not
    # something guessable like a counter or uuid4 -- checked indirectly:
    # 20 issued ids collide with each other zero times, are all at least
    # as long as new_token's own output, and use only new_token's charset.
    import string

    charset = set(string.ascii_letters + string.digits + "-_")
    sample_len = len(new_token())
    svc = _svc()
    seen = {svc.issue("u1", "ws1").id for _ in range(20)}
    assert len(seen) == 20  # no collisions across 20 issues
    assert all(len(sid) >= sample_len for sid in seen)
    assert all(set(sid) <= charset for sid in seen)


def test_list_for_user_excludes_expired_and_revoked_sessions():
    s = _svc()
    live = s.issue("u1", "ws1")
    expiring = s.issue("u1", "ws1")
    revoked = s.issue("u1", "ws1")
    s._repo.put_session(
        type(expiring)(**{**expiring.__dict__, "expires_at": time.time() - 1})
    )
    s.revoke(revoked.id)
    ids = {sess.id for sess in s.list_for_user("u1")}
    assert ids == {live.id}


def test_resolve_of_an_id_that_was_never_issued_is_none():
    s = _svc()
    assert s.resolve("no-such-session-id-at-all") is None
