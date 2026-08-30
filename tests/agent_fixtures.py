"""`make_ctx(...)` -- the one factory every agent test in Tasks 10 through
12c builds its `ctx_*` fixtures from.

The fleet's tests are agent-specific, so their fixtures live in each
agent's own test file (`tests/test_cartographer.py`'s `ctx_uncovered`,
`tests/test_healer.py`'s `ctx_broken_locator`, and so on -- roughly twenty
across the eleven agent tasks). What is NOT agent-specific is how an
`AgentContext` gets built at all: a `Repo` over a fresh `FakeFirestore`, a
`Gateway` wired to a `Ledger` over that same `Repo`, a `FakeModel` scripted
with whatever responses that test wants, a `FakeBrowser` seeded with
whatever pages that test wants. Twenty fixtures each writing those same
dozen lines by hand is twenty places for the wiring to drift; one factory
here is twenty short calls instead, and a change to how a context gets
built (a new field, a different default config) is one edit instead of
twenty.

Deliberately does NOT pre-seed a `Workspace` or `Run` document for
`workspace_id`/`run_id` into the repo it builds. Which fields of a
`Workspace` matter differs per agent -- Chaos cares about `gate_rules`,
Economist cares about `run_limit`/`runs_used`, Surgeon cares about
`policy_version` -- so there is no one "reasonable default" workspace this
factory could seed that would not be wrong for at least one of the eleven.
`Gateway.call` already treats a missing workspace as "use the defaults"
rather than "fail" (see `gateway/gateway.py`'s `_rules_for`), so a context
built with no workspace seeded is a valid, working context on its own; a
test that needs a real `Workspace` row calls `ctx.repo.put_workspace(...)`
itself, the same way `tests/conftest.py`'s `_member` does for the HTTP
route tests.
"""

from agents.base import AgentContext
from agents.browser import FakeBrowser
from app.repo import Repo
from app.settings import PlumblineConfig
from core.fakes import FakeFirestore, FakeModel
from gateway.gateway import Gateway
from gateway.ledger import Ledger

# Matches `tests/conftest.py`'s `config` fixture field-for-field. Not
# imported from there: `tests/conftest.py` is pytest's fixture module, not
# a plain importable helper, and duplicating five literal field values here
# is a smaller, more honest coupling than importing a fixture function and
# calling it outside of pytest's own injection.
_CONFIG = PlumblineConfig(
    project_id="test",
    location="us-central1",
    vertex_location="global",
    model="gemini-3.5-flash",
    firestore_prefix="plumbline",
)


def make_ctx(
    *,
    pages: dict | None = None,
    browsers: dict[str, dict] | None = None,
    model_responses=("ok",),
    spec_results: dict | None = None,
    workspace_id: str = "ws1",
    run_id: str = "r1",
    repo: Repo | None = None,
) -> AgentContext:
    """Build an `AgentContext` wired to fakes throughout. Every agent test
    uses this, directly or through its own `ctx_*` fixture.

    `repo` defaults to a fresh `Repo` over a fresh `FakeFirestore` -- so two
    calls with no `repo=` passed never share a store (see
    `tests/test_agent_fixtures.py`'s `test_two_contexts_do_not_share_a_
    store`) -- but a caller that already built and seeded a `Repo` (put a
    `Workspace` with specific `gate_rules` on it, pre-populated `Route`
    rows for Cartographer to diff against) passes it straight through
    unchanged, so seeding and context-building compose instead of
    fighting each other.

    `model_responses` is a tuple by default (an immutable default is a
    default that cannot be accidentally shared/mutated across calls the
    way a mutable one could), converted to a list because `FakeModel`
    itself pops through its responses by index off a list it owns.

    `browsers` (fix round 1) is `{"env_name": pages_dict, ...}` -- the same
    shape as `pages`, one per named environment -- for Oracle, the one
    agent that needs more than the single `ctx.browser` every other agent
    gets. Each entry becomes its own `FakeBrowser(pages_for_env,
    spec_results)`, landing on `ctx.browsers["env_name"]`; `ctx.browser`
    (the primary/default driver, built from the top-level `pages=`) is
    unaffected either way, so a fixture that does not pass `browsers=` --
    the other ten agents -- gets exactly the context it always has. See
    `AgentContext.browsers`'s docstring for why a named mapping was chosen
    over a second fixed field.
    """
    active_repo = repo or Repo(_CONFIG, client=FakeFirestore())
    return AgentContext(
        workspace_id=workspace_id,
        run_id=run_id,
        gateway=Gateway(active_repo, Ledger(active_repo)),
        model=FakeModel(list(model_responses)),
        browser=FakeBrowser(pages or {}, spec_results),
        repo=active_repo,
        browsers={name: FakeBrowser(env_pages, spec_results)
                  for name, env_pages in (browsers or {}).items()},
    )
