"""Task 11c: Chaos."""

import pytest

from agents.author import Author
from agents.cartographer import Cartographer
from agents.chaos import Chaos, _TOXIC_CORPUS
from app.models import Run
from gateway.gateway import GatewayError
from tests.agent_fixtures import make_ctx


@pytest.fixture
def ctx():
    return make_ctx()


# --- from the brief -----------------------------------------------------


def test_it_injects_latency_into_staging(ctx):
    out = Chaos(target_env="staging").run(ctx)
    assert out.data["latency_ms"] > 0 and out.outcome == "ok"


def test_the_gateway_refuses_production_not_chaos_itself(ctx):
    with pytest.raises(GatewayError):
        Chaos(target_env="prod-eu-west-1").run(ctx)


def test_it_says_why_it_chose_that_latency(ctx):
    assert "p99" in Chaos(target_env="staging").run(ctx).detail


def test_latency_is_derived_from_p99_not_random(ctx):
    a = Chaos(target_env="staging").run(ctx).data["latency_ms"]
    b = Chaos(target_env="staging").run(ctx).data["latency_ms"]
    assert a == b, "same p99 must give the same latency, or runs are not reproducible"


def test_it_can_inject_an_error_rate(ctx):
    assert Chaos(target_env="staging", fault="error_rate").run(ctx).data["fault"] == "error_rate"


def test_toxic_input_comes_from_a_fixed_corpus(ctx):
    a = Chaos(target_env="staging", fault="toxic_input").run(ctx).data
    b = Chaos(target_env="staging", fault="toxic_input").run(ctx).data
    assert a["payloads"] == b["payloads"]


def test_a_preview_environment_is_allowed(ctx):
    assert Chaos(target_env="preview-2211").run(ctx).outcome == "ok"


# --- point 7: what a real system does past the bare contract ------------


def test_a_workspace_with_no_runs_yet_still_gets_a_usable_default_latency(ctx):
    """First-ever run against a brand new site: no `Run` history exists to
    derive a p99 from at all. This must not crash, and must not silently
    inject a nonsensical (zero, negative) latency -- it falls back to a
    documented default and says so."""
    out = Chaos(target_env="staging").run(ctx)
    assert out.data["latency_ms"] > 0
    assert "no observed p99 yet" in out.detail


def test_latency_scales_with_a_real_observed_p99_not_only_the_fallback(ctx):
    """Distinct from `test_latency_is_derived_from_p99_not_random`: this
    proves the p99 figure actually comes from this workspace's own `Run`
    history (Runner's own output), not merely that the same fallback
    constant is returned twice in a row."""
    for i, duration in enumerate([100, 200, 300, 900, 1000]):
        ctx.repo.put_run(Run(id=f"r{i}", workspace_id="ws1", number=i,
                              trigger="manual", duration_ms=duration))
    out = Chaos(target_env="staging").run(ctx)
    assert "observed p99 of 1000ms" in out.detail
    assert out.data["latency_ms"] == 2000


def test_a_fault_the_target_app_is_entirely_immune_to_is_still_a_successful_run(ctx):
    """Chaos's own job ends at injecting the fault -- whether the target
    app breaks under it is Runner/Sentinel's observation to make, not
    something Chaos itself checks. An app that turns out to be immune is
    real information (this fault kind needs to get more aggressive, or the
    upstream really is resilient), not a wasted or failed chaos run."""
    out = Chaos(target_env="staging", fault="error_rate").run(ctx)
    assert out.outcome == "ok"


def test_requesting_two_faults_at_once_is_rejected_before_it_ever_reaches_the_gateway():
    """Chaos injects exactly one fault per run (see agents/chaos.py's
    module docstring) -- a caller wanting two faults injected constructs
    two Chaos instances, not one asking for both. This must fail loudly at
    construction, not silently pick one or merge two payloads together."""
    with pytest.raises(ValueError):
        Chaos(target_env="staging", fault="latency,error_rate")


def test_a_reflected_toxic_payload_is_still_caught_downstream_by_cartographer_and_author():
    """A toxic_input corpus entry is deliberately injection-shaped (see the
    module docstring). If the target app reflects it back onto a later page
    -- an error message, an echoed form value -- and Cartographer crawls
    that page, the reflected string becomes an ACCESSIBLE NAME flowing into
    Author's own prompt. Chaos calls no model, so it screens nothing
    itself; this proves the fleet's existing site-derived payload screening
    (Author's own `payload={"elements_text": ...}`, already wired through
    `check_input`) is what actually stands between an injection-shaped
    toxic payload and a model prompt, once it comes back out of the app."""
    injection_payload = next(p for p in _TOXIC_CORPUS if "ignore" in p.lower())

    ctx = make_ctx(pages={
        "/": {"links": ["/reflected"]},
        "/reflected": {"a11y": [{"ref": "e1", "role": "button", "name": injection_payload}]},
    })
    Cartographer().run(ctx)

    with pytest.raises(GatewayError):
        Author().run(ctx)
