"""Task 14c: agent + policy routes, both mounted from `app/agent_routes.py`
-- see that module's docstring for why they share one file (and this one
test file) even though the brief names only four test files for five
routers."""

from app.models import Run, Step


def _make_run(repo, number, state, workspace_id="ws1"):
    run = Run(id=f"run_{number}", workspace_id=workspace_id, number=number, trigger="manual", state=state)
    repo.put_run(run)
    return run


# --- agents -----------------------------------------------------------------


def test_the_agent_list_reports_live_queue_depth(client_as_owner, repo):
    r0 = client_as_owner.get("/api/agents").json()
    assert all(a["queue_depth"] == 0 for a in r0["agents"])

    _make_run(repo, 1, "queued")
    _make_run(repo, 2, "queued")
    r1 = client_as_owner.get("/api/agents").json()
    assert all(a["queue_depth"] == 2 for a in r1["agents"])


def test_the_agent_list_reports_who_is_working(client_as_owner, repo):
    _make_run(repo, 1, "running")
    repo.append_step(Step(id="st1", run_id="run_1", agent="cartographer", summary="mapping", at=1.0))
    agents = {a["agent"]: a for a in client_as_owner.get("/api/agents").json()["agents"]}
    assert agents["cartographer"]["state"] == "working"
    assert agents["author"]["state"] == "idle"


def test_pausing_the_fleet_is_owner_only(client_as_approver):
    r = client_as_approver.post("/api/agents/pause")
    assert r.status_code == 403


def test_owner_can_pause_and_resume_the_fleet(client_as_owner, repo):
    r = client_as_owner.post("/api/agents/pause")
    assert r.status_code == 200 and r.json()["paused"] is True
    assert repo.workspace("ws1").fleet_paused is True
    r2 = client_as_owner.post("/api/agents/resume")
    assert r2.json()["paused"] is False
    assert repo.workspace("ws1").fleet_paused is False


def test_a_demo_session_can_pause_and_resume_its_own_sandbox_fleet(client_demo):
    # A demo session's writes now land in its own real sandbox workspace
    # -- pausing/resuming the fleet is a genuine write there, not a
    # discarded one. See this task's report.
    me = client_demo.get("/api/auth/me").json()
    r = client_demo.post("/api/agents/pause")
    assert r.status_code == 200 and r.json()["paused"] is True
    r2 = client_demo.post("/api/agents/resume")
    assert r2.json()["paused"] is False
    assert client_demo.get("/api/agents").json()["paused"] is False
    assert me["workspace_id"].startswith("ws_demo_")


# --- policy -------------------------------------------------------------


def test_getting_policy_rules_returns_the_defaults_when_unconfigured(client_as_owner):
    r = client_as_owner.get("/api/policy/rules")
    body = r.json()
    assert body["policy_version"] == 14
    assert any(rule["tool"] == "pr.merge" for rule in body["rules"])


def test_editing_gate_rules_is_owner_only(client_as_approver):
    r = client_as_approver.put(
        "/api/policy/rules",
        json={"rules": [{"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "human"}]},
    )
    assert r.status_code == 403


def test_editing_gate_rules_bumps_the_policy_version(client_as_owner, repo):
    r = client_as_owner.put(
        "/api/policy/rules",
        json={"rules": [{"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "human"}]},
    )
    assert r.status_code == 200
    assert r.json()["policy_version"] == 15
    assert repo.workspace("ws1").policy_version == 15


def test_a_rule_edit_is_written_to_the_ledger(client_as_owner, ledger):
    client_as_owner.put(
        "/api/policy/rules",
        json={"rules": [{"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "human"}]},
    )
    entries = [e for e in ledger.entries("ws1") if e["action"] == "policy.rules_updated"]
    assert len(entries) == 1
    assert entries[0]["actor"].startswith("u_")


def test_a_malformed_rule_is_rejected_with_a_field_specific_400(client_as_owner):
    r = client_as_owner.put("/api/policy/rules", json={"rules": [{"tool": "pr.merge"}]})
    assert r.status_code == 400
    assert "rules[0]" in r.json()["detail"]


def test_gate_rules_cannot_grant_an_agent_a_tool_outside_its_scope(client_as_owner):
    r = client_as_owner.put(
        "/api/policy/rules",
        json={"rules": [{"tool": "pr.delete_repo", "pattern": "*", "effect": "allow"}]},
    )
    assert r.status_code == 400
    assert "not in scope" in r.json()["detail"]


def test_policy_decisions_lists_gateway_records(client_as_owner, ledger):
    ledger.append("ws1", "surgeon", "pr.merge", {"decision": "blocked", "reason": "gated", "target": "x"})
    r = client_as_owner.get("/api/policy/decisions")
    body = r.json()
    assert any(d["action"] == "pr.merge" for d in body["decisions"])
