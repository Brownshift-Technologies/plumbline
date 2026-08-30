"""Task 12f: Oracle.

`make_ctx(browsers={...})` (fix round 1's addition to `tests/agent_fixtures.py`)
builds one `FakeBrowser` per named environment on `ctx.browsers`. Every
fixture here seeds `"baseline"` and `"candidate"`, and a `Route` so
`graph.read` has something to hand back -- Oracle falls back to `["/"]`
with no routes seeded, so most fixtures skip that and rely on the
fallback, keeping the page dicts keyed by `"/"`.
"""

import pytest

from agents.oracle import Oracle
from app.models import Route, Workspace
from tests.agent_fixtures import make_ctx

_PAY_BUTTON = {"ref": "e1", "role": "button", "name": "Pay"}


def _ctx(baseline_page: dict, candidate_page: dict, route="/"):
    return make_ctx(browsers={"baseline": {route: baseline_page}, "candidate": {route: candidate_page}})


@pytest.fixture
def ctx_identical():
    page = {"a11y": [_PAY_BUTTON], "text": "Welcome back", "status": 200}
    return _ctx(dict(page), dict(page))


@pytest.fixture
def ctx_missing_button():
    return _ctx({"a11y": [_PAY_BUTTON], "status": 200}, {"a11y": [], "status": 200})


@pytest.fixture
def ctx_timestamps():
    return _ctx(
        {"text": "Last updated 2026-08-30T12:00:00Z", "status": 200},
        {"text": "Last updated 2026-08-30T12:00:05Z", "status": 200},
    )


@pytest.fixture
def ctx_session_ids():
    return _ctx(
        {"text": "Session sess_abc123XY", "status": 200},
        {"text": "Session sess_zzz999QR", "status": 200},
    )


@pytest.fixture
def ctx_volatile_rule():
    return _ctx(
        {"text": "Order order-12345 confirmed", "status": 200},
        {"text": "Order order-67890 confirmed", "status": 200},
    )


@pytest.fixture
def ctx_two_divergences():
    ctx = make_ctx(browsers={
        "baseline": {
            "/checkout/payment": {"a11y": [_PAY_BUTTON], "status": 200},
            "/footer": {"text": "© 2025", "status": 200},
        },
        "candidate": {
            "/checkout/payment": {"a11y": [], "status": 200},
            "/footer": {"text": "© 2026", "status": 200},
        },
    })
    ctx.repo.put_route(Route(id="rt1", workspace_id="ws1", path="/checkout/payment", coverage_pct=10))
    ctx.repo.put_route(Route(id="rt2", workspace_id="ws1", path="/footer", coverage_pct=10))
    return ctx


@pytest.fixture
def ctx_500_on_candidate():
    return _ctx({"status": 200}, {"status": 500})


@pytest.fixture
def ctx_status_and_content_diverge():
    # A route whose status, text, AND a11y all differ between environments
    # -- pre-fix, this alone produced up to 3 separate divergences for one
    # route. Status diverging should short-circuit the rest.
    return _ctx(
        {"status": 200, "text": "OK", "a11y": [_PAY_BUTTON]},
        {"status": 500, "text": "Internal Server Error", "a11y": []},
    )


@pytest.fixture
def ctx_environment_down():
    # The reviewer's own reproduction (fix round 1, IMPORTANT): 20 routes,
    # baseline healthy and candidate uniformly 500ing -- a total outage of
    # the candidate environment, not 20 unrelated bugs.
    n = 20
    baseline_pages = {
        f"/r{i}": {"status": 200, "text": f"Page {i}",
                    "a11y": [{"ref": f"e{i}", "role": "button", "name": f"Action {i}"}]}
        for i in range(n)
    }
    candidate_pages = {f"/r{i}": {"status": 500, "text": "Internal Server Error"} for i in range(n)}
    ctx = make_ctx(browsers={"baseline": baseline_pages, "candidate": candidate_pages})
    for i in range(n):
        ctx.repo.put_route(Route(id=f"rt{i}", workspace_id="ws1", path=f"/r{i}", coverage_pct=10))
    return ctx


# --- from the brief -------------------------------------------------------


def test_identical_environments_produce_no_divergences(ctx_identical):
    assert Oracle("baseline", "candidate").run(ctx_identical).data["divergences"] == []


def test_a_missing_button_in_the_candidate_is_a_divergence(ctx_missing_button):
    out = Oracle("baseline", "candidate").run(ctx_missing_button)
    assert any(d["type"] == "missing_element" for d in out.data["divergences"])


def test_a_timestamp_difference_is_not_a_divergence(ctx_timestamps):
    assert Oracle("baseline", "candidate").run(ctx_timestamps).data["divergences"] == []


def test_a_session_id_difference_is_not_a_divergence(ctx_session_ids):
    assert Oracle("baseline", "candidate").run(ctx_session_ids).data["divergences"] == []


def test_a_configured_volatile_pattern_is_ignored(ctx_volatile_rule):
    out = Oracle("baseline", "candidate", volatile=[r"order-\d+"]).run(ctx_volatile_rule)
    assert out.data["divergences"] == []


def test_divergences_rank_by_blast_radius(ctx_two_divergences):
    out = Oracle("baseline", "candidate").run(ctx_two_divergences)
    assert out.data["divergences"][0]["route"] == "/checkout/payment"


def test_a_divergence_never_becomes_an_auto_patch(ctx_missing_button):
    Oracle("baseline", "candidate").run(ctx_missing_button)
    assert ctx_missing_button.repo.findings_for_workspace("ws1") == []


def test_a_status_code_change_is_a_divergence(ctx_500_on_candidate):
    out = Oracle("baseline", "candidate").run(ctx_500_on_candidate)
    assert any(d["type"] == "status_code_changed" for d in out.data["divergences"])


# --- extra: judgement calls and fleet-wide rules ---------------------------


def test_a_500_on_candidate_ranks_critical_regardless_of_route(ctx_500_on_candidate):
    # Point 5's judgement call: a candidate 500ing where baseline is fine
    # must never read as low priority just because the route is "/".
    out = Oracle("baseline", "candidate").run(ctx_500_on_candidate)
    assert out.data["divergences"][0]["severity"] == "critical"


def test_a_volatile_configured_pattern_does_not_disable_the_built_in_ones(ctx_timestamps):
    # A caller-supplied `volatile` list is additive to the brief's own
    # default set, never a replacement for it.
    out = Oracle("baseline", "candidate", volatile=[r"unrelated-pattern"]).run(ctx_timestamps)
    assert out.data["divergences"] == []


def test_oracle_holds_no_write_scope():
    from gateway.policy import SCOPES
    assert not any(tool.startswith(("repo.write", "pr.", "graph.write")) for tool in SCOPES["oracle"])


# --- fix round 1: consolidation (IMPORTANT) --------------------------------


def test_a_status_divergence_skips_the_sub_comparisons(ctx_status_and_content_diverge):
    out = Oracle("baseline", "candidate").run(ctx_status_and_content_diverge)
    assert len(out.data["divergences"]) == 1
    assert out.data["divergences"][0]["type"] == "status_code_changed"


def test_a_total_outage_produces_one_finding_not_hundreds(ctx_environment_down):
    # Pre-fix, this scenario returned 20 status + 20 text + 20 network +
    # up to 100 element divergences -- 160 entries that all say the same
    # one thing. Post-fix: one environment-wide finding.
    out = Oracle("baseline", "candidate").run(ctx_environment_down)
    assert out.data["compared"] == 20
    assert len(out.data["divergences"]) == 1
    divergence = out.data["divergences"][0]
    assert divergence["type"] == "environment_wide:status_code_changed"
    assert divergence["baseline"] == 200 and divergence["candidate"] == 500
    assert len(divergence["affected_routes"]) == 20
    assert divergence["severity"] == "critical"


def test_an_isolated_divergence_is_never_rolled_up(ctx_two_divergences):
    # Two DIFFERENT signatures on two routes must never meet the majority
    # threshold -- roll-up is for "this is a fact about the environment",
    # not a way to compress a genuinely isolated regression away.
    out = Oracle("baseline", "candidate").run(ctx_two_divergences)
    assert len(out.data["divergences"]) == 2
    assert all(d["route"] != "*" for d in out.data["divergences"])


# --- fix round 1: policy-deny surfaces as an error (MINOR) -----------------


def test_a_denied_read_surfaces_as_an_error_not_a_silent_skip(ctx_missing_button):
    from gateway.gateway import GatewayError
    ctx_missing_button.repo.put_workspace(Workspace(
        id="ws1", name="Acme", repo="acme/storefront",
        gate_rules=({"tool": "browser.read", "pattern": "*", "effect": "deny"},)))
    with pytest.raises(GatewayError):
        Oracle("baseline", "candidate").run(ctx_missing_button)
