"""Boots one Plumbline API + the built dashboard on a single local port,
for the Playwright suite in `web/e2e/` to drive.

    PLUMBLINE_ENV=test uv run python3 web/e2e/server.py [--port 8000]

Design, and why it looks the way it does:

- **No GCP credentials needed.** `app.main.build_app` is called with a
  `Repo` backed by `core.fakes.FakeFirestore` -- the exact in-memory
  double `tests/conftest.py` gives every backend test -- rather than the
  real `Repo(cfg)` `app.main`'s own module-level `app` builds. That
  module-level `app = build_app()` still runs as an import side effect
  the moment `from app.main import build_app` executes below (Python
  cannot import a function without executing the module around it), but
  its `Repo`'s Firestore client is built lazily on first real query
  (`core/store.py`'s `Store._client` property), so merely importing it
  here is safe with no credentials configured; this script never touches
  that instance, only the second, fake-backed app it builds for itself.

- **`PLUMBLINE_ENV=test` is required, not merely recommended.**
  `build_app` refuses to start with its fixed, source-visible OAuth
  state-signing fallback outside `{"test", "dev"}` -- see
  `app/main.py`'s own module docstring. Set before this module's own
  `from app.main import build_app` line, same as `tests/conftest.py`
  does, so the guard passes for both the module-level `app` (an import
  side effect) and the one this script builds itself.

- **`enqueue_job` is swapped for a scripted, timed pipeline, not the real
  fleet.** `POST /api/runs` normally hands off to a Cloud Run Job running
  `job/worker.py`'s `Orchestrator` -- real Gemini calls, a real
  `PlaywrightDriver` against a real deployed target. None of that exists
  in a locally-cloned checkout with no GCP project, no API keys, and no
  target site. `app.state.enqueue_job` is an injectable hook for exactly
  this reason (see `app/main.py`'s own comment on it) -- a test suite
  that needs `POST /api/runs` to behave like a run without needing a
  live fleet swaps it for something else. `_scripted_pipeline` below
  appends `Step` rows to the SAME `Repo`/`FakeFirestore` the API reads
  from, one at a time, with real wall-clock gaps between them, on a
  background thread -- so `GET /api/runs/{id}/stream` (unmodified, real
  polling code) has something genuinely incremental to observe streaming.
  This is a scripted stand-in for the fleet's OUTPUT, not a mock of the
  SSE mechanism itself: the stream, the polling loop, and the browser's
  `EventSource` are all exercised for real.

- **Seeds two fixtures beyond `seed.demo.seed_demo`.** The demo
  workspace's own session is hardcoded to role `"reader"` everywhere
  (`app/auth_routes.py`'s `demo()`), so it can exercise the reader half
  of the approval gate but never the owner half in the same run. A
  second, non-demo workspace (`ws_e2e_gate`) with two real accounts --
  one confirmed-TOTP owner, one reader, both members of the same
  workspace, looking at the same gated patch -- is what lets
  `patch.spec.ts` compare both roles against one fixture. A third,
  disposable workspace (`ws_e2e_settings`) exists solely so
  `settings.spec.ts` can change its account's password without
  disturbing any fixture another spec file depends on.

- **Serves `web/dist` itself, same-origin.** The production build's
  `VITE_API_BASE` defaults to `/api` (relative) -- see `web/src/lib/
  api.ts` -- so the dashboard only works unmodified when the API and the
  static files share an origin. Mounting `web/dist` on this same FastAPI
  app, rather than running a second static server, is what makes that
  true without editing the built app's own API base at all. A catch-all
  GET route falls back to `index.html` for anything not already matched
  by an API router or a static asset, so a hard navigation straight to a
  client-side route (e.g. Playwright's `page.goto(".../runs/<id>")`)
  works exactly like a real deploy's rewrite rule would.
"""

import argparse
import os
import sys
import threading
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Must be set before `from app.main import build_app` -- see module docstring.
os.environ.setdefault("PLUMBLINE_ENV", "test")

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.main import build_app  # noqa: E402
from app.models import Finding, Membership, Patch, Run, Step, User, Workspace  # noqa: E402
from app.repo import Repo  # noqa: E402
from app.security import hash_password, new_totp_secret  # noqa: E402
from app.settings import PlumblineConfig  # noqa: E402
from core.fakes import FakeFirestore  # noqa: E402
from seed.demo import seed_demo  # noqa: E402

DIST_DIR = REPO_ROOT / "web" / "dist"

# Fixed, well-known accounts + ids -- every Playwright spec that needs one
# hardcodes the same values rather than discovering them at runtime, so a
# spec file is readable on its own without cross-referencing this script.
GATE_WORKSPACE_ID = "ws_e2e_gate"
OWNER_EMAIL = "owner@e2e.example.com"
OWNER_PASSWORD = "owner-e2e-passphrase"
READER_EMAIL = "reader@e2e.example.com"
READER_PASSWORD = "reader-e2e-passphrase"
GATED_FINDING_ID = "finding_e2e_gate"
GATED_RUN_ID = "run_e2e_gate"

SETTINGS_WORKSPACE_ID = "ws_e2e_settings"
SETTINGS_EMAIL = "settings@e2e.example.com"
SETTINGS_PASSWORD = "settings-e2e-passphrase-1"

_GATE_DIFF = """--- a/src/checkout/payment-client.ts
+++ b/src/checkout/payment-client.ts
@@ -118,9 +118,14 @@ async function submitCharge(cart, provider) {
   const key = idempotencyKeyFor(cart);
-  void persistIdempotencyKey(key);
-  return retry(() => provider.charge(cart, key), { attempts: 3 });
+  await persistIdempotencyKey(key);
+
+  return retry(() => provider.charge(cart, key), {
+    attempts: 3,
+    idempotencyKey: key,
+  });
 }
"""

# (agent, summary, detail, outcome, duration_ms)
_GATE_RUN_STEPS: list[tuple[str, str, str, str, int]] = [
    ("chaos", "Chaos injected 240ms of latency on payments-api",
     "Chosen because the provider's p99 is 210ms and nothing exercised the slow path.",
     "ok", 48_000),
    ("runner", "Runner saw two charges",
     "Two POST /v1/charges with different idempotency keys, 30ms apart.", "failed", 83_000),
    ("triager", "Triager reproduced it 5 times out of 5",
     "Not a flake. Deterministic under the same seed.", "ok", 59_000),
    ("surgeon", "Surgeon opened the pull request and stopped",
     "Policy will not let an agent merge anything under payments/*.", "gated", 21_000),
]

# The scripted "fleet" run.spec.ts watches stream in, one entry per
# `enqueue_job` call, each appended on its own delay -- see
# `_scripted_pipeline` below.
_LIVE_RUN_STEPS: list[tuple[str, str, str, str, int]] = [
    ("cartographer", "Cartographer mapped the surface",
     "Found the route this behaviour describes.", "ok", 1_100),
    ("author", "Author wrote the behaviour",
     "Turned the prompt into a runnable spec.", "ok", 900),
    ("runner", "Runner ran it against the live target",
     "One spec, one browser context.", "ok", 1_400),
    ("triager", "Triager confirmed it holds",
     "Reproduced clean; nothing to file.", "ok", 700),
]
_LIVE_STEP_DELAY_S = 1.1


def _seed_gate_fixture(repo: Repo) -> None:
    """A second workspace, alongside the demo one, so `patch.spec.ts` can
    look at the SAME gated patch as both a reader (disabled, with a
    visible reason) and an owner (enabled) -- the demo session's role is
    hardcoded to reader everywhere (`app/auth_routes.py`), so it alone
    cannot exercise the owner half of that comparison."""
    repo.put_workspace(Workspace(
        id=GATE_WORKSPACE_ID, name="E2E Gate Co", repo="acme/gate-fixture",
        plan="team", seats=5, run_limit=500, runs_used=1, policy_version=1,
    ))

    owner = User(
        id="u_e2e_owner", email=OWNER_EMAIL, password_hash=hash_password(OWNER_PASSWORD),
        name="Owner E2E", totp_secret=new_totp_secret(),
    )
    repo.put_user(owner)
    repo.claim_email(owner.email, owner.id)
    repo.put_membership(Membership(id="m_e2e_owner", user_id=owner.id, workspace_id=GATE_WORKSPACE_ID, role="owner"))

    reader = User(
        id="u_e2e_reader", email=READER_EMAIL, password_hash=hash_password(READER_PASSWORD),
        name="Reader E2E",
    )
    repo.put_user(reader)
    repo.claim_email(reader.email, reader.id)
    repo.put_membership(Membership(id="m_e2e_reader", user_id=reader.id, workspace_id=GATE_WORKSPACE_ID, role="reader"))

    repo.put_finding(Finding(
        id=GATED_FINDING_ID, workspace_id=GATE_WORKSPACE_ID,
        title="A retried payment charges the customer twice", route="/checkout/payment",
        found_by="chaos", status="patch_ready", severity="high", repro_count=5,
        # Links this finding to the gated run. GET /api/runs/{id} resolves
        # finding_id via repo.finding_for_run(run_id), and RunDetail gates
        # the whole "Proposed patch" section -- diff, Approve and merge,
        # Reject -- behind that id. Without it the fixture rendered a gated
        # run with no patch section at all, which is why the reader/owner
        # test sat as test.fixme.
        run_id=GATED_RUN_ID,
    ))
    repo.put_patch(Patch(
        id=f"patch_{GATED_FINDING_ID}", finding_id=GATED_FINDING_ID, diff=_GATE_DIFF,
        files=("src/checkout/payment-client.ts",), added=7, removed=2, verified=True,
        pr_url="https://github.com/example/repo/pull/9001", gate_state="awaiting_approval",
    ))

    started = time.time() - 900
    repo.put_run(Run(
        id=GATED_RUN_ID, workspace_id=GATE_WORKSPACE_ID, number=1,
        trigger="Manual · e2e gate fixture", state="finished", commit="e2efix1",
        started_by="Chaos", held=9, failed=1, repaired=0, duration_ms=211_000,
        started_at=started,
    ))
    at = started
    for i, (agent, summary, detail, outcome, duration_ms) in enumerate(_GATE_RUN_STEPS):
        repo.append_step(Step(
            id=f"st_e2e_gate_{i}", run_id=GATED_RUN_ID, agent=agent, summary=summary,
            detail=detail, outcome=outcome, duration_ms=duration_ms, at=at,
        ))
        at += duration_ms / 1000


def _seed_settings_fixture(repo: Repo) -> None:
    """A workspace and account `settings.spec.ts` owns exclusively, so
    changing its password there cannot race or clobber the account
    `patch.spec.ts`/`run.spec.ts` sign in with, however Playwright chooses
    to schedule the spec files."""
    repo.put_workspace(Workspace(id=SETTINGS_WORKSPACE_ID, name="E2E Settings Co", repo="acme/settings-fixture"))
    user = User(
        id="u_e2e_settings", email=SETTINGS_EMAIL, password_hash=hash_password(SETTINGS_PASSWORD),
        name="Settings E2E",
    )
    repo.put_user(user)
    repo.claim_email(user.email, user.id)
    repo.put_membership(Membership(id="m_e2e_settings", user_id=user.id, workspace_id=SETTINGS_WORKSPACE_ID, role="owner"))


def _run_scripted_pipeline(repo: Repo, run_id: str) -> None:
    run = repo.run(run_id)
    if run is None:
        return
    repo.put_run(type(run)(**{**run.__dict__, "state": "running"}))
    at = time.time()
    for i, (agent, summary, detail, outcome, duration_ms) in enumerate(_LIVE_RUN_STEPS):
        time.sleep(_LIVE_STEP_DELAY_S)
        at = time.time()
        repo.append_step(Step(
            id=f"st_{run_id}_{i}", run_id=run_id, agent=agent, summary=summary,
            detail=detail, outcome=outcome, duration_ms=duration_ms, at=at,
        ))
    current = repo.run(run_id)
    if current is None:
        return
    repo.put_run(type(current)(**{
        **current.__dict__, "state": "finished",
        "held": len(_LIVE_RUN_STEPS), "failed": 0, "repaired": 0,
        "duration_ms": int(len(_LIVE_RUN_STEPS) * _LIVE_STEP_DELAY_S * 1000),
    }))


def _enqueue_job_factory(repo: Repo):
    def enqueue_job(job_name: str, args: dict) -> None:
        run_id = args.get("PLUMBLINE_RUN_ID")
        if not run_id:
            return
        threading.Thread(target=_run_scripted_pipeline, args=(repo, run_id), daemon=True).start()

    return enqueue_job


def build_e2e_app():
    if not (DIST_DIR / "index.html").exists():
        raise SystemExit(
            f"{DIST_DIR} has no index.html -- run `npm run build` in web/ first. "
            "The e2e suite drives the BUILT dashboard, not the Vite dev server."
        )

    cfg = PlumblineConfig(
        project_id="plumbline-e2e", location="us-central1", vertex_location="global",
        model="gemini-3.5-flash", firestore_prefix="plumbline_e2e",
        oauth_state_secret="e2e-fixed-oauth-secret-not-for-production",
    )
    repo = Repo(cfg, client=FakeFirestore())
    app = build_app(config=cfg, repo=repo)

    seed_demo(repo, cfg)
    _seed_gate_fixture(repo)
    _seed_settings_fixture(repo)

    app.state.enqueue_job = _enqueue_job_factory(repo)

    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    index_path = DIST_DIR / "index.html"

    @app.get("/favicon.svg")
    def _favicon():
        return FileResponse(str(DIST_DIR / "favicon.svg"))

    @app.get("/icons.svg")
    def _icons():
        return FileResponse(str(DIST_DIR / "icons.svg"))

    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str):
        # Never reached for "/api/*" or "/_health" -- those routers were
        # `include_router`-ed inside `build_app` above, before this catch-all
        # was ever added to `app.router.routes`, and Starlette resolves a
        # request against routes in the order they were registered.
        return FileResponse(str(index_path))

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PLUMBLINE_E2E_PORT", "8130")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    import uvicorn

    app = build_e2e_app()
    print(f"[e2e] Plumbline API + dashboard on http://{args.host}:{args.port}", flush=True)
    print(f"[e2e] demo workspace={GATE_WORKSPACE_ID!r}, owner={OWNER_EMAIL!r}, reader={READER_EMAIL!r}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
