"""Deny-by-default tool scopes, plus tenant-configurable human/deny gates.

There are two kinds of policy here, and only one of them is workspace data:

- `SCOPES` says what an agent *is*. It is code, not configuration -- no
  workspace setting can ever teach the Cartographer to merge a PR, because
  merging isn't a thing a Cartographer does. This table changes only when
  the fleet's roster changes, and that's a code review, not a support
  ticket.
- `rules` (and the `DEFAULT_RULES` used when a caller passes none) are
  per-workspace data an owner configures: which paths need a human before
  `pr.merge`, which environments Chaos may reach with `env.write`. Rules
  may only ever *narrow* what `SCOPES` already permits -- gate it behind a
  human, or deny it outright. A rule can never grant a tool `SCOPES` didn't
  already put in the agent's hands: `decide()` checks scope first, and
  nothing that follows can undo that check. If a change to this file ever
  makes a rule the reason a call is *allowed*, the design has been
  inverted -- rules subtract, they never add.

`decide()` runs on every tool call an agent makes, so it stays pure and
synchronous: no I/O, no workspace lookups. It takes `rules` as a plain
argument -- loading the right rules for a workspace is Task 5's Gateway's
job, not this module's.

Rules are tenant data, so `decide()` treats a malformed rule as noise to
skip, never as a reason to fail closed on every call an agent makes that
day -- see `_is_well_formed`. A well-formed rule that simply doesn't match
the current call is not an error at all; it just doesn't apply.

Matching: a rule carries exactly one of two match forms.
- `"pattern": str` -- an ordinary `fnmatch` glob; the rule fires when
  `target` matches it. This is a review round after the first cut of this
  module used a `"!a,b,c"`-prefixed string to mean "anything but a,b,c",
  overloading `pattern`'s own sigil space -- a pattern that legitimately
  started with `!` had no escape and would silently mismatch rather than
  error. `allow_only` below replaces that DSL entirely: `pattern` is now
  always a plain, literal glob, `!` included.
- `"allow_only": list[str]` -- the rule fires when `target` matches NONE of
  the listed globs. This is what lets one data-driven rule express
  "anything but staging" for `env.write` -- an owner can configure exactly
  which environment names Chaos may reach -- without an unbounded blacklist
  of every possible production name, and without a string micro-syntax that
  needs its own parser. It is also a structured field `PUT /api/policy/rules`
  can validate and a settings UI can render as a real list control, where the
  string form would need a bespoke round-trip that could re-serialise wrong
  on every edit.

`target` is assumed to be a forward-slash path the way a git diff reports
one (`decide()`'s only caller today, Task 5's Gateway, extracts it from PR
diffs and environment names -- neither of which is ever a Windows path in
this product's own pipeline). Call sites are still free to hand it anything,
so `target` is normalised before matching: backslashes become forward
slashes first, then `posixpath.normpath` collapses `.`/`..` segments (both
pure string manipulation, no filesystem I/O). Without the slash swap,
`"src\\checkout\\payment-client.ts"` -- the same file, spelled the way a
Windows-style caller might -- would read as an unrelated opaque string to a
`src/checkout/*` glob and evade the gate; without the traversal collapse,
`"src/catalog/../checkout/payment-client.ts"` -- which *is* the payments
file -- would read as `src/catalog/*` and slip past the same gate.

A gated tool with no usable target fails CLOSED, not open. A tool is
"gated" here if the active rule set has at least one well-formed rule
naming it -- so `rules=[]` ("this workspace configured no gates") also
means no tool is gated, and an empty target is fine, consistently with the
"empty rules is not the same as no rules" split above. But a rule set that
DOES have an opinion about a tool must not silently allow a call that gives
it nothing to evaluate: if none of that tool's rules can even be checked
because `target` is blank, that is exactly the situation a human gate
exists for, so the call returns `needs_human=True` rather than falling
through to the scope-only allow. This closes the gap a reviewer found in
the first cut: `decide("surgeon", "pr.merge")` with no target matched none
of the payments-path patterns (an empty string matches no literal path
glob) and returned a bare allow -- so any bug or omission upstream that
dropped the target off a payments merge would have sailed it through
completely ungated. Task 5's Gateway independently rejects a gated call
whose target is empty or whitespace too; neither layer is allowed to rely
on the other one catching it.

Conflicts: when more than one rule matches the same call, the *strictest*
effect wins -- deny beats human beats allow -- regardless of which rule
came first in the list. Rule order is workspace data too, and a policy
gate should not depend on a caller getting list order right; the safer
reading of two rules that disagree is "at least one of you wanted this
blocked."
"""

from dataclasses import dataclass
import fnmatch
import posixpath

SCOPES: dict[str, frozenset[str]] = {
    "cartographer": frozenset({"browser.read", "graph.write"}),
    "author":       frozenset({"graph.read", "repo.write:specs"}),
    "healer":       frozenset({"repo.write:specs", "trace.read"}),
    # "mcp.seed" (Task 14f): the ONE customer-run MCP server class Chaos
    # may reach -- a database-seeding server, so a fault-injection run can
    # reset state between attempts. See the module docstring's new "MCP
    # tools are scoped by SERVER, not by individual tool" section: this is
    # a scope-KEY, `mcp.<server-name>`, checked against `mcp.<server>.
    # <tool>` calls via `_scope_key` in `decide()` below, never the literal
    # per-tool string (a customer's server can add or rename its own tools
    # without this table ever needing an edit). No other agent gets any
    # `mcp.*` scope entry at all -- see Economist's own note below for why
    # "gains nothing" is the deliberate default, not an oversight, for
    # every agent this table does not explicitly grant one to.
    "chaos":        frozenset({"net.fault", "env.write", "mcp.seed"}),
    "runner":       frozenset({"browser.drive", "artefact.write"}),
    # "repo.write:findings" added for Task 12b -- the original set here had
    # no write tool at all for an agent whose entire job is persisting a
    # `Finding` per distinct failure (see agents/triager.py's module
    # docstring). Every other write in this fleet (Cartographer's routes,
    # Author's/Healer's specs, Runner's artefacts) already goes through a
    # scoped, ledgered gateway call rather than a bare `ctx.repo.put_*`; a
    # Finding -- which drives whether Surgeon ever attempts a patch -- is
    # exactly the kind of consequential write that belongs in the audit
    # trail too, not an exception to the pattern every other agent follows.
    "triager":      frozenset({"trace.read", "repo.read", "repo.write:findings"}),
    # "checks.write" added for Task 14g: Surgeon reports a run's outcome
    # back to GitHub as a check run (`app/github.GitHubApp.create_check_
    # run`) alongside opening the pull request -- `repo.read`/`pr.open`
    # already existed for it (Task 9); this is the one new tool Task 14g's
    # own brief names by name ("Add checks.write for Surgeon"). No other
    # agent gets it, and Economist -- see its own note below -- gains
    # nothing at all from this task.
    "surgeon":      frozenset({"repo.write:src", "pr.open", "pr.merge", "checks.write"}),
    # Added in Task 9's fix round 1: these four exist in the fleet (Task 9's
    # brief names all eleven) but were left out of SCOPES because Task 9's
    # own file list never included this module. Leaving them out any
    # longer would mean each of their four agent tasks discovers, on its
    # own, that `gateway.call` returns "unknown agent" for it -- ruled into
    # this fix round instead. Sentinel and Economist both get exactly one
    # write tool each (`repo.write:specs`, none) -- see below for why
    # Economist gets none at all.
    "sentinel":     frozenset({"telemetry.read", "graph.read", "repo.write:specs"}),
    "auditor":      frozenset({"browser.read", "graph.read"}),
    "oracle":       frozenset({"browser.read", "graph.read"}),
    # Economist is read-only BY DESIGN, not by omission: it recommends —
    # which tests are worth their runtime, which routes are overtested —
    # and a recommendation is only trustworthy because the agent making it
    # has no tool that could act on it unilaterally. An agent that
    # analyses cost and can also delete the tests it judges wasteful is
    # one prompt-injected route away from doing exactly that; SCOPES is
    # the one place in this codebase no workspace rule can override (see
    # the module docstring), so "Economist cannot write" has to be
    # enforced here, not left to a rule an owner might configure away.
    "economist":    frozenset({"graph.read", "repo.read"}),
}

# The defaults every workspace starts with. A workspace that configures its
# own `rules` (including an explicit `rules=[]`, meaning "no gates") uses
# that list *instead of* this one, wholesale -- see `decide()`.
DEFAULT_RULES: list[dict] = [
    {"tool": "pr.merge", "pattern": "src/checkout/payment*", "effect": "human"},
    {"tool": "pr.merge", "pattern": "src/billing/*", "effect": "human"},
    {"tool": "pr.merge", "pattern": "*/payments/*", "effect": "human"},
    # Chaos may only reach environments matching one of these; anything
    # else for env.write is denied. See "allow_only" in the module
    # docstring -- this replaced a "!staging,staging-*,preview-*" string
    # DSL that had no escape for a pattern starting with a literal "!".
    {"tool": "env.write", "allow_only": ["staging", "staging-*", "preview-*"], "effect": "deny"},
]

_VALID_EFFECTS = frozenset({"human", "deny", "allow"})
# Priority when several rules match the same call -- higher wins. "allow"
# sits at the bottom because it is a no-op (see `decide()`): it can never
# out-rank a same-target "human" or "deny", only fail to add anything.
_EFFECT_PRIORITY = {"allow": 0, "human": 1, "deny": 2}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    needs_human: bool = False


def _is_well_formed(rule: object) -> bool:
    """A rule this module can act on: a dict with a str tool, a recognised
    effect, and exactly one match form -- a str `pattern` xor a non-empty
    `allow_only` list of str globs. Anything else -- a stray `None`, a
    string, a dict missing a key, both match forms at once, an effect that
    isn't one of the three words this module understands -- is tenant data
    this module cannot interpret, so it's skipped rather than raised on.
    One bad row must not take down every agent in the workspace.
    """
    if not (isinstance(rule, dict) and isinstance(rule.get("tool"), str)
            and rule.get("effect") in _VALID_EFFECTS):
        return False
    has_pattern = isinstance(rule.get("pattern"), str)
    allow_only = rule.get("allow_only")
    has_allow_only = (
        isinstance(allow_only, list) and len(allow_only) > 0
        and all(isinstance(p, str) for p in allow_only)
    )
    return has_pattern != has_allow_only  # exactly one, not both, not neither


def _rule_matches(rule: dict, target: str) -> bool:
    if "pattern" in rule:
        return fnmatch.fnmatchcase(target, rule["pattern"])
    return not any(fnmatch.fnmatchcase(target, p) for p in rule["allow_only"])


def _normalise(target: str) -> str:
    if not target:
        return target
    return posixpath.normpath(target.replace("\\", "/"))


def _scope_key(tool: str) -> str:
    """The string `decide()` checks membership of `SCOPES` against.

    For an MCP tool (`mcp.<server>.<tool-name>`, Task 14f -- see
    `agents/mcp_client.py`), that is `mcp.<server>`, NOT the full literal
    string: a customer's own MCP server is free to expose any number of
    tools under any names, and SCOPES is code an engineer edits on
    purpose (see the module docstring) -- forcing an edit here every time
    a customer's server adds or renames a tool would be exactly the
    "workspace setting widens scope" failure this module exists to
    prevent, just moved one layer down. Scoping by SERVER is what "an
    agent may call a server only if that server is in its own scope
    entry" (Task 14f's own brief, verbatim) means concretely: SCOPES names
    servers an agent may reach, never individual remote tools. Any other
    tool string (no `mcp.` prefix, or fewer than three dot-separated
    segments -- a malformed mcp tool name with nothing to scope by) is
    returned unchanged, so this is a no-op for every one of Plumbline's
    own built-in tools.
    """
    parts = tool.split(".")
    if len(parts) >= 3 and parts[0] == "mcp":
        return ".".join(parts[:2])
    return tool


def decide(
    agent: str,
    tool: str,
    target: str = "",
    rules: list[dict] | None = None,
) -> Decision:
    scope = SCOPES.get(agent)
    if scope is None:
        return Decision(False, f"unknown agent {agent!r}")
    if _scope_key(tool) not in scope:
        return Decision(False, f"{tool!r} is not in scope for {agent!r}")

    # `rules=None` ("use the defaults") and `rules=[]` ("this workspace
    # configured no gates") are deliberately different states -- collapsing
    # them with a falsy check is the bug this line exists to prevent.
    active_rules = DEFAULT_RULES if rules is None else rules
    norm_target = _normalise(target)

    strictest: dict | None = None
    tool_is_gated = False
    for rule in active_rules:
        if not _is_well_formed(rule) or rule["tool"] != tool:
            continue
        tool_is_gated = True
        if not _rule_matches(rule, norm_target):
            continue
        if strictest is None or _EFFECT_PRIORITY[rule["effect"]] > _EFFECT_PRIORITY[strictest["effect"]]:
            strictest = rule

    if strictest is None:
        if tool_is_gated and not norm_target.strip():
            return Decision(False, f"{tool!r} is gated and no target was given", needs_human=True)
        return Decision(True, "within scope")
    if strictest["effect"] == "allow":
        return Decision(True, "within scope")
    if strictest["effect"] == "deny":
        return Decision(False, f"{target!r} is denied by a rule for {tool!r}")
    return Decision(False, f"{target!r} matches a human-gated rule for {tool!r}", needs_human=True)
