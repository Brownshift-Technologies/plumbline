import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const PORT = Number(process.env.PLUMBLINE_E2E_PORT ?? 8130);
// PLUMBLINE_E2E_BASE_URL points the suite at an already-running app --
// the deployed Cloud Run service, say -- instead of the local fixture
// server. The local server seeds deterministic fixtures the specs rely on,
// so this is for auditing a real deployment, not for the normal run.
const REMOTE = process.env.PLUMBLINE_E2E_BASE_URL;
const BASE_URL = REMOTE ?? `http://127.0.0.1:${PORT}`;

/**
 * Drives the BUILT dashboard (`web/dist`, `npm run build`) against a real,
 * locally running Plumbline API -- `web/e2e/server.py` boots both on one
 * port (see that file's own docstring for why: no GCP credentials, a
 * fake in-memory Firestore, a seeded demo workspace plus two fixture
 * workspaces, and a scripted stand-in for the fleet so a run's steps
 * genuinely stream in over real time instead of needing a live Gemini +
 * Playwright pipeline this checkout has no credentials for).
 *
 * `chromiumSandbox: false` is mandatory, not a convenience: Chromium's
 * unprivileged user namespace sandbox needs a kernel setting AppArmor
 * blocks on this machine, and the flag is required again inside a
 * Cloud Run container for the same reason -- `agents/browser.py`'s
 * `PlaywrightDriver` sets the identical flag for the identical reason.
 * Dropping it does not make anything safer here: this is a local test
 * browser driving a locally seeded fixture, never untrusted content.
 */
export default defineConfig({
  testDir: __dirname,
  fullyParallel: false,
  // One worker, on purpose: every spec signs in against the SAME
  // in-memory `FakeFirestore` server.py boots once for the whole run.
  // `settings.spec.ts` mutates its fixture account's password;
  // `run.spec.ts` starts a real run backed by a scripted pipeline that
  // runs on its own background thread. Parallel workers would mean two
  // spec files racing the same seeded rows for no benefit -- this suite
  // is 4 files, seconds each, not a scale problem worth the flakiness.
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  timeout: 30_000,
  expect: { timeout: 8_000 },

  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      chromiumSandbox: false,
    },
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  ...(REMOTE ? {} : { webServer: {
    command: `uv run python3 web/e2e/server.py --port ${PORT}`,
    cwd: REPO_ROOT,
    url: `${BASE_URL}/_health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      PLUMBLINE_ENV: "test",
    },
    stdout: "pipe",
    stderr: "pipe",
  } }),
});
