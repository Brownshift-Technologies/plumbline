# Plumbline, presentation script

`DEMO_STEPS.md` says where you switch screens and what you should see on each.

Read straight through. Four hundred and seventy spoken words. That is about
three minutes at a brisk pace, against a hard three-minute cap, so do not slow
down for effect. Practise it once against a timer before you record.

Swap the name in the first line for however you want to be introduced.

---

Hi, my name is Roger. I am from Brownshift Technology, and this is Plumbline.

A plumb line is a weight on a string. It does one thing: it tells you what
true vertical is, so you can see how far you have drifted.

Every team that ships quickly has the same problem with testing. Someone
writes the tests by hand, against an interface that changes every sprint, so
the suite rots faster than anyone can repair it. And nobody writes tests for
the failure paths, because writing a test for what happens when the payment
provider is slow is tedious, so it gets skipped. That is exactly where the
expensive bugs live.

So point an AI at it. That is also where the real problem starts.

An agent that can write a test can write any file. An agent that can open a
pull request can merge one. Whether a model can find a bug stopped being the
interesting question a while ago. The question is what it is allowed to do the
second it finds one.

Plumbline is eleven agents that test your software, and one gate everything
they do passes through.

Here is a run. Cartographer crawled the app and mapped forty-seven routes.
Author turned plain English into a Playwright spec. Healer re-anchored four
selectors a refactor had moved.

Then Chaos injected two hundred and forty milliseconds of latency into the
payments API. It picked that number because the provider's own ninety-ninth
percentile is two hundred and ten, and nothing had exercised the slow path.

Runner saw two charges, thirty milliseconds apart, with different idempotency
keys. Runner is the one agent with no model in the loop at all, on purpose,
because a bug you cannot reproduce is a rumour rather than a bug report.

Triager reproduced it five times out of five, and called it a bug rather than
a flake.

Then Surgeon wrote the fix, verified it, opened the pull request, and stopped.

That is the part I most want you to see. It cannot merge this. Not because a
prompt asks it politely. Merge is not in any agent's tool scope, and the
Gateway checks the scope on every single call. A human signs it or it does not
ship.

Let me show you where this runs. This is Google Cloud Run, and that is the
service, live, on its run dot app address. That is the worker, a Cloud Run Job,
one execution per run. And that is Vertex AI. Every request there is Gemini
three point five Flash.

Every decision that gate made is here, hash-chained. And you do not have to
trust it. Verify re-signs every entry and checks it against the next.

Eleven agents, one gate, and a person signing off before anything real
changes.

Thank you.
