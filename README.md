# Plumbline

An agentic software testing and reliability platform. Eleven scoped agents map a customer's
app, write behaviours against it in plain English, repair them as the UI drifts, break the
app on purpose to find what nobody tested, run everything in parallel with no model in the
loop (determinism is the point), root-cause the failures, and open a verified pull request —
stopping at a human approval gate before anything merges.

Built for the **All Things Agentic** hackathon (Google / Devpost), category **Fortified
Enterprise Fleet**, also entered for **Startup Excellence**.

## Try it

**https://plumbline-api-cxotjai2ta-uc.a.run.app** — click **Open the live demo**. No
account, no Google sign-in, nothing to install.

The demo issues a real session against a *per-session sandbox*: your own workspace, seeded
with routes, runs, findings and a patch waiting at an approval gate. It is writable — create
a behaviour, approve the gated patch, start a run — and nothing you do is visible to anyone
else clicking the same link. It behaves like an account: come back later, in the same
browser, and you land in the same sandbox with your behaviours, runs and approvals still
in it. Sandboxes nobody has opened for a year are collected; one you keep using never is.

What the demo cannot do, by design: reach a real repository, a live environment, or an
outbound webhook. For that, connect a GitHub repo in **Settings** and Plumbline clones it,
writes real spec files, and opens a real pull request on a branch — never on your default
branch, and never merged without a human.

See `ARCHITECTURE.md` for the full design and the diagram (`plumbline-architecture.svg`),
`DEMO_SCRIPT.md` for the video script, and `BLOG.md` for how it was built.

## Provenance

`core/` (`config`, `store`, `events`, `gemini`, `guards`, `telemetry`, `web`) is Plumbline's
own code, but it did not start life here. It began as `agentic-substrate`, written earlier in
this same hackathon to back three other, separate submissions. Rather than vendoring a copy
of that library into this repository, it was **absorbed**: ported in whole, its own test
suite carried across, and then edited as part of this codebase from that point on. Plumbline
is one application, not three projects sharing a dependency, and a vendored copy would have
to be checked against a sibling directory (`../agentic-substrate`) that a clean clone of *this*
repository, and the Docker build context, do not have.

`tests/test_no_external_paths.py` enforces the boundary directly — it fails if any source
file in this repository reaches back outside it (`agentic-substrate`, `parents[3+]`,
`../../`), and a second test confirms the old `substrate` import namespace is gone entirely.

## Requirements

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- Node.js (for the dashboard) and npm
- A GCP project with Firestore, Pub/Sub, Vertex AI and Cloud Run enabled, **only** if you want
  to exercise real Firestore/Vertex calls or deploy — the test suite and one of the two local
  spin-up paths below need neither.

## Local spin-up

### 1. Clone and install

```bash
git clone <this-repo> plumbline && cd plumbline
uv pip install -e ".[dev]"
```

### 2. Run the tests

Every layer is testable offline: `core.fakes.FakeFirestore` backs Firestore, `FakeModel`
backs Gemini, and Playwright is faked at the driver boundary so agent logic is tested without
a browser.

```bash
uv run python -m pytest -q
```

973 passed, 15 skipped, on a clean clone. The 15 skips are two opt-in suites that need real
credentials — `PLUMBLINE_LIVE_BROWSER_TESTS=1 uv run python -m pytest tests/test_playwright_live.py`
(a real Chromium; run `uv run playwright install chromium` first) and `tests/test_oauth_live.py`
(a real OAuth exchange) — neither runs by default and neither is required to verify the product.

Use `uv run python -m pytest`, not bare `uv run pytest` — `tests/` has no `__init__.py`, and
several test files import `from tests.agent_fixtures import make_ctx`; running pytest as a
console script rather than as a module leaves the repo root off `sys.path` and every one of
those imports fails at collection.

```bash
cd web
npm install
npm test          # 102 passed
```

### 3a. See the whole product running, with no GCP account (fastest path)

This is what the demo video and `web/e2e/`'s own Playwright suite both drive: the real
FastAPI app, wired to an in-memory Firestore double, serving the real built dashboard on one
port, seeded with a demo workspace, a gated payments patch, and a scripted stand-in for the
fleet so a run's steps genuinely stream in over real time.

```bash
cd web && npm install && npm run build && cd ..
PLUMBLINE_ENV=test uv run python3 web/e2e/server.py --port 8130
```

Open `http://127.0.0.1:8130`, click **open the live demo**, and click through runs, the
surface map, findings, and a gated patch. Sign in as the seeded owner
(`owner@e2e.example.com` / `owner-e2e-passphrase`) instead of the demo account to see the
approval gate enabled rather than disabled-for-a-reader.

The full Playwright suite behind this same server (`cd web && npm run test:e2e`) covers the
demo door, a live SSE run, the approval gate's disabled state for a reader, and password
change signing out other sessions.

### 3b. Boot the real API and dashboard (needs a GCP project)

```bash
export GCP_PROJECT=<your-project-id>       # defaults to the project this was built against
export PLUMBLINE_ENV=dev                    # build_app refuses to start in "production" without a real OAUTH_STATE_SECRET
gcloud auth application-default login        # Firestore/Vertex credentials
uv run uvicorn app.main:app --reload --port 8080
```

`GET http://localhost:8080/_health` returns `{"ok": true, "model": "gemini-3.5-flash",
"gemini_location": "global", ...}` immediately — that part needs no credentials, since
`core/store.py`'s Firestore client is built lazily on first real query. Every route that
actually reads or writes a workspace does need `gcloud auth application-default login` (or
`GOOGLE_APPLICATION_CREDENTIALS`) against a project with Firestore and Vertex AI enabled.

In a second terminal:

```bash
cd web
VITE_API_BASE=http://localhost:8080/api npm run dev
```

`web/src/lib/api.ts` defaults `VITE_API_BASE` to the relative path `/api`, which is what the
built dashboard and `web/e2e/server.py` both rely on to share an origin with the API — set it
explicitly here because Vite's dev server and uvicorn are two separate origins.

## Deploy (Cloud Run)

```bash
cd web && npm install && npm run build && cd ..
GCP_PROJECT=<your-project-id> ./deploy.sh
```

`deploy.sh` is idempotent — safe to re-run — and does the whole thing: enables the required
APIs, creates the Firestore database, the Pub/Sub topic and push subscription, and an
Artifact Registry repo if any are missing; builds and pushes two images (`Dockerfile`, a
small API image with no browser in it; `Dockerfile.worker`, which does install Chromium);
deploys `plumbline-api` to Cloud Run (`--min-instances=0`, scale-to-zero) and
`plumbline-worker` as a Cloud Run Job (one execution per run, `--max-retries=1
--task-timeout=900s`); generates and stores `OAUTH_STATE_SECRET` in Secret Manager the first
time it runs; and grants the API's runtime identity permission to start a job execution and
read that secret. It prints the deployed `.run.app` URL and a `curl .../_health` command at
the end.

GCP facts worth knowing before you deploy, all named where they're enforced in code:

- `gemini-3.5-flash` is served **only** on Vertex location `global` — every regional
  endpoint 404s. `gemini-2.5-flash` runs regionally and would pass locally while silently
  failing the hackathon's model-version gate; `deploy.sh` and `core/config.py` both pin
  `global` deliberately.
- `google-api-core` must stay `>=2.34.0,<2.35.0` — 2.35.0 percent-encodes Firestore's
  `(default)` path and 400s every query (`tests/test_dependency_pins.py`).
- Google's Cloud Run frontend intercepts `/healthz` before it reaches the container; use
  `/_health` for a real liveness check.
- Everything lives in `us-central1`.

## The fleet, briefly

Eleven agents, all behind the Gateway — see `ARCHITECTURE.md` for the full picture and why
the diagram draws the Gateway, not any one agent, as the thing in the middle.

Cartographer (crawls, maps routes) · Author (plain English → Playwright) · Healer (repairs
selector drift, never an assertion) · Chaos (injects latency/faults/toxic input) · Runner
(executes, no model — determinism is the point) · Triager (root cause, reproduces 5×, flake
vs bug) · Surgeon (proposes the patch, opens the PR, stops at the gate) · Sentinel (production
incident → reproducing test) · Auditor (a11y + security findings) · Oracle (differential
across two environments) · Economist (recommends removing low-value tests — the only agent
with no write scope at all).

## What isn't finished

Said here rather than left to be discovered: OAuth completes a real provider handshake
(Google, GitHub and Okta all have working `OAuthProvider` implementations in
`app/providers.py`), a revision of the spec's original plan to stub it to session issuance.
Billing is display plus limit enforcement — there is no payment processor behind it. The
responsive layout below 1100px, on-prem runners, SAML, and a marketing site are all out of
scope for this submission, per the spec.
