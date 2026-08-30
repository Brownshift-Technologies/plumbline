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
class Route:
    id: str
    workspace_id: str
    path: str
    coverage_pct: int
    last_mapped: float = field(default_factory=time.time)


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


def to_dict(obj) -> dict:
    return asdict(obj)
