"""Task 14d: the versioned `/v1/...` public surface -- its own response
models, independent of the internal dashboard API's."""

from app.api_keys import create_api_key
from app.models import Finding, Route, Run, Workspace


def _ws(repo):
    ws = Workspace(id="ws1", name="Acme", repo="acme/storefront")
    repo.put_workspace(ws)
    return ws


def _owner_key(client):
    repo = client.app.state.repo
    if repo.workspace("ws1") is None:
        _ws(repo)
    _, raw = create_api_key(repo.store, "ws1", "k", "owner", "u1")
    return {"Authorization": f"Bearer {raw}"}


def test_v1_create_and_get_run(client):
    # Stub the real Cloud Run Job dispatch -- see tests/test_run_routes.py's
    # own module docstring for why the real default needs live GCP
    # credentials this suite has none of.
    client.app.state.enqueue_job = lambda job_name, args: "op/fake"
    headers = _owner_key(client)
    created = client.post("/v1/runs", json={"trigger": "ci", "commit": "abc123"}, headers=headers)
    assert created.status_code == 202
    body = created.json()
    assert body["state"] == "queued"
    assert body["trigger"] == "ci"

    fetched = client.get(f"/v1/runs/{body['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_v1_findings_and_surface_and_ledger_verify(client):
    headers = _owner_key(client)
    repo = client.app.state.repo
    repo.put_finding(Finding(id="f1", workspace_id="ws1", title="broke", route="/checkout", found_by="triager"))
    repo.put_route(Route(id="r1", workspace_id="ws1", path="/checkout", coverage_pct=50))

    findings = client.get("/v1/findings", headers=headers)
    assert findings.status_code == 200
    assert findings.json()[0]["id"] == "f1"

    surface = client.get("/v1/surface", headers=headers)
    assert surface.status_code == 200
    assert surface.json()["total"] == 1

    verify = client.get("/v1/ledger/verify", headers=headers)
    assert verify.status_code == 200
    assert verify.json()["intact"] is True


def test_the_v1_response_shape_is_independent_of_the_internal_one(client_as_owner):
    """`app/run_routes.py`'s internal `_run_json` and `app/public_routes.py`'s
    `V1Run` must not be the same shape -- this is what makes an internal
    refactor a non-event for a customer's already-deployed pipeline. If
    someone later makes `/v1/runs/{id}` literally call `run_routes.get_run`
    and re-export its dict, this test is what catches it."""
    repo = client_as_owner.app.state.repo
    run = Run(id="run_x", workspace_id="ws1", number=1, trigger="manual", state="finished")
    repo.put_run(run)

    internal = client_as_owner.get(f"/api/runs/{run.id}")
    assert internal.status_code == 200
    internal_run_keys = set(internal.json()["run"].keys())

    _, raw = create_api_key(repo.store, "ws1", "k", "owner", "u1")
    public = client_as_owner.get(f"/v1/runs/{run.id}", headers={"Authorization": f"Bearer {raw}"})
    assert public.status_code == 200
    public_keys = set(public.json().keys())

    # The public shape is its own, hand-defined model -- not the internal
    # one re-exported. Provably different key sets: the internal shape
    # exposes workspace_id/started_by (never needed by a customer, whose
    # auth already scopes them to one workspace); the public shape does
    # not, and renames started_at -> created_at.
    assert internal_run_keys != public_keys
    assert "workspace_id" not in public_keys
    assert "started_by" not in public_keys
    assert "created_at" in public_keys and "started_at" not in public_keys
