"""Task 12a: Runner.

Built on `tests.agent_fixtures.make_ctx`, the same convention every other
agent test in this codebase follows (see `tests/test_cartographer.py`'s
own docstring). Two things this file's own fixtures add on top of that
factory, because Runner is the one agent whose contract genuinely needs
them and no earlier agent did:

- `_ScriptedBrowser` -- a tiny double (not `agents.browser.FakeBrowser`)
  whose `run_spec` actually sleeps for a caller-chosen duration before
  returning a caller-chosen result. `FakeBrowser.run_spec` is
  instantaneous by design (see its own docstring); proving Runner's
  concurrency, its whole-batch timeout watchdog, and that its output stays
  sorted regardless of real completion order all need a driver that
  genuinely blocks for a measurable, controllable amount of wall-clock
  time, which no existing fixture in this codebase provides. Swapping
  `ctx.browser` for this double after `make_ctx` builds the rest of the
  context is exactly what `AgentContext.browser`'s own docstring describes
  as the point of typing that field `object`: agent code (and, here, test
  code) never has to know which concrete class it is holding.
- `ctx.serial_duration_ms`, set directly on the context object returned by
  `ctx_slow_specs` -- not part of `AgentContext`'s own fields, just a
  plain attribute a test fixture is free to hang off a mutable dataclass
  instance for its own test's use, the same way nothing stops a fixture
  from stashing extra state a caller only it needs to read back.
"""

import copy
import time

import pytest

from agents.runner import Runner
from tests.agent_fixtures import make_ctx

_SPEC_OK = "test('ok', async ({ page }) => { await page.goto('/'); });"
_UNLOADABLE = "this is not a playwright spec at all"


class _ScriptedBrowser:
    """`run_spec(path)` sleeps `scripts[path][0]` seconds, then returns a
    deep copy of `scripts[path][1]` -- the one thing `FakeBrowser` cannot
    do (see the module docstring)."""

    def __init__(self, scripts: dict[str, tuple[float, dict]]):
        self._scripts = scripts

    def run_spec(self, path: str) -> dict:
        delay, result = self._scripts[path]
        time.sleep(delay)
        return copy.deepcopy(result)


class _ExplodingBrowser:
    """Stands in for a driver that blows up mid-run (a real browser crash,
    a lost connection) -- as opposed to `_UNLOADABLE` content, which never
    reaches the browser at all. Both are `kind="crash"`, but via two
    entirely different code paths in `agents/runner.py`'s `_run_one`."""

    def run_spec(self, path: str) -> dict:
        raise RuntimeError("browser process crashed")


def _seed(ctx, paths_and_content: dict[str, str]):
    for path, content in paths_and_content.items():
        ctx.repo.put_spec("ws1", path, content)
    return ctx


@pytest.fixture
def ctx_with_specs():
    ctx = make_ctx(spec_results={
        "specs/a.spec.ts": {"passed": True},
        "specs/b.spec.ts": {"passed": True},
        "specs/c.spec.ts": {
            "passed": False, "status": "failed", "matcher": True,
            "error": "expect(locator).toBeVisible() failed",
        },
    })
    return _seed(ctx, {
        "specs/a.spec.ts": _SPEC_OK, "specs/b.spec.ts": _SPEC_OK, "specs/c.spec.ts": _SPEC_OK,
    })


@pytest.fixture
def ctx_many_failures():
    # Delays deliberately run OPPOSITE to alphabetical order, under
    # workers=4 (Runner's own default, so all four run genuinely
    # concurrently, none queued behind another) -- "delta" finishes first,
    # "alpha" finishes last, so the real completion order is the exact
    # reverse of spec-path order. If `Runner.run` ever forgot its own
    # explicit sort, this fixture is what would catch it.
    paths = ["specs/alpha.spec.ts", "specs/bravo.spec.ts", "specs/charlie.spec.ts", "specs/delta.spec.ts"]
    delays = [0.09, 0.06, 0.03, 0.0]
    scripts = {
        p: (d, {"passed": False, "status": "failed", "matcher": True, "error": f"assertion in {p}"})
        for p, d in zip(paths, delays)
    }
    ctx = make_ctx()
    _seed(ctx, {p: _SPEC_OK for p in paths})
    ctx.browser = _ScriptedBrowser(scripts)
    return ctx


@pytest.fixture
def ctx_selector_failure():
    ctx = make_ctx(spec_results={
        "specs/x.spec.ts": {
            "passed": False, "status": "failed", "matcher": False,
            "error": "no element matches getByRole('button', { name: 'Pay' })",
        },
    })
    return _seed(ctx, {"specs/x.spec.ts": _SPEC_OK})


@pytest.fixture
def ctx_assertion_failure():
    ctx = make_ctx(spec_results={
        "specs/x.spec.ts": {
            "passed": False, "status": "failed", "matcher": True,
            "error": "expect(locator).toBeVisible() failed",
        },
    })
    return _seed(ctx, {"specs/x.spec.ts": _SPEC_OK})


@pytest.fixture
def ctx_broken_spec():
    ctx = make_ctx()
    return _seed(ctx, {"specs/broken.spec.ts": _UNLOADABLE})


@pytest.fixture
def ctx_slow_specs():
    delay_s, n = 0.05, 8
    paths = [f"specs/s{i}.spec.ts" for i in range(n)]
    scripts = {p: (delay_s, {"passed": True}) for p in paths}
    ctx = make_ctx()
    _seed(ctx, {p: _SPEC_OK for p in paths})
    ctx.browser = _ScriptedBrowser(scripts)
    ctx.serial_duration_ms = int(n * delay_s * 1000)
    return ctx


# --- the brief's own eight ---------------------------------------------

def test_it_reports_what_held_and_what_failed(ctx_with_specs):
    out = Runner().run(ctx_with_specs)
    assert out.data["held"] == 2 and out.data["failed"] == 1


def test_it_uses_no_model_so_runs_are_reproducible(ctx_with_specs):
    ctx_with_specs.model = None
    assert Runner().run(ctx_with_specs).outcome in {"ok", "failed"}


def test_failures_come_back_in_a_deterministic_order(ctx_many_failures):
    a = [f["spec"] for f in Runner().run(ctx_many_failures).data["failures"]]
    b = [f["spec"] for f in Runner().run(ctx_many_failures).data["failures"]]
    assert a == b == sorted(a)


def test_it_classifies_a_locator_failure_as_selector(ctx_selector_failure):
    assert Runner().run(ctx_selector_failure).data["failures"][0]["kind"] == "selector"


def test_it_classifies_a_failed_expectation_as_assertion(ctx_assertion_failure):
    assert Runner().run(ctx_assertion_failure).data["failures"][0]["kind"] == "assertion"


def test_a_spec_that_cannot_load_is_a_crash_not_a_failure(ctx_broken_spec):
    out = Runner().run(ctx_broken_spec)
    assert out.data["failed"] == 0 and out.data["failures"][0]["kind"] == "crash"


def test_it_captures_artefacts_only_for_failures(ctx_with_specs):
    out = Runner().run(ctx_with_specs)
    assert len(out.data["failures"][0]["artefacts"]) == 4
    assert ctx_with_specs.repo.artefact_count() == 4


def test_it_runs_specs_concurrently(ctx_slow_specs):
    out = Runner(workers=4).run(ctx_slow_specs)
    assert out.data["duration_ms"] < ctx_slow_specs.serial_duration_ms


# --- point 8: what a real suite actually does ---------------------------

def test_it_handles_an_empty_spec_list():
    out = Runner().run(make_ctx())
    assert out.data["held"] == 0
    assert out.data["failed"] == 0
    assert out.data["failures"] == []
    assert out.outcome == "ok"


def test_it_handles_more_workers_than_specs():
    ctx = make_ctx(spec_results={"specs/only.spec.ts": {"passed": True}})
    _seed(ctx, {"specs/only.spec.ts": _SPEC_OK})
    out = Runner(workers=100).run(ctx)
    assert out.data["held"] == 1


def test_a_spec_that_hangs_does_not_hang_the_whole_run():
    ctx = make_ctx()
    _seed(ctx, {"specs/stuck.spec.ts": _SPEC_OK, "specs/fine.spec.ts": _SPEC_OK})
    ctx.browser = _ScriptedBrowser({
        "specs/stuck.spec.ts": (1.0, {"passed": True}),  # outlives the 0.1s budget below
        "specs/fine.spec.ts": (0.0, {"passed": True}),
    })
    start = time.monotonic()
    out = Runner(workers=2, spec_timeout_s=0.1).run(ctx)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, "the whole run waited on the one stuck spec instead of timing it out"
    stuck = next(f for f in out.data["failures"] if f["spec"] == "specs/stuck.spec.ts")
    assert stuck["kind"] == "timeout"
    assert out.data["held"] == 1  # "fine" still completed and counted


def test_two_failing_specs_each_get_their_own_artefacts():
    ctx = make_ctx(spec_results={
        "specs/one.spec.ts": {"passed": False, "status": "failed", "matcher": True, "error": "e1"},
        "specs/two.spec.ts": {"passed": False, "status": "failed", "matcher": True, "error": "e2"},
    })
    _seed(ctx, {"specs/one.spec.ts": _SPEC_OK, "specs/two.spec.ts": _SPEC_OK})
    out = Runner().run(ctx)
    assert ctx.repo.artefact_count() == 8, "both specs write a 'video'/'trace'/'har'/'console' artefact -- neither may overwrite the other's"
    one = next(f for f in out.data["failures"] if f["spec"] == "specs/one.spec.ts")
    two = next(f for f in out.data["failures"] if f["spec"] == "specs/two.spec.ts")
    assert len(one["artefacts"]) == len(two["artefacts"]) == 4
    assert set(one["artefacts"]).isdisjoint(two["artefacts"])


def test_a_pass_that_leaves_the_browser_on_a_different_page_does_not_affect_the_next_spec():
    ctx = make_ctx(
        pages={"/somewhere-else": {}},
        spec_results={
            "specs/a.spec.ts": {"passed": True},
            "specs/b.spec.ts": {
                "passed": False, "status": "failed", "matcher": False, "error": "no element matches",
            },
        },
    )
    _seed(ctx, {"specs/a.spec.ts": _SPEC_OK, "specs/b.spec.ts": _SPEC_OK})
    ctx.browser.goto("/somewhere-else")  # a prior spec left the shared browser elsewhere
    out = Runner().run(ctx)
    assert out.data["held"] == 1
    assert out.data["failures"][0]["kind"] == "selector"


def test_a_crashed_spec_gets_no_artefacts(ctx_broken_spec):
    out = Runner().run(ctx_broken_spec)
    assert out.data["failures"][0]["artefacts"] == []
    assert ctx_broken_spec.repo.artefact_count() == 0


def test_a_driver_exception_mid_run_is_a_crash_not_a_failure():
    ctx = make_ctx()
    _seed(ctx, {"specs/x.spec.ts": _SPEC_OK})
    ctx.browser = _ExplodingBrowser()
    out = Runner().run(ctx)
    assert out.data["failed"] == 0
    assert out.data["failures"][0]["kind"] == "crash"


# --- classification: structured signal first, string-matching as fallback --

def test_it_falls_back_to_string_matching_when_no_structured_fields_are_present():
    ctx = make_ctx(spec_results={
        "specs/x.spec.ts": {"passed": False, "error": "strict mode violation: locator resolved to 2 elements"},
    })
    _seed(ctx, {"specs/x.spec.ts": _SPEC_OK})
    assert Runner().run(ctx).data["failures"][0]["kind"] == "selector"


def test_an_unclassifiable_error_defaults_to_assertion_never_selector():
    # The fail-safe direction: Healer only ever repairs `selector`, so an
    # ambiguous failure must never be classified into the one bucket that
    # would get it auto-"fixed" instead of surfaced.
    ctx = make_ctx(spec_results={
        "specs/x.spec.ts": {"passed": False, "error": "some completely unrecognised failure"},
    })
    _seed(ctx, {"specs/x.spec.ts": _SPEC_OK})
    assert Runner().run(ctx).data["failures"][0]["kind"] == "assertion"


# --- one gateway call per logical act ------------------------------------

def test_it_makes_one_gateway_call_per_act_not_one_per_spec(ctx_with_specs, monkeypatch):
    calls = []
    real = ctx_with_specs.gateway.call
    monkeypatch.setattr(
        ctx_with_specs.gateway, "call",
        lambda *a, **k: (calls.append(a[2]), real(*a, **k))[1],
    )
    Runner().run(ctx_with_specs)
    assert calls.count("browser.drive") == 1
    assert calls.count("artefact.write") == 1
    assert len(calls) == 2, (
        "3 specs and 1 failure should still cost exactly two ledger entries, "
        "not one per spec or one per artefact"
    )
