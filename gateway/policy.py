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

Matching: a pattern is an ordinary `fnmatch` glob against `target`, except
a pattern starting with "!" inverts to an *allow-list*: the rest is a
comma-separated list of glob patterns, and the whole thing matches when
`target` matches none of them. This is what lets one data-driven rule
express "anything but staging" for `env.write` -- an owner can configure
exactly which environment names Chaos may reach -- without a hardcoded,
unbounded blacklist of every possible production name.

`target` is normalised with `posixpath.normpath` (pure string manipulation,
no filesystem I/O) before matching. Without that, a target like
`"src/catalog/../checkout/payment-client.ts"` -- which *is* the
payments file -- would read as `src/catalog/*` to a naive string match and
slip straight past a `src/checkout/*` human gate. Normalising first closes
that off: the pattern is matched against where the target actually
resolves, not against whatever string an agent (or a bug) happened to send.

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
    "chaos":        frozenset({"net.fault", "env.write"}),
    "runner":       frozenset({"browser.drive", "artefact.write"}),
    "triager":      frozenset({"trace.read", "repo.read"}),
    "surgeon":      frozenset({"repo.write:src", "pr.open", "pr.merge"}),
}

# The defaults every workspace starts with. A workspace that configures its
# own `rules` (including an explicit `rules=[]`, meaning "no gates") uses
# that list *instead of* this one, wholesale -- see `decide()`.
DEFAULT_RULES: list[dict] = [
    {"tool": "pr.merge", "pattern": "src/checkout/payment*", "effect": "human"},
    {"tool": "pr.merge", "pattern": "src/billing/*", "effect": "human"},
    {"tool": "pr.merge", "pattern": "*/payments/*", "effect": "human"},
    # "!allow-list,of,patterns" -- see the matching note in the module
    # docstring. Chaos may only reach environments matching one of these;
    # anything else for env.write is denied.
    {"tool": "env.write", "pattern": "!staging,staging-*,preview-*", "effect": "deny"},
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
    """A rule this module can act on: a dict with a str tool, str pattern,
    and a recognised effect. Anything else -- a stray `None`, a string, a
    dict missing a key, an effect that isn't one of the three words this
    module understands -- is tenant data this module cannot interpret, so
    it's skipped rather than raised on. One bad row must not take down
    every agent in the workspace.
    """
    return (
        isinstance(rule, dict)
        and isinstance(rule.get("tool"), str)
        and isinstance(rule.get("pattern"), str)
        and rule.get("effect") in _VALID_EFFECTS
    )


def _pattern_matches(pattern: str, target: str) -> bool:
    if pattern.startswith("!"):
        allowed = [p for p in pattern[1:].split(",") if p]
        return not any(fnmatch.fnmatchcase(target, p) for p in allowed)
    return fnmatch.fnmatchcase(target, pattern)


def decide(
    agent: str,
    tool: str,
    target: str = "",
    rules: list[dict] | None = None,
) -> Decision:
    scope = SCOPES.get(agent)
    if scope is None:
        return Decision(False, f"unknown agent {agent!r}")
    if tool not in scope:
        return Decision(False, f"{tool!r} is not in scope for {agent!r}")

    # `rules=None` ("use the defaults") and `rules=[]` ("this workspace
    # configured no gates") are deliberately different states -- collapsing
    # them with a falsy check is the bug this line exists to prevent.
    active_rules = DEFAULT_RULES if rules is None else rules
    norm_target = posixpath.normpath(target) if target else target

    strictest: dict | None = None
    for rule in active_rules:
        if not _is_well_formed(rule):
            continue
        if rule["tool"] != tool:
            continue
        if not _pattern_matches(rule["pattern"], norm_target):
            continue
        if strictest is None or _EFFECT_PRIORITY[rule["effect"]] > _EFFECT_PRIORITY[strictest["effect"]]:
            strictest = rule

    if strictest is None or strictest["effect"] == "allow":
        return Decision(True, "within scope")
    if strictest["effect"] == "deny":
        return Decision(False, f"{target!r} is denied by rule {strictest['pattern']!r} for {tool!r}")
    return Decision(
        False,
        f"{target!r} matches human-gated rule {strictest['pattern']!r} for {tool!r}",
        needs_human=True,
    )
