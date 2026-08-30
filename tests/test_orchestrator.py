"""Task 13: the Orchestrator.

Task 13's own brief predates Sentinel, Auditor, Oracle, and Economist -- it
only ever sequenced Cartographer, Author, Healer, Chaos, Runner, Triager,
and Surgeon, and its own literal example tests assert on exactly that
seven-agent list. `job/orchestrator.py`'s module docstring lays out where
the four newer agents now sit and why; the tests below assert on THAT
eleven-agent sequence (nine real steps plus Oracle's own explicit skip
step, in the base fixture -- Sentinel is silently absent when no incident
is open, matching its own precondition) rather than the brief's original,
now-stale seven-item list.

Two testing strategies live side by side here, deliberately:

- `orch`/`run` drive the REAL eleven-agent fleet against a small,
  hand-built one-page site -- proof the actual wiring (Repo, Gateway,
  FakeModel, FakeBrowser, and every real agent class) holds together
  end to end, not just that `Orchestrator`'s own control flow is correct
  in isolation. See the fixture's own comment for exactly what happens to
  each agent and why.
- Every other fixture below builds a `_StubAgent` sequence instead, and
  monkeypatches `Orchestrator._execute_sequence` to call `orch._step(...)`
  directly against however many stub agents that one test needs. This
  reuses `_step`'s REAL error-handling/Step-writing logic (nothing about
  gate/block/error classification is re-implemented here) while making
  each edge case (a broken agent, a gate, a block, an unexpected error, an
  empty site, a clean run) trivial to construct and fully deterministic --
  none of Cartographer/Author/Healer/.../Surgeon's own internal complexity
  has to be reverse-engineered into a script of FakeModel/FakeBrowser
  responses just to reach one orchestrator-level edge case.
"""

import pytest

from agents.base import AgentResult
from agents.browser import FakeBrowser
from app.models import Incident, Run, Workspace
from app.repo import Repo
from app.settings import PlumblineConfig
from core.fakes import FakeFirestore, FakeModel
from gateway.gateway import Gateway, GatewayError
from gateway.ledger import Ledger
from job.orchestrator import Orchestrator

_CONFIG = PlumblineConfig(
    project_id="test", location="us-central1", vertex_location="global",
    model="gemini-3.5-flash", firestore_prefix="plumbline",
)

# A minimal, valid Playwright spec Author's own `_is_valid` accepts on the
# first try: contains "test(", "await", no test.only/test.skip, and the
# route ("/") itself (via the goto call).
_VALID_SPEC = "test('home', async () => {\n  await page.goto('/');\n});"


class _StubAgent:
    """A minimal `Agent` (agents/base.py's structural Protocol): a `name`
    and a `run(ctx)`. Scripted to either return a fixed `AgentResult` or
    raise a fixed exception, so an orchestrator-level test can construct
    exactly the shape of agent behaviour it needs without touching any of
    the eleven real agents' own internals."""

    def __init__(self, name, outcome="ok", raises=None):
        self.name = name
        self._outcome = outcome
        self._raises = raises

    def run(self, ctx):
        if self._raises is not None:
            raise self._raises
        return AgentResult(summary=f"{self.name} ran", outcome=self._outcome)


def _repo():
    return Repo(_CONFIG, client=FakeFirestore())


@pytest.fixture
def repo():
    return _repo()


@pytest.fixture
def run(repo):
    repo.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site"))
    r = Run(id="r1", workspace_id="ws1", number=1, trigger="manual")
    repo.put_run(r)
    return r


def _bare_orch(repo, *, pages=None, spec_results=None, model_responses=()):
    gateway = Gateway(repo, Ledger(repo))
    return Orchestrator(
        repo=repo, gateway=gateway,
        model_factory=lambda: FakeModel(list(model_responses)),
        browser_factory=lambda env=None: FakeBrowser(pages or {}, spec_results or {}),
    )


def _stub_sequence(orch, agents):
    """Monkeypatch `orch` to run exactly `agents`, in order, through the
    real `_step` (real Step-writing, real gate/block/error handling) --
    the shared plumbing every fixture below that does not need the real
    fleet builds on."""

    def sequence(ctx, run):
        for agent in agents:
            orch._step(agent, ctx, run)

    orch._execute_sequence = sequence
    return orch


# --- the real fleet, end to end -------------------------------------------


@pytest.fixture
def orch(repo):
    """The real eleven-agent fleet against one page ("/", one button), with
    just enough scripted to walk every agent through to a real `Step`
    without needing a full green-to-red-to-merged pipeline:

    - Cartographer maps "/" (one route) for real.
    - Auditor audits it for real (no model calls of its own).
    - Oracle is skipped-with-a-reason: `ws1` has no `environments`
      configured.
    - Author drafts one spec for "/" -- the ONE scripted model response
      that passes `_is_valid` on the first try.
    - The spec is scripted (`spec_results`) to fail as an ASSERTION every
      time it runs (`matcher=True`, not selector-shaped) -- Healer's own
      rule is to leave an assertion failure alone entirely, so it makes
      zero model calls and zero repairs.
    - Chaos injects a `"latency"` fault into `"staging"`, which
      `gateway/policy.py`'s DEFAULT_RULES always allow.
    - Runner actually runs the one spec, sees it fail (assertion), and
      reports it in `data["failures"]`.
    - The orchestrator hands Triager exactly that one failing spec path
      (ruling 2). It reproduces identically five times (deterministic,
      not a flake), calls the model once for a root cause (the SECOND
      scripted response), and writes a `Finding(status="triaged")`.
    - Surgeon reads that finding. Triager always writes `route=""` on a
      Finding (a documented gap in `agents/triager.py`/`agents/surgeon.py`
      -- see the latter's own module docstring), so Surgeon can never
      resolve a spec path for it; its model call (the THIRD and final
      scripted response) returns prose with no unified-diff header at
      all, so `_blast_radius_violation` rejects it outright, before any
      verification or write -- one deterministic model call, no browser
      calls, no gateway writes.
    - Economist reads the (untagged) suite and recommends nothing.

    Exactly three model calls happen, in this order, across the whole
    run -- `FakeModel` asserts on any unscripted fourth call, so this
    fixture is itself a regression guard on that count.
    """
    pages = {"/": {"a11y": [{"role": "button", "name": "Buy"}], "links": []}}
    spec_results = {"specs/home.spec.ts": {"passed": False, "matcher": True, "error": "expected 42, got 41"}}
    model_responses = [
        _VALID_SPEC,
        "The total price calculation is off by one dollar.",
        "I could not determine a safe, minimal fix for this failure.",
    ]
    return _bare_orch(repo, pages=pages, spec_results=spec_results, model_responses=model_responses)


def test_it_runs_the_fleet_in_order(orch, run):
    orch.execute(run.id)
    agents = [s.agent for s in orch._repo.steps_for_run(run.id)]
    assert agents == [
        "cartographer", "auditor", "oracle", "author", "healer", "chaos",
        "runner", "triager", "surgeon", "economist",
    ]


def test_oracle_is_skipped_with_a_reason_when_theres_no_second_environment(orch, run):
    orch.execute(run.id)
    steps = {s.agent: s for s in orch._repo.steps_for_run(run.id)}
    assert steps["oracle"].outcome == "skipped"
    assert "second environment" in steps["oracle"].detail


def test_the_run_finishes_and_bills_exactly_one_run(orch, run):
    before = orch._repo.workspace("ws1").runs_used
    result = orch.execute(run.id)
    assert result.state == "finished"
    assert orch._repo.workspace("ws1").runs_used == before + 1


def test_every_step_is_written_as_it_happens_not_at_the_end(orch, run):
    seen_counts = []

    def on_step(step):
        # By the time this fires, the Step this callback was just handed
        # must already be persisted -- proving a per-agent write, not a
        # batch flushed once at the very end of execute().
        seen_counts.append(len(orch._repo.steps_for_run(run.id)))

    orch._on_step = on_step
    orch.execute(run.id)
    assert seen_counts == list(range(1, len(seen_counts) + 1))
    assert len(seen_counts) == 10


def test_chaos_can_see_this_workspaces_own_history_because_the_orchestrator_calls_put_run(repo):
    # Ruling 1: the orchestrator itself is what makes Chaos's observed-p99
    # branch reachable in production at all. Seed one PRIOR finished run
    # with a real duration for ws1, then prove a SECOND run's Chaos step
    # reads it (rather than silently falling back to the documented
    # default every time, which is what happens with no put_run caller at
    # all -- see agents/chaos.py's own module docstring).
    repo.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site"))
    repo.put_run(Run(id="r0", workspace_id="ws1", number=1, trigger="manual",
                      state="finished", duration_ms=5000))
    run = Run(id="r1", workspace_id="ws1", number=2, trigger="manual")
    repo.put_run(run)

    orch = _bare_orch(repo, pages={"/": {"links": []}}, model_responses=[_VALID_SPEC])
    orch.execute(run.id)
    chaos_step = next(s for s in orch._repo.steps_for_run(run.id) if s.agent == "chaos")
    assert "observed p99" in chaos_step.detail
    assert "5000" in chaos_step.detail


# --- stub-driven edge cases -------------------------------------------


def test_one_agent_failing_does_not_lose_the_earlier_steps(repo, run):
    orch = _stub_sequence(_bare_orch(repo), [
        _StubAgent("cartographer"), _StubAgent("author"), _StubAgent("healer"),
        _StubAgent("chaos", raises=RuntimeError("boom")),
        _StubAgent("runner"), _StubAgent("triager"), _StubAgent("surgeon"),
    ])
    orch.execute(run.id)
    agents = [s.agent for s in orch._repo.steps_for_run(run.id)]
    assert agents == ["cartographer", "author", "healer", "chaos"]
    assert orch._repo.run(run.id).state == "failed"


def test_a_gated_surgeon_leaves_the_run_finished_not_failed(repo, run):
    orch = _stub_sequence(_bare_orch(repo), [
        _StubAgent("cartographer"), _StubAgent("author"), _StubAgent("healer"),
        _StubAgent("chaos"), _StubAgent("runner"), _StubAgent("triager"),
        _StubAgent("surgeon", raises=GatewayError("needs a human", needs_human=True)),
        _StubAgent("economist"),  # must never run
    ])
    orch.execute(run.id)
    assert orch._repo.run(run.id).state == "finished"
    steps = orch._repo.steps_for_run(run.id)
    assert steps[-1].agent == "surgeon" and steps[-1].outcome == "gated"
    assert "economist" not in [s.agent for s in steps]


def test_a_surgeon_that_self_reports_gated_also_leaves_the_run_finished(repo, run):
    # Surgeon's own real code never raises GatewayError for a pr.merge
    # gate -- it catches it and returns AgentResult(outcome="gated", ...)
    # normally (see agents/surgeon.py). _step must treat that identically
    # to the raised-exception path above.
    orch = _stub_sequence(_bare_orch(repo), [
        _StubAgent("cartographer"),
        _StubAgent("surgeon", outcome="gated"),
        _StubAgent("economist"),  # must never run
    ])
    orch.execute(run.id)
    assert orch._repo.run(run.id).state == "finished"
    agents = [s.agent for s in orch._repo.steps_for_run(run.id)]
    assert agents == ["cartographer", "surgeon"]


def test_a_blocked_chaos_does_not_stop_the_runner(repo, run):
    orch = _stub_sequence(_bare_orch(repo), [
        _StubAgent("cartographer"),
        _StubAgent("chaos", raises=GatewayError("production is denied", needs_human=False)),
        _StubAgent("runner"),
    ])
    orch.execute(run.id)
    agents = [s.agent for s in orch._repo.steps_for_run(run.id)]
    assert "runner" in agents
    chaos_step = next(s for s in orch._repo.steps_for_run(run.id) if s.agent == "chaos")
    assert chaos_step.outcome == "blocked"
    assert orch._repo.run(run.id).state == "finished"


def test_an_unexpected_error_records_the_type_not_the_traceback(repo, run):
    orch = _stub_sequence(_bare_orch(repo), [
        _StubAgent("cartographer", raises=ValueError("kaboom, secret=sk-abc123")),
    ])
    orch.execute(run.id)
    step = orch._repo.steps_for_run(run.id)[-1]
    assert step.outcome == "error"
    assert step.detail == "ValueError"
    assert "Traceback" not in step.detail
    assert "sk-abc123" not in step.detail
    assert orch._repo.run(run.id).state == "failed"


def test_a_run_is_counted_once_even_when_it_fails(repo, run):
    before = repo.workspace("ws1").runs_used
    orch = _stub_sequence(_bare_orch(repo), [_StubAgent("cartographer", raises=RuntimeError("boom"))])
    orch.execute(run.id)
    assert repo.workspace("ws1").runs_used == before + 1


def test_an_agent_that_returns_none_is_a_recorded_error_not_a_crash(repo, run):
    class _NoneAgent:
        name = "cartographer"

        def run(self, ctx):
            return None

    orch = _stub_sequence(_bare_orch(repo), [_NoneAgent()])
    orch.execute(run.id)
    step = orch._repo.steps_for_run(run.id)[-1]
    assert step.outcome == "error"
    assert orch._repo.run(run.id).state == "failed"


def test_no_routes_stops_early_and_says_why(repo, run):
    # The REAL Cartographer, pointed at a page that 404s -- a genuinely
    # empty crawl, not a stub standing in for one.
    orch = _bare_orch(repo, pages={"/": {"error": "404 not found"}})
    orch.execute(run.id)
    assert orch._repo.run(run.id).state == "finished"
    steps = orch._repo.steps_for_run(run.id)
    assert len(steps) == 1
    assert steps[0].agent == "cartographer"
    assert "unreachable" in steps[0].detail


def test_a_clean_run_skips_triager_and_surgeon(repo, run):
    # The real fleet again, but the one spec PASSES outright -- Runner
    # reports zero failures, so there is nothing for Triager to reproduce.
    pages = {"/": {"a11y": [{"role": "button", "name": "Buy"}], "links": []}}
    spec_results = {"specs/home.spec.ts": {"passed": True}}
    orch = _bare_orch(repo, pages=pages, spec_results=spec_results, model_responses=[_VALID_SPEC])
    orch.execute(run.id)
    agents = [s.agent for s in orch._repo.steps_for_run(run.id)]
    assert "triager" not in agents and "surgeon" not in agents
    assert "runner" in agents and "economist" in agents
    assert orch._repo.run(run.id).state == "finished"


# --- Sentinel: conditional on an open incident ----------------------------


def test_sentinel_runs_first_when_an_incident_is_open(repo, run):
    repo.put_incident(Incident(id="inc1", workspace_id="ws1", source="sentry",
                                message="checkout crashed", url="/checkout", status="open"))
    orch = _bare_orch(repo, pages={"/": {"error": "404"}})
    orch.execute(run.id)
    agents = [s.agent for s in orch._repo.steps_for_run(run.id)]
    assert agents[0] == "sentinel"


def test_sentinel_is_silently_absent_with_no_open_incidents(repo, run):
    orch = _bare_orch(repo, pages={"/": {"error": "404"}})
    orch.execute(run.id)
    agents = [s.agent for s in orch._repo.steps_for_run(run.id)]
    assert "sentinel" not in agents


# --- Oracle: conditional on a second environment --------------------------


def test_oracle_runs_when_a_second_environment_is_configured(repo, run):
    repo.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site",
                                  environments=("production", "staging")))
    orch = _bare_orch(repo, pages={"/": {"links": []}}, model_responses=[_VALID_SPEC])
    orch.execute(run.id)
    steps = {s.agent: s for s in orch._repo.steps_for_run(run.id)}
    assert steps["oracle"].outcome != "skipped"
    assert "0 divergence" in steps["oracle"].summary or "Compared" in steps["oracle"].summary


# --- runs_used, claim_run, and the "already running" cases ----------------


def test_execute_is_a_no_op_for_a_run_that_is_not_queued(repo):
    repo.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/site"))
    already_running = Run(id="r_running", workspace_id="ws1", number=1, trigger="manual", state="running")
    repo.put_run(already_running)
    before = repo.workspace("ws1").runs_used

    orch = _bare_orch(repo)
    result = orch.execute("r_running")
    assert result.state == "running"
    assert repo.workspace("ws1").runs_used == before  # not billed again
    assert orch._repo.steps_for_run("r_running") == []  # never ran the fleet


def test_execute_raises_for_an_unknown_run_id(repo):
    orch = _bare_orch(repo)
    with pytest.raises(ValueError):
        orch.execute("does-not-exist")


def test_a_run_whose_workspace_was_deleted_mid_flight_fails_cleanly(repo):
    # No put_workspace at all -- the run exists, its workspace does not.
    run = Run(id="r1", workspace_id="ws_gone", number=1, trigger="manual")
    repo.put_run(run)

    orch = _bare_orch(repo)
    result = orch.execute("r1")
    assert result.state == "failed"
