import { test, expect } from "@playwright/test";
import { openLiveDemo } from "./helpers";

/** Full-system audit: every screen, every control, everything that breaks. */

const SCREENS = ["/", "/runs", "/surface", "/findings", "/behaviours", "/agents", "/policy", "/ledger", "/settings"];
const TABS = ["Profile", "Security", "Members", "Workspace", "Billing"];

type Problem = { where: string; kind: string; detail: string };

// Opt-in: `PLUMBLINE_AUDIT=1 npm run test:e2e -- audit.spec.ts`, optionally
// with PLUMBLINE_E2E_BASE_URL pointing at a real deployment. It clicks
// every enabled control on every screen, which is slow and deliberately
// leaves the app in whatever state those clicks produce -- useful for
// finding crashes, too noisy to gate every commit on. smoke.spec.ts is the
// always-on version.
test.skip(!process.env.PLUMBLINE_AUDIT, "opt-in: set PLUMBLINE_AUDIT=1");

test("AUDIT", async ({ page, browser }) => {
  test.setTimeout(600_000);
  const problems: Problem[] = [];
  let where = "startup";

  page.on("pageerror", (e) => problems.push({ where, kind: "pageerror", detail: e.message }));
  page.on("console", (m) => {
    if (m.type() === "error") problems.push({ where, kind: "console", detail: m.text().split("\n")[0].slice(0, 200) });
  });
  let lastRunPost = "(none)";
  page.on("response", async (r) => {
    const u = new URL(r.url()).pathname;
    if (!u.startsWith("/api")) return;
    if (u === "/api/runs" && r.request().method() === "POST") {
      lastRunPost = `${r.status()} ${(await r.text().catch(() => "")).slice(0, 200)}`;
    }
    if (r.status() >= 400) {
      problems.push({ where, kind: `http${r.status()}`, detail: `${r.request().method()} ${u}` });
      return;
    }
    const ct = r.headers()["content-type"] ?? "";
    if (ct.includes("text/html")) {
      problems.push({ where, kind: "html-for-api", detail: `${r.request().method()} ${u} returned index.html (no such route)` });
    }
  });

  await openLiveDemo(page);

  // --- every screen renders ------------------------------------------
  for (const path of SCREENS) {
    where = `screen ${path}`;
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const crashed = await page.getByText("Unexpected Application Error!").count();
    if (crashed) problems.push({ where, kind: "CRASH", detail: "route threw during render" });
    const h1 = await page.getByRole("heading", { level: 1 }).count();
    if (!h1) problems.push({ where, kind: "no-h1", detail: "screen did not render a heading" });
  }

  // --- every settings tab --------------------------------------------
  await page.goto("/settings");
  for (const tab of TABS) {
    where = `settings/${tab}`;
    await page.getByRole("tab", { name: tab }).click();
    await page.waitForLoadState("networkidle");
    if (await page.getByText("Unexpected Application Error!").count())
      problems.push({ where, kind: "CRASH", detail: "tab threw during render" });
    if (!(await page.getByRole("tabpanel").count()))
      problems.push({ where, kind: "empty", detail: "tabpanel vanished" });
  }

  // --- detail pages: open a run, a finding, a behaviour ---------------
  where = "runs -> open first run";
  await page.goto("/runs");
  await page.waitForLoadState("networkidle");
  const runRow = page.locator("main tbody tr, main [role='row']").first();
  if (await runRow.count()) {
    await runRow.click({ timeout: 5000 }).catch(() => {});
    await page.waitForLoadState("networkidle");
    if (await page.getByText("Unexpected Application Error!").count())
      problems.push({ where, kind: "CRASH", detail: `run detail crashed at ${page.url()}` });
  } else {
    problems.push({ where, kind: "empty", detail: "Runs listed no rows to open" });
  }

  where = "findings -> open first finding";
  await page.goto("/findings");
  await page.waitForLoadState("networkidle");
  const fRow = page.locator("main tbody tr, main [role='row']").first();
  if (await fRow.count()) {
    await fRow.click({ timeout: 5000 }).catch(() => {});
    await page.waitForLoadState("networkidle");
    if (await page.getByText("Unexpected Application Error!").count())
      problems.push({ where, kind: "CRASH", detail: `finding detail crashed at ${page.url()}` });
    if (page.url().endsWith("/findings"))
      problems.push({ where, kind: "dead-row", detail: "clicking a finding navigated nowhere" });
  }

  // --- the primary action: describe a behaviour and run it ------------
  // In its OWN browser context. Sharing the page with the journeys above
  // made this report "did not open the run" while the same steps in a
  // clean context posted a 202 and navigated fine -- a false finding that
  // cost several rounds. A fresh context is what a real visitor has.
  {
    where = "home -> start a run (fresh context)";
    const ctx = await browser.newContext();
    const fresh = await ctx.newPage();
    let posted = "(none)";
    fresh.on("pageerror", (e) => problems.push({ where, kind: "pageerror", detail: e.message }));
    fresh.on("console", (m) => {
      if (m.type() === "error") problems.push({ where, kind: "console", detail: m.text().split("\n")[0].slice(0, 200) });
    });
    fresh.on("response", async (r) => {
      const u = new URL(r.url()).pathname;
      if (u === "/api/runs" && r.request().method() === "POST") posted = String(r.status());
    });

    await openLiveDemo(fresh);
    await fresh.goto("/");
    await fresh.waitForLoadState("networkidle");
    await fresh.getByLabel("Describe a behaviour").fill("Audit sweep: a checkout total must never go negative");
    try {
      await fresh.getByTitle("Write and run this behaviour").click({ timeout: 8000 });
    } catch (e) {
      problems.push({ where, kind: "click-failed", detail: String(e).split("\n")[0].slice(0, 180) });
    }
    // waitForLoadState("networkidle") is NOT enough here and this cost
    // several rounds: it can resolve before the click's fetch has even
    // started, so the URL gets checked while the request is still in
    // flight and the run looks like it never started. Wait for the
    // navigation itself.
    await fresh.waitForURL(/\/runs\/[^/]+$/, { timeout: 20_000 }).catch(() => {});

    if (!/\/runs\/[^/]+$/.test(fresh.url())) {
      problems.push({ where, kind: "no-nav", detail: `POST /api/runs=${posted}, url=${fresh.url()}` });
    } else {
      where = "run detail (live SSE)";
      await fresh.waitForTimeout(6000);
      if (await fresh.getByText("Unexpected Application Error!").count())
        problems.push({ where, kind: "CRASH", detail: "run detail crashed while streaming" });
      // By agent name, not by markup: the step list is not <li>/<tr>, and
      // guessing its tags produced a "no steps streamed" finding on a run
      // that was visibly streaming.
      const steps = await fresh.getByText(/Cartographer|Author|Runner|Triager|Surgeon|Chaos/).count();
      if (steps === 0) {
        const seen = (await fresh.locator("main").innerText().catch(() => "")).replace(/\n+/g, " | ").slice(0, 200);
        problems.push({ where, kind: "no-steps", detail: `no agent steps streamed in. screen: ${seen}` });
      }
    }
    await ctx.close();
  }

  // --- sidebar navigation ---------------------------------------------
  for (const name of ["Home", "Runs", "Surface map", "Findings", "Behaviours", "Agents", "Policy & gates", "Audit ledger", "Settings"]) {
    where = `nav :: ${name}`;
    const link = page.locator("#sidebar").getByRole("link", { name }).first();
    if (!(await link.count())) { problems.push({ where, kind: "missing-nav", detail: `no nav link "${name}"` }); continue; }
    await link.click({ timeout: 5000 }).catch((e) => problems.push({ where, kind: "click-failed", detail: String(e).slice(0, 120) }));
    await page.waitForLoadState("networkidle");
    if (await page.getByText("Unexpected Application Error!").count())
      problems.push({ where, kind: "CRASH", detail: `nav to ${name} crashed` });
  }

  // --- click every enabled control on every screen --------------------
  // LAST, on purpose. This is destructive: it opens menus and dialogs and
  // leaves them open, and after ~100 clicks React's controlled inputs stop
  // tracking what Playwright types into them. Run before the journeys
  // above and 'start a run' silently posts nothing, which reads exactly
  // like a product bug and is not one.
  for (const path of SCREENS) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const labels = await page.locator("main button:enabled").evaluateAll(
      (els) => els.map((e) => (e.textContent ?? "").trim() || e.getAttribute("aria-label") || e.getAttribute("title") || "")
    );
    for (const label of [...new Set(labels)].filter(Boolean).slice(0, 14)) {
      where = `${path} :: "${label}"`;
      const btn = page.locator("main button:enabled").filter({ hasText: label }).first();
      try {
        if (!(await btn.count())) continue;
        // Retry once. These lists reload on their own (findings, runs), so
        // an element can detach between locate and click -- a timeout here
        // is usually the list re-rendering, not an unclickable control.
        // Verified by hand: the row this used to flag navigates fine.
        try {
          await btn.click({ timeout: 4000 });
        } catch {
          await page.waitForLoadState("networkidle").catch(() => {});
          await page.locator("main button:enabled").filter({ hasText: label }).first()
            .click({ timeout: 6000 });
        }
        await page.waitForLoadState("networkidle").catch(() => {});
        if (await page.getByText("Unexpected Application Error!").count())
          problems.push({ where, kind: "CRASH", detail: "clicking this crashed the app" });
        await page.keyboard.press("Escape").catch(() => {});
        // Always restore, comparing the actual pathname. The old guard was
        // `!url.includes(path) && path !== "/"`, which never restored Home:
        // clicking "Review patch" navigates to a run page, and every
        // subsequent click in the sweep then hunted for Home's buttons on a
        // run page and timed out. That produced a persistent "unclickable"
        // finding for a row that clicks fine.
        if (new URL(page.url()).pathname !== path) {
          await page.goto(path);
          await page.waitForLoadState("networkidle");
        }
      } catch (e) {
        problems.push({ where, kind: "click-failed", detail: String(e).split("\n")[0].slice(0, 160) });
      }
    }
  }

  // --- report ---------------------------------------------------------
  const seen = new Set<string>();
  const unique = problems.filter((p) => {
    const k = `${p.kind}|${p.detail}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  console.log("\n===== AUDIT FINDINGS (" + unique.length + ") =====");
  for (const p of unique) console.log(`[${p.kind}] ${p.where}\n    ${p.detail}`);
  console.log("===== END =====\n");
  expect(unique, "see AUDIT FINDINGS above").toEqual([]);
});
