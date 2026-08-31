import { test, expect } from "@playwright/test";
import { signInWithPassword, openLiveDemo } from "./helpers";

const OWNER_EMAIL = "owner@e2e.example.com";
const OWNER_PASSWORD = "owner-e2e-passphrase";
const READER_EMAIL = "reader@e2e.example.com";
const READER_PASSWORD = "reader-e2e-passphrase";
const GATED_RUN_ID = "run_e2e_gate"; // web/e2e/server.py's gate fixture

test.describe("the approval gate", () => {
  test("a run blocked at a gate reads as gated, not failed", async ({ page }) => {
    await signInWithPassword(page, READER_EMAIL, READER_PASSWORD);
    await page.goto(`/runs/${GATED_RUN_ID}`);

    await expect(page.getByText("Blocked at a gate").first()).toBeVisible();
    await expect(page.getByText(/Only an owner can approve it|needs an owner's sign-off/)).toBeVisible();

    // The one thing this test exists to prove: nowhere on the page does a
    // gated run read as "Failed" -- the top status pill and the gate
    // banner are two different signals, and only the second one may ever
    // say the run stopped on purpose.
    await expect(page.getByText("Failed", { exact: true })).toHaveCount(0);
  });

  /**
   * BUG (found by this suite, not by any component test): `GET
   * /api/runs/{id}` (app/run_routes.py's `get_run`) returns
   * `{"run": {...}, "steps": [...]}`, and the SSE `finished` event
   * (`_run_events`) sends only `_run_json(current)` -- neither response
   * shape, ever, includes a `finding_id`. `RunDetail.tsx` derives
   * `findingId` from `run?.finding_id` (`web/src/pages/RunDetail.tsx`),
   * and the entire "Proposed patch" section -- diff, Approve, Reject,
   * Request changes -- is gated behind `{findingId && (...)}`. No `Finding`
   * ever carries a `run_id` either (`app/models.py`'s `Finding` has no
   * such field, and neither `seed/demo.py` nor this suite's own fixture
   * sets one on the frontend `Finding` type's optional `run_id`). The
   * result: the Approve button this whole product's approval-gate claim
   * rests on cannot be reached from ANY run's detail page, seeded fixture
   * or real fleet output alike -- `grep -rn "Approve and merge" web/src`
   * finds exactly one render site, and it never renders.
   *
   * RESOLVED. `app/models.py`'s `Finding` now carries `run_id`, the run
   * response carries `finding_id`, and `_finding_json` actually serialises
   * `run_id` (it did not, which kept every Findings row unclickable long
   * after the model gained the field). The Approve button is reachable,
   * so this runs unmodified, exactly as the note above promised.
   */
  test(
    "a reader sees Approve disabled with a visible reason; an owner sees it enabled and can approve",
    async ({ page, browser }) => {
      // -- reader: disabled, with an explanation in the page, not a tooltip --
      await signInWithPassword(page, READER_EMAIL, READER_PASSWORD);
      await page.goto(`/runs/${GATED_RUN_ID}`);

      const approveButton = page.getByRole("button", { name: /Approve and merge/ });
      await expect(approveButton).toBeDisabled();
      const reasonId = await approveButton.getAttribute("aria-describedby");
      expect(reasonId).toBeTruthy();
      await expect(page.locator(`#${reasonId}`)).toContainText("Readers cannot approve a patch");

      // -- owner: enabled, and approving actually crosses the API boundary --
      const ownerContext = await browser.newContext();
      const ownerPage = await ownerContext.newPage();
      await signInWithPassword(ownerPage, OWNER_EMAIL, OWNER_PASSWORD);
      await ownerPage.goto(`/runs/${GATED_RUN_ID}`);

      const ownerApprove = ownerPage.getByRole("button", { name: /Approve and merge/ });
      await expect(ownerApprove).toBeEnabled();
      await ownerApprove.click();
      await expect(ownerPage.getByText("Patch approved. Merging the pull request.")).toBeVisible();

      await ownerContext.close();
    },
  );
});

test.describe("the demo's hero moment", () => {
  test("a demo visitor can actually approve the gated patch", async ({ page }) => {
    // The seam this exists to hold shut. app/finding_routes.py's
    // _check_approve_permission returns early for `sess.is_demo` and says
    // why in a comment: "approving the gated patch is the demo's own hero
    // moment, and a demo session is the sole, de-facto owner of its own
    // sandbox workspace". The API honours that.
    //
    // RunDetail.tsx did not. It disabled Approve whenever
    // `user.role === "reader"`, and /api/auth/me reports exactly that for
    // a demo session (it holds no Membership row, so there is no truer
    // role to report). Backend and frontend were each self-consistent and
    // said opposite things, so the button the whole demo builds toward
    // read "Readers cannot approve a patch. Ask an owner or approver."
    await openLiveDemo(page);

    // Straight to the gated run, via the id the sandbox itself reports --
    // seed/demo.py names it run_demo_<workspace>_4471 and the workspace id
    // is fresh per demo session, so it cannot be hard-coded here.
    // Fetched from inside the page, not via page.request: the demo session
    // cookie lives in the browser context, and page.request did not carry
    // it here (401 "not signed in").
    const body = await page.evaluate(async () => {
      const r = await fetch("/api/findings", { credentials: "same-origin" });
      return r.json();
    });
    const doubleCharge = body.findings.find(
      (f: { title: string }) => f.title === "A retried payment charges the customer twice",
    );
    expect(doubleCharge, "the demo sandbox seeds the double-charge finding").toBeTruthy();
    expect(doubleCharge.run_id, "and that finding links to its run").toBeTruthy();
    await page.goto(`/runs/${doubleCharge.run_id}`);

    const approve = page.getByRole("button", { name: /^Approve/ });
    await expect(approve).toBeVisible();
    await expect(approve).toBeEnabled();
    await expect(
      page.getByText("Readers cannot approve a patch. Ask an owner or approver."),
    ).toHaveCount(0);
  });
});
