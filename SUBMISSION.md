# Devpost submission — Plumbline

Everything below is ready to paste. Facts verified against the deployed app and the repo on
2026-08-31.

---

## Links (fill these into Devpost first)

| Field | Value |
|---|---|
| **Try it out** | https://plumbline-api-cxotjai2ta-uc.a.run.app |
| **Repository** | https://github.com/rogerkoranteng-crypto/plumbline |
| **Video** | *(paste the YouTube/Vimeo URL once uploaded — must be public/unlisted, not private)* |
| **Category** | Fortified Enterprise Fleet |
| **Also entering** | Startup Excellence |

---

## Project name

```
Plumbline
```

## Tagline (Devpost limits this — keep it short)

```
Eleven governed agents that test your software, find the bug nobody wrote a test for, and open the pull request they are not allowed to merge.
```

---

## Inspiration

Three things are true of every team that ships quickly, and they are all testing problems.

Tests get written by hand against a UI that changes every sprint, so the suite rots faster
than anyone can repair it. Nobody writes tests for the failure paths — what happens when the
payment provider takes 240ms too long — because it is tedious, so that coverage does not
exist. And when something does break, root-causing it and getting a safe fix reviewed costs
hours nobody has.

The obvious answer is "point an AI at it", and the obvious answer is where the real problem
starts. An agent that can write a test can write a file. An agent that can open a pull
request can merge one. The interesting question was never whether a model can find a bug —
it was what happens the moment it finds one, and who is allowed to act on it.

## What it does

Plumbline points a fleet of eleven scoped agents at a running application.

**Cartographer** crawls it and maintains a route graph. **Author** turns plain English into
Playwright specs. **Healer** re-anchors selectors when the UI drifts, without ever changing
what a test asserts. **Chaos** attacks it on purpose — latency, faults, toxic input.
**Runner** executes specs with *no model in the loop at all*, because a bug you cannot
reproduce is a rumour, not a bug report. **Triager** root-causes failures and reproduces them
five times before calling one a bug rather than a flake. **Surgeon** writes the patch and
opens the pull request. **Sentinel**, **Auditor**, **Oracle** and **Economist** watch the
fleet itself.

Then it stops. Surgeon opens the pull request and cannot merge it — not because a prompt asks
it not to, but because `pr.merge` is not in any agent's tool scope, and every tool call in the
system passes through one function that checks.

You describe a behaviour in English, and a run happens: a spec is written, executed,
root-caused, patched, and parked at a human approval gate — with every decision the Gateway
made recorded in a hash-chained ledger you can verify from the UI.

## How we built it

**The Gateway is the whole design.** Every tool call from every agent goes through one
function that resolves the caller's identity, checks the tool against that agent's declared
scope (deny by default), scans model-bound text for injection, redacts PII and secrets,
appends the decision to an append-only ledger, and emits an OpenTelemetry span. There is no
second path. An agent cannot reach a tool except through it.

Two kinds of policy, and only one is tenant-configurable: **tool scopes are static in code**,
because they describe what an agent *is* and no customer setting should be able to grant
Cartographer `pr.merge`; **gate rules are per-workspace and versioned**, because they are what
an owner actually configures. Every ledger entry records the policy version in force, so an
audit can answer "which rules allowed this".

**The ledger is hash-chained** — each entry signs the previous one — with a transactional
head pointer, so tampering is detectable rather than merely discouraged. `GET
/api/ledger/verify` walks the chain and the UI exposes it as a button.

**Split architecture, for a real reason.** The API answers in milliseconds; a run takes
minutes and drives a browser. The API is Cloud Run (scale-to-zero); the worker is a Cloud Run
Job, one execution per run, with a full CPU and no request timeout. They communicate only
through Firestore and Pub/Sub — the worker never calls the API.

**Stack:** FastAPI + Python 3.13, Firestore, Pub/Sub, Cloud Run + Cloud Run Jobs, Vertex AI
`gemini-3.5-flash`, Secret Manager, Artifact Registry, Cloud Build, Playwright/Chromium,
OpenTelemetry. React 18 + Vite + TypeScript on the front, plain CSS tokens, no UI framework.
Plumbline is also an MCP server, and consumes customer MCP servers through the same Gateway.

**Scale:** ~30k lines of Python, ~8.4k of TypeScript, 1,091 backend tests, 113 frontend
tests, 18 Playwright end-to-end tests including accessibility audits, across 77 commits.

## Challenges we ran into

**The demo was specified wrong, and it was my spec.** The original design said demo sessions
should be read-mostly — writes accepted in the UI, discarded server-side. It was implemented
faithfully across 31 refusal sites, and the result was a product where a visitor could not do
anything. It was not a bug; the specification was wrong. Every demo session now gets its own
writable sandbox, isolated by the same workspace scoping every route already applied.

**A missing API route crashed the entire app, invisibly.** `BillingPane` fetched
`/api/billing/invoices`, a route that did not exist. Because the SPA is served from a
catch-all, the request returned `index.html` with a **200**, and a component called `.map` on
HTML. Neither test suite could see it: the backend tests passed because there was no route to
test, the frontend tests passed because they mock the API client. There is now a test that
extracts every path the frontend calls and resolves it against the real OpenAPI schema — it
found a third missing route on its first run.

**Deployed fixes never reached the browser.** `index.html` was served with no `Cache-Control`,
no `ETag`, no `Last-Modified` — which does not mean "do not cache", it means the browser may
cache it heuristically. Since `index.html` names the fingerprinted bundle, a stale copy pinned
users to old, broken code and no amount of redeploying could reach them.

**`gemini-3.5-flash` is served only on Vertex location `global`.** Every regional endpoint
404s. `gemini-2.5-flash` works regionally, which is the trap — it runs fine locally and
silently fails the version requirement.

## Accomplishments that we're proud of

Adversarial review found things a confirming review never would: a sandbox guard whose test
still passed after the flag was deleted, two URL-normalisation bypasses (`/\evil.com` and a
tab-embedded variant), and Surgeon able to **delete** a spec file while reporting success with
a live PR URL.

The discipline that produced those: **a test only proves something if you have watched it fail
for the right reason.** Every fix in this repo was verified by reverting it and confirming the
test went red with the expected message. That caught an all-screens smoke test of my own that
passed against a deliberately broken build — asserting an *absence* matches on the first poll,
before the crash paints, and React Router swallows the error so no page error ever fires.

The audit ledger genuinely verifies. The gate genuinely cannot be bypassed. Both are asserted
by tests that fail when they are broken.

## What we learned

The defects live in the seams. In one session, driving the assembled product found four bugs
that 1,091 backend tests and 113 frontend tests could not: a findings API that dropped
`run_id`, so every row on that screen was silently unclickable; a UI that disabled the Approve
button for the exact session the API deliberately allows to click it; a fixture that never
linked its finding to its run, so the entire patch section had never rendered; and — the
moment that section finally rendered — a serious colour-contrast failure inside it that had
been there all along, in markup nothing had ever drawn.

Each half was correct on its own terms. Only together were they wrong.

## What's next for Plumbline

A Settings pane to connect a GitHub repository — the backend, the shallow clone, real spec
files, real branches and real pull requests are all built and tested; only the UI to attach a
repo is missing, which is what keeps demo runs replayed rather than live. Then on-premise
runners for teams that cannot point a cloud service at a staging environment, mobile app
testing, and SAML.

## Built with

```
python, fastapi, react, typescript, vite, playwright, google-cloud-run, google-cloud-firestore,
google-cloud-pubsub, vertex-ai, gemini, secret-manager, artifact-registry, cloud-build,
opentelemetry, model-context-protocol, chromium, argon2
```

---

## Pre-submission checklist

- [ ] Video recorded, **under 3 minutes**, uploaded, and set to **public or unlisted** (a
      private video is the single most common disqualification)
- [ ] Video shows the Cloud Run console, the live `/_health` response, and Vertex AI logs
      (Stage One pass/fail)
- [ ] Repo is public: https://github.com/rogerkoranteng-crypto/plumbline ✅
- [ ] Live URL loads and the demo door works ✅ (warm it first — cold start is ~15s)
- [ ] Category selected: **Fortified Enterprise Fleet**
- [ ] **Startup Excellence** opted into
- [ ] Submitted on behalf of the incorporated org, with the corporate email
- [ ] Devpost fields pasted from this file
- [ ] Submit before **17:00 PDT, 2026-08-31**
