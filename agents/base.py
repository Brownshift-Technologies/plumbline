"""The shape every one of the fleet's eleven agents takes.

Cartographer, Author, Healer, Chaos, Runner, Triager, Surgeon, Sentinel,
Auditor, Oracle, Economist -- eleven very different jobs, from crawling a
site to opening a PR to answering a plain-English question about a run --
but every one of them is, at the boundary this module defines, the same
shape: something with a `name` that turns an `AgentContext` into an
`AgentResult`. That uniformity is what lets a Runner loop over the fleet
without a `match agent.name` block, what lets `app/repo.py`'s `Step` model
record any agent's output with the same five fields, and what lets this
task's `tests/agent_fixtures.py` hand every later agent test the same
`make_ctx(...)` rather than eleven bespoke context builders.

Nothing here does I/O, calls the Gateway, or touches Firestore. This module
is deliberately just shapes -- the contract Tasks 10 through 12c implement
against, not an implementation of any of them.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class AgentResult:
    """What every agent hands back from `run()`, and the direct shape of a
    `Step` row (`app/models.py`) once a Runner persists it -- `summary`,
    `detail`, `outcome` line up with `Step`'s fields of the same name so
    that persisting a result is `Step(**vars(result), run_id=..., agent=...,
    id=..., duration_ms=...)`-shaped, not a field-by-field translation an
    eleventh agent could get subtly wrong.

    `outcome` is a free string, not a `Literal`, on purpose: `Step.outcome`
    isn't constrained either, and the fleet's outcomes are not one closed
    set -- Runner's is pass/fail/flaky, Healer's is healed/needs-human,
    Chaos's is fault-injected/recovered, Economist's might be
    under-budget/over-budget. Constraining this dataclass to a vocabulary
    that fits one agent would make every other agent's `run()` lie about
    its own outcome to satisfy the type. Each agent task documents its own
    vocabulary; `"ok"` is only ever the default for an agent that never
    fails.

    `data` is the one field with no promised shape at all -- it is where an
    agent puts whatever structured evidence is specific to it (Cartographer's
    diffed route list, Healer's proposed locator, Triager's root-cause
    guess) without this module having to grow a field, or a subclass, for
    every agent that needs one more piece of structured output. A `dict`
    the caller doesn't have to `isinstance`-check into a fixed type is what
    makes it safe to leave this file untouched for all eleven agent tasks.

    Frozen, like every persisted-shape dataclass in this codebase
    (`app/models.py`'s module docstring lays out the same reasoning): a
    result an agent handed back and a caller then logged, ledgered, or
    displayed must not be a value some other code can quietly edit out from
    under it after the fact.
    """

    summary: str
    detail: str = ""
    outcome: str = "ok"
    data: dict = field(default_factory=dict)


@dataclass
class AgentContext:
    """Everything an agent's `run()` needs, and nothing it doesn't.

    Every field here is an interface, not a concrete type -- `gateway`,
    `model`, `browser`, and `repo` are typed `object` deliberately, the same
    way `Gateway.__init__` takes `repo`/`ledger` untyped: an agent's `run()`
    calls `ctx.gateway.call(...)`, `ctx.model.generate(...)`,
    `ctx.browser.goto(...)`, `ctx.repo.put_route(...)` and never once needs
    to know or care whether it is holding the real `gateway.gateway.Gateway`
    wired to Vertex AI and a live Chromium, or the fakes
    `tests/agent_fixtures.py`'s `make_ctx` builds. That symmetry -- same
    object graph, same method calls, only the concrete classes underneath
    swapped -- is the whole reason `agents/browser.py`'s `FakeBrowser`
    exists as something more than a throwaway stub: every agent test in
    Tasks 10 through 12c exercises the real `run()` code path, not a
    parallel test-only branch inside it.

    `workspace_id` and `run_id` are plain strings, not `Workspace`/`Run`
    objects, so an agent that only needs to pass them through to
    `gateway.call(...)` or stamp them onto a new `Step`/`Finding` never
    has to load the full row first. An agent that does need the row reads
    it itself: `ctx.repo.workspace(ctx.workspace_id)` /
    `ctx.repo.run(ctx.run_id)`. This mirrors `Gateway.call`'s own
    `workspace_id: str` parameter (`gateway/gateway.py`) rather than
    introducing a second convention.

    Not frozen. Unlike `AgentResult` -- a finished value a caller hands
    around afterwards -- a context is live scaffolding an agent's `run()`
    is actively working inside of for the duration of one call; nothing in
    this codebase's `Repo`/`Gateway`/browser API is itself immutable, so
    freezing the box that holds references to them would protect nothing
    real while blocking the one legitimate use (a Runner reusing one
    context's `repo`/`gateway` across several agents in a single run,
    swapping only `browser` state via `goto`).
    """

    workspace_id: str
    run_id: str
    gateway: object
    model: object
    browser: object
    repo: object


class Agent(Protocol):
    """Structural, not nominal: an agent is anything with a `name: str` and
    a `run(ctx: AgentContext) -> AgentResult`, not anything that inherits
    from a particular base class. `typing.Protocol` is the right tool
    exactly because the fleet has no shared implementation to inherit --
    Cartographer's `run()` calls `browser.read`/`graph.write`, Surgeon's
    calls `repo.write:src`/`pr.open`/`pr.merge`, and forcing them under one
    concrete superclass would buy nothing but an empty method to override
    eleven times. A Runner (or a test) that wants to check "is this thing
    an agent" can `isinstance()` against this Protocol as long as it is
    marked `runtime_checkable` -- deliberately left unmarked here, since no
    caller in this codebase needs that check yet, and `isinstance` against
    a `Protocol` with a method (not just attributes) also only checks that
    the method *exists*, not that its signature matches; a later task that
    actually needs runtime checking should decide for itself whether that
    weaker guarantee is enough, rather than being handed one it never asked
    for.
    """

    name: str

    def run(self, ctx: AgentContext) -> AgentResult: ...
