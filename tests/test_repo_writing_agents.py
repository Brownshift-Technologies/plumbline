"""Tier 2 (2026-08-30), Agent C's own contract item 2: the fleet-wide rule
shared by all three repo-writing agents, tested once, together, rather
than duplicated three times inside each agent's own test file.

`ctx.checkout is None` means no repo is connected -- a demo sandbox, or a
real workspace that has not connected one yet. Author, Healer, and
Surgeon each own their own real-file/real-PR tests
(`tests/test_author.py`, `tests/test_healer.py`, `tests/test_surgeon.py`);
this file is only for the one thing that is genuinely about all three at
once: every agent either runs, or explains why it did not -- never a
silent no-op, never a crash reaching for a repo that was never connected.
"""

from agents.author import Author
from agents.healer import Healer
from agents.surgeon import Surgeon
from app.models import Behaviour, Finding, Route
from tests.agent_fixtures import make_ctx


def test_no_agent_runs_when_there_is_no_checkout_but_each_says_why():
    ctx = make_ctx(checkout=None, model_responses=["should never be used"])
    ctx.repo.put_route(Route(id="r1", workspace_id="ws1", path="/checkout", coverage_pct=0))
    ctx.repo.put_spec("ws1", "specs/checkout.spec.ts",
                       "test('checkout', async ({ page }) => { await page.goto('/checkout'); });")
    ctx.repo.put_behaviour(Behaviour(
        id="b1", workspace_id="ws1", text="checkout works",
        route="/checkout", spec_path="specs/checkout.spec.ts"))
    ctx.repo.put_finding(Finding(
        id="f1", workspace_id="ws1", title="Checkout total is wrong",
        route="/checkout", found_by="triager", status="triaged", severity="high",
        seed="seed:ws1:checkout", repro_count=3,
    ))

    for agent in (Author(), Healer(), Surgeon()):
        out = agent.run(ctx)
        assert out.outcome == "skipped", f"{agent.name} should have skipped"
        assert out.detail, f"{agent.name} must explain why it did not run"
        assert "repo" in out.detail.lower() or "connect" in out.detail.lower(), \
            f"{agent.name}'s explanation should name the missing repo connection"

    # None of the three ever reached the model or wrote anything real --
    # a genuine no-op, not partial work with a misleading "skipped" label.
    assert ctx.model.calls == []
    assert [b.id for b in ctx.repo.behaviours_for_workspace("ws1")] == ["b1"], \
        "Author must not have added a second Behaviour row"
    assert ctx.repo.patch_for_finding("f1") is None
