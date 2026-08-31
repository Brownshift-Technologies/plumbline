"""Task 13: `job/worker.py`'s `main()` -- the Cloud Run Job entrypoint.

Every collaborator `main()` builds for real (`Repo`, `Gateway`, `Ledger`,
and -- inside `Orchestrator` itself, never exercised from here --
`GeminiModel`/`PlaywrightDriver`) is monkeypatched at the module level
rather than run for real: this file is about `main()`'s OWN control flow
(env var handling, exit codes, when `run.finished` is published) not about
re-testing the orchestrator (`tests/test_orchestrator.py` already owns
that) or standing up a real Firestore/Vertex/Playwright stack in a unit
test. `job.worker.Orchestrator` is replaced with a tiny stand-in whose
`execute(run_id)` returns (or raises) exactly what each test needs.
"""

import pytest

import job.worker as worker
from app.models import Run, Workspace
from app.settings import PlumblineConfig


def _config():
    return PlumblineConfig(
        project_id="test", location="us-central1", vertex_location="global",
        model="gemini-3.5-flash", firestore_prefix="plumbline",
    )


class _FakeOrchestrator:
    """Captures the kwargs `main()` built it with, and returns/raises
    whatever `outcome` says on `execute()`."""

    def __init__(self, outcome):
        self._outcome = outcome
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self

    def execute(self, run_id):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("PLUMBLINE_RUN_ID", raising=False)
    monkeypatch.setattr(worker, "load_settings", lambda: _config())
    monkeypatch.setattr(worker, "Repo", lambda config: object())
    monkeypatch.setattr(worker, "Ledger", lambda repo: object())
    monkeypatch.setattr(worker, "Gateway", lambda repo, ledger: object())


def _published(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker, "publish_event",
        lambda config, event_type, payload: calls.append((event_type, payload)),
    )
    return calls


def test_missing_run_id_exits_nonzero_before_touching_anything(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code != 0


def test_an_unknown_run_id_exits_nonzero_and_publishes_nothing(monkeypatch):
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "does-not-exist")
    published = _published(monkeypatch)
    monkeypatch.setattr(
        worker, "Orchestrator",
        _FakeOrchestrator(ValueError("no such run: 'does-not-exist'")),
    )
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code != 0
    assert published == []


def test_a_finished_run_exits_zero_and_publishes_run_finished(monkeypatch):
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "r1")
    published = _published(monkeypatch)
    run = Run(id="r1", workspace_id="ws1", number=1, trigger="manual", state="finished")
    monkeypatch.setattr(worker, "Orchestrator", _FakeOrchestrator(run))

    with pytest.raises(SystemExit) as exc:
        worker.main()

    assert exc.value.code == 0
    assert published == [("run.finished", {"run_id": "r1", "workspace_id": "ws1", "state": "finished"})]


def test_a_failed_run_exits_nonzero_but_still_publishes_run_finished(monkeypatch):
    # "Either way" -- the brief's own word. A failed run is exactly as
    # newsworthy to a subscriber as a finished one.
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "r1")
    published = _published(monkeypatch)
    run = Run(id="r1", workspace_id="ws1", number=1, trigger="manual", state="failed")
    monkeypatch.setattr(worker, "Orchestrator", _FakeOrchestrator(run))

    with pytest.raises(SystemExit) as exc:
        worker.main()

    assert exc.value.code != 0
    assert published == [("run.finished", {"run_id": "r1", "workspace_id": "ws1", "state": "failed"})]


def test_a_run_this_worker_never_claimed_exits_zero_and_publishes_nothing(monkeypatch):
    # execute() declined to (re-)run this id -- already claimed by another
    # worker, already terminal, or cancelled. This process caused no
    # transition, so it must not publish a run.finished of its own.
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "r1")
    published = _published(monkeypatch)
    run = Run(id="r1", workspace_id="ws1", number=1, trigger="manual", state="running")
    monkeypatch.setattr(worker, "Orchestrator", _FakeOrchestrator(run))

    with pytest.raises(SystemExit) as exc:
        worker.main()

    assert exc.value.code == 0
    assert published == []


def test_main_builds_the_orchestrator_with_a_model_and_browser_factory(monkeypatch):
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "r1")
    _published(monkeypatch)
    run = Run(id="r1", workspace_id="ws1", number=1, trigger="manual", state="finished")
    fake = _FakeOrchestrator(run)
    monkeypatch.setattr(worker, "Orchestrator", fake)

    with pytest.raises(SystemExit):
        worker.main()

    assert callable(fake.kwargs["model_factory"])
    assert callable(fake.kwargs["browser_factory"])


# --- Tier 2 (2026-08-30 contract, item 4): _browser_factory navigation ----


class _RecordingDriver:
    """Stands in for `agents.browser.PlaywrightDriver` -- records `start`/
    `goto` calls without ever touching a real Chromium, the same trade
    `tests/test_agent_base.py`'s own recording doubles make for `start`'s
    `chromium_sandbox=False` kwarg."""

    def __init__(self):
        self.calls: list[tuple] = []

    def start(self, playwright_factory=None):
        self.calls.append(("start",))

    def goto(self, url: str) -> None:
        self.calls.append(("goto", url))


def _fake_repo_for(workspace: Workspace):
    class _FakeRepo:
        def __init__(self, config):
            pass

        def run(self, run_id):
            return Run(id=run_id, workspace_id=workspace.id, number=1, trigger="manual")

        def workspace(self, workspace_id):
            return workspace if workspace_id == workspace.id else None

    return _FakeRepo


def test_browser_factory_navigates_to_the_workspaces_target_url(monkeypatch):
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "r1")
    ws = Workspace(id="ws1", name="Acme", repo="acme/site", target_url="https://acme.example.com")
    monkeypatch.setattr(worker, "Repo", _fake_repo_for(ws))
    driver = _RecordingDriver()
    monkeypatch.setattr("agents.browser.PlaywrightDriver", lambda: driver)

    result = worker._browser_factory()

    assert result is driver
    assert driver.calls == [("start",), ("goto", "https://acme.example.com")]


def test_browser_factory_does_not_navigate_when_target_url_is_unset(monkeypatch):
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "r1")
    ws = Workspace(id="ws1", name="Acme", repo="acme/site")  # target_url left at its default, ""
    monkeypatch.setattr(worker, "Repo", _fake_repo_for(ws))
    driver = _RecordingDriver()
    monkeypatch.setattr("agents.browser.PlaywrightDriver", lambda: driver)

    worker._browser_factory()

    assert driver.calls == [("start",)], "no target configured -- Cartographer's own check is what fails loudly"


def test_browser_factory_falls_back_to_target_url_for_a_named_environment(monkeypatch):
    # `env`, when Oracle names one, is accepted -- there is no per-
    # environment URL on `Workspace` yet, only names, so it resolves to
    # the same `target_url` every other environment does today.
    monkeypatch.setenv("PLUMBLINE_RUN_ID", "r1")
    ws = Workspace(id="ws1", name="Acme", repo="acme/site", environments=("production", "staging"),
                    target_url="https://acme.example.com")
    monkeypatch.setattr(worker, "Repo", _fake_repo_for(ws))
    driver = _RecordingDriver()
    monkeypatch.setattr("agents.browser.PlaywrightDriver", lambda: driver)

    worker._browser_factory("staging")

    assert driver.calls == [("start",), ("goto", "https://acme.example.com")]


def test_browser_factory_with_no_run_id_never_navigates(monkeypatch):
    # `_isolate` (autouse) already clears PLUMBLINE_RUN_ID -- this is the
    # driver-construction path Orchestrator would never actually reach
    # (main() fails fast before building one), exercised directly here so
    # `_resolve_navigation_target`'s own early return is proven, not
    # assumed.
    driver = _RecordingDriver()
    monkeypatch.setattr("agents.browser.PlaywrightDriver", lambda: driver)

    worker._browser_factory()

    assert driver.calls == [("start",)]
