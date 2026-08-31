import { test, expect } from "@playwright/test";
import { openLiveDemo } from "./helpers";

/**
 * Every screen, and every Settings tab, must render for a demo visitor.
 *
 * This exists because Settings -> Billing took the whole application down
 * with "t.map is not a function". `BillingPane.tsx` fetched
 * `/api/billing/invoices`, a route nobody had written; `app/production.py`
 * serves the SPA as a catch-all, so the request came back as index.html
 * with a **200**, and the client handed that HTML string to a component
 * expecting an array. React's error boundary replaced the entire app with
 * a stack trace.
 *
 * Nothing in either test suite could see it. The backend tests passed --
 * there was no route to test. The frontend tests passed -- they mock
 * `api.get`, so the mock returned the array the component wanted. Only the
 * assembled product was wrong.
 *
 * `tests/test_frontend_backend_contract.py` now catches the specific cause
 * (a frontend path with no backend route). This catches the symptom, for
 * every screen, whatever the cause: a render crash anywhere on any of
 * these pages fails here.
 */

const SCREENS = [
  ["/", "Home"],
  ["/runs", "Runs"],
  ["/surface", "Surface"],
  ["/findings", "Findings"],
  ["/behaviours", "Behaviours"],
  ["/agents", "Agents"],
  ["/policy", "Policy"],
  ["/ledger", "Ledger"],
  ["/settings", "Settings"],
] as const;

const SETTINGS_TABS = ["Profile", "Security", "Members", "Workspace", "Billing"] as const;

/**
 * Collect render failures.
 *
 * `pageerror` alone is NOT enough and this cost a round to learn: React
 * Router catches a render throw in its own ErrorBoundary, so no unhandled
 * error ever reaches the page and `pageerror` stays silent. The crash is
 * only visible as console errors ("React Router caught the following error
 * during render ...").
 */
function watchForCrashes(page: import("@playwright/test").Page): string[] {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text().split("\n")[0]}`);
  });
  return problems;
}

test.describe("every screen renders", () => {
  test("no screen crashes, and none logs a console error", async ({ page }) => {
    const problems = watchForCrashes(page);
    await openLiveDemo(page);

    for (const [path, label] of SCREENS) {
      await page.goto(path);
      // Assert POSITIVELY that the screen rendered. Checking only that the
      // error UI is absent is a race that always passes: `toHaveCount(0)`
      // matches on the first poll, before React has painted the crash.
      // The <h1> is inside the route that would have been replaced, so it
      // is gone if the route threw.
      await expect(page.getByRole("heading", { level: 1 }), `${label} (${path}) did not render`).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("heading", { level: 1 }), `${label} (${path}) crashed`).toBeVisible();
      await expect(
        page.getByText("Unexpected Application Error!"),
        `${label} (${path}) crashed`,
      ).toHaveCount(0);
    }

    expect(problems, `screens logged errors:\n${problems.join("\n")}`).toEqual([]);
  });

  test("every Settings tab renders", async ({ page }) => {
    const problems = watchForCrashes(page);
    await openLiveDemo(page);
    await page.goto("/settings");

    for (const tab of SETTINGS_TABS) {
      await page.getByRole("tab", { name: tab }).click();
      // Positive first, for the same reason as above: a crash replaces the
      // whole app, so the tabpanel and the page heading both vanish. Billing
      // is last in this list, and an absence-only check on the last item
      // passes before the crash has painted -- which is exactly how the
      // /api/billing/invoices crash got past an earlier version of this test.
      await expect(page.getByRole("tabpanel"), `Settings -> ${tab} did not render`).toBeVisible();
      await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
      // Wait for the pane's OWN fetches to resolve before judging it.
      // Every assertion above passes on the freshly-mounted, still-loading
      // pane; the /api/billing/invoices crash happened a beat later, when
      // the response arrived, and the test had already moved on. Billing is
      // last in this list, so the run simply ended before it blew up.
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("tabpanel"), `Settings -> ${tab} crashed`).toBeVisible();
      await expect(
        page.getByText("Unexpected Application Error!"),
        `Settings -> ${tab} crashed`,
      ).toHaveCount(0);
    }

    expect(problems, `Settings tabs threw:\n${problems.join("\n")}`).toEqual([]);
  });
});
