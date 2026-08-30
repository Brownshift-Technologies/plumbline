import json

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core.config import load_config
from core.telemetry import log_event, setup_telemetry, span


def test_span_is_recorded_with_attributes():
    exporter = InMemorySpanExporter()
    setup_telemetry(load_config(prefix="t"), "test-service", span_processor=SimpleSpanProcessor(exporter))
    with span("agent.step", tool="verify"):
        pass
    recorded = exporter.get_finished_spans()
    assert recorded[-1].name == "agent.step"
    assert recorded[-1].attributes["tool"] == "verify"


def test_span_records_exception_and_reraises():
    exporter = InMemorySpanExporter()
    setup_telemetry(load_config(prefix="t"), "test-service", span_processor=SimpleSpanProcessor(exporter))
    try:
        with span("agent.fail"):
            raise ValueError("boom")
    except ValueError:
        pass
    recorded = exporter.get_finished_spans()
    assert recorded[-1].status.status_code.name == "ERROR"


def test_log_event_emits_structured_json(capsys):
    log_event("run.started", run_id="r1", repo="acme/site")
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["event"] == "run.started"
    assert payload["run_id"] == "r1"
    assert payload["severity"] == "INFO"
