"""Typed repository over `core.store.Store`.

Every read rebuilds a frozen dataclass from the dict Store hands back;
every write flattens a dataclass to a dict via `models.to_dict` before
handing it to Store. Callers never see a raw Firestore dict.
"""

import dataclasses
import time
import typing

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


def _coerce_tuples(cls, data: dict) -> dict:
    """Widen a raw Firestore document to what `cls`'s own field annotations
    promise, for exactly the one place JSON's type system and this
    codebase's disagree: an array.

    Several models here (`Behaviour.tags`, `Patch.files`, and others)
    annotate an array field `tuple[...]` rather than `list[...]`, on
    purpose -- several of these dataclasses are frozen and hashed
    elsewhere (see `Route`'s own module comment), and a `list` field
    would break that the moment anything hashes the instance. Firestore
    itself has no tuple type: `google-cloud-firestore`'s real client
    decodes every JSON array it reads back as a plain `list`, regardless
    of what was written, so `cls(**data)` on a real document would
    silently build a dataclass whose `tags`/`files` field holds a `list`
    where its own type annotation promises a `tuple`.

    The offline suite never observes this: `core.fakes.FakeFirestore`
    round-trips through `copy.deepcopy`, which preserves whatever type a
    test happened to construct the row with -- a tuple stored comes back
    a tuple, masking exactly the mismatch a real Firestore client
    produces. This function is what makes both agree, applied inside
    `_rebuild` below (and, for the same reason, everywhere a query result
    list is turned back into a dataclass) rather than left for every call
    site to remember.

    `dataclasses.fields(cls)` plus `typing.get_origin` reads each field's
    own annotation rather than hardcoding which two fields need this --
    every current and future `tuple[...]`-annotated field on any model
    gets the same coercion for free. Only the outer array is widened; a
    doubly-nested field (`Route.elements: tuple[tuple[str, str, str],
    ...]`) still leaves its inner arrays as `list`s -- no model in this
    codebase round-trips one through this path today, and going further
    would need to walk the annotation's own type args recursively rather
    than check them once.
    """
    coerced = dict(data)
    for f in dataclasses.fields(cls):
        if typing.get_origin(f.type) is tuple and isinstance(coerced.get(f.name), list):
            coerced[f.name] = tuple(coerced[f.name])
    return coerced


def _elements_to_triples(raw) -> tuple[tuple[str, str, str], ...]:
    """Read `Route.elements` back as triples, whichever shape it was stored in.

    `Repo.put_route` writes a list of maps because Firestore forbids nested
    arrays. Rows written before that, and anything a fake store round-trips
    verbatim, hold lists of three strings instead, so both are accepted --
    the alternative is a migration for a field whose only writer is a crawl
    that rebuilds it from scratch on the next run anyway.
    """
    if not raw:
        return ()
    out = []
    for e in raw:
        if isinstance(e, dict):
            out.append((e.get("ref", ""), e.get("role", ""), e.get("name", "")))
        else:
            ref, role, name = (list(e) + ["", "", ""])[:3]
            out.append((ref, role, name))
    return tuple(out)


def _rebuild(cls, data):
    return cls(**_coerce_tuples(cls, data)) if data else None


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

    def demo_workspaces(self) -> list[Workspace]:
        """Every workspace flagged `is_demo=True` -- live, real, or a
        per-session sandbox the expired-demo-workspace sweep
        (`app/main.py`) is deciding whether to delete. A single-field
        `Store.query` -- see that module's docstring for why an
        `is_demo`+`created_at` composite filter is done client-side in the
        sweep instead of here (no composite index to provision)."""
        return [Workspace(**_coerce_tuples(Workspace, r)) for r in self._store.query("workspaces", "is_demo", "==", True)]

    def delete_workspace_cascade(self, workspace_id: str) -> None:
        """Genuinely delete `workspace_id` and every document keyed to it
        across this codebase's per-workspace collections -- the one place
        in this `Repo` that calls `Store.delete`/`delete_many` rather than
        `put` a tombstone. See `core.store.Store.delete`'s own docstring
        for why a demo sandbox (this method's only caller,
        `app/main.py`'s expired-demo-workspace sweep) is the one row shape
        in this codebase that earns a real delete rather than a tombstone.

        Findings/patches/steps are resolved by walking the workspace's own
        runs/findings first (patches and steps carry no `workspace_id` of
        their own -- `finding_id`/`run_id` respectively -- exactly like
        every read path for them already does, e.g. `patch_for_finding`,
        `steps_for_run`), not queried by a field that does not exist.
        """
        findings = self.findings_for_workspace(workspace_id)
        runs = self.runs_for_workspace(workspace_id)

        patch_ids = [p.id for f in findings if (p := self.patch_for_finding(f.id))]
        step_ids = [s.id for r in runs for s in self.steps_for_run(r.id)]
        ledger_ids = [e["id"] for e in self._store.query("ledger", "workspace_id", "==", workspace_id)]

        self._store.delete_many("routes", [r.id for r in self.routes_for_workspace(workspace_id)])
        self._store.delete_many(
            "behaviours", [b.id for b in self.behaviours_for_workspace(workspace_id)],
        )
        self._store.delete_many("findings", [f.id for f in findings])
        self._store.delete_many("patches", patch_ids)
        self._store.delete_many("runs", [r.id for r in runs])
        self._store.delete_many("steps", step_ids)
        self._store.delete_many("ledger", ledger_ids)
        self._store.delete("ledger_head", workspace_id)
        self._store.delete_many("memberships", [m.id for m in self.members_of(workspace_id)])
        self._store.delete_many(
            "api_keys", [k["id"] for k in self._store.query("api_keys", "workspace_id", "==", workspace_id)],
        )
        self._store.delete_many(
            "webhooks", [w["id"] for w in self._store.query("webhooks", "workspace_id", "==", workspace_id)],
        )
        self._store.delete("run_counters", workspace_id)
        self._store.delete("workspaces", workspace_id)

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

        Fix round 1: also re-stamps `started_at` to the moment OF THIS
        CLAIM, not the moment the `Run` document was first constructed.
        `Run.started_at` defaults via `field(default_factory=time.time)` at
        OBJECT CONSTRUCTION -- for a real run, that is when `POST /api/runs`
        builds and enqueues it, not when a worker actually picks it up and
        starts executing agents. Before this fix, `job/orchestrator.py`'s
        `_finish` computed `duration_ms` from that original, pre-claim
        timestamp, so a run that sat queued for any real stretch (Cloud Run
        Job cold start, a busy queue) recorded queue-wait time as part of
        its own execution duration. That is exactly the number
        `agents/chaos.py`'s observed-p99 branch reads back later to derive
        injected fault latency from -- a wrong `duration_ms` here does not
        stay a cosmetic reporting bug, it silently corrupts a LATER run's
        fault-injection parameters. Restamping here, inside the same
        transaction that flips `state` to `"running"`, is what makes
        "queued" and "running" share one honest boundary: `_finish` (Task
        13) already re-reads the row it is finishing rather than trusting a
        possibly-stale in-memory copy, so it picks up this new value for
        free with no change needed on that side.
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
            run_data["started_at"] = time.time()
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

    # `_SEVERITY_RANK` -- fewest-surprises tiebreak for `finding_for_run`
    # below. Mirrors `agents/oracle.py`'s own `critical > high > medium >
    # low` ordering (see `_severity`/`public_routes.py`'s `Field(...,
    # description="low | medium | high | critical")`) rather than
    # inventing a second ranking for the same four strings.
    _SEVERITY_RANK: dict[str, int] = {"critical": 3, "high": 2, "medium": 1, "low": 0}

    def finding_for_run(self, run_id: str) -> Finding | None:
        """The `Finding` the run `run_id` produced, or `None` if it produced
        none -- what `GET /api/runs/{id}` reads to fill in `finding_id`
        (see `app/run_routes.py`).

        Queried by `run_id`, a dedicated indexed field, not a
        `findings_for_workspace` scan filtered client-side: a workspace
        can hold thousands of findings across thousands of runs, and this
        route is read on every single run-detail page view, not once per
        deploy -- the same "query the field you actually need, don't scan
        and filter" discipline `steps_for_run` already applies to
        `Step.run_id`. See `test_finding_for_run_does_not_scan_the_whole_
        workspace`.

        A run only ever produces one `Finding` today (`agents/triager.py`
        writes at most one Finding per candidate spec per run, and
        `enqueue_run` gives every run its own id) but nothing in the data
        model actually forbids a workspace from somehow ending up with
        more than one row sharing a `run_id` -- a replayed write under a
        different key, a future agent that triages more than one
        candidate onto the same reported id, etc. Rather than let that
        be undefined ("whichever one Firestore happens to return first"),
        ties break on severity, worst first (`_SEVERITY_RANK`): the
        finding this route surfaces is the one a caller most needs to see
        before anything else, and "worse" is the one property a
        `Finding` actually carries that speaks to that -- `at` (recency)
        says nothing about which is more worth a human's attention.
        """
        rows = self._store.query("findings", "run_id", "==", run_id)
        if not rows:
            return None
        findings = [_rebuild(Finding, r) for r in rows]
        return max(findings, key=lambda f: self._SEVERITY_RANK.get(f.severity, -1))

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
        # `Route.elements` is a tuple of `(ref, role, name)` triples, and
        # Firestore rejects a nested array outright: an array may not
        # contain another array. Written as-is it fails the whole document
        # with a bare `InvalidArgument`, which is exactly what happened --
        # Cartographer's `graph.write` errored on every real run, while the
        # demo path stayed green because `seed/demo.py` never writes
        # `elements` at all, so no fixture ever exercised this field.
        #
        # Stored as a list of maps, which Firestore does allow, and turned
        # back into triples on read. The in-memory shape is unchanged, so
        # `Route` stays hashable and Author's
        # `for _, role, name in route.elements` keeps working.
        data = to_dict(r)
        data["elements"] = [
            {"ref": ref, "role": role, "name": name} for ref, role, name in r.elements
        ]
        self._store.put("routes", r.id, data)

    def routes_for_workspace(self, wid: str) -> list[Route]:
        rows = [
            Route(**{**raw, "elements": _elements_to_triples(raw.get("elements"))})
            for raw in self._store.query("routes", "workspace_id", "==", wid)
        ]
        return sorted(rows, key=lambda r: r.coverage_pct)

    def put_behaviour(self, b: Behaviour) -> None:
        self._store.put("behaviours", b.id, to_dict(b))

    def behaviours_for_workspace(self, wid: str) -> list[Behaviour]:
        return [
            _rebuild(Behaviour, r)
            for r in self._store.query("behaviours", "workspace_id", "==", wid)
        ]

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
