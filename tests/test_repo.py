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


def test_patch_for_finding_is_none_when_no_patch_exists():
    assert _repo().patch_for_finding("nope") is None


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


# --- frozen dataclasses support the copy idiom used elsewhere in the plan --


def test_frozen_run_supports_the_dict_copy_idiom():
    run = Run(id="r1", workspace_id="ws1", number=1, trigger="manual", state="queued")
    finished = type(run)(**{**run.__dict__, "state": "finished"})
    assert finished.state == "finished"
    assert run.state == "queued"
    assert finished.id == run.id
