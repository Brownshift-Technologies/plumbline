# Building Plumbline

*Written for the All Things Agentic hackathon (Google/Devpost). This post, like Plumbline
itself, was produced for this submission.*

Plumbline is a fleet of eleven agents that test and repair software: they map an app, write
behaviours against it in plain English, repair those behaviours as the UI drifts, break the
app on purpose, run everything deterministically, root-cause what breaks, and open a pull
request that stops at a human before it merges. The interesting part of building it wasn't
any single agent. It was the handful of times the build produced something that *looked*
correct and wasn't, a test that passed while the thing it guarded was broken, a guard that
covered one shape of a problem and missed an adjacent one, a race condition whose two possible
outcomes turn out to matter very differently. This is a post about those, because they were
more instructive than anything that went right on the first pass.

## The ledger doesn't drop writes: it forks the chain, which is worse

Every decision Plumbline's Gateway makes gets one entry in an append-only ledger, chained by
`signature = SHA256(prior_signature + payload)`. The first version did a straightforward
read-then-write: read the current tip, compute the next entry, write it. Two agents acting on
the same workspace at nearly the same moment could both read the same tip, both compute a
"next" entry with the same sequence number, and the second write would win, the first one's
entry simply never lands.

That's a real bug, but it turns out there's a worse version of it already solved elsewhere in
the codebase: `core/store.py`'s `append_audit`, written earlier for a different product, hits
the identical race and *drops* a write, the trail stays internally consistent, just shorter
than it should be. It looked like `Ledger.append` should copy that fix. A closer look at what
actually happens under Firestore's concurrency model said otherwise: because `Ledger.append`
writes each entry as its own document rather than into one growing array, the same race
doesn't drop anything, it produces **two entries with the same sequence number, each
individually validly signed.** The chain forks. Nothing about either entry looks wrong on its
own; a `verify()` walk would sail through both branches. And a grep turned up something worse
than the bug itself: `verify()` had zero non-test callers. The shipped guarantee, as the
reviewer put it, was "a fork can be written silently and would be found if someone happened to
ask", and nobody was asking.

The fix was a per-workspace `ledger_head/{workspace_id}` document holding the current tip,
written in the *same transaction* as the new entry, so two writers contending for the same
head can't both succeed. Firestore's 1 MB document ceiling ruled out going back to a single
growing array (roughly 5,000 entries: weeks, not years, for a real workspace), so one entry
per document stayed, just gated by a transactional pointer instead of a bare read-then-write.
`verify()` got a comment naming the two callers that would eventually give it a job: the
`GET /api/ledger/verify` endpoint and the audit screen's "verify chain" control. A governance
feature with no caller is not shipped, even if the function itself is correct.

## Why the Runner touches no model at all

Ten of Plumbline's eleven agents call `gemini-3.5-flash`. Runner does not. It is the one
that actually executes a Playwright spec and reports pass, fail, timeout or
selector-not-found, and it calls no model at all. That's deliberate, not an oversight: if execution itself involved a model call, a
"failure" could mean the test found a real bug, or it could mean the model behaved slightly
differently between the run that failed and the run that's supposed to reproduce it. Triager's
whole job is reproducing a failure five times and calling it a bug only if all five agree.
That's meaningless if the thing being reproduced isn't deterministic in the first place. So
Runner's classifier is pure code: `status == "timedOut"` and Playwright's own structured
`matcher is True/False` decide the outcome before any string matching runs, and a batch of
agent tests specifically asserts `ctx.model.calls == []` after a run, to prove nothing quietly
started calling the model later.

## A guard that caught an edit, and missed a delete

Surgeon proposes a patch and opens a pull request. Before it does, a guard checks that the
diff doesn't touch any file in the workspace's own test suite, the whole point of the product
falls apart if the agent fixing a bug can also edit away the test that caught it. The guard
worked, for edits. It read the `+++ b/` side of a unified diff to find which files were
touched, and matched that list against the workspace's known spec paths.

A reviewer tried something the guard's own author hadn't: a diff that *deletes*
`specs/checkout.spec.ts` (`+++ /dev/null`) alongside a real, legitimate fix. `_files_in_diff`
read only the added-file side, so a deletion was invisible to it. The patch went through:
`outcome="ok"`, `verified=True`, a live `pr_url`. A rename to `checkout.spec.ts.bak` evaded
the same way. This is strictly worse than the edit case the guard was built to catch, an
edited test is tampered with and might get noticed on review; a deleted test is *gone*, the
suite passes honestly forever afterward, and the regression it existed to catch has nothing
watching for it again. It's the product's own worst-case failure, produced by the product,
against real code, not a hypothetical. The fix reads both sides of a unified diff and, more
importantly, checks file identity against the workspace's actual list of known spec paths , 
something the module already had available and already used elsewhere, rather than pattern-
matching on which paths a diff happened to add.

## Redaction that knew what PII looked like, and missed what a secret looks like

The Gateway promises that anything a `.read` tool hands back has been scrubbed of PII before
an agent's model prompt or the ledger ever sees it: emails, SSNs, phone numbers, and (added
after a review caught the gap) credit card numbers, Luhn-checked so an order ID or trace ID
doesn't get mistaken for one. What it didn't cover, for a while, was a class of data more
sensitive than most of that: a bearer token, a JWT, a GitHub or Stripe API key. Those have no
PII *shape*, nothing about a token's structure says "personal information", so a pattern
library built to find PII walked straight past one sitting in a HAR capture, and it could land
in a finding's title, which gets rendered in the UI, exported to CSV, and written into a
ledger that by design can never be edited afterward. Worse exposure than the artifact it came
from. Secret patterns went in next: anchored prefixes per credential type, each redacting to
its own distinct marker, secrets running strictly before PII in the pass order.

Then, testing that fix by hand rather than trusting that it was fixed, a Google API key came
back as `AIzaSyD-[PHONE]abcdefghijklmnopqrstuv`. The phone-number pattern had matched a run of
digits *inside* the key before the secret pattern got a turn at the same span, so the prefix
and the entire alphanumeric tail survived intact, a 39-character credential, ten digits
blanked out of the middle, mislabeled as a phone number to boot. The test that should have
caught this asserted `redact_pii(x) != x`, true the moment *anything* changed, which passes
even when a credential is merely dented rather than actually removed. The fix was structural
(every secret pattern runs before every PII pattern, full stop) and the test changed shape to
match: assert that nothing recognizable from the original *survives*, never merely that the
string is different from what it was.

## A test named for a requirement it did not check: three times

The build's most repeated failure mode wasn't a missing feature. It was a test whose *name*
promised something its *assertions* didn't actually verify, which is worse than no test,
because it tells the next reader a requirement is covered when it isn't.

It happened with the Playwright sandbox flag: `chromiumSandbox: false` has to reach the actual
`chromium.launch()` call, or the browser can't start on this machine and can't start inside a
Cloud Run container either. The regression test asserted that the string `chromium_sandbox`
appeared somewhere in the *source code* of the function that launches it, which is also true
of the comment sitting directly above the real call, explaining why the flag is there. Delete
the flag from the actual call, leave the comment, and the test still passes. It happened again
with the dented-credential bug above: `!=` is true for "changed a little" and "changed
completely" alike. And it happened at real scale across sixteen dashboard screens at once:
every loading state was a spinner over blank content, the exact anti-pattern the design spec
forbade in writing, and every corresponding test was named for the requirement
(`"shows skeleton loading rows, not a spinner over a blank page"`) while asserting only that
the text "Loading runs…" appeared somewhere on the page. All three got fixed the same way:
assert the negative as well as the positive. Not just "the correct thing is present," but "the
specific wrong thing is provably absent", a test that fails the moment the guard it exists
for gets deleted, not one that keeps passing regardless.

## What this adds up to

None of these five were caught by "the tests are green." They were caught by someone, a
reviewer, or the same person going back a second time, actually trying to break the thing the
test claimed to guarantee: deleting a spec file and running Surgeon for real, stripping a
sandbox flag and watching whether the test noticed, printing the actual redacted string instead
of only comparing it to the input. That's not a process this build invented; it's the ordinary
discipline of not trusting a name. It just turned out to matter more here than usual, because
the product's whole premise is that a test result should mean what it says.
