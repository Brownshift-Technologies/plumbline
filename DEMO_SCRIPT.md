# Demo script — ≤ 4 minutes

Structure: the friction (what's broken about testing today), the value (what Plumbline does
about it), then the demo — ending on the strongest beat in the product: a failing run
produces a real pull request that the agent that wrote it **cannot merge**.

Record against the deployed Cloud Run service, not a local server — the Google Cloud proof
shot below is a **Stage One pass/fail requirement** for this hackathon and has to be real.

---

## 0:00–0:30 — The friction

> "Every team that ships fast has the same three problems with testing. Someone has to write
> the tests, by hand, against a UI that changes every sprint — so the suite rots faster than
> anyone can fix it. Nobody tests the failure paths, because writing a test for 'what happens
> when the payment API times out' is tedious and gets skipped. And when something breaks in
> production, root-causing it and getting a safe fix reviewed takes hours somebody doesn't have."

Show, briefly, a real flaky-test complaint or a red CI run — anything that visually says
"this is the status quo."

## 0:30–1:05 — The value

> "Plumbline is a fleet of eleven agents that do this instead of a person. One maps your app.
> One turns plain English into a Playwright test. One repairs a test's selectors when your UI
> changes, without ever touching what it actually asserts. One breaks your app on purpose —
> latency, faults, toxic input — because that's the coverage nobody writes by hand. One runs
> everything, deterministically, with **no model in the execution loop** — a bug you can't
> reproduce isn't a bug report, it's a rumor. One root-causes a failure and reproduces it five
> times before calling it a bug instead of a flake. And when it finds something real, one
> proposes the fix and opens a pull request — then stops, because merging a patch to a
> payments file is not something an agent gets to decide alone."

Cut to the surface map or the fleet screen — eleven agent tiles, visibly distinct jobs.

## 1:05–1:30 — Google Cloud proof shot (Stage One)

Non-negotiable, shown plainly, no narration needed beyond naming what's on screen:

1. The Cloud Run console: `plumbline-api` and the `plumbline-worker` job, both **green**,
   in `us-central1`.
2. The service's real `https://plumbline-api-<hash>-uc.a.run.app` URL — paste it into the
   address bar on screen, load `/_health`, show the JSON response
   (`{"ok": true, "model": "gemini-3.5-flash", "gemini_location": "global", ...}`).
3. Vertex AI request logs (Cloud Logging, filtered to `aiplatform.googleapis.com`, `global`
   location) showing real `gemini-3.5-flash` calls from the run about to be demoed.

> "This is running on Cloud Run right now — the API scales to zero when idle, the worker is a
> Cloud Run Job that spins up one execution per run, and every model call goes to Vertex AI's
> `gemini-3.5-flash`, on its `global` endpoint, which is the only place that model is served."

## 1:30–3:35 — The demo

**1:30** Open the deployed URL. Click **open the live demo** — no Google account needed, a
fresh seeded workspace every time.

**1:45** Land on Home. Point at the run history, the findings count, the fleet status —
"eighteen runs, seven findings, one waiting on a human right now."

**2:00** Open the surface map — the route graph Cartographer built. "It found this by
crawling the app, not by reading a sitemap someone maintained by hand."

**2:15** Open Runs, click into the run that found the checkout bug. Let the step list play —
Chaos injecting 240ms of latency on the payments API, Runner seeing two charges instead of
one, Triager reproducing it five times out of five and ruling it a real bug, not a flake.
Point at the ledger entry each step is writing as it happens.

**2:50** Open the finding. Show the patch: Surgeon's diff, the pull request link, and the
gate state — **awaiting approval**. Try to merge it as the reader-role demo account: the
button is disabled, with the actual reason shown ("payments/* requires an owner's approval"),
not just greyed out silently.

**3:10** Switch to an owner account with TOTP. Approve. Show the pull request going from
blocked to merged — a human, not an agent, made the call that mattered.

**3:25** One more screen: the audit ledger, filtered to this run. "Every decision the Gateway
made — allowed, blocked, gated — is right here, signed, in order, and nobody can quietly edit
it afterward."

## 3:35–4:00 — Close

> "Eleven agents, one gate every one of them has to pass through, and a human signing off
> before anything real changes. That's Plumbline."

Show the URL on screen one more time. End.

---

## Recording notes

- Keep the browser at the deployed URL throughout section 2 onward — the demo workspace's
  writes are accepted in the UI and discarded server-side (an honest banner says so), so
  nothing in the recording depends on a specific prior state persisting.
- If a live run needs to be triggered fresh for the recording rather than replayed from the
  seeded history, expect it to take real wall-clock minutes — Cartographer, Author, Runner
  and Triager each make a real Vertex AI call and Runner drives a real Playwright browser.
  Pre-run it once before recording and cut to the finished run rather than watching it live,
  or use the seeded gated-patch fixture, which is already there the moment the demo opens.
