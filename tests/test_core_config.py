import os

from core.config import load_config
from app.settings import load_settings


def test_load_config_uses_vertex_and_resolved_model():
    cfg = load_config(prefix="a11y")
    assert cfg.project_id == "total-fiber-399801"
    assert cfg.location == "us-central1"
    assert cfg.vertex_location == "global"
    assert cfg.model == "gemini-3.5-flash"
    assert cfg.firestore_prefix == "a11y"


def test_load_config_reads_env_overrides(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "other-project")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")
    cfg = load_config(prefix="x")
    assert cfg.project_id == "other-project"
    assert cfg.model == "gemini-test-model"


def test_load_config_reads_vertex_location_override(monkeypatch):
    monkeypatch.setenv("GCP_VERTEX_LOCATION", "us-east5")
    cfg = load_config(prefix="x")
    assert cfg.vertex_location == "us-east5"


def test_load_config_exports_vertex_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    cfg = load_config(prefix="x")
    # GOOGLE_GENAI_USE_VERTEXAI=true is the mandatory hackathon gate: without
    # it google-genai talks to the public Gemini API instead of Vertex.
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == cfg.project_id
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == cfg.vertex_location
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"


def test_load_config_overwrites_ambient_cloud_location(monkeypatch):
    """Regression guard for the production bug.

    `adk deploy cloud_run --region us-central1` sets
    GOOGLE_CLOUD_LOCATION=us-central1 inside the container. load_config must
    overwrite it with the Vertex location, or gemini-3.5-flash 404s on the
    regional host at runtime.
    """
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    cfg = load_config(prefix="x")
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == cfg.vertex_location


def test_load_config_env_export_honours_vertex_location_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GCP_VERTEX_LOCATION", "us-east5")
    cfg = load_config(prefix="x")
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "us-east5"
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == cfg.vertex_location


def test_load_config_overwrites_ambient_cloud_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "stale-ambient-project")
    monkeypatch.setenv("GCP_PROJECT", "other-project")
    cfg = load_config(prefix="x")
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "other-project"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == cfg.project_id


def test_plumbline_settings_keep_the_global_vertex_location(monkeypatch):
    monkeypatch.delenv("GCP_VERTEX_LOCATION", raising=False)
    cfg = load_settings()
    assert cfg.vertex_location == "global", (
        "gemini-3.5-flash is served only on Vertex location 'global'.")


def test_plumbline_settings_keep_the_gated_model(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert load_settings().model == "gemini-3.5-flash"


def test_plumbline_settings_carry_a_firestore_prefix():
    assert load_settings().firestore_prefix == "plumbline"
