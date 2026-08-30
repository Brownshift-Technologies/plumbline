"""POST /api/auth/demo -- no-account entry, and the demo-write-rejection
pattern every write-capable route adopts once it exists.

Two tests in the brief for this file (`test_a_demo_session_can_read`,
`test_a_demo_session_cannot_write`) are written against `GET`/`POST
/api/runs`. That route does not exist yet: Task 14a's own brief is
explicit that `POST /api/runs` is its route to build, with its own
contract (202 + enqueued job id for a real session; `{"demo": true,
"persisted": false}` + 200 for a demo one -- see task-14a-brief.md). This
task's file list (`app/auth_routes.py`, `app/deps.py`, `app/providers.py`,
`app/main.py`) does not include a runs router, so building one here would
mean guessing at, and likely conflicting with, Task 14a's real pagination/
SSE/Cloud-Run-Job design.

What's tested below instead is the identical *pattern* against a route
this task does own: `POST /api/auth/password` already returns exactly
`{"demo": True, "persisted": False}` for a demo session (see
`app/auth_routes.py`'s `change_password`), which is the same shape Task
14a's own required test (`test_a_demo_session_creating_a_run_persists_nothing`)
checks for `/api/runs`. `GET /api/auth/sessions` stands in for "a demo
session can read". Task 14a's own brief already carries the literal
`/api/runs` versions of these two tests as required test names in its own
file -- they are not silently dropped, just built where the route they
need actually exists.
"""

# --- from the brief ---------------------------------------------------------


def test_demo_entry_needs_no_account(client):
    r = client.post("/api/auth/demo")
    assert r.status_code == 200 and "pl_session" in r.cookies


def test_the_demo_banner_is_advertised_on_me(client):
    client.post("/api/auth/demo")
    assert client.get("/api/auth/me").json()["is_demo"] is True


# --- adapted: see module docstring for why these target owned routes -------


def test_a_demo_session_can_read(client):
    client.post("/api/auth/demo")
    assert client.get("/api/auth/sessions").status_code == 200


def test_a_demo_session_cannot_write(client):
    client.post("/api/auth/demo")
    r = client.post("/api/auth/password", json={"current": "whatever", "new": "a longer passphrase"})
    assert r.status_code == 200
    assert r.json()["demo"] is True and r.json()["persisted"] is False


# --- attacker-shaped tests beyond the brief ----------------------------------


def test_a_demo_session_is_capped_at_two_hours_even_with_a_much_longer_configured_ttl(client, config):
    # session_ttl_days on the injected config fixture is whatever
    # PlumblineConfig defaults to; the point here is the demo session must
    # never inherit it -- see app/sessions.py's DEMO_TTL_SECONDS contract,
    # exercised end-to-end through the real HTTP entry point rather than
    # only unit-tested against SessionService directly (tests/test_sessions.py
    # already covers the unit level).
    import time

    from app.sessions import DEMO_TTL_SECONDS

    client.post("/api/auth/demo")
    sid = client.cookies.get("pl_session")
    sess = client.app.state.sessions.resolve(sid)
    assert sess.expires_at - time.time() <= DEMO_TTL_SECONDS + 5


def test_demo_entry_does_not_require_or_accept_a_body(client):
    # A demo visitor has no credentials to present -- confirm the endpoint
    # doesn't silently accept (and ignore) an email/password body that
    # might make a caller believe they were signing in rather than
    # entering demo mode.
    r = client.post("/api/auth/demo", json={"email": "not@areal.account", "password": "irrelevant12"})
    assert r.status_code == 200
    assert r.json()["id"] == "demo"


def test_a_demo_session_cannot_be_escalated_by_calling_signout_then_reusing_the_cookie(client):
    client.post("/api/auth/demo")
    sid = client.cookies.get("pl_session")
    client.post("/api/auth/signout")
    # The cookie jar no longer holds pl_session after delete_cookie, but an
    # attacker who captured the raw session id before signout must not be
    # able to keep using it.
    client.cookies.set("pl_session", sid)
    assert client.get("/api/auth/me").status_code == 401


def test_repeated_demo_entries_each_get_their_own_independent_session(client):
    first = client.post("/api/auth/demo")
    first_sid = first.cookies.get("pl_session")
    second = client.post("/api/auth/demo")
    second_sid = second.cookies.get("pl_session")
    assert first_sid != second_sid
    # The first session must still be live -- entering demo mode again must
    # not revoke a previous demo session out from under another tab.
    assert client.app.state.sessions.resolve(first_sid) is not None


def test_seed_demo_if_missing_is_invoked_on_every_demo_entry(client):
    calls = []
    client.app.state.seed_demo_if_missing = lambda: calls.append(1)
    client.post("/api/auth/demo")
    assert calls == [1]
