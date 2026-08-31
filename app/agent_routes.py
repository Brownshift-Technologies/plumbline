"""Task 14c: agent and policy routes.

**Files, not the brief's own list.** Task 14c's brief names five files to
create (`surface_routes.py`, `behaviour_routes.py`, `agent_routes.py`,
`ledger_routes.py`, `billing_routes.py`) but its interface list names SIX
route groups -- the fifth being `GET /api/policy/decisions` and
`GET`/`PUT /api/policy/rules`, with no file of its own. Policy governs
what the fleet may do and this module already reports what the fleet IS
doing (`GET /api/agents`) and controls whether it runs at all (pause/
resume) -- putting policy here, rather than inventing a sixth file (or a
second test file the brief's own four-file test list does not name
either), keeps "everything about the fleet's live behaviour" in one
place. `tests/test_agent_routes.py` (also not named in the brief's test
list, for the same reason) is where both halves are tested.

**"Live queue depth", honestly.** Nothing in this codebase models a
per-agent work queue -- each run drives its eleven agents sequentially,
in-process, inside `job/orchestrator.py`, and there is no separate
"pending work for agent X" collection to read a real depth out of. Rather
than invent a queue that does not exist, `_agent_status` below reports
what IS real and IS read live from Firestore every call: how many of the
workspace's runs are still `queued` (work no agent has started yet -- the
same number for every agent, since none of them can have started on a
queued run) as `queue_depth`, and which single agent (if any) wrote the
LATEST step of the workspace's current `running` run, marked `"working"`
while every other agent reads `"idle"`. This is a real, live, honestly-
described simplification, not a hard-coded fixture -- flagged here and in
the task report for whoever adds real per-agent queueing later.

**Rule validation at write time, not `decide()`'s job.** `decide()`
(`gateway/policy.py`) is a synchronous hot-path function that skips a
malformed rule as tenant data it cannot interpret -- correct for
`decide()`, and exactly why a typo'd rule would otherwise vanish into a
silent no-op with zero feedback (this task's own carried ruling). This
module owns catching that typo AT THE WRITE, with a field-specific 400,
by running the identical well-formedness check `decide()` itself uses
(`gateway.policy._is_well_formed`, imported rather than re-implemented --
the same "reach into the authoritative private helper" move
`gateway/ledger.py` already makes for `core.store._redact`) plus one
check `decide()` deliberately has no opinion on at all: a rule's `tool`
must be a REAL tool that at least one agent in `gateway.policy.SCOPES`
actually holds. A rule can never widen scope (`decide()` checks scope
first, always -- see that module's docstring), so a rule naming a tool no
agent has is not dangerous, only silently meaningless; rejecting it here
is what turns "meaningless" into "the tenant finds out immediately"
rather than "vanishes".
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.deps import current_session, require_write_role
from gateway.policy import SCOPES, _VALID_EFFECTS, _is_well_formed

router = APIRouter()

_KNOWN_TOOLS = frozenset(tool for tools in SCOPES.values() for tool in tools)


# --- agents -------------------------------------------------------------


def _agent_status(repo, workspace_id: str) -> list[dict]:
    runs = repo.runs_for_workspace(workspace_id)
    queue_depth = sum(1 for r in runs if r.state == "queued")
    running = next((r for r in runs if r.state == "running"), None)
    working_agent = None
    if running is not None:
        steps = repo.steps_for_run(running.id)
        if steps:
            working_agent = steps[-1].agent

    return [
        {
            "agent": agent,
            "tools": sorted(tools),
            "queue_depth": queue_depth,
            "state": "working" if agent == working_agent else "idle",
        }
        for agent, tools in sorted(SCOPES.items())
    ]


@router.get("/api/agents")
def list_agents(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    return {"agents": _agent_status(repo, sess.workspace_id), "paused": _is_paused(repo, sess.workspace_id)}


def _is_paused(repo, workspace_id: str) -> bool:
    workspace = repo.workspace(workspace_id)
    return bool(workspace and workspace.fleet_paused)


def _set_paused(request: Request, sess, paused: bool) -> dict:
    # A demo session pauses/resumes its OWN sandbox workspace's fleet --
    # a real write, same as a real session's -- see this task's report on
    # why every demo session now gets a per-session workspace it can
    # actually write to.
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    repo.put_workspace(type(workspace)(**{**workspace.__dict__, "fleet_paused": paused}))
    return {"paused": paused}


@router.post("/api/agents/pause")
def pause_agents(request: Request, sess=Depends(current_session), _role=Depends(require_write_role("owner"))):
    return _set_paused(request, sess, True)


@router.post("/api/agents/resume")
def resume_agents(request: Request, sess=Depends(current_session), _role=Depends(require_write_role("owner"))):
    return _set_paused(request, sess, False)


# --- policy ---------------------------------------------------------------


class RulesBody(BaseModel):
    rules: list[dict]


def _validate_rules(rules: list[dict]) -> None:
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise HTTPException(400, f"rules[{i}] must be an object")
        tool = rule.get("tool")
        if not isinstance(tool, str) or not tool:
            raise HTTPException(400, f"rules[{i}].tool must be a non-empty string")
        if rule.get("effect") not in _VALID_EFFECTS:
            raise HTTPException(400, f"rules[{i}].effect must be one of {sorted(_VALID_EFFECTS)}")
        if not _is_well_formed(rule):
            raise HTTPException(
                400,
                f"rules[{i}] must set exactly one of pattern (a string) or "
                "allow_only (a non-empty list of strings)",
            )
        if tool not in _KNOWN_TOOLS:
            # A rule can only ever narrow what SCOPES already grants, never
            # widen it (gateway/policy.py's own docstring) -- but a tool no
            # agent has in scope at all is not a narrowing rule, it is a
            # typo that would otherwise vanish into a silent no-op. See
            # the module docstring's carried ruling.
            raise HTTPException(
                400, f"rules[{i}].tool {tool!r} is not in scope for any agent -- cannot gate a tool that does not exist"
            )


@router.get("/api/policy/decisions")
def list_policy_decisions(request: Request, sess=Depends(current_session), limit: int = 50):
    ledger = request.app.state.ledger
    entries = [e for e in ledger.entries(sess.workspace_id) if "decision" in e.get("detail", {})]
    entries.sort(key=lambda e: e["seq"], reverse=True)
    return {"decisions": entries[: max(1, min(limit, 200))]}


@router.get("/api/policy/rules")
def get_policy_rules(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")
    from gateway.policy import DEFAULT_RULES

    return {
        "rules": list(workspace.gate_rules) if workspace.gate_rules else DEFAULT_RULES,
        "policy_version": workspace.policy_version,
    }


@router.put("/api/policy/rules")
def put_policy_rules(
    body: RulesBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    # Editing gate rules is a real write in a demo session's own sandbox
    # too -- see `_set_paused` above.
    _validate_rules(body.rules)

    repo, ledger = request.app.state.repo, request.app.state.ledger
    workspace = repo.workspace(sess.workspace_id)
    if workspace is None:
        raise HTTPException(404, "no such workspace")

    new_version = workspace.policy_version + 1
    updated = type(workspace)(**{
        **workspace.__dict__, "gate_rules": tuple(body.rules), "policy_version": new_version,
    })
    repo.put_workspace(updated)
    ledger.append(
        sess.workspace_id, sess.user_id, "policy.rules_updated",
        {"rules": body.rules, "policy_version": new_version},
    )
    return {"rules": body.rules, "policy_version": new_version}
