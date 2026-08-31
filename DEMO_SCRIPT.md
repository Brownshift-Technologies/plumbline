# Demo script — 3 minutes

Written against the actual screens, in the order you will visit them. Every
label, button and number below was read off the deployed app on 2026-08-31. If
something here does not match what you see, this file is stale — fix the file,
do not improvise on camera.

**Product:** Plumbline, by **Brownshift Technology**
**Live:** https://plumbline-api-cxotjai2ta-uc.a.run.app
**Repo:** https://github.com/rogerkoranteng-crypto/plumbline

---

## Before you record

1. **Warm it.** Cloud Run scales to zero. First hit after idle is ~15s, warm is
   ~2s. Load the app, click through Home → Runs → Findings, then reload.
2. **Hard-refresh** (`Ctrl+Shift+R`).
3. **Open the live demo** and leave the tab open. The sandbox persists, so a
   second take starts where you left off.
4. Three tabs: **(A)** the app, **(B)** Cloud Run console, **(C)** Cloud Logging
   filtered to `aiplatform.googleapis.com`.
5. Browser at 1440x900, zoom 100%, bookmarks bar hidden.

**Say this, not that.** The run you open is replayed from a seeded fixture, and
the run page says so on screen in grey. Do not claim it is executing live. What
IS real and worth claiming: the agents, the Gateway, the scopes, the gate, the
hash-chained ledger, and the Vertex AI calls.

---

## 0:00-0:30 — The problem, and who is solving it

Say this over the sign-in screen, before you click anything.

> "Every team that ships quickly has the same three problems with testing, and
> they compound.
>
> Someone writes the tests by hand, against a UI that changes every sprint, so
> the suite rots faster than anyone can repair it. Nobody writes tests for the
> failure paths, because writing a test for 'what if the payment provider takes
> 240 milliseconds too long' is tedious, so that coverage simply does not exist
> — and that is exactly where the expensive bugs live. And when one of them does
> reach production, working out why and getting a safe fix reviewed eats a day
> that nobody had.
>
> The obvious answer is to point an AI at it. That is also where the real
> problem starts. An agent that can write a test can write any file. An agent
> that can open a pull request can merge one. Whether a model can find a bug
> stopped being the interesting question a while ago. The question is what it is
> allowed to do the second it finds one.
>
> We are **Brownshift Technology**, and we are building **Plumbline**: eleven
> scoped agents that test your software, root-cause what they break, and open
> the pull request they are not permitted to merge. Every tool call they make
> passes through one gate, and every decision that gate makes is written to a
> ledger you can verify yourself."

**Then click `Open the live demo`.**

---

## 0:30-0:50 — Home

**Screen: Home.** You are already here after opening the demo.

Point at the left rail: **Runs 18, Findings 6, Behaviours 99+, Agents 11**.

> "Eleven agents. Eighteen runs. Six findings still open, and one of them is
> sitting on a human right now."

Point at **Needs your attention**: *"Waiting on you since..."* and
**A retried payment charges the customer twice**, `Failing`,
`/checkout/payment`, `Reproduced 5 of 5`.

> "That's the one. A retried payment charging twice, reproduced five times out
> of five. Nobody wrote that test."

---

## 0:50-1:10 — Google Cloud proof shot (Stage One requirement)

Tab B, then C, then back to A. Name what is on screen, nothing more.

1. **Cloud Run console** — `plumbline-api` and the `plumbline-worker` job, both
   green, `us-central1`.
2. Paste into the address bar and press Enter:
   `https://plumbline-api-cxotjai2ta-uc.a.run.app/_health`
   Show: `{"ok":true,"model":"gemini-3.5-flash","gemini_location":"global",...}`
3. **Cloud Logging**, filtered to `aiplatform.googleapis.com` — real
   `gemini-3.5-flash` calls.

> "This runs on Cloud Run. The API scales to zero, the worker is a Cloud Run Job
> that spins up one execution per run, and every model call goes to Vertex AI's
> gemini-3.5-flash on the global endpoint, which is the only place that model is
> served."

---

## 1:10-1:25 — Plain English in

**Screen: Home.** Click the box under *"Good afternoon, Demo. What should we put
under test?"*

**Type exactly:**

```
A customer who retries a slow payment should only be charged once
```

**Press the blue arrow** to the right of the box.

> "That's the whole input. A sentence."

Let the new run open and the first steps appear, then move on. Do not wait for
it to finish on camera.

---

## 1:25-1:50 — What the agents did

**Screen: Runs -> Run 4471.** Click **Runs** in the left rail, then the row for
**Run 4471**.

Point at the header: **All held**, *"Pull request #2211 - retry idempotency -
commit 8f21c04"*, **341 held - 1 failed**.

Then walk the **Reasoning chain**, which shows seven steps with real timings:

- `Cartographer mapped 47 routes` — *"12 new since run 4469. /checkout/3ds still has no behaviour."*
- `Author wrote 6 behaviours for /checkout/payment`
- `Healer repaired 4 selectors` — *"The nav refactor moved the submit control. Re-anchored to roles rather than classes."*
- `Chaos injected 240ms of latency on payments-api` — *"Chosen because the provider's p99 is 210ms and nothing exercised the slow path."*
- `Runner saw two charges` — *"Two POST /v1/charges with different idempotency keys, 30ms apart."*
- `Triager reproduced it 5 times out of 5` — *"Not a flake. Deterministic under the same seed."*

> "Chaos picked 240 milliseconds because the provider's p99 is 210 and nothing
> had ever exercised the slow path. Runner is the one agent with no model in the
> loop at all, on purpose, because a bug you can't reproduce is a rumour. And
> Triager reproduced it five times out of five before it would call it a bug
> instead of a flake."

---

## 1:50-2:20 — The beat the product is built around

**Same screen, scroll to Proposed patch.**

Point at, in order:

1. The last step: **`Surgeon: Surgeon opened the pull request and stopped`**,
   tagged **at gate**
2. The gate box: **Blocked at a gate** — *"Policy will not let an agent merge
   anything under payments/*."*
3. The diff on `src/checkout/payment-client.ts`, **+7 -2**, with **Open on
   GitHub**
4. Under the diff: *"Verified: re-run against the same seed and latency, then
   the full suite. The patch reverts itself if either check fails."*

> "It found the bug, wrote the patch, verified it, opened the pull request, and
> stopped. It cannot merge this. No agent has pr.merge in its scope. That's not
> a prompt asking it politely, the permission isn't there, and the Gateway
> checks on every call."

**Click `Approve and merge`.**

> "A human makes that call, and the ledger records which one."

---

## 2:20-2:35 — Two screens that prove it isn't a mock

**Screen: Surface map.** Click it in the left rail.

> "Seventeen routes, found by crawling, not from a sitemap somebody maintained.
> Fifteen have a behaviour written against them. Two don't, and it says so
> rather than rounding up."

Point at the **Summary**: *"Coverage says what was measured. It is not a promise
about the 2 routes nobody has written a behaviour for yet."*

**Screen: Agents.** Click it.

> "Eleven specialists, each with a scoped set of tools and nothing more. Look at
> cartographer: browser.read and graph.write. That's it. Surgeon is the only one
> that can even open a pull request."

---

## 2:35-2:55 — Policy and the ledger

**Screen: Policy & gates.** Click it.

Point at the one row whose decision reads **gated**: `surgeon` / `pr.merge` /
`src/checkout/payment-client.ts`.

> "Every call, every decision. Allowed, allowed, allowed, and then one gated:
> surgeon asking to merge."

**Screen: Audit ledger.** Click it, then click **Verify chain**.

> "Append-only, hash-chained, and you don't have to trust it. Verify re-signs
> every entry and checks it against the next. Anyone can run this."

Show the result.

---

## 2:55-3:00 — Close

> "Eleven agents, one gate they all pass through, and a person signing off
> before anything real changes. That's Plumbline, by Brownshift Technology."

Leave the URL on screen.

---

## If a take goes wrong

- **Run 4471 is seeded** and is there the moment the demo opens. It needs no run
  started first, so you can skip 1:10 entirely.
- **The demo sandbox persists**, so a second take keeps whatever you did in the
  first.
- **If the app is slow**, it cold-started. Reload once and it is ~2s.

## Optional B-roll if you need 20 more seconds

- **Behaviours** — 342 of them, in plain English, filterable by tag and owner.
- **Settings -> Members / Security / Billing** — it is a product, not a demo.
- **Findings** — six open, each with the agent that found it and how old it is.
