# Plumbline, presentation script

`DEMO_STEPS.md` says where you switch screens and what you should see on each.

Read straight through. Five hundred spoken words. That is about
three minutes at a brisk pace, against a hard three-minute cap, so do not slow
down for effect. Practise it once against a timer before you record.

---

We are Brownshift Technologies, and this is Plumbline.

Here is a bug. A customer buys something. The payment provider is slow that
morning, so the app retries. And because the retry sends a new idempotency key
instead of reusing the old one, the customer is charged twice.

Nobody wrote a test for that, because it only happens when the provider is
slow, and writing a test that makes a provider slow on purpose is tedious work
that gets pushed to next sprint, every sprint.

That is the problem. It is not that teams do not test. It is that the tests
nobody writes are the ones that catch the expensive bugs.

So point an AI at it. That is where the second problem starts.

An agent that can write a test can write any file. An agent that can open a
pull request can merge one. Whether a model can find a bug stopped being the
interesting question a while ago. The question is what it is allowed to do the
second it finds one.

Plumbline is eleven agents that go looking for that bug, and one gate that
everything they do has to pass through.

Anyone can try it. No account, no card. This opens a real workspace with your
own data in it, still there when you come back. Teams sign in with email, or
with GitHub, Google or their own single sign-on.

Here is that payments bug, found. Cartographer mapped forty-seven routes.
Author turned plain English into a Playwright spec. Healer re-anchored four
selectors a refactor had moved.

Then Chaos made the provider slow on purpose. Two hundred and forty
milliseconds, chosen because the provider's own ninety-ninth percentile is two
hundred and ten, and nothing had exercised the slow path.

Runner saw two charges, thirty milliseconds apart, with different idempotency
keys. Runner is the one agent with no model in the loop at all, on purpose,
because a bug you cannot reproduce is a rumour rather than a bug report.

Triager reproduced it five times out of five, and called it a bug rather than a
flake.

Then Surgeon wrote the fix, verified it, opened the pull request, and stopped.

That is the part I most want you to see. It cannot merge this. Not because a
prompt asks it politely. Merge is not in any agent's tool scope, and the
Gateway checks the scope on every single call. A human signs it or it does not
ship.

Let me show you where this runs. This is Google Cloud Run, and that is the
service, live, on its run dot app address. That is the worker, a Cloud Run Job,
one execution per run. And that is Vertex AI. Every request is Gemini three
point five Flash.

Every decision that gate made is here, hash-chained. And you do not have to
trust it. Verify re-signs every entry and checks it against the next.

Eleven agents, one gate, and a person signing off before anything real changes.

Thank you.
