import { test, expect } from "@playwright/test";
import { assertNoSeriousA11yViolations, signInWithPassword } from "./helpers";

const OWNER_EMAIL = "owner@e2e.example.com";
const OWNER_PASSWORD = "owner-e2e-passphrase";

// Matches web/e2e/server.py's `_LIVE_RUN_STEPS` -- the scripted pipeline
// `enqueue_job` runs in place of the real fleet. See that file's module
// docstring for why a real Gemini + Playwright run is not available in a
// locally cloned checkout, and why a real SSE stream is still exercised
// even though the step CONTENT is scripted.
const EXPECTED_STEP_COUNT = 4;

test.describe("a run actually streams over SSE", () => {
  test("describing a behaviour and starting a run shows steps arriving incrementally, not all at once", async ({ page }) => {
    await signInWithPassword(page, OWNER_EMAIL, OWNER_PASSWORD);

    await page.getByLabel("Describe a behaviour").fill(
      "A customer who edits their cart mid-checkout keeps the original price",
    );
    await page.getByTitle("Write and run this behaviour").click();

    // A real (non-demo) run navigates straight to its own detail page.
    await expect(page).toHaveURL(/\/runs\/run_/);

    const stepRows = page.locator(".tl-row");

    // The first step shows up well before the run is done.
    await expect.poll(async () => stepRows.count(), { timeout: 4_000 }).toBeGreaterThanOrEqual(1);
    const firstSeenAt = Date.now();
    const countAtFirstSight = await stepRows.count();
    expect(countAtFirstSight, "the first poll already saw every step -- that is 'all at once', not a stream").toBeLessThan(EXPECTED_STEP_COUNT);

    // Every remaining step arrives before the run finishes.
    await expect.poll(async () => stepRows.count(), { timeout: 10_000 }).toBe(EXPECTED_STEP_COUNT);
    const lastSeenAt = Date.now();

    // A real, human-noticeable gap between "first step visible" and "all
    // steps visible" -- the one thing a component test (which renders a
    // fully-formed `steps` array in one shot) structurally cannot prove.
    expect(lastSeenAt - firstSeenAt).toBeGreaterThan(1_000);

    // The run reaches a terminal state once its steps are all in.
    await expect(page.getByText(/steps? · Finished/)).toBeVisible({ timeout: 5_000 });
  });

  test("Run detail has no serious or critical axe violations", async ({ page }) => {
    await signInWithPassword(page, OWNER_EMAIL, OWNER_PASSWORD);
    // The gate fixture run (web/e2e/server.py) is already finished --
    // steady state, nothing still streaming, a stable page for axe.
    await page.goto("/runs/run_e2e_gate");
    await expect(page.getByRole("heading", { name: /^Run \d+/ })).toBeVisible();
    await assertNoSeriousA11yViolations(page, "Run detail");
  });
});
