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

test("AUDIT", async ({ page }) => {
  test.setTimeout(600_000);
  const problems: Problem[] = [];
  let where = "startup";

  page.on("pageerror", (e) => problems.push({ where, kind: "pageerror", detail: e.message }));
  page.on("console", (m) => {
    if (m.type() === "error") problems.push({ where, kind: "console", detail: m.text().split("\n")[0].slice(0, 200) });
  });
  page.on("response", async (r) => {
    const u = new URL(r.url()).pathname;
    if (!u.startsWith("/api")) return;
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

  // --- click every enabled control on every screen --------------------
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
        await btn.click({ timeout: 4000 });
        await page.waitForLoadState("networkidle").catch(() => {});
        if (await page.getByText("Unexpected Application Error!").count())
          problems.push({ where, kind: "CRASH", detail: "clicking this crashed the app" });
        await page.keyboard.press("Escape").catch(() => {});
        if (!page.url().includes(path) && path !== "/") { await page.goto(path); await page.waitForLoadState("networkidle"); }
      } catch (e) {
        problems.push({ where, kind: "click-failed", detail: String(e).split("\n")[0].slice(0, 160) });
      }
    }
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
  where = "home -> start a run";
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const prompt = page.getByLabel("Describe a behaviour");
  if (await prompt.count()) {
    await prompt.fill("Audit sweep: a checkout total must never go negative");
    await page.getByTitle("Write and run this behaviour").click({ timeout: 5000 }).catch(() => {});
    await page.waitForLoadState("networkidle");
    if (await page.getByText("Unexpected Application Error!").count())
      problems.push({ where, kind: "CRASH", detail: "starting a run crashed the app" });
    if (page.url().endsWith("/")) problems.push({ where, kind: "no-nav", detail: "starting a run did not open the run" });
    else {
      where = "run detail (live)";
      await page.waitForTimeout(6000); // let SSE stream
      if (await page.getByText("Unexpected Application Error!").count())
        problems.push({ where, kind: "CRASH", detail: "run detail crashed while streaming" });
    }
  } else {
    problems.push({ where, kind: "missing", detail: "no behaviour prompt on Home" });
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
