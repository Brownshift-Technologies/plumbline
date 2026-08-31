"""Task 14b: finding and patch routes, with the approval gate."""

import pytest

from app.models import Finding, Patch

_PAYMENTS_FILE = "src/checkout/payment-client.ts"


def _make_finding(repo, workspace_id="ws1", fid="f1", title="A retried payment charges the customer twice",
                   route="/checkout/payment", status="triaged") -> Finding:
    finding = Finding(id=fid, workspace_id=workspace_id, title=title, route=route,
                       found_by="chaos", status=status)
    repo.put_finding(finding)
    return finding


def _make_patch(repo, finding_id="f1", files=(_PAYMENTS_FILE,), gate_state="awaiting_approval") -> Patch:
    patch = Patch(
        id=f"patch_{finding_id}", finding_id=finding_id, diff="--- a/x\n+++ b/x\n",
        files=files, added=7, removed=2, verified=True,
        pr_url="https://github.com/example/repo/pull/2211", gate_state=gate_state,
    )
    repo.put_patch(patch)
    return patch


def _non_payments(repo, fid="f2"):
    _make_finding(repo, fid=fid, title="Cart total drifts a cent", route="/cart")
    return _make_patch(repo, finding_id=fid, files=("src/cart/total.ts",))


@pytest.fixture(autouse=True)
def payments_finding(repo):
    _make_finding(repo)
    return _make_patch(repo)


# --- the approval gate ------------------------------------------------------


def test_a_reader_cannot_approve_a_patch(client_as_reader):
    r = client_as_reader.post("/api/findings/f1/patch/approve")
    assert r.status_code == 403


def test_an_approver_cannot_approve_a_payments_patch(client_as_approver):
    r = client_as_approver.post("/api/findings/f1/patch/approve")
    assert r.status_code == 403


def test_an_owner_without_totp_cannot_approve_a_payments_patch(client_owner_no_totp):
    r = client_owner_no_totp.post("/api/findings/f1/patch/approve")
    assert r.status_code == 403
    assert "totp" in r.json()["detail"].lower()


def test_an_owner_with_totp_can_approve_a_payments_patch(client_as_owner, repo):
    r = client_as_owner.post("/api/findings/f1/patch/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["already_approved"] is False
    assert repo.patch_for_finding("f1").gate_state == "merged"


def test_an_approver_can_approve_a_non_payments_patch(client_as_approver, repo):
    _non_payments(repo)
    r = client_as_approver.post("/api/findings/f2/patch/approve")
    assert r.status_code == 200
    assert repo.patch_for_finding("f2").gate_state == "merged"


def test_approving_twice_opens_only_one_pull_request(client_as_owner, repo):
    first = client_as_owner.post("/api/findings/f1/patch/approve").json()
    second = client_as_owner.post("/api/findings/f1/patch/approve").json()
    assert first["already_approved"] is False
    assert second["already_approved"] is True
    assert first["pr_url"] == second["pr_url"] == "https://github.com/example/repo/pull/2211"


def test_approving_writes_the_approver_id_to_the_ledger(client_as_owner, ledger):
    client_as_owner.post("/api/findings/f1/patch/approve")
    approve_entries = [e for e in ledger.entries("ws1") if e["action"] == "patch.approve"]
    assert len(approve_entries) == 1
    # The acting user's id -- not "surgeon" or any other agent name. See
    # the module docstring for why this goes through `ledger.append`
    # directly rather than `Gateway.call`.
    assert approve_entries[0]["actor"].startswith("u_")


def test_a_demo_session_can_approve_the_gated_patch(client_demo, repo):
    # `payments_finding`'s autouse fixture seeds "f1" into "ws1", not this
    # demo session's own sandbox -- so the finding/patch this test approves
    # is seeded directly into the demo's real `workspace_id` instead,
    # exactly the shape `seed/demo.py`'s own gated `finding_double_charge`
    # takes in production.
    ws_id = client_demo.get("/api/auth/me").json()["workspace_id"]
    _make_finding(repo, workspace_id=ws_id, fid="demo_f1")
    _make_patch(repo, finding_id="demo_f1")

    r = client_demo.post("/api/findings/demo_f1/patch/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "demo" not in body
    assert repo.patch_for_finding("demo_f1").gate_state == "merged"


def test_approving_a_patch_whose_finding_was_already_resolved_still_works(client_as_owner, repo):
    finding = repo.findings_for_workspace("ws1")[0]
    repo.put_finding(type(finding)(**{**finding.__dict__, "status": "accepted"}))
    r = client_as_owner.post("/api/findings/f1/patch/approve")
    assert r.status_code == 200
    assert repo.patch_for_finding("f1").gate_state == "merged"


# --- reject / request changes ------------------------------------------------


def test_rejecting_without_a_note_is_400(client_as_owner):
    r = client_as_owner.post("/api/findings/f1/patch/reject", json={"note": "short"})
    assert r.status_code == 400


def test_rejecting_carries_the_note_to_the_ledger(client_as_owner, ledger, repo):
    r = client_as_owner.post(
        "/api/findings/f1/patch/reject", json={"note": "This breaks the refund flow entirely."}
    )
    assert r.status_code == 200
    entries = [e for e in ledger.entries("ws1") if e["action"] == "patch.reject"]
    assert len(entries) == 1
    assert entries[0]["detail"]["note"] == "This breaks the refund flow entirely."
    assert repo.patch_for_finding("f1").gate_state == "rejected"
    assert repo.findings_for_workspace("ws1")[0].status == "triaged"


def test_a_reader_cannot_reject_a_patch(client_as_reader):
    r = client_as_reader.post("/api/findings/f1/patch/reject", json={"note": "not good enough at all"})
    assert r.status_code == 403


def test_a_demo_session_can_reject_a_patch_in_its_own_sandbox(client_demo, repo):
    ws_id = client_demo.get("/api/auth/me").json()["workspace_id"]
    _make_finding(repo, workspace_id=ws_id, fid="demo_f1")
    _make_patch(repo, finding_id="demo_f1")

    r = client_demo.post("/api/findings/demo_f1/patch/reject", json={"note": "not good enough at all"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "demo" not in body
    assert repo.patch_for_finding("demo_f1").gate_state == "rejected"


def test_requesting_changes_requires_a_substantive_note(client_as_owner):
    r = client_as_owner.post("/api/findings/f1/patch/changes", json={"note": "no"})
    assert r.status_code == 400


def test_requesting_changes_is_recorded(client_as_owner, repo, ledger):
    r = client_as_owner.post(
        "/api/findings/f1/patch/changes", json={"note": "Please add a regression test too."}
    )
    assert r.status_code == 200
    assert repo.patch_for_finding("f1").gate_state == "changes_requested"
    assert any(e["action"] == "patch.request_changes" for e in ledger.entries("ws1"))


# --- listing / detail ---------------------------------------------------


def test_findings_filter_by_status(client_as_owner, repo):
    _make_finding(repo, fid="f-triaged", title="Order history paginates past the last page",
                  route="/account/orders", status="needs_repro")
    r = client_as_owner.get("/api/findings", params={"status": "needs_repro"})
    body = r.json()
    assert body["total"] == 1
    assert body["findings"][0]["id"] == "f-triaged"


def test_getting_a_finding_from_another_workspace_is_404(client_as_owner, repo):
    _make_finding(repo, workspace_id="ws-other", fid="f-other")
    r = client_as_owner.get("/api/findings/f-other")
    assert r.status_code == 404


def test_getting_a_patch_for_a_finding_with_none_is_404(client_as_owner, repo):
    _make_finding(repo, fid="f-nopatch", title="Nothing to patch here", route="/x")
    r = client_as_owner.get("/api/findings/f-nopatch/patch")
    assert r.status_code == 404


def test_accepting_a_finding(client_as_owner, repo):
    _make_finding(repo, fid="f-accept", title="Admin pricing table sorts unstably", route="/admin/pricing")
    r = client_as_owner.post("/api/findings/f-accept/accept")
    assert r.status_code == 200
    assert repo.findings_for_workspace("ws1")[0].status in ("accepted", "triaged")
    found = next(f for f in repo.findings_for_workspace("ws1") if f.id == "f-accept")
    assert found.status == "accepted"


def test_snoozing_a_finding(client_as_owner, repo):
    _make_finding(repo, fid="f-snooze", title="Cart total drifts a cent", route="/cart")
    r = client_as_owner.post("/api/findings/f-snooze/snooze")
    assert r.status_code == 200
    found = next(f for f in repo.findings_for_workspace("ws1") if f.id == "f-snooze")
    assert found.status == "snoozed"
