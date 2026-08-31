import { test, expect } from "@playwright/test";
import { assertNoSeriousA11yViolations, signInWithPassword } from "./helpers";

// Matches web/e2e/server.py's `_seed_settings_fixture` -- a workspace and
// account this spec file owns exclusively, so mutating its password here
// cannot race patch.spec.ts/run.spec.ts, which sign in with a different
// account entirely (see playwright.config.ts's `workers: 1` for the other
// half of that isolation).
const EMAIL = "settings@e2e.example.com";
const ORIGINAL_PASSWORD = "settings-e2e-passphrase-1";
const NEW_PASSWORD = "settings-e2e-passphrase-2-longer";

// Runs before the password-changing test below, on purpose: this file's
// tests execute in declaration order (`workers: 1`, `fullyParallel: false`
// in playwright.config.ts), and the next test mutates this fixture
// account's password -- an axe pass that needed the ORIGINAL password
// would break if it ran second.
test("Settings has no serious or critical axe violations", async ({ page }) => {
  await signInWithPassword(page, EMAIL, ORIGINAL_PASSWORD);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await assertNoSeriousA11yViolations(page, "Settings");
});

test("changing your password signs the other session out, everywhere else", async ({ page, browser }) => {
  // Session A: this test's own page.
  await signInWithPassword(page, EMAIL, ORIGINAL_PASSWORD);

  // Session B: a second, independent browser context/session for the SAME
  // account -- the "other device" this test proves gets signed out.
  const otherContext = await browser.newContext();
  const otherPage = await otherContext.newPage();
  await signInWithPassword(otherPage, EMAIL, ORIGINAL_PASSWORD);
  await otherPage.goto("/settings");
  await expect(otherPage.getByRole("heading", { name: "Settings" })).toBeVisible();

  // In session A: change the password.
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Security" }).click();
  await page.getByLabel("Current password").fill(ORIGINAL_PASSWORD);
  await page.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("Confirm new password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Update password" }).click();

  await expect(page.getByText("Password updated. Other sessions signed out.")).toBeVisible();

  // Session A stays signed in -- the account that just proved knowledge
  // of the new password is not the one that gets revoked.
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

  // Session B's cookie is now for a revoked session -- the API boundary
  // half of this flow, independent of whatever the UI happens to render
  // for a 401 (there is no global redirect-to-signin on 401 today, see
  // task-17f-report.md).
  const meResponse = await otherPage.request.get("/api/auth/me");
  expect(meResponse.status()).toBe(401);

  // And the UI boundary half: reloading session B's already-open page
  // surfaces that failure rather than pretending the account is still there.
  await otherPage.reload();
  await expect(otherPage.getByText("Couldn't load your account")).toBeVisible();

  await otherContext.close();

  // Sign the account back in with the NEW password, in a fresh context,
  // to leave the fixture in a state a re-run of this same spec can use
  // again (`signInWithPassword` would otherwise be the only remaining
  // proof the change actually took).
  const verifyContext = await browser.newContext();
  const verifyPage = await verifyContext.newPage();
  await signInWithPassword(verifyPage, EMAIL, NEW_PASSWORD);
  await verifyContext.close();
});
