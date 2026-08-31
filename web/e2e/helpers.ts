import { expect, type Page } from "@playwright/test";
import { injectAxe, getViolations } from "axe-playwright";

/** The four breakpoints Task 16 built the responsive layout for. */
export const BREAKPOINTS = [
  { name: "375 (mobile)", width: 375, height: 812 },
  { name: "768 (tablet)", width: 768, height: 1024 },
  { name: "1024 (small desktop)", width: 1024, height: 800 },
  { name: "1440 (desktop)", width: 1440, height: 900 },
];

/**
 * Asserts the page body never grows wider than the viewport -- a
 * horizontal scrollbar on the BODY, not on some inner panel that
 * deliberately scrolls its own overflow (a wide table, a diff).
 */
export async function assertNoHorizontalScroll(page: Page, label: string) {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(scrollWidth, `${label}: document scrollWidth (${scrollWidth}) should not exceed clientWidth (${clientWidth}) -- the page body is scrolling horizontally`).toBeLessThanOrEqual(clientWidth);
}

/**
 * Injects axe-core and fails the test on any "serious" or "critical"
 * violation, printing every one it finds (title, impact, node count) so a
 * failure names the actual problem rather than just "axe failed". Minor/
 * moderate violations are logged, not failed -- see the task report for
 * the reasoning on where that line sits.
 */
export async function assertNoSeriousA11yViolations(page: Page, pageLabel: string) {
  await injectAxe(page);
  const violations = await getViolations(page);
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");

  if (violations.length > 0) {
    // eslint-disable-next-line no-console
    console.log(
      `[axe] ${pageLabel}: ${violations.length} violation(s) -- ` +
        violations.map((v) => `${v.id} (${v.impact}, ${v.nodes.length} node[s])`).join("; "),
    );
  }

  expect(
    blocking,
    `${pageLabel}: ${blocking.length} serious/critical a11y violation(s):\n` +
      blocking
        .map((v) => `  - [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node[s]) -- ${v.helpUrl}`)
        .join("\n"),
  ).toEqual([]);
}

/** Signs in through the real sign-in form (not the demo door). */
export async function signInWithPassword(page: Page, email: string, password: string) {
  await page.goto("/signin");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("/");
}

/** Opens the seeded live demo, the exact path a judge takes from sign-in. */
export async function openLiveDemo(page: Page) {
  await page.goto("/signin");
  await page.getByRole("button", { name: "Open the live demo" }).click();
  await expect(page).toHaveURL("/");
}
