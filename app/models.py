"""Frozen dataclasses for every Firestore document Plumbline persists.

Every model here is `@dataclass(frozen=True)`. Frozen dataclasses without
`__slots__` still keep `__dict__`, so a caller that needs a modified copy
uses `type(obj)(**{**obj.__dict__, "field": value})` rather than mutation.
"""

import time
from dataclasses import asdict, dataclass, field
from typing import Literal

Role = Literal["owner", "approver", "reader"]


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    name: str
    # `totp_secret` is the CONFIRMED, usable secret -- the one every
    # approval-gate check (Task 14b) reads, and the one `_member` in
    # `tests/conftest.py` sets directly to mean "this fixture user has 2FA
    # enabled". `totp_pending_secret` is where `POST /api/auth/totp/enrol`
    # (Task 8b) stores a freshly generated secret before it has been proven
    # -- an attacker who can reach `enrol` (e.g. via a stolen session with
    # no second factor of its own) must not thereby gain a usable secret,
    # so enrol never touches `totp_secret`. Only `POST /api/auth/totp/verify`,
    # after checking a current code against the *pending* secret, promotes
    # it into `totp_secret` and clears this field. Enrolling a second time
    # while already confirmed only ever overwrites the pending slot, never
    # the confirmed one -- see tests/test_totp.py's overwrite-attack tests.
    totp_secret: str | None = None
    totp_pending_secret: str | None = None
    # RFC 6238's own replay mitigation: the highest TOTP step (30s counter)
    # ever successfully redeemed for this user, checked transactionally by
    # `Repo.consume_totp_step` before a code is accepted anywhere (sign-in
    # gate checks, `totp/verify`, `DELETE /api/auth/totp`). A per-process
    # dict (Task 6/7's original approach, removed in fix round 1 -- see
    # `app/security.py`'s module docstring) is invisible to a sibling Cloud
    # Run instance; a field on the document already read/written for that
    # user is not -- see `Repo.consume_totp_step`'s docstring for the
    # transactional detail.
    last_used_totp_step: int = 0
    # The profile photo, stored inline as a `data:` URI rather than in a
    # bucket. Capped hard at `MAX_PHOTO_BYTES` by the only route that
    # writes it (`app/auth_routes.py`'s `upload_photo`), which is what
    # makes inline storage safe: a Firestore document has a 1 MiB ceiling,
    # and base64 inflates by ~4/3, so the cap has to leave room for the
    # rest of the row. Chosen over Cloud Storage deliberately -- an avatar
    # is a few tens of KB, and a bucket would add a second storage system,
    # its own IAM, its own lifecycle rules and its own bill for something
    # that fits in the row it belongs to. Empty string means no photo, and
    # every consumer falls back to initials.
    photo_url: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    repo: str
    plan: str = "team"
    seats: int = 5
    run_limit: int = 500
    runs_used: int = 0
    policy_version: int = 14
    is_demo: bool = False
    # Tenant-configurable gate rules (see gateway/policy.py's module
    # docstring for the tool-scope/gate-rule split). An empty list is
    # deliberately NOT distinguished here from "never configured" -- see
    # Gateway._rules_for in gateway/gateway.py, which treats both the same
    # way (falls back to DEFAULT_RULES) so a workspace can never end up
    # unconstrained just because nobody has set gate_rules yet.
    gate_rules: tuple[dict, ...] = ()
    # Added for Task 13: the ordered list of live environment names this
    # workspace has wired a browser up to (e.g. `("production", "staging")`)
    # -- `job/orchestrator.py`'s whole basis for "does Oracle have a second
    # environment to diff against". Oracle (agents/oracle.py) needs TWO
    # named drivers on `ctx.browsers` to run at all (`ctx.browsers[baseline_
    # env]`/`ctx.browsers[candidate_env]` -- a `KeyError` otherwise), so the
    # orchestrator only ever builds those two browsers, and only ever
    # instantiates Oracle, when this tuple holds at least two names; an
    # empty or single-entry tuple (the default -- most workspaces, and every
    # workspace before this task, have configured nothing here) means Oracle
    # is skipped for that run rather than crashing it. `environments[0]`/
    # `environments[1]` are read as baseline/candidate in that order -- a
    # deliberately simple two-slot convention, not an open comparison matrix
    # (see agents/oracle.py's own module docstring for why "more than two
    # environments" is a real future need this shape does not foreclose:
    # a workspace that wants a third comparison adds a third orchestrator
    # step, not a new field here).
    environments: tuple[str, ...] = ()
    # Task 14c: `POST /api/agents/pause` sets this; `job/orchestrator.py`'s
    # `execute()` checks it before ever calling `Repo.claim_run` and, if
    # set, leaves a `queued` run exactly as it found it -- unclaimed,
    # unbilled, untouched -- rather than starting the fleet. That is what
    # "takes effect on the next run, not mid-run" (the contract's own
    # words) actually means: a run already `claim_run`'d and mid-sequence
    # is never interrupted by a pause that lands after it started: this
    # flag is only ever consulted once, at the very top of `execute()`,
    # before any agent has run.
    fleet_paused: bool = False
    # Task 14d: the public API's per-key token-bucket ceiling, in requests
    # per minute -- shared by every `pk_live_` key this workspace has
    # issued (rate limiting is per KEY, not per workspace-wide total; see
    # `app/api_keys.py`). Living on `Workspace` rather than a hardcoded
    # constant is the whole point of "on the workspace's plan so it can be
    # raised without a deploy" (the brief's own words): a support engineer
    # raises a customer's ceiling with a `repo.put_workspace` write, not a
    # code change and a redeploy. 60/minute is `_PLAN_CATALOGUE`'s
    # (app/billing_routes.py) own "team" plan's implicit default -- there is
    # no per-plan table for this yet, deliberately: unlike seats/run_limit,
    # which every plan tier already prices distinctly, nothing in this task
    # ties API rate ceilings to a plan name, so a single workspace-level
    # default that any workspace can be individually raised past is the
    # honest shape for what actually exists today.
    api_rate_limit_per_minute: int = 60
    # Task 14f: the customer-run MCP servers THIS workspace has declared,
    # e.g. `({"name": "seed", "url": "https://seed.acme.internal/mcp"},)`.
    # `agents/mcp_client.py`'s `McpToolSource` is built from exactly this
    # tuple -- an agent may only ever discover/call a server that appears
    # here, and even then only one its own `gateway.policy.SCOPES` entry
    # names (see that module's `_scope_key`). A plain tuple of dicts,
    # matching `gate_rules`' own shape just above, rather than a typed
    # dataclass: this is tenant-declared configuration with no fixed
    # Plumbline-side schema to validate beyond "has a name and a url",
    # the same trade `gate_rules` already makes for the identical reason.
    mcp_servers: tuple[dict, ...] = ()
    # Task 14g: the connected GitHub App installation. `installation_id`
    # is GitHub's own id for this workspace's app installation (empty
    # until `GET /api/github/callback` succeeds); `repo_full_name`
    # (`"acme/storefront"`) and `default_branch` (`"main"`) are set
    # together by `POST /api/workspaces/{id}/repo` once a customer picks
    # ONE repo from `GET /api/github/repos` to connect. Deliberately
    # separate from the pre-existing `repo` field above (a free-text
    # display string every workspace has carried since Task 1, still read
    # by fixtures and the dashboard) rather than repurposing it -- this
    # task's brief names `repo_full_name` as its own field, and giving the
    # GitHub-App-authoritative identity its own name means nothing that
    # already reads `Workspace.repo` needs to change meaning out from
    # under it.
    installation_id: str = ""
    repo_full_name: str = ""
    default_branch: str = "main"
    # Tier 2 (2026-08-30 contract, item 1): the application under test --
    # the ONE fact `job/worker.py`'s own module docstring flagged as
    # missing everywhere else in this model: "no field anywhere records
    # what URL 'staging' or 'production' actually resolves to for a given
    # workspace." `""` means unconfigured. `agents/cartographer.py`'s
    # `run()` refuses to crawl at all when this is empty -- it returns an
    # explanatory `AgentResult(outcome="error", ...)` naming this exact
    # field rather than starting a breadth-first walk from a bare `"/"`
    # with no origin, which is what silently "crawled nothing and
    # reported a green run" before this field existed. Validated at
    # WRITE time (`app/workspace_routes.py`'s `PUT /api/workspace`), not
    # at run time -- a malformed URL costs a form error there, a whole
    # run here, so nothing this codebase writes into this field is ever
    # anything other than `""` or a URL that already passed
    # `agents.cartographer.validate_target_url`.
    target_url: str = ""
    # A per-session demo sandbox's own timestamp. Every non-demo
    # `Workspace` gets one too (a plain field default, not a demo-only
    # one) rather than adding a second dataclass shape just for demo rows.
    created_at: float = field(default_factory=time.time)
    # When this sandbox was last opened. The cleanup sweep
    # (`app/main.py`'s `_sweep_expired_demo_workspaces_factory`) reaps on
    # THIS, not on `created_at`.
    #
    # That distinction is the whole point. A demo sandbox is meant to
    # behave like an account -- come back next month and your behaviours,
    # runs and approvals are still there -- so a sandbox somebody is
    # actively using must never be collected, however old it is. Reaping
    # on `created_at` would have deleted a year-old sandbox out from under
    # a visitor who had opened it that morning. What the sweep is really
    # for is the other case: `POST /api/auth/demo` is public and
    # unauthenticated, so abandoned (or maliciously mass-created)
    # sandboxes would otherwise accumulate in Firestore forever. One
    # untouched for a full `DEMO_TTL_SECONDS` is provably unreachable --
    # no cookie that could still name it can be alive -- so it is garbage,
    # not somebody's workspace.
    last_seen_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Membership:
    id: str
    user_id: str
    workspace_id: str
    role: Role


@dataclass(frozen=True)
class Session:
    id: str
    user_id: str
    workspace_id: str
    expires_at: float
    user_agent: str = ""
    ip_city: str = ""
    is_demo: bool = False


@dataclass(frozen=True)
class PasswordReset:
    # `id` is the SHA-256 hash of the raw token, never the raw token itself
    # -- a leaked `password_resets` collection (a Firestore export, a
    # misconfigured rule, a compromised backup) must not hand an attacker a
    # working reset link for every user in it. `Repo.consume_password_reset`
    # looks this row up by re-hashing whatever the caller presents, exactly
    # like a session id would if this codebase stored hashed session ids.
    id: str
    user_id: str
    expires_at: float
    used: bool = False
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ApiKey:
    """A `pk_live_` public-API credential (Task 14d). Never carries the raw
    key -- only `key_hash` (SHA-256 hex of the raw value), exactly like
    `PasswordReset.id` above: a leaked `api_keys` collection must not hand
    an attacker a working key for anything. `id` is a separate, unrelated
    random id (not derived from the key at all), used for `GET`/`DELETE
    /api/keys/{id}` -- a customer managing their own keys never needs, and
    is never shown, anything the raw key or its hash would leak.

    `role` mirrors `Membership.role` (`Role`, `app/models.py`'s own type):
    a key's role is fixed at creation, and everywhere a key is used
    (`app/api_keys.py`'s `require_api_role`, the MCP server's per-key tool
    filtering in Task 14e) checks THIS field, never anything wider. A key
    can never exceed the role it was issued with because nothing here
    reads any role but this one.

    `expires_at=None` means no expiry; `revoked_at` is set (not deleted --
    Firestore has no delete, see `core/store.py`'s own module docstring)
    the moment `DELETE /api/keys/{id}` runs, and is checked on every
    authentication alongside expiry so a revoked key "stops working
    immediately" rather than merely being hidden from `GET /api/keys`.
    """

    id: str
    workspace_id: str
    name: str
    role: Role
    key_hash: str
    prefix: str
    created_by: str
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    revoked_at: float | None = None


@dataclass(frozen=True)
class Webhook:
    """An outbound delivery target for `run.finished`/`finding.created`/
    `patch.ready`/`patch.approved` (Task 14d). `secret` signs every
    delivery's body as an HMAC (`app/webhooks.py`'s `sign_payload`) so a
    receiver can verify a POST actually came from Plumbline -- generated
    once at creation, alongside the key, and never re-shown either (same
    "shown once" discipline as `ApiKey`, though here the receiver is
    expected to have copied it down at setup time rather than this being
    a security-critical secret at the same tier as an API key: it signs
    outbound calls WE make, it does not authenticate inbound ones).

    `status`/`failure_count` are what "an endpoint is failing" means
    concretely -- `app/webhooks.py`'s delivery loop increments
    `failure_count` on each failed attempt and flips `status` to
    `"failing"` once the retry budget (five attempts) is exhausted, so the
    UI has a real field to render rather than inferring health from ledger
    entries.
    """

    id: str
    workspace_id: str
    url: str
    secret: str
    events: tuple[str, ...] = ()
    status: str = "active"
    failure_count: int = 0
    created_by: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Route:
    id: str
    workspace_id: str
    path: str
    coverage_pct: int
    last_mapped: float = field(default_factory=time.time)
    # Added for Task 11a (Author). `gateway/policy.py`'s SCOPES gives Author
    # exactly `{"graph.read", "repo.write:specs"}` -- no `browser.read` --
    # so a live page is not something Author's `run()` is ever allowed to
    # visit itself. Its prompt still needs to describe "what's on this
    # route" (Task 11a's brief: "the snapshot's interactive elements"), so
    # that description has to already be sitting in the graph by the time
    # Author reads it -- which means Cartographer, the one agent that IS
    # scoped for `browser.read`, is the one that has to capture it during
    # its crawl. `(ref, role, name)` triples rather than the richer dicts
    # `BrowserDriver.a11y()` returns: a plain tuple of strings keeps `Route`
    # hashable (frozen dataclasses hash on every field by default, and a
    # `dict` inside would break that the moment anything ever hashes a
    # `Route`), and Author only ever needs role+name to describe a control
    # in a prompt -- level/state/disabled add detail an LLM prompt has no
    # use for. Empty by default so a `Route` built the way Task 10's own
    # tests build one (path + coverage only) stays valid.
    elements: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class Behaviour:
    id: str
    workspace_id: str
    text: str
    route: str
    spec_path: str = ""
    tags: tuple[str, ...] = ()
    owner: str = ""
    status: str = "active"
    # Fix round 1 (Economist, Task 12g): provenance as a durable, typed
    # field, not a string riding along in `tags`. `tags` is documented
    # (and used elsewhere in this fleet -- Economist's own
    # green_streak/repairs/duration_ms/asserts convention) as free-form and
    # rewritable wholesale; a caller that reconstructs the tuple instead of
    # appending to it silently drops anything encoded there, "sentinel"
    # included. `source` cannot be lost that way -- it is set once, at
    # creation, by whichever agent actually wrote this behaviour, and nothing
    # about editing `tags` later can touch it. `"author"` is the default
    # because that is the fleet's default writer (`agents/author.py`);
    # `agents/sentinel.py` sets `"sentinel"` explicitly for every behaviour
    # it derives from a real production incident, which is the one signal
    # `agents/economist.py` must never lose track of.
    source: str = "author"


@dataclass(frozen=True)
class Run:
    id: str
    workspace_id: str
    number: int
    trigger: str
    state: str = "queued"
    commit: str = ""
    started_by: str = ""
    held: int = 0
    failed: int = 0
    repaired: int = 0
    duration_ms: int = 0
    started_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Step:
    id: str
    run_id: str
    agent: str
    summary: str
    detail: str = ""
    outcome: str = "ok"
    duration_ms: int = 0
    at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Finding:
    id: str
    workspace_id: str
    title: str
    route: str
    found_by: str
    status: str = "triaged"
    severity: str = "high"
    seed: str = ""
    repro_count: int = 0
    at: float = field(default_factory=time.time)
    # The run whose trace produced this finding -- Task "close the
    # approval gate" territory. Default "" (not `None`) so every existing
    # row and every test that builds a `Finding` without naming a run
    # keeps working unchanged; only `agents/triager.py` (the one agent
    # that turns a run's failure into a `Finding`) sets it on every new
    # row it writes. This is the field `Repo.finding_for_run` and
    # `GET /api/runs/{id}`'s `finding_id` are both built on -- see
    # `app/run_routes.py` and `app/repo.py`.
    run_id: str = ""


@dataclass(frozen=True)
class Patch:
    id: str
    finding_id: str
    diff: str
    files: tuple[str, ...] = ()
    added: int = 0
    removed: int = 0
    verified: bool = False
    pr_url: str = ""
    gate_state: str = "awaiting_approval"


@dataclass(frozen=True)
class Incident:
    """A live-site problem Sentinel found by watching a running deployment
    -- a browser console error, a spike in a known error signature, a
    request that started failing -- as opposed to a `Finding`, which is
    what Triager produces from a *test run's* trace. Both land in the
    workspace's problem backlog, but they start from opposite ends: a
    `Finding` starts from a test that failed; an `Incident` starts from
    production behaving badly with no test involved at all.

    `count` and `first_seen` exist because Sentinel's job is to watch
    continuously, not just report once: the same `source`+`message` seen
    again is a repeat of an existing incident, not a new row, so a caller
    bumps `count` in place rather than accumulating one row per occurrence
    the way `Finding` (one row per triage) does not need to.
    """

    id: str
    workspace_id: str
    source: str
    message: str
    url: str = ""
    stack: str = ""
    count: int = 1
    first_seen: float = field(default_factory=time.time)
    status: str = "open"


@dataclass(frozen=True)
class Artefact:
    """One captured file (video, Playwright trace, HAR, or console log)
    from one failing spec in one Runner run (Task 12a). Unlike `specs`
    (deliberately a bare dict in `Repo` -- see that method's docstring for
    why arbitrary generated source has no fixed shape to validate), an
    artefact's shape IS fixed and small, so it gets the same frozen
    dataclass treatment every other Firestore document in this file gets.

    `id` is a composite key -- `run_id`, `spec_path`, and `kind` joined
    together (see `agents/runner.py`'s `_artefact_id`) -- deliberately NOT
    a random uuid. Two failing specs in the same run each write a "video"
    kind; without spec_path baked into the id, both writes would collide on
    the same document and the second would silently overwrite the first.
    Baking in `run_id` too means two runs of the same suite (a re-run,
    Triager's five-times-under-one-seed reproduction) never collide with
    each other's artefacts either. Deterministic and idempotent: re-running
    the same failing spec in the same run and writing the same kind again
    overwrites its own prior artefact rather than accumulating a duplicate.

    `content` is a plain string standing in for what would be binary or
    structured data in production (an actual video, a Playwright trace
    zip, a HAR JSON blob, a console log). Firestore documents cap at 1 MiB
    and are not where real binary artefacts belong at scale -- this field
    is deliberately synthetic, matching how far this task's own execution
    primitive (`agents/browser.py`'s `FakeBrowser.run_spec`) goes: capturing
    and pointing at the REAL files a real Chromium run would produce is
    `PlaywrightDriver`'s job once someone wires it up (that class's
    `run_spec` still raises `NotImplementedError` -- see its docstring),
    not this task's.
    """

    id: str
    workspace_id: str
    run_id: str
    spec_path: str
    kind: str
    content: str = ""
    created_at: float = field(default_factory=time.time)


def to_dict(obj) -> dict:
    return asdict(obj)
