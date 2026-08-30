"""Server-side sessions.

Sessions are opaque tokens (see `app.security.new_token`) stored in
Firestore via `Repo`, not signed/stateless tokens -- `resolve` and `revoke`
both need to reach a real row so a session can be invalidated server-side
before its `expires_at` (password change, "log out everywhere", an admin
action). That is the whole reason this module exists instead of an
itsdangerous-signed cookie.

`Repo.delete_session` cannot delete (Store never exposes delete -- see
core/store.py); it tombstones instead, overwriting the row with
`user_id=""`, `workspace_id=""`, `expires_at=0.0`. `Repo.session(sid)`
after that still returns a `Session` dataclass, not `None` -- a frozen
dataclass without `__slots__` still has no `__bool__`, so it is truthy
regardless of its field values. `resolve` below must never trust that
truthiness; it must check `user_id` and `expires_at` explicitly. This is
exactly the bug a prior review flagged, and every code path here is written
to keep it from coming back.
"""

import time

from app.models import Session
from app.security import new_token

DEMO_TTL_SECONDS = 2 * 3600


class SessionService:
    def __init__(self, repo, config):
        self._repo = repo
        self._config = config

    def issue(
        self,
        user_id: str,
        workspace_id: str,
        *,
        is_demo: bool = False,
        user_agent: str = "",
        ip_city: str = "",
    ) -> Session:
        # Demo sessions always get the fixed 2h cap, never
        # config.session_ttl_days -- a demo session must not be able to
        # outlive its cap just because an operator raises the ordinary TTL
        # for real tenants. The two branches are independent on purpose;
        # this is not "min(demo_ttl, configured_ttl)", it is "demo ignores
        # the config entirely".
        ttl = DEMO_TTL_SECONDS if is_demo else self._config.session_ttl_days * 86400
        sess = Session(
            id=new_token(),
            user_id=user_id,
            workspace_id=workspace_id,
            expires_at=time.time() + ttl,
            user_agent=user_agent,
            ip_city=ip_city,
            is_demo=is_demo,
        )
        self._repo.put_session(sess)
        return sess

    def resolve(self, sid: str) -> Session | None:
        s = self._repo.session(sid)
        # `not s` guards the genuine None case (no row for sid at all).
        # `not s.user_id` and the expiry check are what actually matter: a
        # tombstoned session round-trips as a truthy Session with
        # user_id="" and expires_at=0.0, and must be rejected on those
        # field values, never on `bool(s)` (see module docstring).
        if not s or not s.user_id or s.expires_at <= time.time():
            return None
        return s

    def revoke(self, sid: str) -> None:
        self._repo.delete_session(sid)

    def revoke_all_except(self, user_id: str, keep_sid: str) -> None:
        # Fails closed by construction: every session belonging to user_id
        # whose id does not exactly equal keep_sid is revoked, with no
        # existence check on keep_sid first. If a caller passes a stale,
        # forged, or already-expired keep_sid, the result is that *every*
        # session for that user gets revoked (including, in the worst case,
        # the caller's own current one) rather than silently keeping
        # something alive that should not be. Over-revocation costs a
        # re-login; under-revocation leaves a live session for an attacker.
        for s in self._repo.sessions_for_user(user_id):
            if s.id != keep_sid:
                self.revoke(s.id)

    def list_for_user(self, user_id: str) -> list[Session]:
        return [s for s in self._repo.sessions_for_user(user_id) if s.expires_at > time.time()]
