"""Task 14c: `GET /api/surface`, `POST /api/surface/remap`."""

import pytest

from app.models import Route


def _make_route(repo, path, pct, workspace_id="ws1"):
    route = Route(id=f"route_{path.strip('/').replace('/', '_') or 'root'}",
                   workspace_id=workspace_id, path=path, coverage_pct=pct)
    repo.put_route(route)
    return route


@pytest.fixture(autouse=True)
def stub_enqueue(app):
    calls = []
    app.state.enqueue_job = lambda job_name, args: calls.append((job_name, args)) or "op/fake"
    return calls


def test_the_surface_lists_routes_sorted_by_coverage(client_as_owner, repo):
    _make_route(repo, "/checkout", 72)
    _make_route(repo, "/cart", 30)
    _make_route(repo, "/", 100)
    r = client_as_owner.get("/api/surface")
    body = r.json()
    pcts = [row["coverage_pct"] for row in body["routes"]]
    assert pcts == sorted(pcts)
    assert body["total"] == 3


def test_the_surface_reports_uncovered_routes(client_as_owner, repo):
    _make_route(repo, "/checkout/3ds", 0)
    _make_route(repo, "/", 100)
    r = client_as_owner.get("/api/surface")
    assert r.json()["uncovered"] == 1


def test_remapping_is_owner_only(client_as_reader):
    r = client_as_reader.post("/api/surface/remap")
    assert r.status_code == 403


def test_remapping_enqueues_a_run(client_as_owner, stub_enqueue):
    r = client_as_owner.post("/api/surface/remap")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "queued"
    assert stub_enqueue == [("plumbline-worker", {"PLUMBLINE_RUN_ID": body["run_id"]})]


def test_a_demo_session_remap_is_a_no_op(client_demo, stub_enqueue):
    r = client_demo.post("/api/surface/remap")
    assert r.json() == {"demo": True, "persisted": False}
    assert stub_enqueue == []
