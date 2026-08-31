# Demo script — 3 minutes

Every label, button and keystroke below was read off the deployed app on 2026-08-31. If a
label here does not match what you see, the app changed and this file is stale — fix this
file, don't improvise on camera.

**Live URL:** https://plumbline-api-cxotjai2ta-uc.a.run.app
**Repo:** https://github.com/rogerkoranteng-crypto/plumbline

---

## Before you hit record

1. **Warm the service.** Cloud Run scales to zero; the first request after an idle period
   takes ~15s, warm it's ~2s. Load the URL once and click through Home → Runs → Findings,
   then reload. Do this within a couple of minutes of recording.
2. **Hard-refresh** (`Ctrl+Shift+R`) so you are on the current bundle.
3. **Open the live demo once before recording** and leave that tab open. Your sandbox now
   persists, so the run you start in step 4 will still be there if you need a second take.
4. Have three tabs ready: **(A)** the app, **(B)** Cloud Run console, **(C)** Cloud Logging
   filtered to `aiplatform.googleapis.com`.
5. Browser at 1440×900. Zoom 100%. Hide bookmarks bar.

**One honesty note.** Demo-sandbox runs are *replayed from a seeded fixture* — the run page
says so on screen, in a grey line under the header. Do not say "this is executing against a
live app right now" over that screen. What is genuinely real, and what you should say: the
agents, the Gateway, the policy decisions, the hash-chained ledger, the approval gate, and
the Vertex AI calls. Real spec execution against a customer's own app requires connecting a
GitHub repo, which is a Settings pane that does not exist yet.

---

## 0:00–0:25 — The friction

> "If you ship fast, you already know how this goes. Someone writes the tests by hand,
> against a UI that changes every sprint, so the suite rots faster than anyone repairs it.
> Nobody writes tests for the failure paths, because writing a test for 'what if the payment
> API times out' is boring, so that coverage just doesn't exist. And when something does
> break, working out why and getting a safe fix reviewed eats a day."

## 0:25–0:50 — What it is

> "Plumbline is eleven agents that do that work instead. One maps your app. One turns plain
> English into a Playwright test. One repairs selectors when your UI drifts, without touching
> what the test actually asserts. One attacks the app on purpose. One runs everything with no
> model in the execution loop at all, because a bug you can't reproduce is a rumour, not a bug
> report. One works out why it failed. And one writes the fix, opens the pull request, and
> then stops."

**Click** `Agents` in the left nav. Eleven tiles, each with its own tool scope.

## 0:50–1:15 — Google Cloud proof shot (Stage One requirement)

Tab B, then tab C, then back. Say what is on screen, nothing more:

1. **Cloud Run console** — `plumbline-api` (service) and `plumbline-worker` (job), both
   green, `us-central1`.
2. **Paste** `https://plumbline-api-cxotjai2ta-uc.a.run.app/_health` into the address bar and
   **press Enter**. Show the JSON:
   `{"ok":true,"model":"gemini-3.5-flash","gemini_location":"global","service":"plumbline-api"}`
3. **Cloud Logging**, filtered to `aiplatform.googleapis.com` — real `gemini-3.5-flash` calls.

> "This is running on Cloud Run right now. The API scales to zero, the worker is a Cloud Run
> Job that spins up one execution per run, and every model call goes to Vertex AI's
> gemini-3.5-flash on the global endpoint, which is the only place that model is served."

## 1:15–1:35 — The demo door

**Go to tab A.** If you are already signed in, that's fine — otherwise **click**
`Open the live demo` on the sign-in screen.

> "No account, no Google sign-in. You get your own sandbox, and it's still there when you
> come back to it."

**Point at** the blue banner: *"This is your own live sandbox — everything you do here really
works, and it's still here when you come back."*

## 1:35–1:55 — Plain English in, a test out

**Click** the box under *"Good afternoon, Demo. What should we put under test?"*

**Type exactly:**

```
A customer who retries a slow payment should only be charged once
```

**Press** the blue **↑** button to the right of the box (or `Enter`).

The app navigates to a new run. Agent steps stream in one at a time — Cartographer, Author,
Runner, Triager.

> "That was a sentence. Author turns it into a Playwright spec, Runner executes it, and every
> step you're watching land is getting written to an append-only ledger as it happens."

## 1:55–2:20 — The failure that nobody would have written by hand

**Click** `Findings` in the left nav. **Click** the row
*"A retried payment charges the customer twice."*

That opens **Run 4471**. **Point at** the step list:

- Chaos injecting latency on the payments API
- Runner seeing two charges instead of one
- Triager: **Reproduced 5 of 5** — a bug, not a flake

> "Nobody wrote this test. Chaos went looking for it, which is exactly the coverage teams
> skip. And Triager reproduced it five times out of five before it would call it a bug rather
> than a flake."

## 2:20–2:50 — The beat the whole product is built around

Still on Run 4471. **Point at**, in order:

1. **Blocked at a gate**
2. *"Policy will not let an agent merge anything under payments/*."*
3. Surgeon's diff and **Pull request #2211**
4. The last step: *"Surgeon opened the pull request and stopped"*

> "So it found the bug, wrote the patch, verified it, opened the pull request, and stopped.
> It cannot merge this. No agent here has pr.merge in its tool scope. That's not a prompt
> asking it politely. The permission isn't there, and the Gateway checks on every single
> call."

**Click** `Approve and merge`.

> "A human makes that call, and the ledger records which one."

## 2:50–3:00 — Close

**Click** `Audit ledger` in the left nav, then **click** `Verify chain`.

Show `intact: true`.

> "Every decision the Gateway made, allowed or blocked or gated, hash-chained and in order,
> so you can tell if anyone edited it. Eleven agents, one gate they all go through, and a
> person signing off before anything real changes. That's Plumbline."

Leave the URL on screen. End.

---

## If you have 30 seconds spare (optional B-roll)

- `Surface map` → the route graph Cartographer built by crawling, plus the
  **Write the 2 missing** button.
- `Policy & gates` → the rules that produced the block you just showed.
- `Settings → Billing`, `Members`, `Security` → it's a product, not a demo.

## Fallback if a live run misbehaves on camera

Run 4471 is seeded and present the moment the demo opens — it needs no run to be started
first. Skip section 1:35 and go straight to Findings.
