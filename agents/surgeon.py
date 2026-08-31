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

**Never destroys a spec, and never trusts a suffix to know one when it
sees one.** Fix round 1: the first cut of this guard only ever looked at
`+++ b/<path>` lines (a diff's post-image side) and only ever recognised
`.spec.ts` by name. A reviewer demonstrated both holes with one diff: a
patch that fixes an unrelated source file while ALSO deleting
`specs/checkout.spec.ts` (`--- a/specs/checkout.spec.ts` / `+++
/dev/null`) never mentions the spec on its `+++ b/` side at all -- the old
guard saw nothing wrong, verified the (now permanently un-tested) "fix",
and opened a real PR. A rename to `specs/checkout.spec.ts.bak` evaded the
same way, and a spec named `checkout.spec.js`/`.test.ts` was never even
suffix-matched. Deleting a regression test is WORSE than editing one --
editing tampers with it, deleting removes it forever, and the suite then
passes honestly, with the bug still live.

Two independent fixes, both required (see `_all_touched_paths`/`_blast_
radius_violation`):

1. **Both sides of every file header are parsed**, not just `+++ b/`: `---
   a/<path>` (the pre-image), `+++ b/<path>` (the post-image), and git's
   own `rename from`/`rename to` header pair. `/dev/null` on either side
   means "this path is being created/destroyed", not "this path doesn't
   count" -- a path that appears ANYWHERE in the diff, on either side, is
   in scope for the guard below. This is what makes a deletion or a
   rename-away visible at all: the spec's OLD path still appears on the
   `--- a/` side even though no `+++ b/` line ever names it again.
2. **Identity beats suffix.** `_blast_radius_violation` checks every
   touched path against `ctx.repo.specs_for_workspace(...)` FIRST -- the
   actual, authoritative set of files this workspace considers a spec,
   whatever they are named -- and only falls back to a suffix check
   (`.spec.ts`/`.spec.js`/`.test.ts`/`.test.js`, belt-and-braces) for a
   path that is not yet a known spec at all (a brand-new spec file a
   patch tries to smuggle in and immediately delete, say). Suffix-only
   matching protected nothing against a `.spec.js` suite, a `.test.ts`
   one, or a shared helper a spec `import`s; the identity check protects
   the actual files this workspace already trusts as tests, by name, not
   by guessing a naming convention.

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

**The model is called from inside a gateway call, screened first.** Fix
round 1: the original draft called `ctx.model.generate(...)` directly in
`run()`, before any `ctx.gateway.call` existed to screen its `payload`
through `check_input` -- unlike every other agent in this fleet (Author,
Healer, Sentinel), which all wrap their own model call inside the `fn`
passed to a gateway call specifically so site-derived text is screened
BEFORE the model ever sees it, not re-examined after the fact. `run()` now
drafts, blast-radius-checks, verifies, and persists all inside ONE
`repo.write:src` call (see below) -- `payload` (the Finding's own `title`/
`route`, both ultimately built from a live page's error text) is screened
by `check_input` before that call's `fn` -- and therefore the model call
inside it -- ever runs.

Three gateway calls when a patch verifies, none of them looped:

1. `repo.write:src` -- drafts the diff (the model call lives here, behind
   `payload` screening), blast-radius-checks it, verifies it, and persists
   the `Patch` row, all as one call. `target` is `finding.route or
   finding.id` -- the file(s) a patch will touch are not known until AFTER
   the model has drafted something INSIDE this call, so they cannot also
   be this call's OWN gate target; a workspace that wants to gate on the
   actual touched path does so on `pr.merge` below, which runs after the
   real files are known and uses exactly that as its target.
2. `pr.open` -- opens the PR (only reached if drafting/verification
   succeeded) and stamps its URL onto the same `Patch` row. `target` is
   the real, comma-joined touched-file list.
3. `pr.merge` -- attempted every time a patch verifies, with the same
   real file-path target as `pr.open` -- so THIS is the call a workspace's
   own gate rules (payment/billing path patterns) actually fire against.
   This is the one call that can raise the human-gate `GatewayError`
   described above.
"""

import re

from agents.base import AgentResult
from app.models import Patch
from gateway.gateway import GatewayError

DEFAULT_ATTEMPTS = 3

# Both sides of a unified diff's own file-path convention, plus git's
# rename headers -- see the module docstring's fix-round-1 note for why
# BOTH sides (not just "+++ b/") have to be parsed, and why a git-style
# rename (which may carry no "---"/"+++" hunk at all when content is
# otherwise unchanged) needs its own pair of patterns.
_OLD_PATH = re.compile(r"^--- (\S.*)$", re.M)
_NEW_PATH = re.compile(r"^\+\+\+ (\S.*)$", re.M)
_RENAME_FROM = re.compile(r"^rename from (\S.*)$", re.M)
_RENAME_TO = re.compile(r"^rename to (\S.*)$", re.M)

# Suffix is belt-and-braces ONLY -- see `_blast_radius_violation`. The
# actual guard is identity against `ctx.repo.specs_for_workspace(...)`.
_SPEC_SUFFIXES = (".spec.ts", ".spec.js", ".spec.tsx", ".spec.jsx",
                   ".test.ts", ".test.js", ".test.tsx", ".test.jsx")


def _patch_id(finding_id: str) -> str:
    return f"patch_{finding_id}"


def _clean_path(raw: str) -> str | None:
    """A diff-header path, stripped and `a/`/`b/`-unprefixed -- or `None`
    for `/dev/null`, the diff format's own way of saying "this side of the
    pair does not exist" (a pure creation has no `--- a/<path>`; a pure
    deletion has no `+++ b/<path>`)."""
    path = raw.strip()
    if path == "/dev/null":
        return None
    if path[:2] in ("a/", "b/"):
        path = path[2:]
    return path


def _all_touched_paths(diff: str) -> set[str]:
    """Every path this diff mentions on EITHER side of a file header, or a
    git rename -- a deletion's old path, a creation's new path, a
    modification's (identical) old-and-new path, and a rename's both
    paths, all land here. This is deliberately broader than "the files
    this patch will end up writing to" (`_new_paths`, used only once a
    diff has already cleared this check) -- the whole point is to see a
    file that is being DESTROYED, which never appears on the `+++ b/`
    side at all."""
    paths = {p for raw in _OLD_PATH.findall(diff) if (p := _clean_path(raw)) is not None}
    paths |= {p for raw in _NEW_PATH.findall(diff) if (p := _clean_path(raw)) is not None}
    paths |= {p for raw in _RENAME_FROM.findall(diff) if (p := _clean_path(raw)) is not None}
    paths |= {p for raw in _RENAME_TO.findall(diff) if (p := _clean_path(raw)) is not None}
    return paths


def _new_paths(diff: str) -> list[str]:
    """The paths this diff actually WRITES to (its post-image side, plus
    `rename to`), sorted and de-duplicated -- what `Patch.files` and the
    `pr.open`/`pr.merge` target are built from once a diff has already
    cleared `_blast_radius_violation`. A pure deletion contributes nothing
    here (there is no post-image to write) even though its old path is
    still very much a member of `_all_touched_paths`."""
    paths = {p for raw in _NEW_PATH.findall(diff) if (p := _clean_path(raw)) is not None}
    paths |= {p for raw in _RENAME_TO.findall(diff) if (p := _clean_path(raw)) is not None}
    return sorted(paths)


def _diff_sections(diff: str) -> dict[str, str]:
    """`new_path -> everything between that file's own '+++ b/<path>' header
    and the next '--- a/' header (or the end of the diff)` -- Tier 2's own
    addition, used only AFTER a diff has cleared `_blast_radius_violation`,
    to find which part of the diff belongs to which file when realising it
    onto the checkout (see `_change_groups`/`_apply_diff_to_checkout`
    below). A deletion (post path `None`, via `_clean_path`) is never a
    key here -- there is nothing to apply for a path this diff removes,
    and the guard above has already refused any diff that removes a
    spec; a non-spec deletion is simply not realised onto disk (Surgeon
    proposes source FIXES, never file removals, per its own prompt)."""
    sections: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("--- "):
            if current is not None:
                sections[current] = "".join(body)
            current, body = None, []
            continue
        if line.startswith("+++ "):
            current = _clean_path(line[4:].strip())
            continue
        if current is not None:
            body.append(line)
    if current is not None:
        sections[current] = "".join(body)
    return sections


def _change_groups(section_body: str) -> list[tuple[str, str]]:
    """`[(old_text, new_text), ...]` -- every contiguous run of removed
    ('-') lines paired with the contiguous run of added ('+') lines that
    immediately follows it, read off one file's own diff section.
    Deliberately ignores hunk range headers ('@@ ... @@') and their own
    (often, in this fleet's own model-drafted diffs, imprecise) line
    counts entirely -- `_apply_diff_to_checkout` below matches by
    CONTENT, not by position, which is what makes it robust to a diff
    whose header counts do not exactly match its body (this module's own
    prompt, `_prompt` below, asks a model for a diff; nothing about a
    model's output is guaranteed to satisfy `git apply`'s own stricter
    bookkeeping). This is deliberately not a general patch engine: it is
    exactly as much structure as Surgeon's own prompt ever asks a model
    to produce -- one minimal replacement per fix."""
    groups: list[tuple[str, str]] = []
    removed: list[str] = []
    added: list[str] = []

    def flush():
        if removed or added:
            groups.append(("".join(removed), "".join(added)))
        removed.clear()
        added.clear()

    for line in section_body.splitlines(keepends=True):
        if line.startswith("@@"):
            flush()
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        else:
            flush()
    flush()
    return groups


def _apply_diff_to_checkout(checkout, diff: str, paths: list[str]) -> None:
    """Realises `diff` onto `checkout`'s real files, for exactly the
    `paths` a caller has already resolved as this diff's post-image
    (`_new_paths`) -- content-matched, not position-matched (see
    `_change_groups`), so a diff whose hunk header counts are slightly
    off from its own body (a real risk with model-drafted output) still
    applies cleanly rather than being rejected the way a strict `git
    apply` would refuse it."""
    sections = _diff_sections(diff)
    for path in paths:
        try:
            content = checkout.read_file(path)
        except FileNotFoundError:
            content = ""
        for old_text, new_text in _change_groups(sections.get(path, "")):
            if old_text and old_text in content:
                content = content.replace(old_text, new_text, 1)
            elif not old_text:
                content += new_text
        checkout.write_file(path, content)


def _blast_radius_violation(all_paths: set[str], known_specs: set[str]) -> str | None:
    """`None` when every file the diff touches -- on EITHER side of a
    header -- is a legitimate patch target; otherwise the reason it is
    not, for the caller to report verbatim rather than inventing its own
    wording twice.

    Identity first, suffix second (see the module docstring): a path
    already tracked as a spec in THIS workspace is rejected outright
    regardless of what it is named; a path not yet tracked but shaped like
    one (`.spec.ts`, `.spec.js`, `.test.ts`, `.test.js`) is rejected too --
    a model should never be inventing new test files inside a source
    patch, whatever it names them.
    """
    if not all_paths:
        return "the proposed diff named no file to patch"
    for f in sorted(all_paths):
        if f in known_specs:
            return f"the proposed diff touches {f!r}, a known spec file in this workspace -- refusing outright"
        if f.endswith(_SPEC_SUFFIXES):
            return f"the proposed diff touches {f!r}, which is shaped like a spec/test file -- refusing outright"
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

        # Tier 2 (2026-08-30): a real patch needs somewhere real to
        # commit and a real client to open a pull request through. See
        # `agents/author.py`/`agents/healer.py`'s identical guards and
        # `job/checkout.py`'s own module docstring -- same fleet-wide
        # rule: no connected repo means Surgeon skips outright, with an
        # explanatory step, rather than crash reaching for a checkout
        # that was never built. Demo sandboxes take this path every
        # time, by construction (`job/worker.py`'s `_checkout_factory`
        # never builds one for `is_demo`) -- "a demo run stays
        # simulated" is this task's own non-negotiable.
        if ctx.checkout is None:
            return AgentResult(
                summary="Surgeon skipped -- no repository connected",
                detail="This workspace has no connected GitHub repository, so there is "
                       "nowhere to commit a fix or open a pull request. Connect a "
                       "repository (Settings > GitHub) to let Surgeon act on triaged findings.",
                outcome="skipped",
                data=empty_data,
            )

        findings = [f for f in ctx.repo.findings_for_workspace(ctx.workspace_id) if f.status == "triaged"]
        if not findings:
            return AgentResult(summary="No findings ready for a patch", outcome="ok", data=empty_data)

        # Most recently triaged first -- `findings_for_workspace` already
        # sorts newest-first, so "the first one" IS that priority order.
        finding = findings[0]

        spec_path = _spec_for_finding(ctx, finding)
        known_specs = set(ctx.repo.specs_for_workspace(ctx.workspace_id))

        def draft_and_persist():
            # The model call lives HERE, inside `fn`, so the `payload`
            # below (the site-derived Finding text) is screened by
            # `check_input` before this line ever runs -- see the module
            # docstring's fix-round-1 note.
            diff = ctx.model.generate(_prompt(finding, spec_path or "(unresolved spec)"))

            all_paths = _all_touched_paths(diff)
            violation = _blast_radius_violation(all_paths, known_specs)
            if violation:
                return {"status": "violation", "diff": diff, "files": _new_paths(diff), "reason": violation}

            if spec_path is None or not _verify(ctx, spec_path, self.attempts):
                ctx.repo.put_finding(type(finding)(**{**finding.__dict__, "status": "patch_failed"}))
                return {"status": "verify_failed", "diff": diff, "files": _new_paths(diff)}

            files = _new_paths(diff)
            ctx.repo.put_patch(Patch(
                id=_patch_id(finding.id), finding_id=finding.id, diff=diff,
                files=tuple(files), added=diff.count("\n+"), removed=diff.count("\n-"),
                verified=True,
            ))
            return {"status": "ok", "diff": diff, "files": files}

        payload = {"title": finding.title, "route": finding.route}
        result = ctx.gateway.call(
            ctx.workspace_id, self.name, "repo.write:src",
            target=finding.route or finding.id, payload=payload, fn=draft_and_persist,
        )

        if result["status"] == "violation":
            return AgentResult(
                summary="Patch rejected before verification",
                detail=f"Refusing to write: {result['reason']}.",
                outcome="failed",
                data={**empty_data, "diff": result["diff"], "files": result["files"]},
            )
        if result["status"] == "verify_failed":
            return AgentResult(
                summary="Patch discarded",
                detail="Verification failed: the patch did not turn the failure green, "
                       "or it broke another spec. The finding stays open for another attempt.",
                outcome="failed",
                data={**empty_data, "diff": result["diff"], "files": result["files"]},
            )

        diff, files = result["diff"], result["files"]
        target = ", ".join(files)

        def open_pr():
            # Tier 2: a real branch, a real commit, a real push, then a
            # real pull request via `app/github.py`'s own client -- the
            # fabricated `f"https://github.com/example/repo/pull/{uuid4()...}"`
            # this replaced never touched a checkout at all. `branch()`
            # NEVER names `ctx.checkout.default_branch` -- a fresh,
            # per-finding branch name is the one thing standing between
            # this call and a push straight to `main` (see the module
            # docstring's "never contents: write on the default branch").
            branch_name = f"plumbline/patch-{finding.id}"
            ctx.checkout.branch(branch_name)
            _apply_diff_to_checkout(ctx.checkout, diff, files)
            ctx.checkout.commit_all(f"plumbline: {finding.title}"[:72])
            ctx.checkout.push()

            changes = {}
            for path in files:
                try:
                    changes[path] = ctx.checkout.read_file(path)
                except FileNotFoundError:
                    continue

            url = ctx.checkout.github.open_pull_request(
                ctx.checkout.repo_full_name, branch_name,
                title=f"plumbline: {finding.title}"[:120],
                body=f"Automated fix for finding `{finding.id}`.\n\n"
                     f"{finding.title}\n\nRoute: {finding.route or '(unresolved)'}",
                changes=changes, default_branch=ctx.checkout.default_branch,
            )
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
