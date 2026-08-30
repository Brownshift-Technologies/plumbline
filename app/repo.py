"""Typed repository over `core.store.Store`.

Every read rebuilds a frozen dataclass from the dict Store hands back;
every write flattens a dataclass to a dict via `models.to_dict` before
handing it to Store. Callers never see a raw Firestore dict.
"""

from app.models import (
    Behaviour,
    Finding,
    Membership,
    Patch,
    Route,
    Run,
    Session,
    Step,
    User,
    Workspace,
    to_dict,
)
from app.settings import PlumblineConfig
from core.store import Store


def _rebuild(cls, data):
    return cls(**data) if data else None


class Repo:
    def __init__(self, config: PlumblineConfig, client=None):
        self._store = Store(config, client=client)

    @property
    def store(self) -> Store:
        """Public handle for collaborators that need raw collection access
        (the Ledger). Keeps them off Repo's private attribute."""
        return self._store

    # users -----------------------------------------------------------
    def put_user(self, u: User) -> None:
        # Lower-case the stored email at this one choke point, matching
        # `user_by_email`'s query below. Firestore's `==` is case-sensitive,
        # so a caller that stored "Roger@Acme.com" verbatim and later queried
        # "roger@acme.com" would silently find nobody -- a genuine sign-in
        # failure that looks like a wrong password, not a normalisation bug.
        # Doing this here, once, is what keeps every future caller of
        # put_user from having to remember to lower-case first (Task 2's
        # review carried this forward as a defect).
        data = to_dict(u)
        data["email"] = data["email"].lower()
        self._store.put("users", u.id, data)

    def claim_email(self, email: str, user_id: str) -> bool:
        """Atomically reserve `email` (case-insensitive) for `user_id`.
        Returns False if it is already claimed by someone else.

        `user_by_email` is a query and `put_user` is a separate write, with
        no atomicity between them -- two signups racing on the same email
        can both see `user_by_email` miss before either has called
        `put_user`, and without something serialising them, both proceed
        and leave two `User` documents that share one email. Worse than a
        plain lost-update: `user_by_email` returns `rows[0]` of whatever
        `query()`'s underlying stream yields, which is not even guaranteed
        stable across reads, so which of the two accounts a later sign-in
        reaches becomes nondeterministic. `app/auth_routes.py`'s `signup`
        calls this, inside the same request, after its own cheap
        `user_by_email` check but before `put_user` -- the transactional
        claim below is what actually closes the race window; the earlier
        check is just a fast path for the common, non-concurrent case.
        Backed by one document per email in its own collection (not a
        field on `users`) so the claim and the account can be reasoned
        about, and tested, independently. Same transactional pattern
        `gateway/ledger.py` uses for its head pointer.
        """
        from google.cloud import firestore

        ref = self._store.doc("user_emails", email.lower())

        @firestore.transactional
        def _claim(transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            transaction.set(ref, {"user_id": user_id})
            return True

        return _claim(self._store.transaction())

    def user(self, uid: str) -> User | None:
        return _rebuild(User, self._store.get("users", uid))

    def user_by_email(self, email: str) -> User | None:
        rows = self._store.query("users", "email", "==", email.lower())
        return _rebuild(User, rows[0]) if rows else None

    # workspaces ------------------------------------------------------
    def put_workspace(self, w: Workspace) -> None:
        self._store.put("workspaces", w.id, to_dict(w))

    def workspace(self, wid: str) -> Workspace | None:
        return _rebuild(Workspace, self._store.get("workspaces", wid))

    def put_membership(self, m: Membership) -> None:
        self._store.put("memberships", m.id, to_dict(m))

    def memberships_for_user(self, uid: str) -> list[Membership]:
        return [Membership(**r) for r in self._store.query("memberships", "user_id", "==", uid)]

    def members_of(self, wid: str) -> list[Membership]:
        return [Membership(**r) for r in self._store.query("memberships", "workspace_id", "==", wid)]

    def role_of(self, uid: str, wid: str) -> str | None:
        for m in self.memberships_for_user(uid):
            if m.workspace_id == wid:
                return m.role
        return None

    # runs ------------------------------------------------------------
    def put_run(self, r: Run) -> None:
        self._store.put("runs", r.id, to_dict(r))

    def run(self, rid: str) -> Run | None:
        return _rebuild(Run, self._store.get("runs", rid))

    def runs_for_workspace(self, wid: str) -> list[Run]:
        rows = [Run(**r) for r in self._store.query("runs", "workspace_id", "==", wid)]
        return sorted(rows, key=lambda r: r.number, reverse=True)

    def append_step(self, s: Step) -> None:
        self._store.put("steps", s.id, to_dict(s))

    def steps_for_run(self, rid: str) -> list[Step]:
        rows = [Step(**r) for r in self._store.query("steps", "run_id", "==", rid)]
        return sorted(rows, key=lambda s: s.at)

    # findings and patches --------------------------------------------
    def put_finding(self, f: Finding) -> None:
        self._store.put("findings", f.id, to_dict(f))

    def findings_for_workspace(self, wid: str) -> list[Finding]:
        rows = [Finding(**r) for r in self._store.query("findings", "workspace_id", "==", wid)]
        return sorted(rows, key=lambda f: f.at, reverse=True)

    def put_patch(self, p: Patch) -> None:
        self._store.put("patches", p.id, to_dict(p))

    def patch_for_finding(self, fid: str) -> Patch | None:
        rows = self._store.query("patches", "finding_id", "==", fid)
        return _rebuild(Patch, rows[0]) if rows else None

    # surface -----------------------------------------------------------
    def put_route(self, r: Route) -> None:
        self._store.put("routes", r.id, to_dict(r))

    def routes_for_workspace(self, wid: str) -> list[Route]:
        rows = [Route(**r) for r in self._store.query("routes", "workspace_id", "==", wid)]
        return sorted(rows, key=lambda r: r.coverage_pct)

    def put_behaviour(self, b: Behaviour) -> None:
        self._store.put("behaviours", b.id, to_dict(b))

    def behaviours_for_workspace(self, wid: str) -> list[Behaviour]:
        return [Behaviour(**r) for r in self._store.query("behaviours", "workspace_id", "==", wid)]

    # sessions ------------------------------------------------------------
    def put_session(self, s: Session) -> None:
        self._store.put("sessions", s.id, to_dict(s))

    def session(self, sid: str) -> Session | None:
        return _rebuild(Session, self._store.get("sessions", sid))

    def sessions_for_user(self, uid: str) -> list[Session]:
        return [
            Session(**r)
            for r in self._store.query("sessions", "user_id", "==", uid)
            if r.get("user_id")
        ]

    def delete_session(self, sid: str) -> None:
        # Store has no delete; write a tombstone instead. user_id is blanked
        # so sessions_for_user's query (== uid) can never match it again, and
        # sessions_for_user also filters out empty user_id defensively in
        # case a caller queries a workspace/uid of "".
        self._store.put(
            "sessions",
            sid,
            {"id": sid, "user_id": "", "workspace_id": "", "expires_at": 0.0},
        )
