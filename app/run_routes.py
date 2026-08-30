"""Task 14a: run routes and the live SSE stream.

`POST /api/runs` never runs the fleet in-process. It writes a `queued`
`Run` row (its number allocated by a Firestore transaction -- see
`_allocate_run_number` -- so two concurrent creates cannot collide) and
hands `{"PLUMBLINE_RUN_ID": run.id}` to `app.state.enqueue_job`, which
`app/main.py` wires to `core.events.enqueue_job` (a Cloud Run Job
execution) by default. Nothing here calls `job.orchestrator.Orchestrator`
directly, and nothing here blocks on it -- `job/worker.py`'s container is
what actually runs the fleet, on its own process, on its own time. A
request to this route returns as soon as the job is *started*, not when it
finishes; see `test_creating_a_run_does_not_block_on_the_fleet`.

`app.state.enqueue_job` is an injectable hook (the same pattern
`app.state.seed_demo_if_missing`/`app.state.deliver_reset_email` already
use) rather than a bare call to `core.events.enqueue_job` here, precisely
so a test can swap in a stub that never touches a real Cloud Run Jobs
client -- constructing the real one resolves Application Default
Credentials immediately (see `core.events.enqueue_job`'s own docstring),
which this test suite has none of.

**Why the stream polls Firestore instead of fanning out from Pub/Sub.** A
Pub/Sub push for `run.step` would land on exactly one Cloud Run instance;
an SSE client connected to a *different* instance would simply never see
it. That is not a rare edge case at this product's own target scale -- it
is the default the moment the service runs on more than one instance,
which is the whole point of deploying it on Cloud Run rather than a
single box. Polling `Repo.steps_for_run` every `sse_poll_seconds` (default
1s, overridable on `app.state` -- tests turn it down so they do not spend
real wall-clock seconds waiting) is correct regardless of which instance
answers, at the cost of one small query per second per *connected*
client. `job/worker.py` still publishes `run.finished` over Pub/Sub for
the audit trail and any future consumer; this stream simply does not
depend on it.

**Replay, not "what happened since I connected".** Every connection --
the first, a reconnect after a drop, a client that opens the stream
minutes into a run that is already half done -- starts its own `sent_ids`
set from empty and reads every step already recorded for the run on its
very first poll. A step already sitting in Firestore when the connection
opens is indistinguishable, from this generator's point of view, from one
written a second later: both are simply "not yet sent on THIS
connection", and both go out as an `event: step` before the loop ever
sleeps. That is what makes "connects late" and "reconnects" the same code
path rather than two.

Judgement calls (see task-14a-15-report.md for the fuller writeup):
- `GET /api/runs`'s `cursor` is opaque (the previous page's last run id).
  A cursor that does not resolve in the caller's own workspace --
  forged, stale, or lifted from a different workspace entirely -- is
  treated as "start from the top" rather than a 400 or (worse) an index
  computed against another workspace's ordering. It fails safe, silently,
  the same way an unresolvable session cookie does (`app/deps.py`'s
  `current_session`): a client cannot use it to learn anything about a
  cursor it does not own.
- `limit` is clamped to `_MAX_PAGE_SIZE` regardless of what is requested
  -- `?limit=100000` gets the max, not an attempt to hand back the whole
  collection in one response.
"""

import asyncio
import json
import time
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.deps import current_session, require_write_role
from app.models import Run

router = APIRouter(prefix="/api/runs")

_MAX_PAGE_SIZE = 200
_DEFAULT_PAGE_SIZE = 20
_TERMINAL_STATES = frozenset({"finished", "failed", "cancelled"})
_RUN_JOB_NAME = "plumbline-worker"


def _run_json(r: Run) -> dict:
    return {
        "id": r.id, "workspace_id": r.workspace_id, "number": r.number,
        "trigger": r.trigger, "state": r.state, "commit": r.commit,
        "started_by": r.started_by, "held": r.held, "failed": r.failed,
        "repaired": r.repaired, "duration_ms": r.duration_ms,
        "started_at": r.started_at,
    }


def _step_json(s) -> dict:
    return {
        "id": s.id, "run_id": s.run_id, "agent": s.agent, "summary": s.summary,
        "detail": s.detail, "outcome": s.outcome, "duration_ms": s.duration_ms,
        "at": s.at,
    }


def _next_reset_date() -> str:
    """The first of next calendar month, UTC -- an honest, computed answer
    rather than a fixed string, so a workspace that hits its limit near a
    month boundary sees a date that is actually close."""
    now = datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return date(year, month, 1).isoformat()


def _allocate_run_number(repo, workspace_id: str) -> int:
    """Atomically hand out the next run number for `workspace_id`.

    Backed by its own counter document (`run_counters/{workspace_id}`),
    not a scan-and-increment over existing runs -- two concurrent calls
    must never compute the same next number from the same "current max"
    read, which a plain read-then-write over `runs_for_workspace` would
    let happen exactly like `Repo.claim_email`'s own docstring describes
    for two racing signups. `fallback` (the workspace's current highest
    run number + 1) is computed OUTSIDE the transaction, once, only to
    seed the counter document the very first time this is ever called for
    a workspace -- including a demo workspace that Task 15 already seeded
    with runs 4454-4471 -- after which every call reads and increments the
    counter document itself. Two concurrent first-calls can both compute
    the same `fallback`; only one of them wins the transactional write of
    the counter document, and the loser's retry re-reads the number the
    winner just wrote rather than reusing its own stale fallback -- the
    same optimistic-concurrency guarantee `Repo.claim_email`/
    `Repo.consume_totp_step` already rely on.
    """
    from google.cloud import firestore

    ref = repo.store.doc("run_counters", workspace_id)
    fallback = max((r.number for r in repo.runs_for_workspace(workspace_id)), default=0) + 1

    @firestore.transactional
    def _claim(transaction) -> int:
        snapshot = ref.get(transaction=transaction)
        next_number = snapshot.to_dict()["next"] if snapshot.exists else fallback
        transaction.set(ref, {"next": next_number + 1})
        return next_number

    return _claim(repo.store.transaction())


def enqueue_run(request: Request, sess, trigger: str, commit: str = "") -> Run:
    """Allocate, persist, and enqueue one new `queued` `Run` for the
    caller's workspace. Shared by `POST /api/runs` and
    `app/surface_routes.py`'s `POST /api/surface/remap` (a remap is
    nothing more than a run whose trigger says why it started) so the
    402-at-limit check, the transactional numbering, and the
    enqueue-not-execute contract live in exactly one place rather than
    two call sites that could quietly drift apart.

    Raises `HTTPException` (404 no workspace, 402 at limit) the same way
    a route handler would -- callers do not need their own try/except.
    """
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    if workspace.runs_used >= workspace.run_limit:
        raise HTTPException(402, {
            "error": "this workspace has reached its run limit",
            "limit": workspace.run_limit,
            "used": workspace.runs_used,
            "resets_at": _next_reset_date(),
        })

    number = _allocate_run_number(repo, workspace.id)
    run = Run(
        id=f"run_{uuid.uuid4().hex[:12]}", workspace_id=workspace.id, number=number,
        trigger=trigger, commit=commit, started_by=sess.user_id, state="queued",
    )
    repo.put_run(run)
    request.app.state.enqueue_job(_RUN_JOB_NAME, {"PLUMBLINE_RUN_ID": run.id})
    return run


class CreateRun(BaseModel):
    trigger: str = "manual"
    commit: str = ""


@router.get("")
def list_runs(
    request: Request, limit: int = _DEFAULT_PAGE_SIZE, cursor: str | None = None,
    state: str | None = None, trigger: str | None = None, sort: str = "number",
    sess=Depends(current_session),
):
    repo = request.app.state.repo
    page_size = max(1, min(limit, _MAX_PAGE_SIZE))

    rows = repo.runs_for_workspace(sess.workspace_id)  # already number-descending
    if state:
        rows = [r for r in rows if r.state == state]
    if trigger:
        rows = [r for r in rows if r.trigger == trigger]
    if sort == "duration":
        rows = sorted(rows, key=lambda r: r.duration_ms, reverse=True)

    start = 0
    if cursor:
        ids = [r.id for r in rows]
        try:
            start = ids.index(cursor) + 1
        except ValueError:
            # Unknown, stale, or foreign-workspace cursor -- fail safe to
            # the first page rather than error or leak positional info
            # about another workspace's ordering. See the module docstring.
            start = 0

    page = rows[start:start + page_size]
    next_cursor = page[-1].id if page and (start + page_size) < len(rows) else None
    return {
        "runs": [_run_json(r) for r in page],
        "next_cursor": next_cursor,
        "total": len(rows),
    }


@router.post("", status_code=202)
def create_run(
    body: CreateRun, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner", "approver")),
):
    if sess.is_demo:
        return JSONResponse({"demo": True, "persisted": False}, status_code=200)
    run = enqueue_run(request, sess, body.trigger, body.commit)
    return {"id": run.id, "number": run.number, "state": run.state}


@router.get("/{run_id}")
def get_run(run_id: str, request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    run = repo.run(run_id)
    if run is None or run.workspace_id != sess.workspace_id:
        # Same shape for "does not exist" and "exists in another
        # workspace" -- a 404 that distinguished the two would let a
        # caller enumerate run ids across tenants by status code alone.
        raise HTTPException(404, "no such run")
    steps = repo.steps_for_run(run_id)
    return {"run": _run_json(run), "steps": [_step_json(s) for s in steps]}


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner", "approver")),
):
    if sess.is_demo:
        return {"demo": True, "persisted": False}
    repo = request.app.state.repo
    run = repo.run(run_id)
    if run is None or run.workspace_id != sess.workspace_id:
        raise HTTPException(404, "no such run")
    if run.state != "queued":
        raise HTTPException(409, f"run {run_id!r} is {run.state!r}, not queued")
    repo.put_run(type(run)(**{**run.__dict__, "state": "cancelled"}))
    return {"id": run.id, "state": "cancelled"}


async def _run_events(repo, run_id: str, poll_seconds: float, heartbeat_seconds: float, is_disconnected=None):
    """The stream's actual poll loop, factored out of `stream_run` as a
    plain async generator with no `Request`/`StreamingResponse` of its
    own -- so a test can drive it directly (`async for chunk in
    _run_events(...): ...; break`) without going through an ASGI
    transport at all.

    That is not a nicety, it is the only way `test_the_stream_sends_a_
    heartbeat` can exist at all for a run that never reaches a terminal
    state: `httpx.ASGITransport.handle_async_request` (what both
    `fastapi.testclient.TestClient` and `httpx.AsyncClient` use to drive
    an app in-process) awaits the WHOLE ASGI application call to
    completion before it hands back a `Response` -- there is no partial,
    lazily-paced delivery for a test to read a few chunks from and then
    disconnect out of. A generator that only stops when the client goes
    away, exercised through that transport, hangs the test forever
    regardless of what the client does; the transport itself, not this
    endpoint, is what does not support it. Iterating this generator
    directly sidesteps the transport entirely: stopping early just calls
    `aclose()` on a plain Python async generator, which is well-defined
    and immediate.

    `is_disconnected` is `None` in that direct/unit-tested form (skip the
    check) and `request.is_disconnected` (a bound method) from the real
    route -- real disconnect detection is exercised, over the wire, in
    `test_the_stream_sends_a_heartbeat`, which uses `httpx.AsyncClient`
    directly against a run that finishes.
    """
    sent_ids: set[str] = set()
    last_sent = time.monotonic()
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        current = repo.run(run_id)
        if current is None:
            return
        for step in repo.steps_for_run(run_id):
            if step.id in sent_ids:
                continue
            sent_ids.add(step.id)
            last_sent = time.monotonic()
            yield f"event: step\ndata: {json.dumps(_step_json(step))}\n\n"
        if current.state in _TERMINAL_STATES:
            yield f"event: finished\ndata: {json.dumps(_run_json(current))}\n\n"
            return
        if time.monotonic() - last_sent >= heartbeat_seconds:
            # A comment line (":"-prefixed): valid SSE, ignored by every
            # client's `onmessage`, and exactly what keeps an idle proxy
            # from timing the connection out.
            yield ": heartbeat\n\n"
            last_sent = time.monotonic()
        await asyncio.sleep(poll_seconds)


@router.get("/{run_id}/stream")
async def stream_run(run_id: str, request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    run = repo.run(run_id)
    if run is None or run.workspace_id != sess.workspace_id:
        raise HTTPException(404, "no such run")

    # Overridable on `app.state` -- tests turn these down so a
    # heartbeat/poll test does not cost real wall-clock seconds. Real
    # deployments never set either, and get the documented 1s/15s.
    poll_seconds = getattr(request.app.state, "sse_poll_seconds", 1.0)
    heartbeat_seconds = getattr(request.app.state, "sse_heartbeat_seconds", 15.0)

    return StreamingResponse(
        _run_events(repo, run_id, poll_seconds, heartbeat_seconds, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
