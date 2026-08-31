"""`GET /api/summary` -- the sidebar's counts.

Exists because the sidebar hardcoded them. `count: 18` on Runs, `count: 7`
on Findings and a `"7"` badge on Agents were literals carried over from the
design prototype, so every visitor saw the prototype's numbers rather than
their own workspace's -- a brand-new sandbox with nothing in it still
claimed 18 runs. The agent count was wrong even as a constant: there are
eleven agents, not seven.
"""

from app.models import Behaviour, Finding, Run
from gateway.policy import SCOPES


def test_counts_reflect_this_workspace_not_a_constant(client_as_owner, repo):
    before = client_as_owner.get("/api/summary").json()
    assert before["runs"] == 0 and before["findings"] == 0

    repo.put_run(Run(id="r1", workspace_id="ws1", number=1, trigger="t", state="finished"))
    repo.put_run(Run(id="r2", workspace_id="ws1", number=2, trigger="t", state="finished"))
    repo.put_finding(Finding(
        id="f1", workspace_id="ws1", title="Something broke", route="/x", found_by="chaos",
    ))
    repo.put_behaviour(Behaviour(
        id="b1", workspace_id="ws1", text="A thing must hold", route="/x",
    ))

    after = client_as_owner.get("/api/summary").json()
    assert after["runs"] == 2
    assert after["findings"] == 1
    assert after["behaviours"] == 1


def test_the_agent_count_is_the_real_fleet_size(client_as_owner):
    """Read from gateway.policy.SCOPES, the same source the Agents screen
    counts from, so the sidebar and that screen cannot disagree."""
    body = client_as_owner.get("/api/summary").json()
    assert body["agents"] == len(SCOPES)
    assert body["agents"] == 11, "the fleet is eleven agents; the sidebar said 7"


def test_accepted_findings_do_not_count_as_needing_attention(client_as_owner, repo):
    """The Findings badge is a to-do count. A finding somebody has already
    accepted is not outstanding work, so it must not keep the number up."""
    repo.put_finding(Finding(
        id="open", workspace_id="ws1", title="Still open", route="/a",
        found_by="chaos", status="triaged",
    ))
    repo.put_finding(Finding(
        id="done", workspace_id="ws1", title="Already accepted", route="/b",
        found_by="chaos", status="accepted",
    ))
    assert client_as_owner.get("/api/summary").json()["findings"] == 1


def test_one_workspace_never_sees_another_workspace_count(client_as_owner, repo):
    repo.put_run(Run(id="mine", workspace_id="ws1", number=1, trigger="t", state="finished"))
    repo.put_run(Run(id="theirs", workspace_id="ws-other", number=1, trigger="t", state="finished"))
    assert client_as_owner.get("/api/summary").json()["runs"] == 1


def test_an_anonymous_caller_gets_401(client):
    assert client.get("/api/summary").status_code == 401
