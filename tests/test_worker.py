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
from app.models import Run
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
