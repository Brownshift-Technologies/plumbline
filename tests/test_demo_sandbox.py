"""Per-session demo sandbox.

Before this task every demo visitor shared one read-only workspace
(`config.demo_workspace_id`) and every write-capable route discarded the
write with `{"demo": true, "persisted": false}` -- correct against the
original spec ("read-mostly ... with an honest banner"), and useless as a
demo: a judge could look but never touch. The spec was wrong, not the 31
implementations of it. This file tests the fix: `POST /api/auth/demo` now
mints a FRESH, isolated sandbox workspace per session, seeded from the
same fixture, that session can fully write to -- and the small remaining
set of actions that genuinely reach outside the sandbox (a real GitHub
repository, a real environment, ...) still refuse, with a reason instead
of the old bare "nothing was saved".

See the task report (`task-demo-sandbox-report.md`) for the full design
rationale, including why cleanup is an opportunistic bounded sweep rather
than a `DELETE` route or a cron job.
"""

import time

import pytest
from starlette.testclient import TestClient

from gateway.gateway import GatewayError


def _second_client(app) -> TestClient:
    # An independent cookie jar against the SAME app/repo -- what actually
    # models "a second demo visitor", as opposed to a second request on
    # the one `client` fixture's own jar (which would just reuse its
    # existing `pl_session` cookie).
    return TestClient(app, base_url="https://testserver")


def test_each_demo_session_gets_its_own_workspace(client, repo, app):
    ws1 = client.post("/api/auth/demo").json()["workspace_id"]
    ws2 = _second_client(app).post("/api/auth/demo").json()["workspace_id"]

    assert ws1 != ws2
    workspace1, workspace2 = repo.workspace(ws1), repo.workspace(ws2)
    assert workspace1 is not None and workspace2 is not None
    assert workspace1.is_demo is True and workspace2.is_demo is True


def test_two_demo_sessions_cannot_see_each_others_data(client, app):
    client.post("/api/auth/demo")
    other = _second_client(app)
    other.post("/api/auth/demo")

    created = client.post("/api/behaviours", json={"text": "only this session should see this", "route": "/x"})
    assert created.status_code == 200
    created_id = created.json()["id"]

    mine = client.get("/api/behaviours").json()["behaviours"]
    assert any(b["id"] == created_id for b in mine)

    theirs = other.get("/api/behaviours").json()["behaviours"]
    assert not any(b["id"] == created_id for b in theirs)


def test_a_demo_session_can_create_a_behaviour_and_read_it_back(client):
    client.post("/api/auth/demo")
    r = client.post("/api/behaviours", json={"text": "Checkout total holds under retry", "route": "/checkout"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "Checkout total holds under retry" and "demo" not in body

    listed = client.get("/api/behaviours").json()["behaviours"]
    assert any(b["id"] == body["id"] for b in listed)


def test_a_demo_session_can_approve_the_gated_patch(client):
    # The pre-seeded double-charge finding/patch every fresh demo
    # workspace ships with (`seed/demo.py`) -- the product's own hero
    # moment, and the one thing a judge lands on without ever clicking
    # "start a run" first. Its id is scoped by workspace id (see
    # `seed/demo.py`'s "per-workspace-scoped ids" section), so it is
    # looked up here rather than hard-coded.
    client.post("/api/auth/demo")
    findings = client.get("/api/findings?status=patch_ready").json()["findings"]
    gated = next(f for f in findings if f["route"] == "/checkout/payment")

    r = client.post(f"/api/findings/{gated['id']}/patch/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "demo" not in body

    patch = client.get(f"/api/findings/{gated['id']}/patch").json()
    assert patch["gate_state"] == "merged"


def test_a_demo_session_can_edit_gate_rules(client):
    client.post("/api/auth/demo")
    rules = [{"tool": "pr.merge", "pattern": "src/checkout/payment*", "effect": "human"}]
    r = client.put("/api/policy/rules", json={"rules": rules})
    assert r.status_code == 200
    body = r.json()
    assert body["rules"] == rules and "demo" not in body
    assert client.get("/api/policy/rules").json()["rules"] == rules


def test_a_demo_run_produces_steps_and_reaches_the_gated_patch(client):
    client.post("/api/auth/demo")
    r = client.post("/api/runs", json={"trigger": "manual"})
    assert r.status_code == 202
    run_id = r.json()["id"]

    detail = None
    for _ in range(200):
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["run"]["state"] == "finished":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("the simulated demo run never reached a terminal state")

    assert len(detail["steps"]) == 7
    assert detail["finding_id"]
    patch = client.get(f"/api/findings/{detail['finding_id']}/patch").json()
    assert patch["gate_state"] == "awaiting_approval"


def test_a_demo_session_still_cannot_connect_a_real_repository(client):
    ws_id = client.post("/api/auth/demo").json()["workspace_id"]
    r = client.post(
        f"/api/workspaces/{ws_id}/repo",
        json={"repo_full_name": "acme/storefront", "default_branch": "main"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["demo"] is True and body["persisted"] is False
    assert "repository" in body["reason"].lower()


def test_a_demo_session_still_cannot_write_to_a_real_environment(client):
    # Not an `is_demo` special case at all -- `gateway.policy.DEFAULT_RULES`
    # denies `env.write` against anything but staging/preview for EVERY
    # workspace, demo sandbox included (see gateway/policy.py). This
    # confirms a demo workspace gets no quiet carve-out from that rule.
    ws_id = client.post("/api/auth/demo").json()["workspace_id"]
    gateway = client.app.state.gateway
    with pytest.raises(GatewayError) as exc_info:
        gateway.call(ws_id, "chaos", "env.write", "production")
    assert exc_info.value.needs_human is False  # denied outright, not gated


def test_a_refusal_explains_why_rather_than_saying_nothing_was_saved(client):
    client.post("/api/auth/demo")
    r = client.post("/api/keys", json={"name": "x", "role": "owner"})
    body = r.json()
    assert body["demo"] is True and body["persisted"] is False
    assert body["reason"]
    assert body["reason"] != "Nothing was saved."
    assert "In the demo" not in body["reason"]


def test_an_expired_demo_workspace_is_cleaned_up(client, repo):
    from app.sessions import DEMO_TTL_SECONDS

    ws_id = client.post("/api/auth/demo").json()["workspace_id"]
    workspace = repo.workspace(ws_id)
    # Backdate it past its 2-hour window -- the sweep's own cutoff.
    repo.put_workspace(type(workspace)(**{
        **workspace.__dict__, "created_at": time.time() - DEMO_TTL_SECONDS - 10,
    }))

    deleted = client.app.state.sweep_expired_demo_workspaces(limit=10)

    assert deleted >= 1
    assert repo.workspace(ws_id) is None
    assert repo.routes_for_workspace(ws_id) == []
    assert repo.behaviours_for_workspace(ws_id) == []
