from app.models import (
    Behaviour,
    Finding,
    Incident,
    Membership,
    Patch,
    Route,
    Run,
    Session,
    Step,
    User,
    Workspace,
)
from app.repo import Repo
from app.settings import PlumblineConfig
from core.fakes import FakeFirestore


def _config():
    return PlumblineConfig(
        project_id="t",
        location="us-central1",
        vertex_location="global",
        model="gemini-3.5-flash",
        firestore_prefix="plumbline",
    )


def _repo():
    return Repo(_config(), client=FakeFirestore())


# --- from the brief -------------------------------------------------------


def test_user_round_trips_by_email():
    r = _repo()
    r.put_user(User(id="u1", email="roger@acme.com", password_hash="x", name="Roger K."))
    assert r.user_by_email("roger@acme.com").id == "u1"


def test_unknown_email_is_none_not_an_error():
    assert _repo().user_by_email("nobody@acme.com") is None


def test_role_of_returns_none_without_a_membership():
    r = _repo()
    r.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/storefront"))
    assert r.role_of("u1", "ws1") is None


def test_role_of_reads_the_membership():
    r = _repo()
    r.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/storefront"))
    r.put_membership(Membership(id="m1", user_id="u1", workspace_id="ws1", role="owner"))
    assert r.role_of("u1", "ws1") == "owner"


def test_runs_come_back_newest_first():
    r = _repo()
    for n in (4468, 4471, 4470):
        r.put_run(Run(id=f"r{n}", workspace_id="ws1", number=n, trigger="manual", state="finished"))
    assert [x.number for x in r.runs_for_workspace("ws1")] == [4471, 4470, 4468]


# --- store property is public (Ledger in Task 3 relies on this) ----------


def test_store_property_is_public_and_backed_by_the_same_store():
    r = _repo()
    r.put_user(User(id="u1", email="a@b.com", password_hash="x", name="A"))
    assert r.store.get("users", "u1")["email"] == "a@b.com"


# --- sessions: tombstone semantics -----------------------------------------


def test_session_round_trips():
    r = _repo()
    r.put_session(Session(id="s1", user_id="u1", workspace_id="ws1", expires_at=123.0))
    got = r.session("s1")
    assert got.user_id == "u1"
    assert got.workspace_id == "ws1"


def test_delete_session_tombstones_rather_than_erroring():
    r = _repo()
    r.put_session(Session(id="s1", user_id="u1", workspace_id="ws1", expires_at=123.0))
    r.delete_session("s1")
    # The document still exists (Store has no delete) but is blanked out.
    got = r.session("s1")
    assert got is not None
    assert got.user_id == ""


def test_deleted_session_cannot_be_resurrected_by_sessions_for_user():
    r = _repo()
    r.put_session(Session(id="s1", user_id="u1", workspace_id="ws1", expires_at=123.0))
    r.put_session(Session(id="s2", user_id="u1", workspace_id="ws1", expires_at=456.0))
    r.delete_session("s1")
    remaining = r.sessions_for_user("u1")
    assert [s.id for s in remaining] == ["s2"]


# --- runs: steps ------------------------------------------------------------


def test_steps_for_run_come_back_oldest_first():
    r = _repo()
    r.put_run(Run(id="r1", workspace_id="ws1", number=1, trigger="manual"))
    r.append_step(Step(id="st2", run_id="r1", agent="mapper", summary="second", at=2.0))
    r.append_step(Step(id="st1", run_id="r1", agent="mapper", summary="first", at=1.0))
    assert [s.summary for s in r.steps_for_run("r1")] == ["first", "second"]


# --- findings and patches ---------------------------------------------------


def test_findings_for_workspace_newest_first():
    r = _repo()
    r.put_finding(
        Finding(id="f1", workspace_id="ws1", title="old", route="/a", found_by="explorer", at=1.0)
    )
    r.put_finding(
        Finding(id="f2", workspace_id="ws1", title="new", route="/b", found_by="explorer", at=2.0)
    )
    assert [f.title for f in r.findings_for_workspace("ws1")] == ["new", "old"]


def test_patch_for_finding_round_trips():
    r = _repo()
    r.put_patch(Patch(id="p1", finding_id="f1", diff="--- a\n+++ b"))
    assert r.patch_for_finding("f1").id == "p1"


# --- finding_for_run: the approval-gate link --------------------------------


def test_finding_for_run_returns_the_finding_that_run_produced():
    r = _repo()
    r.put_finding(
        Finding(id="f1", workspace_id="ws1", title="a bug", route="/a",
                found_by="triager", run_id="run_1")
    )
    assert r.finding_for_run("run_1").id == "f1"


def test_finding_for_run_with_no_finding_returns_none():
    r = _repo()
    assert r.finding_for_run("run_nobody_wrote") is None


def test_finding_for_run_breaks_ties_on_severity_worst_first():
    r = _repo()
    r.put_finding(Finding(id="f_low", workspace_id="ws1", title="minor", route="/a",
                           found_by="triager", run_id="run_1", severity="low"))
    r.put_finding(Finding(id="f_critical", workspace_id="ws1", title="severe", route="/a",
                           found_by="triager", run_id="run_1", severity="critical"))
    r.put_finding(Finding(id="f_medium", workspace_id="ws1", title="middling", route="/a",
                           found_by="triager", run_id="run_1", severity="medium"))
    assert r.finding_for_run("run_1").id == "f_critical"


def test_finding_for_run_does_not_scan_the_whole_workspace(monkeypatch):
    """`finding_for_run` must query the dedicated `run_id` field, not read
    every finding in the workspace and filter client-side -- the same
    discipline `steps_for_run` already applies to `Step.run_id`. Breaking
    `findings_for_workspace` here proves the read path this test targets
    never goes through it."""
    r = _repo()
    r.put_finding(
        Finding(id="f1", workspace_id="ws1", title="a bug", route="/a",
                found_by="triager", run_id="run_1")
    )

    def _boom(self, wid):
        raise AssertionError("finding_for_run must not scan the whole workspace")

    monkeypatch.setattr(Repo, "findings_for_workspace", _boom)
    assert r.finding_for_run("run_1").id == "f1"


def test_patch_for_finding_is_none_when_no_patch_exists():
    assert _repo().patch_for_finding("nope") is None


def test_incidents_for_workspace_newest_first():
    r = _repo()
    r.put_incident(
        Incident(id="i1", workspace_id="ws1", source="sentinel", message="old", first_seen=1.0)
    )
    r.put_incident(
        Incident(id="i2", workspace_id="ws1", source="sentinel", message="new", first_seen=2.0)
    )
    assert [i.message for i in r.incidents_for_workspace("ws1")] == ["new", "old"]


def test_incidents_for_workspace_is_empty_for_an_unknown_workspace():
    assert _repo().incidents_for_workspace("nope") == []


# --- surface: routes and behaviours -----------------------------------------


def test_routes_for_workspace_sorted_by_coverage_ascending():
    r = _repo()
    r.put_route(Route(id="r1", workspace_id="ws1", path="/checkout", coverage_pct=90))
    r.put_route(Route(id="r2", workspace_id="ws1", path="/cart", coverage_pct=30))
    assert [x.path for x in r.routes_for_workspace("ws1")] == ["/cart", "/checkout"]


def test_behaviours_for_workspace():
    r = _repo()
    r.put_behaviour(Behaviour(id="b1", workspace_id="ws1", text="user can checkout", route="/checkout"))
    assert [b.id for b in r.behaviours_for_workspace("ws1")] == ["b1"]


# --- Task 2 review carry-forward: tuple fields against a real Firestore ----
#
# `core.fakes.FakeFirestore` round-trips through `copy.deepcopy`, which
# preserves whatever type a test constructed a row with -- so the offline
# suite alone never proves this. These tests instead call `_rebuild`
# directly with a hand-built dict whose array fields are plain `list`s, the
# shape a real `google-cloud-firestore` client actually hands back, and
# assert the rebuilt dataclass still matches its own `tuple[...]`
# annotation.


def test_rebuild_coerces_a_list_tags_field_back_to_the_declared_tuple():
    from app.repo import _rebuild

    data = {
        "id": "b1",
        "workspace_id": "ws1",
        "text": "user can checkout",
        "route": "/checkout",
        "tags": ["sentinel", "flaky"],  # list, as a real Firestore read hands back
    }
    behaviour = _rebuild(Behaviour, data)
    assert type(behaviour.tags) is tuple
    assert behaviour.tags == ("sentinel", "flaky")


def test_rebuild_coerces_a_list_files_field_back_to_the_declared_tuple():
    from app.repo import _rebuild

    data = {
        "id": "p1",
        "finding_id": "f1",
        "diff": "--- a\n+++ b",
        "files": ["a.py", "b.py"],  # list, as a real Firestore read hands back
    }
    patch = _rebuild(Patch, data)
    assert type(patch.files) is tuple
    assert patch.files == ("a.py", "b.py")


def test_behaviours_for_workspace_survives_a_real_firestore_style_list_field():
    # End-to-end version of the two `_rebuild` tests above: a document
    # already sitting in the fake store with `tags` as a `list` (as a real
    # Firestore document would decode) must still come back typed `tuple`
    # via the normal read path, not just via a direct `_rebuild` call.
    r = _repo()
    r.store.put(
        "behaviours",
        "b1",
        {
            "id": "b1",
            "workspace_id": "ws1",
            "text": "user can checkout",
            "route": "/checkout",
            "spec_path": "",
            "tags": ["sentinel"],
            "owner": "",
            "status": "active",
            "source": "author",
        },
    )
    behaviour = r.behaviours_for_workspace("ws1")[0]
    assert type(behaviour.tags) is tuple
    assert behaviour.tags == ("sentinel",)


# --- frozen dataclasses support the copy idiom used elsewhere in the plan --


def test_frozen_run_supports_the_dict_copy_idiom():
    run = Run(id="r1", workspace_id="ws1", number=1, trigger="manual", state="queued")
    finished = type(run)(**{**run.__dict__, "state": "finished"})
    assert finished.state == "finished"
    assert run.state == "queued"
    assert finished.id == run.id


# --- Task 2's review, carried forward: put_user normalises email -----------


def test_put_user_lowercases_a_mixed_case_email_at_write_time():
    r = _repo()
    r.put_user(User(id="u1", email="Roger@Acme.com", password_hash="x", name="Roger K."))
    assert r.user("u1").email == "roger@acme.com"
    assert r.user_by_email("roger@acme.com").id == "u1"
    assert r.user_by_email("Roger@Acme.com").id == "u1"


def test_put_user_normalisation_does_not_mutate_the_caller_supplied_dataclass():
    # A frozen dataclass cannot be mutated, but this guards the *stored*
    # representation specifically: the object the caller passed in must
    # still report the email it was constructed with.
    original = User(id="u2", email="Mixed@Case.com", password_hash="x", name="M")
    r = _repo()
    r.put_user(original)
    assert original.email == "Mixed@Case.com"


# --- claim_email: the transactional guard against a signup race -----------


def test_claim_email_succeeds_once_and_then_refuses():
    r = _repo()
    assert r.claim_email("race@acme.com", "u1") is True
    assert r.claim_email("race@acme.com", "u2") is False


def test_claim_email_is_case_insensitive():
    r = _repo()
    assert r.claim_email("Race@Acme.com", "u1") is True
    assert r.claim_email("race@acme.com", "u2") is False


def test_two_real_concurrent_claims_for_the_same_email_only_one_wins():
    # Mirrors tests/test_ledger.py's
    # test_concurrent_appends_to_one_workspace_do_not_fork_the_chain:
    # force a real interleaving (writer B runs its whole claim to
    # completion in the middle of writer A's transaction, right after A
    # has read the "not claimed yet" snapshot but before A commits) rather
    # than just calling claim_email twice in sequence, which never
    # exercises FakeTransaction's abort-and-retry path at all. A's commit
    # must abort against the version B just wrote and retry, so A's retry
    # reads B's claim and correctly loses -- exactly one of the two calls
    # observed by the *test* returns True.
    from core import fakes

    fake = FakeFirestore()
    r_a = Repo(_config(), client=fake)
    r_b = Repo(_config(), client=fake)

    original_get = fakes.FakeDoc.get
    interleaved = []
    b_result = []

    def get_then_let_the_other_writer_in(self, transaction=None):
        snapshot = original_get(self, transaction=transaction)
        if self._path == "plumbline_user_emails/racer@acme.com" and not interleaved:
            interleaved.append(True)
            b_result.append(r_b.claim_email("racer@acme.com", "u_b"))
        return snapshot

    fakes.FakeDoc.get = get_then_let_the_other_writer_in
    try:
        a_result = r_a.claim_email("racer@acme.com", "u_a")
    finally:
        fakes.FakeDoc.get = original_get

    assert interleaved == [True], "the interleaving never happened"
    assert b_result == [True]  # B ran to completion first and won
    assert a_result is False  # A's retry saw B's claim and correctly lost


# --- claim_run: Task 13's atomic "start running, bill once" guard --------


def test_claim_run_transitions_to_running_and_bills_the_workspace_once():
    r = _repo()
    r.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site"))
    r.put_run(Run(id="run1", workspace_id="ws1", number=1, trigger="manual"))

    claimed = r.claim_run("run1")

    assert claimed.state == "running"
    assert r.run("run1").state == "running"
    assert r.workspace("ws1").runs_used == 1


def test_claim_run_stamps_started_at_when_it_takes_the_run():
    # Fix round 1: Run.started_at defaults at OBJECT CONSTRUCTION -- when a
    # run is created/enqueued -- not when a worker actually claims and
    # starts executing it. claim_run must overwrite it with the moment OF
    # THE CLAIM, or job/orchestrator.py's own duration calculation ends up
    # including however long the run sat queued (see
    # tests/test_orchestrator.py's test_duration_excludes_time_spent_queued
    # for the end-to-end consequence).
    r = _repo()
    r.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site"))
    an_hour_ago = 1000.0
    r.put_run(Run(id="run1", workspace_id="ws1", number=1, trigger="manual", started_at=an_hour_ago))

    claimed = r.claim_run("run1")

    assert claimed.started_at != an_hour_ago
    assert claimed.started_at > an_hour_ago
    # Persisted, not just returned -- a later plain read sees the same
    # restamped value, not the original one.
    assert r.run("run1").started_at == claimed.started_at


def test_claim_run_refuses_a_run_that_is_not_queued():
    r = _repo()
    r.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site"))
    r.put_run(Run(id="run1", workspace_id="ws1", number=1, trigger="manual", state="running"))

    assert r.claim_run("run1") is None
    assert r.workspace("ws1").runs_used == 0  # never billed twice


def test_claim_run_returns_none_for_an_unknown_run():
    r = _repo()
    assert r.claim_run("does-not-exist") is None


def test_claim_run_returns_none_when_the_workspace_no_longer_exists():
    r = _repo()
    r.put_run(Run(id="run1", workspace_id="ws_gone", number=1, trigger="manual"))
    assert r.claim_run("run1") is None
    assert r.run("run1").state == "queued"  # untouched -- never half-claimed


def test_two_real_concurrent_claims_for_the_same_run_only_one_wins():
    # Mirrors test_two_real_concurrent_claims_for_the_same_email_only_one_wins
    # above: force a real interleaving so FakeTransaction's abort-and-retry
    # path actually runs, rather than calling claim_run twice in sequence
    # (which never proves the race is closed at all).
    from core import fakes

    fake = FakeFirestore()
    r_a = Repo(_config(), client=fake)
    r_b = Repo(_config(), client=fake)
    r_a.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site"))
    r_a.put_run(Run(id="run1", workspace_id="ws1", number=1, trigger="manual"))

    original_get = fakes.FakeDoc.get
    interleaved = []
    b_result = []

    def get_then_let_the_other_worker_in(self, transaction=None):
        snapshot = original_get(self, transaction=transaction)
        if self._path == "plumbline_runs/run1" and not interleaved:
            interleaved.append(True)
            b_result.append(r_b.claim_run("run1"))
        return snapshot

    fakes.FakeDoc.get = get_then_let_the_other_worker_in
    try:
        a_result = r_a.claim_run("run1")
    finally:
        fakes.FakeDoc.get = original_get

    assert interleaved == [True], "the interleaving never happened"
    assert b_result[0] is not None and b_result[0].state == "running"  # B won
    assert a_result is None  # A's retry saw B's claim and correctly lost
    assert r_a.workspace("ws1").runs_used == 1  # billed exactly once, not twice


def test_route_elements_round_trip_without_a_nested_array(repo):
    """Firestore rejects an array inside an array.

    `Route.elements` is a tuple of `(ref, role, name)` triples, so writing
    it straight through failed the whole document with a bare
    `InvalidArgument`. Cartographer's `graph.write` errored on every real
    run because of it, and nothing caught it: `seed/demo.py` never writes
    `elements`, so the demo path -- the only path anyone exercised -- never
    touched this field.
    """
    from app.models import Route

    repo.put_route(Route(
        id="rt1", workspace_id="ws1", path="/checkout", coverage_pct=0,
        elements=(("e1", "button", "Pay"), ("e2", "link", "Cart")),
    ))

    stored = repo._store.get("routes", "rt1")
    assert all(isinstance(e, dict) for e in stored["elements"]), (
        "elements must be stored as maps; a list of lists is a nested array "
        "and Firestore refuses the document"
    )

    back = repo.routes_for_workspace("ws1")[0]
    assert back.elements == (("e1", "button", "Pay"), ("e2", "link", "Cart"))
    hash(back)  # Route is frozen and must stay hashable


def test_a_route_stored_as_lists_still_reads_back(repo):
    """Tolerates the pre-fix shape rather than needing a migration."""
    from app.models import Route

    repo.put_route(Route(id="rt2", workspace_id="ws1", path="/x", coverage_pct=0))
    repo._store.put("routes", "rt2", {
        "id": "rt2", "workspace_id": "ws1", "path": "/x", "coverage_pct": 0,
        "last_mapped": 0.0, "elements": [["e1", "button", "Pay"]],
    })
    assert repo.routes_for_workspace("ws1")[0].elements == (("e1", "button", "Pay"),)


def test_a_route_with_no_elements_is_still_fine(repo):
    from app.models import Route

    repo.put_route(Route(id="rt3", workspace_id="ws1", path="/y", coverage_pct=0))
    assert repo.routes_for_workspace("ws1")[0].elements == ()
