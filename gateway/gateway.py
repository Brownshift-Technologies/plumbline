"""The Gateway -- the single door every agent tool call passes through.

Every agent action in Plumbline -- Cartographer reading a page, Surgeon
opening a PR, Chaos faulting a network call -- goes through
`Gateway.call(workspace_id, agent, tool, target, payload, fn)` rather than
calling out directly. That gives one place to enforce, in order:

1. **Input safety.** `payload` (if any) is scanned by `core.guards.check_input`
   for a prompt-injection attempt before anything else runs. This is a
   blanket check independent of scope -- a poisoned payload is rejected
   whether or not the tool it was headed for would otherwise be allowed.
2. **A missing target on a gated tool is a blocked call, not an open one.**
   `gateway/policy.py`'s `decide()` can only gate a path it is given a
   `target` for; called with `target=""` it has nothing to match a
   `pr.merge`/`env.write` gate pattern against, and returns allowed. The
   Gateway is the caller that supplies `target`, so it refuses to hand
   `decide()` an empty one for any tool with a gate space to begin with
   (`_GATED_TOOLS`) -- this is defence in depth alongside policy's own
   fail-closed behaviour, not a substitute for it: neither layer should
   rely on the other catching what it lets through.
3. **Authorisation.** `decide(agent, tool, target, rules)` -- see below for
   where `rules` comes from -- says whether the call is allowed outright,
   allowed only with a human in the loop, or denied.
4. **Execution.** Only once 1-3 have all cleared does `fn()` actually run.
5. **Redaction on the way out.** Any result from a `*.read` tool -- a bare
   string or a nested dict/list/tuple (a HAR capture, a trace object) -- is
   walked by `core.guards.redact_deep` before it reaches the caller. The
   Gateway is what makes "every artefact a `.read` tool hands back has had
   PII scrubbed" a promise the platform keeps automatically, not something
   each of the seven agents has to remember to do itself, and not a promise
   that quietly only held for the tools that happened to return a plain
   string (fix round 1: an earlier version of this method redacted only
   `isinstance(result, str)`, which meant a `.read` tool returning
   structured data slipped past redaction entirely).

Every one of those outcomes -- allowed, blocked, human-gated, or an error
raised out of `fn()` -- is written to the workspace's ledger before `call`
returns control to its caller (an exception from `fn()` included: see the
`try` around it below). That is what "one gateway call per logical act"
means in practice: nothing here loops the ledger per item, and nothing
that runs outside this method's own `fn()` call gets to skip being
recorded.

Two kinds of policy, only one of them workspace data
------------------------------------------------------
`SCOPES` (what an agent *is*) lives in code, in `gateway/policy.py`, and no
workspace setting can widen it -- see that module's docstring. `gate_rules`
(which paths need a human, which environments Chaos may touch) is exactly
the tenant-configurable half, stored on `Workspace.gate_rules` alongside
`Workspace.policy_version`. `Gateway.call` is the thing that loads a
workspace, hands its rules to `decide()` as a plain, opaque argument --
this module never inspects or parses a rule itself; that stays entirely
policy.py's job -- and records which `policy_version` was in force in
every ledger entry, so an audit can answer "which rules were in effect
when this call was allowed" rather than only "what do the rules say
today".

A workspace lookup that misses -- the workspace does not exist, or exists
but has never had `gate_rules` configured -- falls back to
`gateway.policy.DEFAULT_RULES`, the same defaults `decide()` itself falls
back to when `rules=None`. Both are read as "nobody has configured
anything for this workspace yet", not as "this workspace has no gates";
collapsing that distinction the other way -- treating a lookup miss as
"no gates apply" -- is exactly the fail-open bug this module exists to
not have. See `_rules_for`.
"""

from app.models import Workspace
from core.guards import check_input, redact_deep
from core.telemetry import log_event, span
from gateway.policy import decide

# Every tool that has a gate space in gateway/policy.py's DEFAULT_RULES (or
# could plausibly have one in a workspace's own rules) needs a real target
# to be gated against -- see the module docstring's point 2. This list is
# deliberately the tools with a *pattern to match against*, not every tool
# in SCOPES: a scope-only tool like `browser.read` has no gate concept at
# all, and forcing a target on it would just break ordinary read calls that
# never had one.
#
# Hand-maintained, and that is a real coupling to keep an eye on: a
# workspace's own `gate_rules` (see `_rules_for`) are entirely data-driven
# and can name any tool at all -- policy.py's layer of defence does not
# need this set updated to gate a new tool. This set is the Gateway's OWN,
# independent layer of defence-in-depth on top of that (see point 2 above),
# and it only covers what it is told to: a future tool added to SCOPES with
# its own gate pattern in some workspace's rules, but never added here,
# silently loses this half of the protection -- policy.py would still
# gate/deny it correctly, but the Gateway's missing-target check would not
# fire for it. There is no mechanical way to derive this set from rules
# data (rules are per-workspace and loaded per-call, not known up front),
# so keeping it in sync with what gate_rules actually gate is a manual,
# ongoing responsibility, not a one-time list.
_GATED_TOOLS = frozenset({"pr.merge", "pr.open", "repo.write:src", "env.write"})


class GatewayError(Exception):
    """Raised for every call `Gateway.call` does not let through: an unsafe
    payload, a missing target on a gated tool, an out-of-scope tool, or a
    tool a rule denies or gates.

    `needs_human` is what lets a caller tell "this is permanently denied"
    apart from "this needs a human to approve it" -- a UI surfaces an
    "request approval" action only when it is True. It defaults to False so
    every other rejection reads as an outright block, matching
    `gateway.policy.Decision`'s own default.
    """

    def __init__(self, reason: str, needs_human: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.needs_human = needs_human


class Gateway:
    def __init__(self, repo, ledger):
        self._repo = repo
        self._ledger = ledger

    def call(self, workspace_id, agent, tool, target="", payload=None, fn=None):
        # str(...) on every span attribute rather than passing agent/tool/
        # target through as-is: OpenTelemetry's set_attribute accepts only
        # str/bool/int/float (or a homogeneous sequence of one of those),
        # and this method is explicitly expected to survive an agent of
        # `None` -- passing that straight through would make the span
        # itself the thing that raises, before check_input or decide ever
        # get a say.
        with span("gateway.call", agent=str(agent), tool=str(tool), target=str(target)):
            rules, policy_version = self._rules_for(workspace_id)

            if payload:
                text = " ".join(str(v) for v in payload.values())
                guard = check_input(text)
                if not guard.allowed:
                    reason = f"input rejected: {guard.reason}"
                    self._record(workspace_id, agent, tool, target, "blocked", reason, policy_version)
                    raise GatewayError(reason)

            if tool in _GATED_TOOLS and not (target or "").strip():
                reason = f"missing target for gated tool {tool!r}"
                self._record(workspace_id, agent, tool, target, "blocked", reason, policy_version)
                raise GatewayError(reason)

            decision = decide(agent, tool, target, rules=rules)
            if not decision.allowed:
                outcome = "gated" if decision.needs_human else "blocked"
                self._record(workspace_id, agent, tool, target, outcome, decision.reason, policy_version)
                raise GatewayError(decision.reason, needs_human=decision.needs_human)

            # fn() runs only after every check above has cleared, and
            # nothing here swallows what it raises: a call that was
            # authorised but then failed to execute still leaves exactly
            # one ledger entry (so the audit trail has no silent gap for
            # "we said yes, then what?"), and the caller sees the real
            # exception rather than a GatewayError that would misreport an
            # execution failure as a policy decision.
            try:
                result = fn() if fn is not None else None
            except Exception as exc:
                self._record(workspace_id, agent, tool, target, "errored", str(exc), policy_version)
                raise

            # redact_deep walks a dict/list/tuple result down to its string
            # leaves and reassembles the same shape around the redacted
            # values -- a redacted dict stays a dict, a redacted list of
            # findings stays a list of findings. A bare string result is
            # just the depth-zero case of the same walk. Fix round 1: an
            # earlier version only redacted `isinstance(result, str)`, so a
            # `.read` tool returning structured data (a HAR-shaped dict, a
            # trace object) slipped past redaction entirely -- see
            # core.guards.redact_deep for the full contract, including how
            # it handles a type it does not recognise (passes it through,
            # never raises) and a cyclic structure (marks the re-entered
            # container "[CIRCULAR]" rather than hanging).
            # Task 14f: an `mcp.<server>.<tool>` result is redacted
            # UNCONDITIONALLY, not only when the tool name happens to end
            # in `.read` -- a customer's own MCP server can name a tool
            # anything at all (`get_user`, `reset_database`, ...), and
            # nothing about "this came from a third-party server, not our
            # own code" is contingent on a naming convention we do not
            # control. Treating every MCP result as read-shaped for
            # redaction purposes is the safe default: structural PII
            # scrubbing on a result that turns out to be a plain `{"ok":
            # true}` costs nothing, and skipping it on one that turns out
            # to embed a customer's row of PII is the actual defect this
            # branch exists to prevent.
            if tool.endswith(".read") or tool.startswith("mcp."):
                result = redact_deep(result)

            self._record(workspace_id, agent, tool, target, "allowed", decision.reason, policy_version)
            return result

    def _rules_for(self, workspace_id):
        """The (rules, policy_version) pair to authorise this call with.

        A missing workspace or a workspace with no configured gate_rules
        both resolve to `rules=None` -- `decide()`'s own signal to fall
        back to `gateway.policy.DEFAULT_RULES` -- so a workspace lookup
        that misses can never leave an agent unconstrained. `policy_version`
        still needs a value in that case for the ledger entry to record
        (see the module docstring): `Workspace.policy_version`, the class's
        own default, is what a real workspace starts at before its first
        edit, so it is the honest answer to "what policy was in force" when
        there is no workspace document to read a version off of at all.
        """
        workspace = self._repo.workspace(workspace_id)
        if workspace is None:
            return None, Workspace.policy_version
        return (list(workspace.gate_rules) if workspace.gate_rules else None), workspace.policy_version

    def _record(self, workspace_id, agent, tool, target, decision, reason, policy_version):
        log_event("gateway.decision", agent=str(agent), tool=str(tool), decision=decision)
        self._ledger.append(
            workspace_id,
            agent,
            tool,
            {
                "decision": decision,
                "reason": reason,
                "target": target,
                "policy_version": policy_version,
            },
        )
