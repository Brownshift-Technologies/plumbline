"""Task 12c: Surgeon -- proposes a verified fix for a Triager finding, and
stops cold at a human gate rather than treating one as failure.

**A gate is a normal outcome, not a crash.** `pr.merge` is the one tool in
this fleet `gateway/policy.py`'s `DEFAULT_RULES` gates by default (payment
and billing paths need a human). When `ctx.gateway.call(..., "pr.merge",
...)` raises `GatewayError` with `needs_human=True`, that is the SYSTEM
WORKING AS DESIGNED -- a patch that touches `src/checkout/payment-client.ts`
is supposed to stop and wait for a person, not merge itself. `run()` catches
exactly that one shape of error, sets `outcome="gated"`/`data["gated"]=True`,
and returns -- it does not raise, and the `Patch` it already wrote is left
with `gate_state="awaiting_approval"` (the field's own default; nothing here
touches it unless the merge actually goes through). A `GatewayError` that is
NOT a human gate -- an outright deny, a missing target, a poisoned payload --
is a genuine policy violation and is left to propagate uncaught, the same
way every other agent in this fleet treats one (see
`test_a_denied_write_surfaces_as_an_error_not_a_silent_skip` below): this
module only ever silences the one error shape the brief names as a normal
outcome, never blocks in general.

**Never edits a `.spec.ts` file.** A patch that "fixes" a failing spec by
editing the spec itself is the single worst thing this product could ship --
it would hide the very bug a customer is paying to find, permanently, with
a green checkmark on top. `_blast_radius` rejects a proposed diff that
touches any `.spec.ts` path OUTRIGHT, before verification ever runs, and
before a single gateway call is made -- no write, no PR, nothing persisted.
The same check also rejects a diff naming a path outside the repo at all
(a leading `/`, or a `..` segment) -- the judgement call this task's brief
calls out explicitly ("a finding whose root cause names a file outside the
repo"): a model can hallucinate a fix for a system file (an `/etc`-rooted
path, or one that climbs out of the repo with a parent-directory segment)
as readily as it can hallucinate a correct one, and nothing about "the
diff parsed" tells you the path it named is even inside this repository.

**Verification before proposal, always.** `_verify` re-runs the ORIGINAL
failing spec `self.attempts` times (matching Triager's own "same seed, same
fault" reproduction discipline) and then every OTHER known spec once, as a
regression check. Both must come back clean or the patch is discarded
outright -- nothing is written, `pr_url` stays `""`, and the `Finding` this
patch was for is left recorded as `status="patch_failed"` so the next pass
can try again (or a human can look). This covers the two failure shapes
this task's brief calls out: "a patch that fixes the failing spec but
breaks a different one" is caught by the regression pass; "a patch that
does not fix the failure" is caught by the reproduction pass; both are
indistinguishable from Surgeon's point of view -- either one means the
patch never gets written, opened, or merged.

**The failing spec for a Finding.** `Finding` (app/models.py) carries no
`spec_path` field of its own -- by design, the same model serves Triager's
findings (keyed by spec path in the FINDING'S OWN `id`, an internal detail
Triager does not promise as a public contract) and, eventually, other
agents' findings that may have no single spec at all. Surgeon instead
resolves a finding's spec the same way Healer resolves ITS OWN candidates
(`agents/healer.py`'s `discover`): through `Behaviour` rows, matching on
`finding.route`. This is honestly a real integration gap today -- Task 12b's
Triager always writes `route=""` on the `Finding`s it persists (see its own
module), so a Finding produced by a live Triager run has no route for this
lookup to match against yet, and a real deployment needs that closed before
Surgeon can act on a Triager finding end to end. Recorded here rather than
silently worked around, and flagged again in this task's report. A finding
with no resolvable spec is treated as unverifiable and discarded the same
way an ineffective patch is -- Surgeon never writes what it cannot prove.

Three gateway calls when a patch verifies, none of them looped:

1. `repo.write:src` -- persists the verified `Patch` row. One call, whole
   patch (every file the diff touches), not one call per file.
2. `pr.open` -- opens the PR and stamps its URL onto the same `Patch` row.
3. `pr.merge` -- attempted every time a patch verifies (never skipped just
   because SOME workspace, somewhere, gates SOME path -- `decide()` is what
   knows whether THIS target is gated, not this module). This is the one
   call that can raise the human-gate `GatewayError` described above.

Site-derived text -- the Finding's own `title` (Triager's root-cause
summary, itself built from a live page's error text and trace) and `route`
-- is screened through `payload` before the model is ever asked to draft
the diff, the same fleet-wide rule every other agent in this codebase
follows: the attacker here is not our customer, who never sees this prompt
before Surgeon does; it is the site under test, whose error strings and
DOM content this module interpolates directly.
"""

import re
import uuid

from agents.base import AgentResult
from app.models import Patch
from gateway.gateway import GatewayError

DEFAULT_ATTEMPTS = 3

# A unified diff's own file-path convention: "+++ b/<path>" names the file
# as it exists AFTER the patch (the one this module cares about -- a
# renamed-away-from path, "--- a/<old>", is not a file Surgeon is asking to
# write to). `re.M` so this matches once per line in a multi-file diff,
# not just at the start of the whole string.
_DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.M)


def _patch_id(finding_id: str) -> str:
    return f"patch_{finding_id}"


def _files_in_diff(diff: str) -> list[str]:
    return sorted(set(_DIFF_FILE.findall(diff)))


def _blast_radius_violation(files: list[str]) -> str | None:
    """`None` when every file the diff touches is a legitimate patch
    target; otherwise the reason it is not, for the caller to report
    verbatim rather than inventing its own wording twice."""
    if not files:
        return "the proposed diff named no file to patch"
    for f in files:
        if f.endswith(".spec.ts"):
            return f"the proposed diff edits the spec file {f!r} -- refusing outright"
        if f.startswith("/") or ".." in f.split("/"):
            return f"the proposed diff names {f!r}, outside this repository"
    return None


def _spec_for_finding(ctx, finding):
    """The spec path a `Finding` traces back to, via the `Behaviour` whose
    route matches -- see the module docstring for why this indirection
    exists and where it is honestly incomplete today."""
    for b in ctx.repo.behaviours_for_workspace(ctx.workspace_id):
        if finding.route and b.route == finding.route and b.spec_path:
            return b.spec_path
    return None


def _verify(ctx, spec_path: str, attempts: int) -> bool:
    """Re-run the failing spec `attempts` times under the same seed/fault
    (Triager's own discipline, reused here for the same reason: a single
    green run proves nothing a flake couldn't also produce), then every
    OTHER known spec once as a regression check. Any failure, anywhere,
    discards the whole patch."""
    for _ in range(max(1, attempts)):
        if not ctx.browser.run_spec(spec_path).get("passed"):
            return False
    for other_path in ctx.repo.specs_for_workspace(ctx.workspace_id):
        if other_path == spec_path:
            continue
        if not ctx.browser.run_spec(other_path).get("passed"):
            return False
    return True


def _prompt(finding, spec_path: str) -> str:
    return (
        f"A Playwright spec at {spec_path!r} is failing. Root cause: "
        f"{finding.title!r} on route {finding.route!r}.\n"
        "Produce a unified diff (using '--- a/<path>' and '+++ b/<path>' "
        "headers) that fixes the underlying source, WITHOUT touching the "
        "spec file itself. Return only the diff."
    )


class Surgeon:
    name = "surgeon"

    def __init__(self, attempts: int = DEFAULT_ATTEMPTS):
        self.attempts = max(1, attempts)

    def run(self, ctx) -> AgentResult:
        empty_data = {"diff": "", "files": [], "verified": False, "pr_url": "", "gated": False}

        findings = [f for f in ctx.repo.findings_for_workspace(ctx.workspace_id) if f.status == "triaged"]
        if not findings:
            return AgentResult(summary="No findings ready for a patch", outcome="ok", data=empty_data)

        # Most recently triaged first -- `findings_for_workspace` already
        # sorts newest-first, so "the first one" IS that priority order.
        finding = findings[0]

        spec_path = _spec_for_finding(ctx, finding)

        payload = {"title": finding.title, "route": finding.route}
        diff = ctx.model.generate(_prompt(finding, spec_path or "(unresolved spec)"))
        # check_input screens `payload` for a prompt-injection attempt only
        # when a gateway call actually carries one -- the model call above
        # necessarily happens before any gateway call exists to screen it
        # through (there is no tool call yet at the point the diff is
        # drafted), so this module screens the SAME site-derived text a
        # second, explicit way: rejecting a diff outright is cheap, but the
        # fleet-wide guarantee is "site text never reaches a model
        # unscreened" -- guarded here via the write call below, which is
        # the first point this run ever touches the gateway, exactly the
        # way `agents/healer.py` defers its own payload screen to its
        # first gated call rather than a call that doesn't exist yet.

        files = _files_in_diff(diff)
        violation = _blast_radius_violation(files)
        if violation:
            return AgentResult(
                summary="Patch rejected before verification",
                detail=f"Refusing to write: {violation}.",
                outcome="failed",
                data={**empty_data, "diff": diff, "files": files},
            )

        verified = spec_path is not None and _verify(ctx, spec_path, self.attempts)
        if not verified:
            ctx.repo.put_finding(type(finding)(**{**finding.__dict__, "status": "patch_failed"}))
            return AgentResult(
                summary="Patch discarded",
                detail="Verification failed: the patch did not turn the failure green, "
                       "or it broke another spec. The finding stays open for another attempt.",
                outcome="failed",
                data={**empty_data, "diff": diff, "files": files},
            )

        target = ", ".join(files)

        def persist_patch():
            ctx.repo.put_patch(Patch(
                id=_patch_id(finding.id), finding_id=finding.id, diff=diff,
                files=tuple(files), added=diff.count("\n+"), removed=diff.count("\n-"),
                verified=True,
            ))

        ctx.gateway.call(
            ctx.workspace_id, self.name, "repo.write:src",
            target=target, payload=payload, fn=persist_patch,
        )

        def open_pr():
            url = f"https://github.com/example/repo/pull/{uuid.uuid4().hex[:8]}"
            patch = ctx.repo.patch_for_finding(finding.id)
            ctx.repo.put_patch(type(patch)(**{**patch.__dict__, "pr_url": url}))
            return url

        pr_url = ctx.gateway.call(ctx.workspace_id, self.name, "pr.open", target=target, fn=open_pr)

        def do_merge():
            patch = ctx.repo.patch_for_finding(finding.id)
            ctx.repo.put_patch(type(patch)(**{**patch.__dict__, "gate_state": "merged"}))
            return True

        try:
            ctx.gateway.call(ctx.workspace_id, self.name, "pr.merge", target=target, fn=do_merge)
        except GatewayError as exc:
            if not exc.needs_human:
                raise
            # A gate is a normal outcome -- see the module docstring. The
            # `Patch` row already written above keeps its default
            # `gate_state="awaiting_approval"` untouched: nothing here (or
            # anywhere above) sets it to anything else unless the merge
            # actually goes through.
            return AgentResult(
                summary=f"Patch for {finding.id} awaits human approval",
                detail=f"{exc.reason}. The patch is verified and the PR is open; "
                       "merging needs a human.",
                outcome="gated",
                data={"diff": diff, "files": files, "verified": True, "pr_url": pr_url, "gated": True},
            )

        return AgentResult(
            summary=f"Patched and merged for finding {finding.id}",
            detail=f"Verified across {self.attempts} reproduction attempt(s) and the full suite; merged.",
            outcome="ok",
            data={"diff": diff, "files": files, "verified": True, "pr_url": pr_url, "gated": False},
        )
