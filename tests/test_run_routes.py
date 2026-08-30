"""Task 14a: run routes and the live SSE stream.

`stub_enqueue` is autouse for this whole file: every test here swaps
`app.state.enqueue_job` for an in-process recorder BEFORE the first
`POST /api/runs` -- the real default (`core.events.enqueue_job`) builds a
live `google.cloud.run_v2.JobsClient`, which resolves Application Default
Credentials immediately and has none available in this test process. This
mirrors how `tests/conftest.py` never lets a test touch a real Firestore
client either.
"""

import time

import pytest

from app.models import Run, Step, Workspace


@pytest.fixture(autouse=True)
def stub_enqueue(app):
    calls = []
    app.state.enqueue_job = lambda job_name, args: calls.append((job_name, args)) or "op/fake"
    return calls


@pytest.fixture(autouse=True)
def fast_sse(app):
    """Every test in this file gets a fast poll/heartbeat -- see
    `app/run_routes.py`'s `stream_run` for why these are read off
    `app.state` rather than hard-coded."""
    app.state.sse_poll_seconds = 0.01
    app.state.sse_heartbeat_seconds = 0.05


def _make_run(repo, workspace_id="ws1", number=1, state="queued", **kw) -> Run:
    run = Run(id=f"run_{number}", workspace_id=workspace_id, number=number, trigger="manual", state=state, **kw)
    repo.put_run(run)
    return run


# --- creation -----------------------------------------------------------


def test_creating_a_run_returns_202_and_an_id(client_as_owner, stub_enqueue):
    r = client_as_owner.post("/api/runs", json={"trigger": "manual"})
    assert r.status_code == 202
    body = r.json()
    assert body["id"] and body["state"] == "queued"
    assert stub_enqueue == [("plumbline-worker", {"PLUMBLINE_RUN_ID": body["id"]})]


def test_creating_a_run_does_not_block_on_the_fleet(client_as_owner):
    started = time.monotonic()
    r = client_as_owner.post("/api/runs", json={"trigger": "manual"})
    elapsed = time.monotonic() - started
    assert r.status_code == 202
    # A real fleet run takes minutes; this must return in well under a
    # second, proving nothing here ran the orchestrator in-process.
    assert elapsed < 1.0


def test_two_concurrent_creates_get_different_numbers(client_as_owner):
    first = client_as_owner.post("/api/runs", json={}).json()
    second = client_as_owner.post("/api/runs", json={}).json()
    assert first["number"] != second["number"]


def test_a_workspace_at_its_limit_gets_402_with_the_reset_date(client_at_limit):
    r = client_at_limit.post("/api/runs", json={})
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["limit"] == 500
    assert "resets_at" in detail


def test_a_reader_cannot_create_a_run(client_as_reader):
    r = client_as_reader.post("/api/runs", json={})
    assert r.status_code == 403


def test_a_demo_session_creating_a_run_persists_nothing(client_demo, stub_enqueue):
    r = client_demo.post("/api/runs", json={"trigger": "manual"})
    assert r.status_code == 200
    assert r.json() == {"demo": True, "persisted": False}
    assert stub_enqueue == []


# --- listing --------------------------------------------------------------


def test_listing_runs_paginates_with_a_cursor(client_as_owner, repo):
    for i in range(1, 6):
        _make_run(repo, number=i, state="finished")
    first = client_as_owner.get("/api/runs", params={"limit": 2}).json()
    assert len(first["runs"]) == 2
    assert first["next_cursor"] is not None
    second = client_as_owner.get(
        "/api/runs", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len(second["runs"]) == 2
    assert {r["id"] for r in first["runs"]}.isdisjoint({r["id"] for r in second["runs"]})


def test_an_unknown_cursor_falls_back_to_the_first_page(client_as_owner, repo):
    for i in range(1, 4):
        _make_run(repo, number=i, state="finished")
    r = client_as_owner.get("/api/runs", params={"cursor": "not-a-real-run-id"})
    assert r.status_code == 200
    assert len(r.json()["runs"]) == 3


def test_a_huge_limit_is_clamped(client_as_owner, repo):
    _make_run(repo, number=1, state="finished")
    r = client_as_owner.get("/api/runs", params={"limit": 100000})
    assert r.status_code == 200  # never tries to hand back an unbounded page


def test_listing_runs_filters_by_state(client_as_owner, repo):
    _make_run(repo, number=1, state="finished")
    _make_run(repo, number=2, state="queued")
    r = client_as_owner.get("/api/runs", params={"state": "queued"}).json()
    assert [row["state"] for row in r["runs"]] == ["queued"]


# --- detail / cancel --------------------------------------------------------


def test_getting_a_run_from_another_workspace_is_404(client_as_owner, repo):
    _make_run(repo, workspace_id="ws-someone-else", number=1, state="finished")
    r = client_as_owner.get("/api/runs/run_1")
    assert r.status_code == 404


def test_cancelling_a_queued_run_marks_it_cancelled(client_as_owner, repo):
    _make_run(repo, number=1, state="queued")
    r = client_as_owner.post("/api/runs/run_1/cancel")
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"
    assert repo.run("run_1").state == "cancelled"


def test_cancelling_a_running_run_is_a_conflict(client_as_owner, repo):
    _make_run(repo, number=1, state="running")
    r = client_as_owner.post("/api/runs/run_1/cancel")
    assert r.status_code == 409


# --- SSE --------------------------------------------------------------------


def test_the_stream_emits_a_step_event_per_step(client_as_owner, repo):
    _make_run(repo, number=1, state="running")
    repo.append_step(Step(id="st1", run_id="run_1", agent="cartographer", summary="mapped", at=1.0))
    repo.put_run(Run(id="run_1", workspace_id="ws1", number=1, trigger="manual", state="finished"))
    with client_as_owner.stream("GET", "/api/runs/run_1/stream") as resp:
        body = "".join(resp.iter_text())
    assert 'event: step' in body
    assert '"agent": "cartographer"' in body
    assert 'event: finished' in body


def test_the_stream_closes_after_finished(client_as_owner, repo):
    _make_run(repo, number=1, state="finished")
    with client_as_owner.stream("GET", "/api/runs/run_1/stream") as resp:
        lines = list(resp.iter_text())
    assert any("event: finished" in line for line in lines)


def test_connecting_late_replays_the_steps_already_recorded(client_as_owner, repo):
    _make_run(repo, number=1, state="running")
    for i in range(3):
        repo.append_step(Step(id=f"st{i}", run_id="run_1", agent="runner", summary=f"step {i}", at=float(i)))
    repo.put_run(Run(id="run_1", workspace_id="ws1", number=1, trigger="manual", state="finished"))
    with client_as_owner.stream("GET", "/api/runs/run_1/stream") as resp:
        body = "".join(resp.iter_text())
    assert body.count("event: step") == 3


def test_reconnecting_does_not_lose_steps(client_as_owner, repo):
    _make_run(repo, number=1, state="running")
    repo.append_step(Step(id="st1", run_id="run_1", agent="author", summary="wrote specs", at=1.0))
    repo.put_run(Run(id="run_1", workspace_id="ws1", number=1, trigger="manual", state="finished"))
    with client_as_owner.stream("GET", "/api/runs/run_1/stream") as resp:
        first_body = "".join(resp.iter_text())
    with client_as_owner.stream("GET", "/api/runs/run_1/stream") as resp:
        second_body = "".join(resp.iter_text())
    assert first_body.count("event: step") == 1
    assert second_body.count("event: step") == 1


async def test_the_stream_sends_a_heartbeat(client_as_owner, repo):
    # A real, never-finishing run -- the same shape a client that stays
    # connected through a quiet stretch of a run sees. See
    # `app/run_routes.py`'s `_run_events` docstring for why this test
    # drives the poll loop directly rather than through any HTTP
    # transport: a generator that only ever stops when the client goes
    # away hangs `httpx.ASGITransport` forever (it awaits the whole ASGI
    # call to completion before returning anything), regardless of what
    # the test client does.
    from app.run_routes import _run_events

    _make_run(repo, number=1, state="running")
    gen = _run_events(repo, "run_1", poll_seconds=0.01, heartbeat_seconds=0.02)
    chunks = ""
    async for chunk in gen:
        chunks += chunk
        if ": heartbeat" in chunks:
            break
    await gen.aclose()
    assert ": heartbeat" in chunks


def test_streaming_a_run_from_another_workspace_is_404(client_as_owner, repo):
    _make_run(repo, workspace_id="ws-someone-else", number=1, state="finished")
    r = client_as_owner.get("/api/runs/run_1/stream")
    assert r.status_code == 404
