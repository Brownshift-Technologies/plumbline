#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Removes every GCP resource deploy.sh creates, in the reverse order they
# were created, so an idle Plumbline project costs nothing at all -- not
# "scaled to zero", actually gone.
#
# Safe to re-run: every delete below is guarded by "does this exist?" and
# uses --quiet, so a resource already removed (by a previous run of this
# script, or by hand) is skipped rather than erroring the whole script out.
#
# Usage: ./teardown.sh   (run from the plumbline/ repo root)
# -----------------------------------------------------------------------------

cd "$(dirname "${BASH_SOURCE[0]}")"

PROJECT="${GCP_PROJECT:-total-fiber-399801}"
REGION="${GCP_LOCATION:-us-central1}"

REPO_NAME="plumbline"
API_SERVICE="plumbline-api"
JOB_NAME="plumbline-worker"
TOPIC="plumbline-events"
SUBSCRIPTION="plumbline-events-push"
SECRET_NAME="plumbline-oauth-state-secret"

echo "== Plumbline teardown: project=${PROJECT} region=${REGION} =="

# --- 1. Pub/Sub subscription, then topic (subscription must go first) ------
if gcloud pubsub subscriptions describe "${SUBSCRIPTION}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- deleting subscription ${SUBSCRIPTION}"
  gcloud pubsub subscriptions delete "${SUBSCRIPTION}" --project="${PROJECT}" --quiet
else
  echo "-- subscription ${SUBSCRIPTION} already gone"
fi

if gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- deleting topic ${TOPIC}"
  gcloud pubsub topics delete "${TOPIC}" --project="${PROJECT}" --quiet
else
  echo "-- topic ${TOPIC} already gone"
fi

# --- 2. Cloud Run Job ---------------------------------------------------------
if gcloud run jobs describe "${JOB_NAME}" --project="${PROJECT}" --region="${REGION}" >/dev/null 2>&1; then
  echo "-- deleting Cloud Run Job ${JOB_NAME}"
  gcloud run jobs delete "${JOB_NAME}" --project="${PROJECT}" --region="${REGION}" --quiet
else
  echo "-- Cloud Run Job ${JOB_NAME} already gone"
fi

# --- 3. Cloud Run service ------------------------------------------------------
if gcloud run services describe "${API_SERVICE}" --project="${PROJECT}" --region="${REGION}" >/dev/null 2>&1; then
  echo "-- deleting Cloud Run service ${API_SERVICE}"
  gcloud run services delete "${API_SERVICE}" --project="${PROJECT}" --region="${REGION}" --quiet
else
  echo "-- Cloud Run service ${API_SERVICE} already gone"
fi

# --- 4. Secret Manager secret ---------------------------------------------------
if gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- deleting secret ${SECRET_NAME}"
  gcloud secrets delete "${SECRET_NAME}" --project="${PROJECT}" --quiet
else
  echo "-- secret ${SECRET_NAME} already gone"
fi

# --- 5. Artifact Registry repo (both images live in one repo) ------------------
if gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "-- deleting Artifact Registry repo ${REPO_NAME} (and every image in it)"
  gcloud artifacts repositories delete "${REPO_NAME}" --location="${REGION}" --project="${PROJECT}" --quiet
else
  echo "-- Artifact Registry repo ${REPO_NAME} already gone"
fi

# --- 6. Firestore database -----------------------------------------------------
# Left for last, and NOT deleted by default: this is the one resource here
# that holds actual data (every workspace, run, finding, patch...), and
# Firestore itself costs nothing while idle -- unlike the Cloud Run
# service/job above, there is no ongoing charge from leaving it in place.
# Pass --delete-firestore to also drop the database and everything in it.
if [ "${1:-}" = "--delete-firestore" ]; then
  if gcloud firestore databases describe --database='(default)' --project="${PROJECT}" >/dev/null 2>&1; then
    echo "-- deleting Firestore database (--delete-firestore was passed)"
    gcloud firestore databases delete --database='(default)' --project="${PROJECT}" --quiet
  else
    echo "-- Firestore database already gone"
  fi
else
  echo "-- leaving the Firestore database in place (it is free while idle;"
  echo "   pass --delete-firestore to this script to also remove it and its data)"
fi

echo "== done =="
