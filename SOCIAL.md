# Social posts

Written for the All Things Agentic hackathon (Google/Devpost) submission.

## LinkedIn

Most "AI testing" demos show a bot writing a test. The interesting problem is what happens
after: who's allowed to fix the bug it finds.

I built Plumbline for the All Things Agentic hackathon — a fleet of eleven scoped agents that
map an app, write behaviours in plain English, repair them as the UI drifts, break the app on
purpose (latency, faults, toxic input), run everything deterministically with no model in the
execution loop, root-cause failures with a five-times reproduction before calling something a
bug, and propose a verified patch.

The part I'd actually stand behind: every tool call from every one of those eleven agents
passes through a single Gateway function — scope check, prompt-injection screen, per-workspace
gate rules, then and only then execution, then redaction and a signed, hash-chained ledger
entry, on every outcome including a raised exception. When the fleet finds a real bug in a
payments flow, the agent that proposed the fix opens a pull request and stops there. A human
with TOTP has to sign it before it merges. That gate isn't a UI affordance — it's enforced at
the same layer that scopes what each agent is allowed to touch in the first place.

Built on Cloud Run (API + a Cloud Run Job worker), Firestore, Pub/Sub, and Vertex AI's
gemini-3.5-flash. Deployed, tested (970+ backend + 102 frontend tests, all offline against
fakes for CI, with opt-in suites against a real browser and real OAuth), and documented —
README, architecture doc and diagram, and a build writeup on what actually went wrong along
the way, because that was more worth writing about than what went right.

Category: Fortified Enterprise Fleet, also entered for Startup Excellence.

#AllThingsAgenticHackathon #GoogleCloud #VertexAI #AgenticAI

## X / Twitter

Built Plumbline for #AllThingsAgenticHackathon — 11 agents that map, test, break, and repair
your app, and a Gateway every single one of their tool calls has to pass through: scope check
→ injection scan → gate rules → execute → redact + hash-chained ledger entry.

The agent that finds and fixes a payments bug opens a real PR. It cannot merge it. A human
with TOTP has to.

Cloud Run + Firestore + Pub/Sub + Vertex AI (gemini-3.5-flash). Deployed, 970+102 tests,
docs and a build writeup included.

#GoogleCloud #VertexAI
