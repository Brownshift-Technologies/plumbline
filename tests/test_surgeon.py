"""Task 12c: Surgeon.

Every fixture seeds a `Finding(status="triaged")`, the `Behaviour` Surgeon
resolves it through (see `agents/surgeon.py`'s `_spec_for_finding`), the
spec content, and a model response that is a unified diff. `spec_results`
on the fixture's `FakeBrowser` is what makes verification pass or fail --
a `dict` is returned on every call, a `list` is popped one result per call
(see `agents/browser.py`'s `FakeBrowser.run_spec` docstring), which is
exactly what "re-run the failing spec N times" needs.
"""

import pytest

from agents.surgeon import Surgeon
from app.models import Behaviour, Finding, Workspace
from gateway.gateway import GatewayError
from tests.agent_fixtures import make_ctx

_SPEC_PATH = "specs/checkout.spec.ts"
_SPEC_CONTENT = "test('checkout total is correct', async ({ page }) => { await page.goto('/checkout'); });"
_OTHER_SPEC_PATH = "specs/catalog.spec.ts"
_OTHER_SPEC_CONTENT = "test('catalog lists items', async ({ page }) => { await page.goto('/catalog'); });"

_GOOD_DIFF = (
    "--- a/src/checkout/total.ts\n"
    "+++ b/src/checkout/total.ts\n"
    "@@ -1,3 +1,3 @@\n"
    "-return price;\n"
    "+return price + tax;\n"
)
_PAYMENT_DIFF = (
    "--- a/src/checkout/payment-client.ts\n"
    "+++ b/src/checkout/payment-client.ts\n"
    "@@ -1,2 +1,2 @@\n"
    "-timeout = 1000;\n"
    "+timeout = 5000;\n"
)
_SPEC_EDITING_DIFF = (
    "--- a/specs/checkout.spec.ts\n"
    "+++ b/specs/checkout.spec.ts\n"
    "@@ -1,1 +1,1 @@\n"
    "-expect(total).toBe(50);\n"
    "+expect(total).toBe(49);\n"
)
_OUTSIDE_REPO_DIFF = (
    "--- a/config/../etc/passwd\n"
    "+++ b/config/../etc/passwd\n"
    "@@ -1,1 +1,1 @@\n"
    "-root:x:0:0\n"
    "+root:x:0:1\n"
)


def _base_ctx(
    spec_results, model_responses, *, route="/checkout", finding_id="f_catalog",
    spec_path=_SPEC_PATH, spec_content=_SPEC_CONTENT, other_results=None,
):
    ctx = make_ctx(
        spec_results={
            spec_path: spec_results,
            _OTHER_SPEC_PATH: other_results if other_results is not None else {"passed": True},
        },
        model_responses=model_responses,
    )
    ctx.repo.put_spec("ws1", spec_path, spec_content)
    ctx.repo.put_spec("ws1", _OTHER_SPEC_PATH, _OTHER_SPEC_CONTENT)
    ctx.repo.put_behaviour(Behaviour(
        id="b1", workspace_id="ws1", text="checkout total is correct",
        route=route, spec_path=spec_path,
    ))
    ctx.repo.put_finding(Finding(
        id=finding_id, workspace_id="ws1", title="Stale tax calculation drops the surcharge",
        route=route, found_by="triager", status="triaged", severity="high",
        seed="seed:ws1:checkout", repro_count=3,
    ))
    return ctx


@pytest.fixture
def ctx_payment_finding():
    return _base_ctx(
        [{"passed": True}] * 3, (_PAYMENT_DIFF,),
        route="/checkout/payment", finding_id="f_payment",
    )


@pytest.fixture
def ctx_catalog_finding():
    return _base_ctx([{"passed": True}] * 3, (_GOOD_DIFF,), finding_id="f_catalog")


@pytest.fixture
def ctx_ineffective_patch():
    # Still failing after the "fix" -- every reproduction attempt fails.
    return _base_ctx([{"passed": False, "error": "still $49"}] * 3, (_GOOD_DIFF,))


@pytest.fixture
def ctx_regressing_patch():
    # The target spec goes green, but the catalog spec -- previously fine
    # -- now fails: a real regression the patch introduced.
    return _base_ctx(
        [{"passed": True}] * 3, (_GOOD_DIFF,),
        other_results={"passed": False, "error": "catalog page 500s"},
    )


@pytest.fixture
def ctx_patch_touching_a_spec():
    return _base_ctx([{"passed": True}] * 3, (_SPEC_EDITING_DIFF,))


@pytest.fixture
def ctx_patch_outside_the_repo():
    return _base_ctx([{"passed": True}] * 3, (_OUTSIDE_REPO_DIFF,))


@pytest.fixture
def ctx_no_findings():
    return make_ctx()


# --- from the brief ------------------------------------------------------


def test_it_stops_at_a_payments_gate(ctx_payment_finding):
    out = Surgeon().run(ctx_payment_finding)
    assert out.data["gated"] is True and out.outcome == "gated"


def test_a_gate_saves_the_patch_awaiting_approval(ctx_payment_finding):
    Surgeon().run(ctx_payment_finding)
    patch = ctx_payment_finding.repo.patch_for_finding("f_payment")
    assert patch.gate_state == "awaiting_approval"


def test_a_gate_does_not_raise(ctx_payment_finding):
    Surgeon().run(ctx_payment_finding)   # must not raise


def test_it_opens_a_pull_request_outside_a_gate(ctx_catalog_finding):
    assert Surgeon().run(ctx_catalog_finding).data["pr_url"].startswith("https://")


def test_it_verifies_the_patch_before_proposing_it(ctx_catalog_finding):
    assert Surgeon().run(ctx_catalog_finding).data["verified"] is True


def test_a_patch_that_does_not_fix_the_failure_is_discarded(ctx_ineffective_patch):
    out = Surgeon().run(ctx_ineffective_patch)
    assert out.data["pr_url"] == "" and out.outcome == "failed"


def test_a_patch_that_breaks_another_spec_is_discarded(ctx_regressing_patch):
    assert Surgeon().run(ctx_regressing_patch).data["pr_url"] == ""


def test_it_refuses_to_edit_a_spec_file(ctx_patch_touching_a_spec):
    out = Surgeon().run(ctx_patch_touching_a_spec)
    assert out.data["pr_url"] == "" and "spec" in out.detail


# --- extra: judgement calls and fleet-wide rules -------------------------


def test_an_ineffective_patch_leaves_the_finding_open_for_another_attempt(ctx_ineffective_patch):
    Surgeon().run(ctx_ineffective_patch)
    findings = ctx_ineffective_patch.repo.findings_for_workspace("ws1")
    assert findings[0].status == "patch_failed"


def test_a_root_cause_naming_a_file_outside_the_repo_is_rejected(ctx_patch_outside_the_repo):
    # Point 5 of the task: a model can hallucinate a fix for a path outside
    # this repository as readily as a real one -- ".." must never be
    # trusted just because the diff parsed.
    out = Surgeon().run(ctx_patch_outside_the_repo)
    assert out.data["pr_url"] == "" and out.outcome == "failed"
    assert "outside this repository" in out.detail


def test_no_findings_to_patch_is_a_quiet_no_op(ctx_no_findings):
    out = Surgeon().run(ctx_no_findings)
    assert out.outcome == "ok" and out.data["pr_url"] == "" and out.data["gated"] is False


def test_a_denied_merge_surfaces_as_an_error_not_a_silent_skip(ctx_catalog_finding):
    # Fleet-wide rule: a policy BLOCK (not a human gate) must propagate,
    # never disappear as a quiet no-op -- distinct from the payments gate
    # above, which Surgeon is specifically required to swallow.
    ctx_catalog_finding.repo.put_workspace(Workspace(
        id="ws1", name="Acme", repo="acme/storefront",
        gate_rules=({"tool": "pr.merge", "pattern": "*", "effect": "deny"},)))
    with pytest.raises(GatewayError):
        Surgeon().run(ctx_catalog_finding)


def test_a_denied_write_surfaces_as_an_error_too(ctx_catalog_finding):
    ctx_catalog_finding.repo.put_workspace(Workspace(
        id="ws1", name="Acme", repo="acme/storefront",
        gate_rules=({"tool": "repo.write:src", "pattern": "*", "effect": "deny"},)))
    with pytest.raises(GatewayError):
        Surgeon().run(ctx_catalog_finding)


def test_it_carries_the_model_calls_prompt_for_inspection(ctx_catalog_finding):
    Surgeon().run(ctx_catalog_finding)
    assert "diff" in ctx_catalog_finding.model.calls[-1]["prompt"].lower()
