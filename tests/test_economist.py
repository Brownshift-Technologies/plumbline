"""Task 12g: Economist.

`Behaviour.tags` carries this module's own "key:value" history convention
(see `agents/economist.py`'s module docstring for why no dedicated
history store exists yet): `"green_streak:N"`, `"repairs:N"`,
`"duration_ms:N"`, `"asserts:<fingerprint>"`. Provenance (fix round 1) is
NOT one of these -- `Behaviour.source` is its own durable field, set once
at creation, precisely so a caller reconstructing `tags` wholesale can
never accidentally un-protect a Sentinel-written behaviour.
"""

import pytest

from agents.economist import Economist
from app.models import Behaviour, Incident, Route, Workspace
from gateway.gateway import GatewayError
from gateway.policy import SCOPES
from tests.agent_fixtures import make_ctx


def _behaviour(id, route, spec_path, tags=(), status="active", source="author"):
    return Behaviour(id=id, workspace_id="ws1", text=f"covers {route}", route=route,
                      spec_path=spec_path, tags=tags, status=status, source=source)


def _ctx(behaviours, routes=(), incidents=()):
    ctx = make_ctx()
    for b in behaviours:
        ctx.repo.put_behaviour(b)
    for r in routes:
        ctx.repo.put_route(r)
    for i in incidents:
        ctx.repo.put_incident(i)
    return ctx


@pytest.fixture
def ctx_always_green():
    return _ctx(
        [_behaviour("b1", "/about", "specs/about.spec.ts", tags=("green_streak:40", "duration_ms:2000"))],
        routes=[Route(id="rt1", workspace_id="ws1", path="/about", coverage_pct=5)],
    )


@pytest.fixture
def ctx_flaky_history():
    return _ctx(
        [_behaviour("b1", "/search", "specs/search.spec.ts", tags=("repairs:5", "duration_ms:3000"))],
        routes=[Route(id="rt1", workspace_id="ws1", path="/search", coverage_pct=20)],
    )


@pytest.fixture
def ctx_duplicates():
    return _ctx(
        [
            _behaviour("b1", "/checkout", "specs/checkout-a.spec.ts", tags=("asserts:checkout-total",)),
            _behaviour("b2", "/checkout", "specs/checkout-b.spec.ts", tags=("asserts:checkout-total",)),
        ],
        routes=[Route(id="rt1", workspace_id="ws1", path="/checkout", coverage_pct=60)],
    )


@pytest.fixture
def ctx_any():
    return _ctx(
        [_behaviour("b1", "/about", "specs/about.spec.ts", tags=("green_streak:30", "duration_ms:1000"))],
        routes=[Route(id="rt1", workspace_id="ws1", path="/about", coverage_pct=5)],
    )


@pytest.fixture
def ctx_sentinel_behaviour():
    return _ctx(
        [_behaviour("b1", "/checkout/submit", "specs/sentinel-checkout.spec.ts",
                     tags=("green_streak:99", "incident"), source="sentinel")],
        routes=[Route(id="rt1", workspace_id="ws1", path="/checkout/submit", coverage_pct=15)],
    )


@pytest.fixture
def ctx_sentinel_behaviour_with_rewritten_tags():
    # Fix round 1: `tags` wholly reconstructed (as this module's own
    # history convention -- Healer/Runner appending green_streak/repairs --
    # legitimately would), carrying no "sentinel" string at all. Protection
    # must survive because it lives on `source`, not `tags`.
    return _ctx(
        [_behaviour("b1", "/checkout/submit", "specs/sentinel-checkout.spec.ts",
                     tags=("green_streak:99",), source="sentinel")],
        routes=[Route(id="rt1", workspace_id="ws1", path="/checkout/submit", coverage_pct=15)],
    )


@pytest.fixture
def ctx_slow_but_valuable():
    return _ctx(
        [
            _behaviour("b1", "/checkout", "specs/checkout-flow.spec.ts", tags=("duration_ms:600000",)),
            _behaviour("b2", "/about", "specs/about.spec.ts", tags=("duration_ms:1000",)),
        ],
        routes=[Route(id="rt1", workspace_id="ws1", path="/checkout", coverage_pct=95),
                Route(id="rt2", workspace_id="ws1", path="/about", coverage_pct=5)],
    )


@pytest.fixture
def ctx_no_history():
    return _ctx(
        [_behaviour("b1", "/about", "specs/about.spec.ts")],
        routes=[Route(id="rt1", workspace_id="ws1", path="/about", coverage_pct=5)],
    )


# --- from the brief -------------------------------------------------------


def test_a_behaviour_green_for_every_run_is_flagged_low_information(ctx_always_green):
    out = Economist().run(ctx_always_green)
    assert any(r["type"] == "never_failed" for r in out.data["recommendations"])


def test_a_chronically_repaired_behaviour_is_flagged_as_costly(ctx_flaky_history):
    out = Economist().run(ctx_flaky_history)
    assert any(r["type"] == "chronically_flaky" for r in out.data["recommendations"])


def test_two_behaviours_asserting_the_same_thing_are_flagged_redundant(ctx_duplicates):
    out = Economist().run(ctx_duplicates)
    assert any(r["type"] == "redundant" for r in out.data["recommendations"])


def test_every_recommendation_carries_both_time_saved_and_coverage_cost(ctx_any):
    out = Economist().run(ctx_any)
    assert out.data["recommendations"]
    for r in out.data["recommendations"]:
        assert isinstance(r["minutes_saved"], float) and isinstance(r["coverage_cost"], float)
        assert r["minutes_saved"] >= 0 and r["coverage_cost"] >= 0


def test_it_holds_no_write_scope(ctx_any):
    assert not any(tool.startswith(("repo.write", "pr.", "graph.write")) for tool in SCOPES["economist"])


def test_it_never_recommends_removing_a_sentinel_written_behaviour(ctx_sentinel_behaviour):
    out = Economist().run(ctx_sentinel_behaviour)
    assert out.data["recommendations"] == []


def test_protection_survives_a_wholesale_tags_rewrite(ctx_sentinel_behaviour_with_rewritten_tags):
    # Fix round 1: provenance lives on `source`, not on a "sentinel" string
    # inside `tags` -- a behaviour whose `tags` were entirely reconstructed
    # (carrying no such string at all) must still be protected.
    behaviour = ctx_sentinel_behaviour_with_rewritten_tags.repo.behaviours_for_workspace("ws1")[0]
    assert "sentinel" not in behaviour.tags and behaviour.source == "sentinel"
    out = Economist().run(ctx_sentinel_behaviour_with_rewritten_tags)
    assert out.data["recommendations"] == []


def test_a_slow_behaviour_that_covers_a_lot_is_not_flagged(ctx_slow_but_valuable):
    out = Economist().run(ctx_slow_but_valuable)
    assert not any(r["spec_path"] == "specs/checkout-flow.spec.ts" for r in out.data["recommendations"])


# --- extra: judgement calls and fleet-wide rules ---------------------------


def test_a_suite_with_no_history_produces_no_recommendations(ctx_no_history):
    out = Economist().run(ctx_no_history)
    assert out.data["recommendations"] == []
    assert out.data["minutes_saved"] == 0.0 and out.data["coverage_delta"] == 0.0


def test_a_never_failed_route_that_has_an_incident_is_not_flagged():
    ctx = _ctx(
        [_behaviour("b1", "/checkout", "specs/checkout.spec.ts", tags=("green_streak:99", "duration_ms:500"))],
        routes=[Route(id="rt1", workspace_id="ws1", path="/checkout", coverage_pct=40)],
        incidents=[Incident(id="inc1", workspace_id="ws1", source="sentry", message="500",
                             url="/checkout", count=1)],
    )
    out = Economist().run(ctx)
    assert not any(r["type"] == "never_failed" for r in out.data["recommendations"])


def test_economist_never_calls_a_write_tool_even_if_offered_one(ctx_flaky_history):
    # Belt and braces on top of the SCOPES-only assertion above: run the
    # agent and confirm nothing was ever written to the repo as a result.
    Economist().run(ctx_flaky_history)
    assert ctx_flaky_history.repo.behaviours_for_workspace("ws1")[0].status == "active"


def test_a_denied_read_surfaces_as_an_error_not_a_silent_skip(ctx_any):
    ctx_any.repo.put_workspace(Workspace(
        id="ws1", name="Acme", repo="acme/storefront",
        gate_rules=({"tool": "graph.read", "pattern": "*", "effect": "deny"},)))
    with pytest.raises(GatewayError):
        Economist().run(ctx_any)
