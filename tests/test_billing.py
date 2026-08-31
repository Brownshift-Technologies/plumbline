"""Task 14c: `GET /api/billing`, `POST /api/billing/plan`, and the run-limit
interplay from the billing side (`test_a_workspace_at_its_run_limit_cannot_
start_a_run`, required by this task's own brief even though the route it
exercises belongs to `app/run_routes.py` -- see task-14a-15-report.md)."""

import pytest


@pytest.fixture(autouse=True)
def stub_enqueue(app):
    app.state.enqueue_job = lambda job_name, args: "op/fake"


def test_billing_reports_both_meters(client_as_owner):
    r = client_as_owner.get("/api/billing")
    body = r.json()
    assert body["plan"] == "team"
    assert body["runs_used"] == 0 and body["run_limit"] == 500
    assert body["seat_limit"] == 5
    assert body["seats_used"] == 1  # the owner fixture's own membership


def test_billing_reports_the_fields_the_frontend_actually_reads(client_as_owner):
    """Fix round 1: `BillingInfo` (web/src/lib/types.ts) and
    `BillingPane.tsx` read a FLAT shape -- `renews_at`/`interval` were
    missing entirely, and everything else lived one level down under a
    `meters` key nothing in the frontend ever looked for. This checks the
    real, shipped contract field by field rather than just the two
    explicitly-named-missing ones."""
    body = client_as_owner.get("/api/billing").json()
    assert set(body) == {
        "plan", "price", "interval", "renews_at", "runs_used", "run_limit",
        "seats_used", "seat_limit", "payment_method",
    }
    assert body["interval"] == "monthly"
    assert isinstance(body["renews_at"], (int, float)) and body["renews_at"] > 0
    assert body["price"] == 490
    assert body["payment_method"] == ""


def test_a_workspace_at_its_run_limit_cannot_start_a_run(client_at_limit):
    r = client_at_limit.post("/api/runs", json={})
    assert r.status_code == 402


def test_changing_plan_is_owner_only(client_as_approver):
    r = client_as_approver.post("/api/billing/plan", json={"plan": "scale"})
    assert r.status_code == 403


def test_owner_can_change_plan(client_as_owner, repo):
    r = client_as_owner.post("/api/billing/plan", json={"plan": "scale"})
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "scale" and body["seats"] == 20
    assert repo.workspace("ws1").plan == "scale"


def test_changing_to_an_unknown_plan_is_400(client_as_owner):
    r = client_as_owner.post("/api/billing/plan", json={"plan": "enterprise-plus-plus"})
    assert r.status_code == 400


def test_a_demo_session_change_plan_is_refused_with_a_reason(client_demo):
    # Billing/payment stays refused even in a demo session's own sandbox
    # -- see this task's report's "must stay refused" list -- but with a
    # reason explaining why, not the old bare "nothing was saved".
    r = client_demo.post("/api/billing/plan", json={"plan": "scale"})
    body = r.json()
    assert body["demo"] is True and body["persisted"] is False
    assert body["reason"]
