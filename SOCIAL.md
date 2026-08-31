# Social posts

Written for the All Things Agentic hackathon (Google/Devpost) submission.

## LinkedIn

Most "AI testing" demos show a bot writing a test. The interesting problem is the next
second: who is allowed to fix the bug it just found.

That question is why we built Plumbline at Brownshift Technology. Eleven scoped agents map
an app, write behaviours against it in plain English, repair those behaviours as the UI
drifts, and attack the app on purpose with latency, faults and toxic input. Runner executes
with no model in the loop at all, because a bug you cannot reproduce is a rumour rather than
a bug report. Triager reproduces a failure five times before it will call it a bug instead of
a flake.

The part I would actually stand behind is the Gateway. Every tool call from every one of
those eleven agents goes through one function: scope check, prompt-injection screen,
per-workspace gate rules, then execution, then redaction and a signed, hash-chained ledger
entry, on every outcome including a raised exception. There is no second path.

So when the fleet finds a real bug in a payments flow, the agent that wrote the fix opens a
pull request and stops. It cannot merge it. Not because a prompt asks it nicely: pr.merge is
not in any agent's tool scope, and the Gateway checks the scope on every call. A human with
two-factor has to sign it.

Built on Cloud Run, with a Cloud Run Job worker, Firestore, Pub/Sub, and Vertex AI's
gemini-3.5-flash. Deployed and tested: 1,082 backend tests, 117 frontend, 20 end-to-end,
all offline against fakes for CI, with opt-in suites that drive a real browser.

There is also a build writeup on what went wrong along the way, which was more worth writing
about than what went right. A ledger race that forked the chain instead of dropping a write.
A test suite that passed for an entire build while the feature it covered had never once
worked in deployment.

Category: Fortified Enterprise Fleet, also entered for Startup Excellence.

#AllThingsAgenticHackathon #GoogleCloud #VertexAI #AgenticAI

## X / Twitter

Built Plumbline for #AllThingsAgenticHackathon. 11 agents that map, test, break and repair
your app, and one Gateway every single tool call has to pass through: scope check, injection
scan, gate rules, execute, then redact and write a hash-chained ledger entry.

The agent that finds and fixes a payments bug opens a real PR. It cannot merge it. pr.merge
is not in its scope, so a human with 2FA has to.

Cloud Run, Firestore, Pub/Sub, Vertex AI (gemini-3.5-flash). Deployed, 1,082 backend and 117
frontend tests, docs and a build writeup included.

#GoogleCloud #VertexAI
