"""Typed repository over `core.store.Store`.

Every read rebuilds a frozen dataclass from the dict Store hands back;
every write flattens a dataclass to a dict via `models.to_dict` before
handing it to Store. Callers never see a raw Firestore dict.
"""

import time

from app.models import (
    Artefact,
    Behaviour,
    Finding,
    Incident,
    Membership,
    PasswordReset,
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

    def consume_totp_step(self, user_id: str, step: int) -> bool:
        """RFC 6238's own replay mitigation, made to survive horizontal
        scaling (Task 8b). Atomically accepts `step` as the new
        `last_used_totp_step` for `user_id` and returns True, UNLESS `step`
        is `<=` the value already recorded, in which case it changes
        nothing and returns False.

        A legitimate user never needs to redeem an older or equal step than
        one already used -- time only moves forward -- so this closes the
        replay window completely, not just "for however long some
        process's memory happens to remember it" the way Task 6/7's
        original in-process dict did (removed in fix round 1 -- see
        `app/security.py`'s module docstring). It has to be transactional,
        not a plain read-then-write, for the same reason `claim_email` above and
        `gateway/ledger.py`'s `append` are: two concurrent requests
        presenting the same captured code would both read the old step
        before either writes the new one, and both would be accepted. The
        `@firestore.transactional` retry loop is what makes exactly one of
        them win.

        Reads and writes the *whole* user document (not a partial update)
        inside the transaction -- `core/fakes.py`'s `FakeTransaction` only
        implements `set()`, matching the whole-document-replace pattern
        every other transactional write in this codebase already uses
        (`claim_email`, `gateway/ledger.py`'s `append`), so the real client
        and the test fake behave identically here.
        """
        from google.cloud import firestore

        ref = self._store.doc("users", user_id)

        @firestore.transactional
        def _consume(transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            data = snapshot.to_dict()
            if step <= data.get("last_used_totp_step", 0):
                return False
            data["last_used_totp_step"] = step
            transaction.set(ref, data)
            return True

        return _consume(self._store.transaction())

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

    def claim_run(self, run_id: str) -> Run | None:
        """Atomically transitions a `"queued"` `Run` to `"running"` AND
        increments its workspace's `runs_used`, in the SAME transaction --
        Task 13's own carried ruling: `runs_used` increments exactly once
        per execution, at the start, so a worker that crashes mid-run still
        consumed a run, and it must never happen twice for one run.

        Bundling both writes into one transaction is what makes "exactly
        once" survive two workers racing on the same `run_id` (Cloud Run
        Jobs retrying an execution, or any other double-dispatch): both
        transactions read the same `state=="queued"` snapshot, both attempt
        to commit, and Firestore's optimistic-concurrency check -- the exact
        mechanism `claim_email`/`consume_totp_step` above already rely on --
        aborts whichever commits second. Its retry re-reads the run, now
        sees `state=="running"`, and this method returns `None` for it: the
        workspace is billed once, not twice, and only one caller ever goes
        on to build an `AgentContext` and run the fleet.

        Returns the claimed `Run` (now `state=="running"`) on success, or
        `None` if: the run does not exist; it is not `"queued"` (already
        claimed, already finished, cancelled -- `job/orchestrator.py`'s
        caller is expected to have already checked this cheaply before ever
        reaching here, but this method re-checks inside the transaction
        regardless, since that cheap check is not itself race-safe); or its
        workspace no longer exists (deleted between the run being enqueued
        and a worker picking it up -- `job/orchestrator.py` is the caller
        that decides what a run with no workspace left to bill becomes).
        """
        from google.cloud import firestore

        run_ref = self._store.doc("runs", run_id)

        @firestore.transactional
        def _claim(transaction) -> Run | None:
            run_snapshot = run_ref.get(transaction=transaction)
            if not run_snapshot.exists:
                return None
            run_data = run_snapshot.to_dict()
            if run_data.get("state") != "queued":
                return None

            workspace_ref = self._store.doc("workspaces", run_data["workspace_id"])
            workspace_snapshot = workspace_ref.get(transaction=transaction)
            if not workspace_snapshot.exists:
                return None
            workspace_data = workspace_snapshot.to_dict()
            workspace_data["runs_used"] = workspace_data.get("runs_used", 0) + 1
            transaction.set(workspace_ref, workspace_data)

            run_data["state"] = "running"
            transaction.set(run_ref, run_data)
            return Run(**run_data)

        return _claim(self._store.transaction())

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

    # incidents ---------------------------------------------------------
    def put_incident(self, i: Incident) -> None:
        self._store.put("incidents", i.id, to_dict(i))

    def incidents_for_workspace(self, wid: str) -> list[Incident]:
        rows = [Incident(**r) for r in self._store.query("incidents", "workspace_id", "==", wid)]
        return sorted(rows, key=lambda i: i.first_seen, reverse=True)

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

    # spec files -------------------------------------------------------
    #
    # Added for Tasks 11a/11b. A spec is Playwright *source text*, not one
    # of the frozen dataclasses in app/models.py -- there is no fixed shape
    # to validate it against, and Author's whole job is producing arbitrary
    # generated source, so a plain path->content mapping is the right
    # amount of structure (none). Keyed by (workspace_id, path) rather than
    # path alone, the same tenancy discipline every other collection here
    # keeps (Route, Behaviour, Finding, ...) -- two workspaces both writing
    # "specs/checkout.spec.ts" must never collide.
    def put_spec(self, workspace_id: str, path: str, content: str) -> None:
        self._store.put(
            "specs", f"{workspace_id}:{path}",
            {"workspace_id": workspace_id, "path": path, "content": content},
        )

    def spec(self, workspace_id: str, path: str) -> str | None:
        row = self._store.get("specs", f"{workspace_id}:{path}")
        return row["content"] if row else None

    def specs_for_workspace(self, workspace_id: str) -> dict[str, str]:
        rows = self._store.query("specs", "workspace_id", "==", workspace_id)
        return {r["path"]: r["content"] for r in rows}

    # artefacts ----------------------------------------------------------
    #
    # Added for Task 12a (Runner). See `Artefact`'s own docstring in
    # app/models.py for why this gets a typed dataclass where `specs` above
    # deliberately does not, and for why `id` is a composite key rather
    # than a random uuid.
    def put_artefact(self, a: Artefact) -> None:
        self._store.put("artefacts", a.id, to_dict(a))

    def artefact_count(self, workspace_id: str | None = None) -> int:
        """How many artefacts exist -- scoped to `workspace_id` when given,
        or the whole store when not. The no-argument form exists because a
        Runner test builds one workspace per context (see
        `tests/agent_fixtures.py`'s `make_ctx`) and wants "how many did
        this run write" without also having to know or repeat the
        workspace id it just used to build that same context.
        """
        if workspace_id is None:
            return len(self._store.all("artefacts"))
        return len(self._store.query("artefacts", "workspace_id", "==", workspace_id))

    def artefacts_for_spec(self, workspace_id: str, spec_path: str) -> list[Artefact]:
        """Every artefact (video/trace/har/console) captured for one spec,
        newest first. Added for Task 12b (Triager): `Store.query` takes
        exactly one `(field, op, value)` filter (see `core/store.py`'s
        `query`), so this queries on `spec_path` -- the more selective of
        the two fields a caller has -- and filters `workspace_id` client
        side, the same trade `runs_for_workspace` and `steps_for_run`
        already make by sorting client side after a single-field query.
        Two workspaces are most unlikely to share a literal spec path
        (`specs/checkout.spec.ts`), but tenancy is enforced here regardless
        rather than assumed.

        Newest-first (`created_at` descending) so a caller that only wants
        the CURRENT trace/HAR for a spec -- Triager reading what the most
        recent run captured, not every run this spec has ever failed in --
        can just take the first entry of each kind without also having to
        sort or de-duplicate by `kind` itself.
        """
        rows = [Artefact(**r) for r in self._store.query("artefacts", "spec_path", "==", spec_path)]
        return sorted(
            (a for a in rows if a.workspace_id == workspace_id),
            key=lambda a: a.created_at, reverse=True,
        )

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

    # password resets ------------------------------------------------------
    def put_password_reset(self, r: PasswordReset) -> None:
        self._store.put("password_resets", r.id, to_dict(r))

    def consume_password_reset(self, token_hash: str) -> PasswordReset | None:
        """Atomically read-and-mark-used the reset row keyed by
        `token_hash`. Returns the row as it stood the moment it was
        consumed (so the caller still has `user_id`) if it existed, was not
        already used, and had not yet expired -- otherwise `None`, covering
        an unknown token, a second use of a spent one, and an expired one
        in a single check.

        Transactional for the same reason `consume_totp_step` above is: two
        concurrent submissions of one captured reset link (an attacker
        racing the legitimate user, or a client double-submitting a form)
        must not both succeed. A plain get-then-set would let both read
        `used=False` before either writes `used=True`.
        """
        from google.cloud import firestore

        ref = self._store.doc("password_resets", token_hash)

        @firestore.transactional
        def _consume(transaction) -> PasswordReset | None:
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            if data.get("used") or data.get("expires_at", 0) <= time.time():
                return None
            result = PasswordReset(**data)
            transaction.set(ref, {**data, "used": True})
            return result

        return _consume(self._store.transaction())
