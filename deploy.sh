#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Plumbline deploy: builds both images, deploys the API to Cloud Run and the
# run worker as a Cloud Run Job, and creates whatever GCP resources (the
# Firestore database, the Pub/Sub topic + push subscription, the Artifact
# Registry repo, the OAUTH_STATE_SECRET) are missing.
#
# Safe to re-run. Every resource-creation step below checks "does this
# already exist?" first, and the two deploy steps already use gcloud's own
# create-or-update verbs (`run deploy`, `run jobs deploy`) -- running this
# script twice in a row updates everything in place rather than erroring or
# duplicating anything.
#
# Cost discipline (the owner is paying for this project):
#   - `plumbline-api` runs `--min-instances=0` (scales to zero, costs
#     nothing while idle) with a low `--max-instances` cap, so a runaway
#     retry loop or a traffic spike cannot spin up hundreds of billable
#     instances.
#   - `plumbline-worker` is a Cloud Run Job: it is never "running" between
#     invocations, only while one execution is in flight, and
#     `--max-retries`/`--task-timeout` below bound how long and how many
#     times a single bad run can bill for.
#   - Firestore and Pub/Sub stay well inside free-tier volume at hackathon
#     scale; nothing here provisions a paid tier of either.
#   - `gemini-3.5-flash` (never `-pro`) is the only model these env vars
#     ever name, and GEMINI_LOCATION is pinned to `global` (see below).
#
# Usage: ./deploy.sh   (run from the plumbline/ repo root)
# -----------------------------------------------------------------------------

cd "$(dirname "${BASH_SOURCE[0]}")"

PROJECT="${GCP_PROJECT:-total-fiber-399801}"
REGION="${GCP_LOCATION:-us-central1}"

# `gemini-3.5-flash` is served ONLY on Vertex AI's `global` location --
# every regional endpoint (us-central1, us-east5, europe-west4, ...) 404s
# for it. This must stay `global`: never point it at a region, and never
# swap the model itself for `gemini-2.5-flash` (which *does* work
# regionally and would silently fail the hackathon's version gate instead
# of loudly 404ing). This value becomes GCP_VERTEX_LOCATION in both
# deployed services -- see core/config.py's own comment for why infra
# location (GCP_LOCATION, above) and Vertex model-access location are two
# genuinely separate fields that must never collapse into one.
GEMINI_LOCATION=global
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"

REPO_NAME="plumbline"
API_SERVICE="plumbline-api"
# Must match app/run_routes.py's `_RUN_JOB_NAME` exactly -- that constant is
# the job name `core.events.enqueue_job` is called with on every `POST
# /api/runs`.
JOB_NAME="plumbline-worker"
# Must match core/events.py's `publish_event`, which derives this topic
# name from the project alone -- it is not configurable there, so it is not
# configurable here either.
TOPIC="plumbline-events"
SUBSCRIPTION="plumbline-events-push"
SECRET_NAME="plumbline-oauth-state-secret"

AR_HOST="${REGION}-docker.pkg.dev"
API_IMAGE="${AR_HOST}/${PROJECT}/${REPO_NAME}/api:latest"
WORKER_IMAGE="${AR_HOST}/${PROJECT}/${REPO_NAME}/worker:latest"

echo "== Plumbline deploy: project=${PROJECT} region=${REGION} model=${GEMINI_MODEL} vertex_location=${GEMINI_LOCATION} =="

if [ ! -f "web/dist/index.html" ]; then
  echo "web/dist/index.html is missing -- run 'cd web && npm install && npm run build' first." >&2
  echo "app/production.py serves the API alone without it, but this deploy wants one URL for both." >&2
  exit 1
fi

# --- 1. Enable the APIs this deploy and the running app both need ----------
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  --project="${PROJECT}"

# --- 2. Firestore database (Native mode) ------------------------------------
if gcloud firestore databases describe --database='(default)' --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- Firestore database already exists"
else
  echo "-- creating Firestore database (Native mode, ${REGION})"
  gcloud firestore databases create --database='(default)' --location="${REGION}" \
    --type=firestore-native --project="${PROJECT}"
fi

# --- 3. Artifact Registry repo for both images ------------------------------
if gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- Artifact Registry repo already exists"
else
  echo "-- creating Artifact Registry repo ${REPO_NAME}"
  gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker --location="${REGION}" --project="${PROJECT}" \
    --description="Plumbline API and worker images"
fi
gcloud auth configure-docker "${AR_HOST}" --quiet

# --- 4. Pub/Sub topic --------------------------------------------------------
if gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- Pub/Sub topic already exists"
else
  echo "-- creating Pub/Sub topic ${TOPIC}"
  gcloud pubsub topics create "${TOPIC}" --project="${PROJECT}"
fi

# --- 5. OAUTH_STATE_SECRET, generated once and kept only in Secret Manager --
# `app/main.py`'s `build_app` refuses to start with no real
# OAUTH_STATE_SECRET when PLUMBLINE_ENV is not "test"/"dev" -- see that
# module's own comment on `_INSECURE_DEV_OAUTH_SECRET`. Both deployed
# services below set PLUMBLINE_ENV=production, so this MUST be set or
# neither container ever comes up. Generated here and referenced via
# `--set-secrets`, never written into an image or into this script.
if gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- OAUTH_STATE_SECRET already exists in Secret Manager (not rotating it)"
else
  echo "-- generating and storing OAUTH_STATE_SECRET in Secret Manager"
  openssl rand -hex 32 | gcloud secrets create "${SECRET_NAME}" \
    --data-file=- --replication-policy=automatic --project="${PROJECT}"
fi

# Both services run as the project's default Compute Engine service
# account unless one is configured with `--service-account` (this script
# does not, to keep the resource list this teardown.sh has to reverse as
# short as possible) -- so that is the identity that needs read access to
# the secret. Granting is idempotent: re-running this binding is a no-op
# if it is already in place.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
  --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor" >/dev/null

# --- 6. Build both images ----------------------------------------------------
# Two Dockerfiles, one build context (`.`) -- `-f` picks the file, so
# Dockerfile.worker's Chromium install never reaches the small API image
# and Dockerfile's static dashboard files never reach the worker image.
echo "-- building ${API_SERVICE} image (small, no browser)"
docker build -f Dockerfile -t "${API_IMAGE}" .
docker push "${API_IMAGE}"

echo "-- building ${JOB_NAME} image (installs Chromium -- this one is slow)"
docker build -f Dockerfile.worker -t "${WORKER_IMAGE}" .
docker push "${WORKER_IMAGE}"

# --- 7. Deploy the API to Cloud Run ------------------------------------------
# --min-instances=0: scales to zero, costs nothing while idle.
# --max-instances=3: caps a runaway loop or traffic spike at 3 billable
#   instances, never hundreds.
# --cpu=1 --memory=512Mi: the smallest workable shape -- this image never
#   launches a browser (see Dockerfile's own comment on why).
gcloud run deploy "${API_SERVICE}" \
  --project="${PROJECT}" --region="${REGION}" \
  --image="${API_IMAGE}" \
  --min-instances=0 --max-instances=3 \
  --cpu=1 --memory=512Mi --timeout=60 --concurrency=40 \
  --set-env-vars="PLUMBLINE_ENV=production,GCP_PROJECT=${PROJECT},GCP_LOCATION=${REGION},GCP_VERTEX_LOCATION=${GEMINI_LOCATION},GEMINI_MODEL=${GEMINI_MODEL}" \
  --set-secrets="OAUTH_STATE_SECRET=${SECRET_NAME}:latest" \
  --allow-unauthenticated

SERVICE_URL="$(gcloud run services describe "${API_SERVICE}" \
  --project="${PROJECT}" --region="${REGION}" --format='value(status.url)')"

# --- 8. Deploy the run worker as a Cloud Run Job -----------------------------
# --cpu=2 --memory=4Gi: this container carries Chromium and Node, and
#   `agents/browser.py` launches a real browser inside it. At the original
#   1 CPU / 1Gi it never got as far as printing anything: Cloud Run logged
#   "Application failed to start: The container may have exited abnormally"
#   with no container output at all, and every real run sat in `queued`
#   forever while the execution quietly failed. Chromium alone wants more
#   than a gigabyte before it has rendered a page. The job is short-lived
#   and billed per execution second, so a bigger shape for a few minutes
#   costs less than a small one that fails and retries.
# --tasks=1: one task per execution -- nothing about a Plumbline run is
#   parallelisable across tasks.
# --max-retries=1 --task-timeout=900s: bounds both how many times and how
#   long a single bad run execution can bill for. job/worker.py's own
#   module docstring documents why this is a short-lived, one-run-per-
#   process job rather than a warm server -- that design is exactly what
#   makes a hard timeout here safe rather than something that could cut off
#   legitimate long-running state.
gcloud run jobs deploy "${JOB_NAME}" \
  --project="${PROJECT}" --region="${REGION}" \
  --image="${WORKER_IMAGE}" \
  --cpu=2 --memory=4Gi \
  --tasks=1 --max-retries=1 --task-timeout=900s \
  --set-env-vars="PLUMBLINE_ENV=production,GCP_PROJECT=${PROJECT},GCP_LOCATION=${REGION},GCP_VERTEX_LOCATION=${GEMINI_LOCATION},GEMINI_MODEL=${GEMINI_MODEL}" \
  --set-secrets="OAUTH_STATE_SECRET=${SECRET_NAME}:latest"

# The API's own runtime identity is what calls `core.events.enqueue_job`
# (via `app.state.enqueue_job`, wired in `app/main.py`'s `build_app`) --
# without permission to start an execution of this job, the API deploys
# and boots fine, and every `POST /api/runs` silently fails to enqueue.
gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
  --project="${PROJECT}" --region="${REGION}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/run.invoker" >/dev/null

# --- 9. Pub/Sub push subscription, now that the service URL is known --------
if gcloud pubsub subscriptions describe "${SUBSCRIPTION}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- updating push subscription ${SUBSCRIPTION} -> ${SERVICE_URL}/events"
  gcloud pubsub subscriptions update "${SUBSCRIPTION}" \
    --push-endpoint="${SERVICE_URL}/events" --project="${PROJECT}"
else
  echo "-- creating push subscription ${SUBSCRIPTION} -> ${SERVICE_URL}/events"
  gcloud pubsub subscriptions create "${SUBSCRIPTION}" \
    --topic="${TOPIC}" --push-endpoint="${SERVICE_URL}/events" --project="${PROJECT}"
fi

echo "== done =="
echo "Service URL: ${SERVICE_URL}"
echo "Health check: curl ${SERVICE_URL}/_health"
