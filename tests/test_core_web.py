import asyncio
import base64
import json
import threading
import time

from fastapi.testclient import TestClient

import core.web
from core.web import create_app


def _push(payload: dict) -> dict:
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": encoded, "messageId": "m1"}}


def test_healthz_returns_ok():
    client = TestClient(create_app(on_event=lambda payload: None, service_name="t"))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_events_invokes_handler_with_decoded_payload():
    seen = []
    client = TestClient(create_app(on_event=seen.append, service_name="t"))
    response = client.post("/events", json=_push({"run_id": "r1"}))
    assert response.status_code == 204
    assert seen == [{"run_id": "r1"}]


def test_events_acks_malformed_push_without_retry():
    client = TestClient(create_app(on_event=lambda payload: None, service_name="t"))
    response = client.post("/events", json={"nope": True})
    assert response.status_code == 204


def test_events_acks_when_handler_raises():
    def explode(payload):
        raise RuntimeError("handler blew up")

    client = TestClient(create_app(on_event=explode, service_name="t"))
    assert client.post("/events", json=_push({"a": 1})).status_code == 204


# The brief's first draft read the body with `await request.json()` *before*
# entering the try/except around `parse_pubsub_push`. A body that is not
# valid JSON at all -- as opposed to valid JSON that fails the push envelope
# shape -- raises `json.JSONDecodeError` from that unguarded read, which
# escapes the handler as an unhandled exception and becomes a 500. Pub/Sub
# retries any non-2xx response forever, so that 500 is a poison message. This
# test is the regression check for that bug: the body-read has to be inside
# the guarded region too.
def test_events_acks_non_json_body_without_retry():
    client = TestClient(create_app(on_event=lambda payload: None, service_name="t"))
    response = client.post(
        "/events",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 204


def test_events_acks_empty_body_without_retry():
    client = TestClient(create_app(on_event=lambda payload: None, service_name="t"))
    response = client.post("/events", content=b"", headers={"Content-Type": "application/json"})
    assert response.status_code == 204


# --- ASGI-level tests -------------------------------------------------------
#
# The task-8 report claimed the broad `except Exception` around the body read
# was not deterministically testable, because TestClient offers no way to
# simulate a mid-body disconnect. True of TestClient, and irrelevant: the app
# *is* an ASGI callable, so calling it with a scripted `receive` needs no
# client at all. That claim being wrong mattered -- mutating the guard to
# `except json.JSONDecodeError` left every one of the tests above passing, so
# the guard the retry-loop fix depends on was unverified, and a future
# "tighten this except" cleanup would have silently restored the bug.


async def _asgi(app, method, path, body_messages=None):
    """Drive the app directly as an ASGI callable. Returns (status, chunks).

    `body_messages` is the exact `receive` script: ASGI event dicts, or an
    exception instance to have `receive()` raise it. That is the only way to
    produce a disconnect or a transport error at the moment of the body read.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    pending = list(body_messages or [{"type": "http.request", "body": b"", "more_body": False}])

    async def receive():
        message = pending.pop(0) if pending else {"type": "http.disconnect"}
        if isinstance(message, BaseException):
            raise message
        return message

    status = None
    chunks = []

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return status, b"".join(chunks)


def _logged(capsys) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]


async def test_events_acks_client_disconnect_mid_body(capsys):
    app = create_app(on_event=lambda payload: None, service_name="t")
    status, _ = await _asgi(
        app,
        "POST",
        "/events",
        [{"type": "http.request", "body": b'{"mes', "more_body": True}, {"type": "http.disconnect"}],
    )
    assert status == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.body_undecodable"
    # Minor 6: `str(ClientDisconnect())` is "", so `error=str(exc)` logged an
    # empty string -- no diagnostic at all, from the event name that exists to
    # keep causes distinguishable. The type name is the diagnostic here.
    assert entry["error"] == "ClientDisconnect: "


async def test_events_acks_when_receive_raises_transport_error(capsys):
    app = create_app(on_event=lambda payload: None, service_name="t")
    status, _ = await _asgi(app, "POST", "/events", [OSError("connection reset by peer")])
    assert status == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.body_undecodable"
    assert entry["error"] == "OSError: connection reset by peer"


async def test_events_acks_truncated_body_then_disconnect(capsys):
    app = create_app(on_event=lambda payload: None, service_name="t")
    status, _ = await _asgi(
        app,
        "POST",
        "/events",
        [
            {"type": "http.request", "body": b'{"message": {"da', "more_body": True},
            {"type": "http.disconnect"},
        ],
    )
    assert status == 204
    assert _logged(capsys)[-1]["event"] == "event.body_undecodable"


def test_events_acks_when_parser_raises_something_unexpected(capsys, monkeypatch):
    # core.events is explicit that its guarded-case list "is not a proof
    # that nothing else can escape", so this endpoint must not depend on that
    # totality. Anything other than InvalidPushError is acked too, under its
    # own name and at ERROR severity.
    def boom(body):
        raise MemoryError("out of memory")

    monkeypatch.setattr("core.web.parse_pubsub_push", boom)
    client = TestClient(create_app(on_event=lambda payload: None, service_name="t"))
    assert client.post("/events", json=_push({"a": 1})).status_code == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.parse_failed"
    assert entry["severity"] == "ERROR"
    assert entry["error"] == "MemoryError: out of memory"


def test_handler_failure_is_logged_without_pii(capsys):
    def explode(payload):
        raise ValueError(f"could not process {payload}")

    client = TestClient(create_app(on_event=explode, service_name="t"))
    secrets = {"email": "jane@example.com", "ssn": "123-45-6789", "phone": "415-555-0132"}
    assert client.post("/events", json=_push(secrets)).status_code == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.handler_failed"
    for secret in secrets.values():
        assert secret not in entry["error"]
    assert "[EMAIL]" in entry["error"] and "[SSN]" in entry["error"] and "[PHONE]" in entry["error"]
    assert entry["error"].startswith("ValueError: could not process ")


def test_redaction_leaves_parse_diagnostics_intact(capsys):
    # The redaction pass must not eat the positions that make these log lines
    # useful: the patterns match 9- and 10-digit runs, and byte offsets in a
    # 10 MB body top out at eight digits.
    client = TestClient(create_app(on_event=lambda payload: None, service_name="t"))
    body = b'{"email": "jane@example.com", }'
    assert client.post("/events", content=body, headers={"Content-Type": "application/json"}).status_code == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.body_undecodable"
    assert entry["error"] == (
        "JSONDecodeError: Expecting property name enclosed in double quotes: "
        "line 1 column 31 (char 30)"
    )


async def test_handler_runs_off_the_event_loop_thread():
    # Calling a sync handler inline from the `async def` blocks the loop for
    # the handler's whole duration.
    loop_thread = threading.get_ident()
    seen = []
    app = create_app(on_event=lambda payload: seen.append(threading.get_ident()), service_name="t")
    status, _ = await _asgi(
        app, "POST", "/events", [{"type": "http.request", "body": json.dumps(_push({"a": 1})).encode()}]
    )
    assert status == 204
    assert seen and seen[0] != loop_thread


async def test_healthz_answers_while_a_slow_handler_is_in_flight():
    # Measured against real uvicorn before the fix: a GET /healthz issued
    # 0.3 s into a 1.5 s handler took 1.224 s -- it waited the handler out.
    # Cloud Run's liveness and startup probes hit exactly this path, and
    # container concurrency collapses to 1 while it holds.
    #
    # Both assertions below are load-bearing, and the first draft of this test
    # had neither: it awaited the handler's "I have started" signal before
    # starting the clock, and with the handler inline that await cannot itself
    # resume until the handler is *finished* -- so the clock started after the
    # block had already lifted and measured nothing. Nothing here may await
    # anything that depends on the loop being free before the measurement.
    hold = 2.0
    entered = threading.Event()
    release = threading.Event()
    finished = []

    def slow(payload):
        entered.set()
        release.wait(hold)
        finished.append(True)

    app = create_app(on_event=slow, service_name="t")
    started = time.monotonic()
    event_call = asyncio.create_task(
        _asgi(app, "POST", "/events", [{"type": "http.request", "body": json.dumps(_push({"a": 1})).encode()}])
    )
    try:
        await asyncio.sleep(0)  # one yield: enough for the POST to reach the handler
        status, body = await _asgi(app, "GET", "/healthz")
        elapsed = time.monotonic() - started
        assert status == 200
        assert json.loads(body) == {"status": "ok"}
        # Ordering: with the handler inline the loop cannot dispatch this
        # request until the handler returns, so the handler is always finished
        # by the time /healthz is answered.
        assert not finished, "/healthz was only served after the handler finished"
        # Latency: off the loop this costs about a millisecond, so the bound
        # sits four times clear of that and a quarter of the way to `hold`.
        assert elapsed < 0.5, f"/healthz took {elapsed:.3f}s while a handler was in flight"
    finally:
        release.set()
        assert (await event_call)[0] == 204
    assert entered.is_set() and finished


async def test_async_handler_is_awaited():
    # Before this, `create_app(on_event=<coroutine function>)` returned 204,
    # never awaited the coroutine, dropped the message, and left only a
    # `RuntimeWarning: coroutine was never awaited` on stderr.
    seen = []

    async def handler(payload):
        await asyncio.sleep(0)
        seen.append(payload)

    app = create_app(on_event=handler, service_name="t")
    status, _ = await _asgi(
        app, "POST", "/events", [{"type": "http.request", "body": json.dumps(_push({"a": 1})).encode()}]
    )
    assert status == 204
    assert seen == [{"a": 1}]


async def test_handler_returning_an_awaitable_is_awaited():
    # `inspect.iscoroutinefunction` does not see an object whose `__call__` is
    # `async def`, so the return value is checked too.
    seen = []

    class Handler:
        async def __call__(self, payload):
            seen.append(payload)

    app = create_app(on_event=Handler(), service_name="t")
    status, _ = await _asgi(
        app, "POST", "/events", [{"type": "http.request", "body": json.dumps(_push({"a": 1})).encode()}]
    )
    assert status == 204
    assert seen == [{"a": 1}]


async def test_async_handler_failure_is_acked_and_redacted(capsys):
    async def explode(payload):
        raise ValueError(f"could not process {payload}")

    app = create_app(on_event=explode, service_name="t")
    body = json.dumps(_push({"email": "jane@example.com"})).encode()
    status, _ = await _asgi(app, "POST", "/events", [{"type": "http.request", "body": body}])
    assert status == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.handler_failed"
    assert "jane@example.com" not in entry["error"]


def test_generated_docs_routes_are_not_exposed():
    # /events is machine-to-machine and the service is deployed
    # --allow-unauthenticated, so the schema and its two UIs are surface
    # published to anyone who asks. This does not touch a console UI a
    # project mounts on the same app.
    client = TestClient(create_app(on_event=lambda payload: None, service_name="t"))
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404, path


def test_events_acks_when_the_exception_itself_cannot_be_printed(capsys):
    # The log call runs inside the arms that make this endpoint fail closed,
    # so it must not be able to raise. Before `_describe` guarded formatting,
    # this returned 500 -- the exact retry loop the arms exist to prevent.
    class Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("nope")

    def explode(payload):
        raise Unprintable()

    client = TestClient(
        create_app(on_event=explode, service_name="t"), raise_server_exceptions=False
    )
    assert client.post("/events", json=_push({"a": 1})).status_code == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.handler_failed"
    assert entry["error"] == "Unprintable: <unprintable>"


# --- the unprintable-exception guard must be independent of the try ----
# The first version of that guard re-evaluated `type(exc).__name__` in its
# except arm -- the same subexpression that had just thrown. An exception
# whose *name* raises therefore raised again from inside the arm and escaped
# as a 500, which is the Pub/Sub retry loop the arms exist to prevent. All
# three shapes below returned 500 before `_type_name` was split out.


class _RaisingName(type):
    @property
    def __name__(cls):
        raise RuntimeError("__name__ is not available")


class _NameAndStrRaise(Exception, metaclass=_RaisingName):
    def __str__(self):
        raise RuntimeError("__str__ is not available")


class _NameOnlyRaises(Exception, metaclass=_RaisingName):
    def __str__(self):
        return "this str is fine"


def test_events_acks_when_both_the_name_and_the_str_raise(capsys):
    def explode(payload):
        raise _NameAndStrRaise()

    client = TestClient(
        create_app(on_event=explode, service_name="t"), raise_server_exceptions=False
    )
    assert client.post("/events", json=_push({"a": 1})).status_code == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.handler_failed"
    assert entry["error"] == "<unnameable>: <unprintable>"


def test_events_acks_when_only_the_name_raises(capsys):
    # `__str__` is fine here, so the *first* try never fails and the old code
    # never entered its except arm -- it raised from the try's own f-string.
    def explode(payload):
        raise _NameOnlyRaises()

    client = TestClient(
        create_app(on_event=explode, service_name="t"), raise_server_exceptions=False
    )
    assert client.post("/events", json=_push({"a": 1})).status_code == 204
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.handler_failed"
    assert entry["error"] == "<unnameable>: this str is fine"


def test_events_acks_when_an_unnameable_exception_comes_from_the_parser(capsys):
    # The handler arm is not the only site: `_describe` runs in all four, and
    # the parser arm reaches it with an exception type this module never sees.
    def boom(body):
        raise _NameAndStrRaise()

    monkey = core.web.parse_pubsub_push
    core.web.parse_pubsub_push = boom
    try:
        client = TestClient(
            create_app(on_event=lambda payload: None, service_name="t"),
            raise_server_exceptions=False,
        )
        assert client.post("/events", json=_push({"a": 1})).status_code == 204
    finally:
        core.web.parse_pubsub_push = monkey
    entry = _logged(capsys)[-1]
    assert entry["event"] == "event.parse_failed"
    assert entry["error"] == "<unnameable>: <unprintable>"


def test_a_name_that_is_not_a_plain_str_does_not_reach_the_f_string(capsys):
    # Guarding only the lookup would have moved the problem: a `str` subclass
    # with a raising `__format__` passes the try and then throws in both of
    # `_describe`'s f-strings, the fallback included.
    class Hostile(str):
        def __format__(self, spec):
            raise RuntimeError("__format__ is not available")

    class _HostileName(type):
        @property
        def __name__(cls):
            return Hostile("Boom")

    class HostileNamed(Exception, metaclass=_HostileName):
        pass

    def explode(payload):
        raise HostileNamed("plain message")

    client = TestClient(
        create_app(on_event=explode, service_name="t"), raise_server_exceptions=False
    )
    assert client.post("/events", json=_push({"a": 1})).status_code == 204
    assert _logged(capsys)[-1]["error"] == "<unnameable>: plain message"


# --- the log detail is capped before it is redacted --------------------


def test_a_huge_exception_detail_is_truncated_before_redaction(capsys):
    # redact_pii is linear now, but this endpoint is the wire-reachable end
    # of it and the cap holds whatever the pattern later becomes.
    def explode(payload):
        raise ValueError("x" * 50_000)

    client = TestClient(create_app(on_event=explode, service_name="t"))
    assert client.post("/events", json=_push({"a": 1})).status_code == 204
    error = _logged(capsys)[-1]["error"]
    assert error.endswith("… <truncated>")
    assert len(error) < 2_100


def test_a_pathological_body_does_not_stall_the_endpoint():
    # The shape that made `_EMAIL` quadratic, at a size that took ~1.7 s
    # before the bounds in core.guards. The budget is slack by two
    # orders of magnitude so it measures the complexity class, not the box.
    def explode(payload):
        raise ValueError(f"could not process {payload}")

    client = TestClient(create_app(on_event=explode, service_name="t"))
    body = _push({"text": "a@" + "a." * 21_600})
    start = time.perf_counter()
    assert client.post("/events", json=body).status_code == 204
    assert time.perf_counter() - start < 0.5
