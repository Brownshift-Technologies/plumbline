# Plumbline, demo steps

## What the video has to contain

| Requirement | Where it happens |
|---|---|
| Short overview of the problem | B1, the first three paragraphs |
| Value proposition | B1, "an agent that can write a test can write any file" |
| Demo of the app in action | B2 to B5, run 4471 and the ledger |
| **Backend running on Google Cloud** | **B6, three pages, about twenty-five seconds** |

The last one is pass/fail. A submission without visible Google Cloud evidence
is eliminated before anyone scores the idea, so B6 is the highest-stakes part
of the recording.

---

Follow this while reading `DEMO_SCRIPT.md` aloud. Each step is cued by the
words you will be saying, so you never have to count paragraphs or watch a
clock.

Every step tells you three things: what to do, what you should see, and why it
matters. If what you see does not match, stop and fix it before recording. It
is cheaper than a bad take.

---

# Part A, before you press record

## A1. Wake the service up

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://plumbline-api-cxotjai2ta-uc.a.run.app/_health
```

**You should see:** `200`

**Why:** Cloud Run scales to zero when nobody is using it. The next request has
to start the container again, which takes about fifteen seconds against two
when it is warm. Skip this and that fifteen seconds happens on camera and looks
like the app is broken.

## A2. Open the demo and leave it open

Click **Open the live demo** once, before recording. Your sandbox persists, so
a second take starts exactly where the first one left off.

**You should see:** a blue banner reading *This is your own live sandbox*, and
in the left rail `Runs 18`, `Findings 6`, `Behaviours 99+`, `Agents 11`.

**Why:** those numbers are read from your own workspace. If they are missing or
wrong, the page has not finished loading and you will be pointing at nothing.

## A3. Hard-refresh once

`Ctrl+Shift+R`.

**Why:** `index.html` is served `no-store` now, but a browser that cached it
before that fix will still hold an old bundle. One hard refresh clears it.

## Open these tabs, left to right, before you record

Order matters. You move left to right and never go back.

```
https://plumbline-api-cxotjai2ta-uc.a.run.app
https://console.cloud.google.com/run?project=total-fiber-399801
https://console.cloud.google.com/run/jobs/details/us-central1/plumbline-worker/executions?project=total-fiber-399801
https://console.cloud.google.com/apis/api/aiplatform.googleapis.com/metrics?project=total-fiber-399801
```

Or from a terminal:

```bash
xdg-open https://plumbline-api-cxotjai2ta-uc.a.run.app
xdg-open "https://console.cloud.google.com/run?project=total-fiber-399801"
xdg-open "https://console.cloud.google.com/run/jobs/details/us-central1/plumbline-worker/executions?project=total-fiber-399801"
xdg-open "https://console.cloud.google.com/apis/api/aiplatform.googleapis.com/metrics?project=total-fiber-399801"
```

| Tab | Used at | For |
|---|---|---|
| 1 | B2 to B5, B7 | The app: the run, the gate, the ledger |
| 2 | B6 | Cloud Run services, with the `.run.app` URL visible |
| 3 | B6 | Job executions, the background work |
| 4 | B6 | Vertex AI traffic |

Use the Vertex **metrics** page, not Cloud Logging. `generateContent` calls do
not appear in Cloud Logging unless data-access audit logging is on, and it is
not. An empty log search on camera is worse than not looking.

## A4. Set up OBS

- 1920x1080, 30fps
- Screen capture as one source, your microphone as a second, on the same
  recording
- MP4, not MKV, so you can upload without converting

Close Slack, mail, and anything that shows notifications.

---

# Part B, during the take

## B1. The opening, on the app's Home screen

Start with tab 1 on Home and just speak. Nothing to click for the first five
paragraphs, through *"one gate everything they do passes through."*

**Why not slides:** the Home screen already carries the numbers you are
implicitly claiming, and a live product on screen from the first second is a
stronger opening than a title card.

## B2. When you say *"Here is a run..."*

Click **Runs** in the left rail, then the row for **Run 4471**.

**You should see:** the header reading **All held**, *Pull request #2211,
retry idempotency, commit 8f21c04*, and **341 held, 1 failed**. Below it the
**Reasoning chain**, seven steps with real timings.

**Why this run:** it is seeded and always present the moment the demo opens, so
it needs no run started first and cannot fail on camera.

**Say this if you want to be precise:** the grey line under the header says
this run is replayed from a seeded fixture. Do not claim it is executing live.
Everything you point at in it, the agents, the scopes, the gate, the ledger,
is real.

## B3. Through the agent paragraphs

Let the reasoning chain sit on screen while you read the Cartographer, Chaos,
Runner and Triager paragraphs. Point at each row as you name it.

**You should see,** in order: `Cartographer mapped 47 routes`, `Author wrote 6
behaviours`, `Healer repaired 4 selectors`, `Chaos injected 240ms of latency on
payments-api`, `Runner saw two charges`, `Triager reproduced it 5 times out of
5`.

**Why:** every one of those rows carries its own reasoning underneath. Chaos
says it chose 240ms because the provider's p99 is 210. That detail is what
separates this from a tool that picked a round number.

## B4. When you say *"Then Surgeon wrote the fix..."*

Scroll to **Proposed patch**.

**You should see:** the last step, `Surgeon opened the pull request and
stopped`, tagged **at gate**. The gate box reading *Policy will not let an
agent merge anything under payments/\*.* The diff on
`src/checkout/payment-client.ts`, **+7 -2**.

**Leave it on screen through the whole "it cannot merge this" paragraph.**

**Why:** this is the most persuasive twenty seconds in your video. Every other
entry will show their agent succeeding. You are showing yours stopping, and
saying out loud why it cannot proceed. Do not rush it.

## B5. Click Approve and merge

Click it as you say *"A human signs it or it does not ship."*

## B6. When you say *"Let me show you where this runs..."*

Move through tabs 2, 3 and 4, slowly enough that a paused frame is readable.
About twenty-five seconds.

**Tab 2, Cloud Run services.** `plumbline-api` in the list with its `.run.app`
URL beside it. That single row covers both "Cloud Run dashboard" and "URL of
.run" from the requirements.

**Tab 3, Job executions.** `plumbline-worker` with completed executions. This
is the strongest single frame you have: an agent doing real background work.

**Tab 4, Vertex AI metrics.** A live traffic graph. Point at it while you say
the Gemini line.

**Do not narrate the URLs.** Let the address bar do it.

## B7. When you say *"Every decision that gate made is here..."*

Back to tab 1. Click **Audit ledger**, then click **Verify chain**.

**You should see:** the result confirming the chain is intact.

Read the last two lines, pause two seconds, then stop recording. Do not cut the
moment you stop speaking.

---

# Part C, when something goes wrong

## The app is slow on camera

It cold-started. You skipped A1. Reload once and it is about two seconds.

## The sidebar numbers are wrong or missing

The page has not finished loading, or `/api/summary` failed. Reload. If they
are still wrong, the deploy is behind the code and you should redeploy before
recording.

## The Approve button is disabled

You are signed in as something other than the demo session, or the page loaded
before `/api/auth/me` resolved. Reload. A demo session is allowed to approve;
that is deliberate, and it is the demo's whole hero moment.

## Someone asks whether the run is real

Be straight about it. Run 4471 is replayed from a seeded fixture and the page
says so on screen. The fleet does execute for real against a live URL on a real
account, and the crawl and audit in that path are genuine, but writing spec
files and opening pull requests needs a connected GitHub repository, and that
connection has no user interface yet. Say that rather than implying otherwise.

## Someone asks why the agents skip on a real run

Author, Healer and Surgeon skip with an explanatory step when no repository is
connected. That is designed behaviour, not a crash: every agent either runs or
says why it did not.

## Never fake a run

If a take goes wrong, reset and go again. A failed take costs three minutes. A
judge noticing a staged result costs the submission.
