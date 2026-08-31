import { test, expect } from "@playwright/test";
import { signInWithPassword } from "./helpers";

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
   * This is deliberately left as `test.fixme`, not deleted and not
   * loosened to check something the bug doesn't affect -- see task-17f's
   * brief. The body below is the real test: flip `test.fixme` to `test`
   * once a run response (REST or SSE) carries `finding_id` (or a
   * `Finding` carries `run_id` and RunDetail is wired to use it) and this
   * should pass unmodified.
   */
  test.fixme(
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
