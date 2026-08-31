# Devpost submission: Plumbline

Everything below is ready to paste. Facts verified against the deployed app and the repo on
2026-08-31.

---

## Links (fill these into Devpost first)

| Field | Value |
|---|---|
| **Try it out** | https://plumbline-api-cxotjai2ta-uc.a.run.app |
| **Repository** | https://github.com/rogerkoranteng-crypto/plumbline |
| **Video** | *(paste the YouTube/Vimeo URL once uploaded; must be public or unlisted, never private)* |
| **Built by** | Brownshift Technology |
| **Category** | Fortified Enterprise Fleet |
| **Also entering** | Startup Excellence |

---

## Project name

```
Plumbline
```

## Tagline

```
Eleven governed agents that test your software, find the bug nobody wrote a test for, and open a pull request they are not allowed to merge.
```

---

## Inspiration

We are Brownshift Technology, and Plumbline is what we are building.

The starting point was three things we have watched go wrong on every team we have worked on
that ships quickly.

Someone writes the tests by hand, against a UI that changes every sprint, so the suite rots
faster than anyone repairs it. Nobody writes tests for the failure paths, because writing a
test for "what if the payment provider takes 240ms too long" is boring, so it gets skipped
and that coverage simply does not exist. And when something breaks, finding out why and
getting a safe fix reviewed eats a day.

Point an AI at it, obviously. Except that is where the actual problem starts. An agent that
can write a test can write any file. An agent that can open a pull request can merge one.
Whether a model can find a bug stopped being the interesting question early on. What kept us
up was the next second: it found one, so now what is it allowed to do about it?

That question is the company. Plenty of people are building agents that write tests. We are
building the part that decides what those agents may touch, proves what they did, and stops
them at a human before anything real changes.

## What it does

You point Plumbline at a running app and eleven agents go to work on it.

Cartographer crawls the app and keeps a route graph. Author turns plain English into
Playwright specs. Healer re-anchors selectors when your UI drifts, without touching what the
test actually asserts. Chaos attacks the thing on purpose, with latency and faults and toxic
input. Runner executes specs with no model in the loop at all, which was a deliberate call: a
bug you cannot reproduce is a rumour, not a bug report, and a model in the execution path
makes every failure unreproducible. Triager works out why something failed and reproduces it
five times before it will call it a bug instead of a flake. Surgeon writes the patch and
opens the pull request. Sentinel, Auditor, Oracle and Economist watch the fleet itself.

Then Surgeon stops. It cannot merge what it just opened. Not because a prompt asks it nicely,
but because `pr.merge` is not in any agent's tool scope, and every tool call in the system
goes through one function that checks.

The end result is that you type a sentence in English and get a spec that was written, run,
root-caused, patched, and left at a gate with a human's name required on it. Or you never open
the browser at all: Plumbline is an MCP server, so your own coding agent can start the run and
read the finding, and it gets exactly the tools its API key's role allows. Every decision the Gateway
made along the way is in a hash-chained ledger you can verify from a button in the UI.

## How we built it

The Gateway is the entire design, and everything else is downstream of it. One function.
Every tool call from every agent goes through it. It resolves who is calling, checks the tool
against that agent's declared scope and denies by default, scans model-bound text for
injection, redacts PII and secrets, writes the decision to an append-only ledger, and emits
an OpenTelemetry span. There is no second path to a tool, which is the only reason any of
the guarantees below hold.

There are two kinds of policy and only one of them is yours to configure. Tool scopes live in
code, because they describe what an agent *is*, and no customer setting should be able to
hand Cartographer the ability to merge a pull request. Gate rules are per-workspace and
versioned, because those are the thing an owner actually tunes. Every ledger entry records
which policy version was in force, so an audit can answer "what rule allowed this", which a
static table never can.

The ledger is hash-chained, each entry signing the one before it, with a transactional head
pointer. That is the difference between tamper-evident and merely discouraged. There is a
`/api/ledger/verify` endpoint that walks the chain, and a button in the UI that calls it.

The API and the worker are split, for a boring and real reason: the API has to answer in
milliseconds and a run takes minutes and drives a browser. So the API is Cloud Run and scales
to zero, and the worker is a Cloud Run Job with a full CPU and no request timeout, one
execution per run. They only talk through Firestore and Pub/Sub. The worker never calls the
API.

**MCP runs in both directions, and the Gateway is why that is safe.** Plumbline is an MCP
server: `POST /mcp` speaks JSON-RPC, `GET /mcp` streams over SSE, both authenticated with the
same hashed API keys as the public `/v1/` surface. Eight tools, so your coding agent can start
a run, read a finding, check coverage, write a behaviour in plain English, verify the ledger
or approve a gated patch. `visible_tools(role)` filters the manifest by the calling key's
role, so a reader's client never sees `plumbline_approve_patch` at all rather than seeing it
and being refused on call.

Our agents are also MCP clients, pointed at a customer's own servers. `McpToolSource` knows
how to speak JSON-RPC and nothing else, deliberately: an agent makes an MCP call through
`Gateway.call` exactly as it makes a `browser.read` call, so the call gets scope checking
against `mcp.<server>`, a ledger entry, an OTel span and redaction on the way back. The
Gateway redacts every `mcp.*` result unconditionally rather than trusting a `.read` suffix,
because a customer's server names its own tools and a naming convention is not a guarantee.

And a discovered tool is untrusted input. `discover()` runs every manifest entry's name and
description through the same injection scanner the Gateway runs over a payload. A tool
description is text a third party controls that ends up in an agent's prompt, which is the
whole shape of an MCP tool-poisoning attack. Screening the manifest stops being optional the
moment you let customers point agents at servers you did not write.

**Stack.** FastAPI and Python 3.13 on the back, React 18 and Vite and TypeScript on the front
with plain CSS tokens and no UI framework. Firestore, Pub/Sub, Cloud Run and Cloud Run Jobs,
Vertex AI `gemini-3.5-flash`, Secret Manager, Artifact Registry, Cloud Build, Playwright
driving real Chromium, OpenTelemetry throughout.

Roughly 30k lines of Python and 8.4k of TypeScript, with 1,091 backend tests, 113 frontend
tests and 18 Playwright end-to-end tests including accessibility audits, over 77 commits.

## Challenges we ran into

I specified the demo wrong, and it took a user unable to click anything to find out. The
original design said demo sessions should be read-mostly: accept writes in the UI, throw them
away server-side, show an honest banner. It got built exactly that way across 31 separate
refusal sites. It was not a bug. The spec was wrong, and the result was a product you could
look at but not use. Every demo session gets its own writable sandbox now.

A missing API route took the whole app down, and neither test suite could see it. The billing
pane fetched `/api/billing/invoices`, which did not exist. Because the SPA is served from a
catch-all route, that request came back as `index.html` with a 200, and a component called
`.map` on a string of HTML. The backend tests passed because there was no route to test. The
frontend tests passed because they mock the API client. Only the assembled product was wrong.
There is now a test that pulls every path the frontend calls and resolves it against the real
OpenAPI schema, and it found a third missing route the first time it ran.

Then I fixed that, deployed it, verified it with curl, and the user still hit the crash. That
one hurt. `index.html` was being served with no `Cache-Control`, no `ETag`, no
`Last-Modified`, which does not mean "do not cache" to a browser, it means "cache this
however you like". And since `index.html` is the only file that names the fingerprinted
bundle, a stale copy pins you to old broken code forever. No amount of redeploying reaches
you.

Also, for anyone else building on Vertex: `gemini-3.5-flash` is served only on location
`global`. Every regional endpoint 404s. `gemini-2.5-flash` works regionally, which is exactly
the trap, because it runs fine on your machine and quietly fails the version requirement.

## Accomplishments that we're proud of

I ran reviews adversarially, told to attack rather than confirm, and they found things a
friendlier review would have sailed past. A sandbox guard whose test still passed after the
reviewer deleted the flag it was supposed to be testing. Two URL normalisation bypasses,
`/\evil.com` and a tab-embedded variant. Surgeon able to delete a spec file outright and
report success, with a real PR URL attached.

What made those findable was one rule I stuck to: a test only proves something if you have
watched it fail for the right reason. Every fix in this repo got verified by reverting it and
checking the test went red with the message I expected. That is how I caught a smoke test of
my own that passed against a build I had deliberately broken. Asserting that something bad is
absent matches on the first poll, before the crash has painted, and React Router swallows the
error so no page error ever fires. The test was decorative and I would never have known.

The ledger really does verify and the gate really cannot be bypassed, and both have tests
that go red when that stops being true. I would not claim either otherwise.

## What we learned

Most of what was actually broken lived between components, not inside them.

In a single session, clicking through the deployed app turned up four defects that 1,091
backend tests and 113 frontend tests had no way of catching. A findings API that dropped
`run_id`, so every row on that screen was silently unclickable. A UI that disabled the
Approve button for exactly the session type the API deliberately allows to press it. A test
fixture that never linked its finding to its run, so the entire patch section had never once
rendered in a test. And when that section finally did render, a serious colour-contrast
failure sitting inside it that had been wrong the whole time, in markup nothing had ever
drawn.

Every one of those halves was correct on its own terms. They were only wrong together.

## What's next for Plumbline

A settings pane to connect a GitHub repo. That is genuinely the only thing standing between
the demo and live runs against your own app: the backend, the shallow clone, writing real
spec files, real branches, real pull requests are all built and tested. There is just no UI
to attach a repo yet, which is why demo runs are replayed rather than live.

After that, on-premise runners, because plenty of teams cannot point a cloud service at their
staging environment. Then mobile testing and SAML.

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
- [x] Repo is public: https://github.com/rogerkoranteng-crypto/plumbline
- [x] Live URL loads and the demo door works (warm it first; cold start is ~15s)
- [ ] Category selected: **Fortified Enterprise Fleet**
- [ ] **Startup Excellence** opted into
- [ ] Submitted on behalf of the incorporated org, with the corporate email
- [ ] Devpost fields pasted from this file
- [ ] Submit before **17:00 PDT, 2026-08-31**
