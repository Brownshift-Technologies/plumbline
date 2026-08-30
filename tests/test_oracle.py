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
from app.models import Route
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
