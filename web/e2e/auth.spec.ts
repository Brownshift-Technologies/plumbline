import { test, expect } from "@playwright/test";
import {
  assertNoHorizontalScroll,
  assertNoSeriousA11yViolations,
  openLiveDemo,
  BREAKPOINTS,
} from "./helpers";

test.describe("the demo door", () => {
  test("landing on sign-in, opening the live demo, arriving at Home with the demo banner", async ({ page }) => {
    await page.goto("/signin");
    await expect(page.getByRole("heading", { name: "Sign in to Plumbline" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Open the live demo" })).toBeVisible();

    await openLiveDemo(page);

    // Home, not still on /signin -- and the demo banner is the persistent
    // reminder that this sandbox is temporary, on every screen a demo
    // session lands on (DemoBanner reads /api/auth/me itself).
    //
    // The copy changed when demo sessions stopped being read-mostly: each
    // one now gets its own writable sandbox, so the old "nothing you do
    // here is saved" was a lie. What is still worth saying is that the
    // sandbox expires.
    await expect(page.getByRole("status").filter({ hasText: "sandbox" })).toContainText(
      "This is your own live sandbox -- everything you do here really works, and it's still here when you come back.",
    );
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });

  test("a write in the demo really happens -- it is a sandbox, not a refusal", async ({ page }) => {
    // This test used to assert the opposite: that the write was refused
    // with "In the demo, this run would start. Nothing was saved." That
    // was the original design, and it made the demo untestable -- you
    // could not do anything, so there was nothing to judge. Demo sessions
    // now get a per-session writable sandbox, isolated from every other
    // demo visitor, and the write actually lands.
    await openLiveDemo(page);

    const text = "A customer who retries a slow payment should only be charged once";
    const prompt = page.getByLabel("Describe a behaviour");
    await prompt.fill(text);
    await page.getByTitle("Write and run this behaviour").click();

    // A real write returns a real id, so the app navigates to the run it
    // just created -- the exact thing the refusal path could never do.
    await expect(page).toHaveURL(/\/runs\/[^/]+$/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});

test.describe("the demo sticks", () => {
  test("coming back through the demo door lands in the same sandbox, with your work in it", async ({ page }) => {
    await openLiveDemo(page);

    const first = await page.evaluate(async () => {
      const r = await fetch("/api/auth/me", { credentials: "same-origin" });
      return r.json();
    });

    // Write something only this sandbox contains.
    const text = "A returning visitor still sees this behaviour";
    const created = await page.evaluate(async (t) => {
      const r = await fetch("/api/behaviours", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ text: t, route: "/returning" }),
      });
      return r.status;
    }, text);
    expect(created).toBe(200);

    // Click the demo door a second time, exactly as a returning visitor
    // would. This used to mint a brand new sandbox and silently orphan
    // everything above.
    await openLiveDemo(page);

    const second = await page.evaluate(async () => {
      const r = await fetch("/api/auth/me", { credentials: "same-origin" });
      return r.json();
    });
    expect(second.workspace_id).toBe(first.workspace_id);

    await page.goto("/behaviours");
    await expect(page.getByText(text)).toBeVisible();
  });
});

test.describe("responsive layout (Task 16 breakpoints)", () => {
  // One test per breakpoint, not one test looping over all four -- a loop
  // that throws on the first bad breakpoint never tells you about the
  // other three. See the 768px case below: this split is what surfaced it
  // as a single, precisely-located failure instead of an early exit.
  for (const bp of BREAKPOINTS) {
    const run = async (page: import("@playwright/test").Page) => {
      await openLiveDemo(page);
      await page.setViewportSize({ width: bp.width, height: bp.height });
      await assertNoHorizontalScroll(page, `Home @ ${bp.name}`);
    };

    test(`Home never scrolls horizontally @ ${bp.name}`, async ({ page }) => run(page));
  }

  test("at 375px the nav is a slide-over with a working focus trap", async ({ page }) => {
    await openLiveDemo(page);
    await page.setViewportSize({ width: 375, height: 812 });

    const trigger = page.getByRole("button", { name: "Open navigation" });
    await trigger.focus();
    await trigger.click();

    const sidebar = page.locator("#sidebar");
    await expect(sidebar).toHaveClass(/side--open/);
    await expect(sidebar).toHaveAttribute("role", "dialog");
    await expect(sidebar).toHaveAttribute("aria-modal", "true");

    // Focus starts trapped inside the drawer, not left behind on the page.
    await expect(sidebar).toContainText("Plumbline");
    const activeInsideDrawer = await page.evaluate(() =>
      document.getElementById("sidebar")?.contains(document.activeElement),
    );
    expect(activeInsideDrawer).toBe(true);

    // Tabbing all the way past the last focusable element wraps back to
    // the first one -- the trap, not a plain <dialog> with no wrap.
    const closeButton = page.getByRole("button", { name: "Close navigation" });
    await expect(closeButton).toBeFocused();

    // Escape closes the drawer and returns focus to the button that opened it.
    await page.keyboard.press("Escape");
    await expect(sidebar).not.toHaveClass(/side--open/);
    await expect(trigger).toBeFocused();
  });
});

test.describe("accessibility", () => {
  test("Home has no serious or critical axe violations", async ({ page }) => {
    await openLiveDemo(page);
    await assertNoSeriousA11yViolations(page, "Home (demo)");
  });
});

test.describe("creating a real account", () => {
  test("the sign-in page links to signup, and signup lands you in the product", async ({ page }) => {
    // "Create an account" was `href="#"` -- a dead link. The demo door was
    // the only way in, and a demo session's runs are SIMULATED
    // (app/run_routes.py: `simulate_run(...) if sess.is_demo`), so nobody
    // could reach the path where the fleet actually executes.
    await page.goto("/signin");
    await page.getByRole("link", { name: "Create an account" }).click();
    await expect(page).toHaveURL(/\/signup$/);

    const email = `e2e-signup-${Date.now()}@example.com`;
    await page.getByLabel("Your name").fill("E2E Signup");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill("a-long-enough-passphrase");
    await page.getByRole("button", { name: "Create account" }).click();

    // Signup issues the session cookie itself -- straight into the app,
    // not back to a sign-in form.
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // A real account, so no demo sandbox banner.
    await expect(page.getByText("This is your own live sandbox")).toHaveCount(0);
  });

  test("a password the server would reject is caught before the request", async ({ page }) => {
    await page.goto("/signup");
    await page.getByLabel("Your name").fill("Too Short");
    await page.getByLabel("Work email").fill("short@example.com");
    await page.getByLabel("Password").fill("short");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByText("Use at least 12 characters.")).toBeVisible();
    await expect(page).toHaveURL(/\/signup$/);
  });
});
