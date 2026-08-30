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
    totp_secret: str | None = None
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


def to_dict(obj) -> dict:
    return asdict(obj)
