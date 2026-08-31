"""deploy.sh's own text, asserted on offline -- no gcloud, no GCP project,
no credentials. Task 18's brief names these three tests verbatim; the rest
guard the same GCP facts (see the plan's Global Constraints) that have
already cost this project real time once each.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).parents[1]
DEPLOY = (ROOT / "deploy.sh").read_text()
TEARDOWN = (ROOT / "teardown.sh").read_text()

# --- from the brief ---------------------------------------------------------


def test_gemini_is_pinned_to_the_global_location():
    assert "GEMINI_LOCATION=global" in DEPLOY


def test_no_regional_gemini_endpoint_creeps_in():
    assert (
        "gemini" not in DEPLOY.replace("GEMINI_LOCATION=global", "").lower()
        or "us-central1-aiplatform" not in DEPLOY
    )


def test_a_worker_job_is_created():
    assert "gcloud run jobs" in DEPLOY


# --- beyond the brief: the rest of the plan's Global Constraints -----------


def test_never_falls_back_to_gemini_2_5_flash():
    # The regional model that would silently pass the hackathon's version
    # gate for the wrong reason -- never wired in as an actual env var
    # value. (Naming it in a warning comment, the way core/config.py's own
    # module docstring does, is fine -- what matters is that
    # GEMINI_MODEL/--set-env-vars never assigns it.)
    assert "GEMINI_MODEL=gemini-2.5-flash" not in DEPLOY
    assert "GEMINI_MODEL:-gemini-2.5-flash" not in DEPLOY


def test_model_is_gemini_3_5_flash_not_pro():
    assert "gemini-3.5-flash" in DEPLOY
    assert "gemini-3.5-pro" not in DEPLOY
    assert "gemini-pro" not in DEPLOY.lower()


def test_healthz_is_never_the_deployed_probe_path():
    # Google's own Cloud Run frontend intercepts a literal /healthz before
    # it reaches the container -- this script must never reference it as
    # something to deploy, curl, or otherwise rely on.
    assert "/healthz" not in DEPLOY


def test_health_check_uses_the_real_path():
    assert "/_health" in DEPLOY


def test_api_core_pin_is_not_loosened_by_a_deploy_time_override():
    # deploy.sh must never pip-install or pin google-api-core itself --
    # pyproject.toml (tests/test_dependency_pins.py) is the one place that
    # pin lives.
    assert "google-api-core" not in DEPLOY


# --- cost discipline: scale-to-zero and a bounded blast radius -------------


def test_api_service_scales_to_zero():
    assert "--min-instances=0" in DEPLOY


def test_api_service_caps_max_instances():
    # Matches only where the flag is actually assigned a bare number (the
    # real gcloud invocation), not the prose in this script's own comments
    # explaining why. Never absent, never unbounded, never absurdly high.
    matches = re.findall(r"--max-instances=(\d+)", DEPLOY)
    assert matches, "--max-instances= is never assigned an actual number"
    assert all(1 <= int(v) <= 10 for v in matches)


def test_worker_job_bounds_retries_and_timeout():
    assert "--max-retries=" in DEPLOY
    assert "--task-timeout=" in DEPLOY


def test_oauth_state_secret_is_referenced_via_secret_manager_not_hardcoded():
    assert "OAUTH_STATE_SECRET" in DEPLOY
    assert "--set-secrets" in DEPLOY
    # The literal fixed dev fallback string from app/main.py must never
    # appear here -- referencing Secret Manager is the whole point.
    assert "DO-NOT-USE-IN-PRODUCTION" not in DEPLOY


def test_plumbline_env_is_not_test_or_dev_in_the_deployed_service():
    assert "PLUMBLINE_ENV=production" in DEPLOY


def test_job_name_matches_run_routes_run_job_name():
    # app/run_routes.py's `_RUN_JOB_NAME` constant, verbatim -- if this
    # drifts, every enqueued run silently targets a job that does not
    # exist.
    run_routes = (ROOT / "app" / "run_routes.py").read_text()
    assert '_RUN_JOB_NAME = "plumbline-worker"' in run_routes
    assert "plumbline-worker" in DEPLOY


def test_topic_name_matches_core_events_publish_event():
    # core.events.publish_event derives this topic name from the project
    # alone -- it is not configurable there, so it must not be
    # reconfigured here either.
    core_events = (ROOT / "core" / "events.py").read_text()
    assert "plumbline-events" in core_events
    assert "plumbline-events" in DEPLOY


def test_deploy_script_is_re_runnable_not_a_bare_create():
    # Every resource-creation step must be guarded, not a plain `create`
    # that errors on a second run.
    assert "gcloud firestore databases create" in DEPLOY
    assert "describe" in DEPLOY  # existence checks precede creation


def test_deploy_script_prints_the_service_url():
    assert "Service URL" in DEPLOY


# --- teardown.sh: the other half of "an idle project costs nothing" -------


def test_teardown_removes_the_cloud_run_service_and_job():
    assert "gcloud run services delete" in TEARDOWN
    assert "gcloud run jobs delete" in TEARDOWN


def test_teardown_removes_pubsub_and_the_secret():
    assert "gcloud pubsub subscriptions delete" in TEARDOWN
    assert "gcloud pubsub topics delete" in TEARDOWN
    assert "gcloud secrets delete" in TEARDOWN


def test_teardown_is_re_runnable_too():
    assert TEARDOWN.count("describe") >= 4
