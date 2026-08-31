"""Task 14d: the versioned public API, `/v1/...` -- authenticated by
`pk_live_` key (`app/api_keys.py`), not by `pl_session` cookie.

**Versioning is a promise, so it is structural, not conventional.** Every
response model below (`V1Run`, `V1Finding`, `V1Route`, `V1LedgerStatus`)
is its OWN Pydantic model, defined in this file, independent of
`app/run_routes.py`'s `_run_json`/`app/finding_routes.py`'s
`_finding_json`/etc. Nothing here imports or returns an internal
handler's dict shape. That is deliberate, not merely tidy: if this
router re-exported `run_routes.get_run` (or its `_run_json` helper)
directly, the day someone renames an internal field, adds a debug field
to a `Step`, or reshapes `Run` for an unrelated internal reason, a
customer's already-deployed pipeline breaks with no warning and no
version bump to blame. Defining the public shape here, once, and hand-
mapping the internal model onto it (`_v1_run`, `_v1_finding`, ...) is
what makes an internal refactor a NON-event for `/v1/` -- the only way
this router's response ever changes is a deliberate edit to one of the
functions in this file. `tests/test_public_api.py`'s
`test_the_v1_response_shape_is_independent_of_the_internal_one` is what
keeps this true: it asserts on the exact `/v1/` key set, so drift here
fails the suite, not a customer's integration.

**OpenAPI is enriched here, at the route decorator, not bolted on
after.** Every route below carries `summary=`, `description=`, and
`responses=` with a worked example -- see `app/docs.py` for the
`GET /docs`/`GET /openapi.json` wiring these feed, and its module
docstring for why an empty-description page would be worse than none.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api_keys import require_api_role
from app.models import ApiKey
from app.run_routes import enqueue_run
from gateway.policy import decide

router = APIRouter(prefix="/v1", tags=["v1"])


# --- public response models -- independent of every internal *_json() ----

class V1Run(BaseModel):
    id: str = Field(..., description="Opaque run identifier.", examples=["run_ab12cd34ef56"])
    number: int = Field(..., description="This workspace's sequential run number.", examples=[4471])
    state: str = Field(..., description="queued | running | finished | failed | cancelled.", examples=["finished"])
    trigger: str = Field(..., description="What started this run.", examples=["manual"])
    commit: str = Field("", description="The commit SHA under test, if known.", examples=["a1b2c3d"])
    created_at: float = Field(..., description="Unix timestamp the run was claimed.", examples=[1735689600.0])
    duration_ms: int = Field(0, description="Wall-clock duration once finished.", examples=[184320])
    held: int = Field(0, description="Findings awaiting human approval.", examples=[1])
    failed: int = Field(0, description="Specs that failed this run.", examples=[2])
    repaired: int = Field(0, description="Specs Healer repaired in-flight.", examples=[1])


class V1Finding(BaseModel):
    id: str = Field(..., description="Opaque finding identifier.", examples=["fnd_9f8e7d6c"])
    title: str = Field(..., description="One-line description of what broke.", examples=["Checkout button unresponsive on mobile"])
    route: str = Field(..., description="The route this finding was found on.", examples=["/checkout"])
    severity: str = Field(..., description="low | medium | high | critical.", examples=["high"])
    status: str = Field(..., description="triaged | accepted | snoozed.", examples=["triaged"])
    found_by: str = Field(..., description="Which agent found this.", examples=["triager"])
    created_at: float = Field(..., description="Unix timestamp this finding was recorded.", examples=[1735689600.0])


class V1Route(BaseModel):
    path: str = Field(..., description="A mapped route on the surface.", examples=["/checkout"])
    coverage_pct: int = Field(..., description="Percent of this route's interactive surface under test.", examples=[72])


class V1Surface(BaseModel):
    routes: list[V1Route]
    total: int = Field(..., examples=[41])
    uncovered: int = Field(..., description="Routes with zero coverage.", examples=[3])


class V1LedgerStatus(BaseModel):
    intact: bool = Field(..., description="Whether the hash chain verifies end to end.", examples=[True])
    checked: int = Field(..., description="Entries checked.", examples=[318])


class CreateRunBody(BaseModel):
    trigger: str = Field("api", description="Free-text label for what started this run.", examples=["ci"])
    commit: str = Field("", description="The commit SHA under test.", examples=["a1b2c3d"])


def _v1_run(r) -> V1Run:
    return V1Run(
        id=r.id, number=r.number, state=r.state, trigger=r.trigger, commit=r.commit,
        created_at=r.started_at, duration_ms=r.duration_ms, held=r.held,
        failed=r.failed, repaired=r.repaired,
    )


def _v1_finding(f) -> V1Finding:
    return V1Finding(
        id=f.id, title=f.title, route=f.route, severity=f.severity,
        status=f.status, found_by=f.found_by, created_at=f.at,
    )


def _workspace_or_404(request: Request, key: ApiKey):
    workspace = request.app.state.repo.workspace(key.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    return workspace


def _fake_session(key: ApiKey):
    """`enqueue_run` (app/run_routes.py) takes a session-shaped object with
    `.workspace_id`/`.user_id`/`.is_demo` -- reused here rather than
    duplicating its 402-at-limit check and transactional run numbering
    (see this module's own docstring: shared BUSINESS LOGIC is fine and
    intended, only the response SHAPE must stay independent). A `pk_live_`
    key is never a demo credential, so `is_demo` is always False."""

    class _Sess:
        workspace_id = key.workspace_id
        user_id = f"apikey:{key.id}"
        is_demo = False

    return _Sess()


@router.post(
    "/runs", response_model=V1Run, status_code=202,
    summary="Start a run",
    description=(
        "Enqueues a new run against the connected repository and returns immediately -- "
        "the run executes asynchronously. Poll `GET /v1/runs/{id}` or subscribe to the "
        "`run.finished` webhook to learn the outcome. Requires an `owner` or `approver` key."
    ),
    responses={202: {"content": {"application/json": {"example": {
        "id": "run_ab12cd34ef56", "number": 4472, "state": "queued", "trigger": "ci",
        "commit": "a1b2c3d", "created_at": 1735689600.0, "duration_ms": 0,
        "held": 0, "failed": 0, "repaired": 0,
    }}}}},
)
def v1_create_run(body: CreateRunBody, request: Request, key: ApiKey = Depends(require_api_role("owner", "approver"))):
    sess = _fake_session(key)
    run = enqueue_run(request, sess, body.trigger, body.commit)
    return _v1_run(run)


@router.get(
    "/runs/{run_id}", response_model=V1Run,
    summary="Get a run",
    description="Fetches one run by id, scoped to the authenticating key's workspace.",
    responses={200: {"content": {"application/json": {"example": {
        "id": "run_ab12cd34ef56", "number": 4472, "state": "finished", "trigger": "ci",
        "commit": "a1b2c3d", "created_at": 1735689600.0, "duration_ms": 184320,
        "held": 1, "failed": 2, "repaired": 1,
    }}}}},
)
def v1_get_run(run_id: str, request: Request, key: ApiKey = Depends(require_api_role("owner", "approver", "reader"))):
    repo = request.app.state.repo
    run = repo.run(run_id)
    if run is None or run.workspace_id != key.workspace_id:
        raise HTTPException(404, "no such run")
    return _v1_run(run)


@router.get(
    "/findings", response_model=list[V1Finding],
    summary="List findings",
    description="Every finding in the authenticating key's workspace, newest first. Filter with `status`.",
    responses={200: {"content": {"application/json": {"example": [{
        "id": "fnd_9f8e7d6c", "title": "Checkout button unresponsive on mobile", "route": "/checkout",
        "severity": "high", "status": "triaged", "found_by": "triager", "created_at": 1735689600.0,
    }]}}}},
)
def v1_list_findings(request: Request, status: str | None = None, key: ApiKey = Depends(require_api_role("owner", "approver", "reader"))):
    rows = request.app.state.repo.findings_for_workspace(key.workspace_id)
    if status:
        rows = [f for f in rows if f.status == status]
    return [_v1_finding(f) for f in rows]


@router.get(
    "/surface", response_model=V1Surface,
    summary="Get the mapped surface",
    description="Every route Cartographer has mapped for this workspace, with per-route test coverage.",
    responses={200: {"content": {"application/json": {"example": {
        "routes": [{"path": "/checkout", "coverage_pct": 72}], "total": 41, "uncovered": 3,
    }}}}},
)
def v1_surface(request: Request, key: ApiKey = Depends(require_api_role("owner", "approver", "reader"))):
    routes = request.app.state.repo.routes_for_workspace(key.workspace_id)
    uncovered = sum(1 for r in routes if r.coverage_pct == 0)
    return V1Surface(
        routes=[V1Route(path=r.path, coverage_pct=r.coverage_pct) for r in routes],
        total=len(routes), uncovered=uncovered,
    )


@router.get(
    "/ledger/verify", response_model=V1LedgerStatus,
    summary="Verify the audit ledger",
    description="Recomputes the hash chain for this workspace's audit ledger end to end and reports whether it verifies intact.",
    responses={200: {"content": {"application/json": {"example": {"intact": True, "checked": 318}}}}},
)
def v1_verify_ledger(request: Request, key: ApiKey = Depends(require_api_role("owner", "approver", "reader"))):
    ledger = request.app.state.ledger
    intact = ledger.verify(key.workspace_id)
    checked = len(ledger.entries(key.workspace_id))
    return V1LedgerStatus(intact=intact, checked=checked)


# --- shared "may this key approve this patch" gate, reused by Task 14e's --
# `plumbline_approve_patch` MCP tool so the two entry points can never
# disagree about what a machine caller is and is not allowed to sign off
# on. See the module docstring on why this lives here rather than
# duplicated: it is the ONE place "is this patch human-gated" is answered
# for a non-browser caller, by delegating to the exact `decide()` every
# other gate check in this codebase already uses (never re-deriving the
# payments-path rule set itself).

class GateRefusal(Exception):
    """Raised instead of approving a gated patch for a machine caller.
    `gate` names the rule reason a human would need to act on -- see the
    module docstring and Task 14e's brief: "a structured refusal naming
    the gate, so the calling agent can tell its human what to do rather
    than retrying."""

    def __init__(self, gate: str):
        super().__init__(gate)
        self.gate = gate


def patch_gate_reason(workspace, patch) -> str | None:
    """The human-gate reason blocking `patch`, or `None` if it is not
    gated. Reuses `decide()` exactly like `app/finding_routes.py`'s
    `_patch_needs_owner_totp` does -- see that function's docstring for
    why re-deriving "is this a payments path" here, independently, would
    be the actual defect."""
    rules = list(workspace.gate_rules) if workspace and workspace.gate_rules else None
    targets = list(patch.files) or [patch.finding_id]
    for target in targets:
        decision = decide("surgeon", "pr.merge", target=target, rules=rules)
        if decision.needs_human:
            return decision.reason
    return None


def approve_patch_as_key(repo, ledger, key: ApiKey, finding_id: str) -> dict:
    """Shared by `POST /v1/findings/{id}/approve` (below) and Task 14e's
    `plumbline_approve_patch` MCP tool. A machine key may approve an
    UNGATED patch if its role is `owner`; it may NEVER approve a gated
    one -- there is no TOTP a `pk_live_` key can present, and the whole
    point of a human gate is that a human, not a caller holding a bearer
    token, signs for `payments/*`. Raises `GateRefusal(gate=...)` for
    that case rather than a bare permission error, so a calling agent can
    read the gate's own reason and relay it to its human instead of
    retrying (Task 14e's own contract, in these exact words)."""
    if key.role != "owner":
        raise HTTPException(403, f"approving a patch needs an 'owner' key, this key is {key.role!r}")

    finding = next((f for f in repo.findings_for_workspace(key.workspace_id) if f.id == finding_id), None)
    if finding is None:
        raise HTTPException(404, "no such finding")
    patch = repo.patch_for_finding(finding.id)
    if patch is None:
        raise HTTPException(404, "this finding has no patch")

    workspace = repo.workspace(key.workspace_id)
    gate = patch_gate_reason(workspace, patch)
    if gate is not None:
        raise GateRefusal(gate)

    if patch.gate_state == "merged":
        return {"already_approved": True, "ok": True, "pr_url": patch.pr_url}

    repo.put_patch(type(patch)(**{**patch.__dict__, "gate_state": "merged"}))
    ledger.append(
        key.workspace_id, f"apikey:{key.id}", "patch.approve",
        {"finding_id": finding.id, "patch_id": patch.id, "pr_url": patch.pr_url},
    )
    return {"already_approved": False, "ok": True, "pr_url": patch.pr_url}


@router.post(
    "/findings/{finding_id}/approve",
    summary="Approve a finding's patch",
    description=(
        "Approves the patch attached to a finding, so it may merge. Requires an `owner` key. "
        "Refused with a structured `gate` reason for any patch a human must sign for -- "
        "there is no machine path around that gate."
    ),
    responses={200: {"content": {"application/json": {"example": {
        "already_approved": False, "ok": True, "pr_url": "https://github.com/acme/storefront/pull/842",
    }}}}},
)
def v1_approve_patch(finding_id: str, request: Request, key: ApiKey = Depends(require_api_role("owner"))):
    repo, ledger = request.app.state.repo, request.app.state.ledger
    try:
        return approve_patch_as_key(repo, ledger, key, finding_id)
    except GateRefusal as exc:
        raise HTTPException(403, {
            "error": "this patch is human-gated and cannot be approved by an API key",
            "gate": exc.gate,
        }) from exc
