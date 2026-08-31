# Plumbline API image -- deployed to Cloud Run (`plumbline-api`, see
# deploy.sh). Fast cold start on purpose: `deploy.sh` runs this service with
# `--min-instances=0`, so every request after an idle period pays this
# image's own boot time, and Cloud Run bills that time.
#
# What this image deliberately does NOT do, and why:
#   - No `playwright install --with-deps chromium`. That step alone adds a
#     few hundred MB of browser binary plus its apt dependencies, and
#     nothing this image serves ever launches a browser -- `agents/browser.py`'s
#     `PlaywrightDriver` is only ever constructed by `job/worker.py`
#     (`Dockerfile.worker`, run as the Cloud Run Job `plumbline-worker`).
#     `playwright` the *pip package* still installs below (it is a normal
#     dependency in pyproject.toml, shared by both images per the plan's
#     Global Constraints), which is small; it is the browser binary that is
#     excluded here.
#   - `job/` is not copied in. Nothing under `app/`, `core/`, `gateway/`,
#     `seed/`, or `agents/` imports it (verified: no `from job` / `import job`
#     anywhere in those packages) -- it exists solely for
#     `Dockerfile.worker`'s entrypoint.
#
# `uv pip install --system -e .` installs from the pinned `pyproject.toml`
# exactly, notably `google-api-core>=2.34.0,<2.35.0` -- 2.35.0 percent-encodes
# Firestore paths and 400s every query (see tests/test_no_external_paths.py
# and tests/test_dependency_pins.py, which assert on this pin directly). Do
# not loosen it here or anywhere else.

FROM python:3.13-slim
WORKDIR /app

# Dependencies before source, so this layer is cache-friendly across code-only
# changes -- pyproject.toml (and uv.lock, if present) change far less often
# than app/core/gateway/seed/agents do.
COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir uv \
 && uv pip install --system -e .

# Only the packages the API actually imports (see the module docstring
# above for the "why" behind each inclusion/exclusion).
COPY app ./app
COPY core ./core
COPY gateway ./gateway
COPY seed ./seed
COPY agents ./agents
# The built dashboard, served same-origin by app/production.py -- see that
# module's own docstring for why this is a separate entrypoint from
# app/main.py rather than a change to it. `web/dist` must exist before
# `docker build` runs (`cd web && npm run build`); deploy.sh checks this
# and fails loudly, rather than silently, if it is missing.
COPY web/dist ./web/dist

ENV PORT=8080
# Cloud Run's own env var: `deploy.sh` overrides this at deploy time with
# `--set-env-vars`, but a value here means `docker run` alone (no gcloud)
# still boots into a real, non-test/dev deploy tier by default -- matching
# how this image is actually meant to be run. A genuinely local/dev run
# should pass `-e PLUMBLINE_ENV=dev` explicitly, the same way it would pass
# any other override.
ENV PLUMBLINE_ENV=production
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.production:app --host 0.0.0.0 --port ${PORT}"]
