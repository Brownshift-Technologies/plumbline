"""Task 14c: `GET/POST /api/behaviours`, `PATCH/DELETE /api/behaviours/{id}`."""

from app.models import Behaviour


def _make_behaviour(repo, bid, workspace_id="ws1", tags=(), owner="", route="/checkout"):
    b = Behaviour(id=bid, workspace_id=workspace_id, text="Something that must hold",
                  route=route, tags=tags, owner=owner)
    repo.put_behaviour(b)
    return b


def test_behaviours_filter_by_tag(client_as_owner, repo):
    _make_behaviour(repo, "b1", tags=("payments",))
    _make_behaviour(repo, "b2", tags=("security",))
    r = client_as_owner.get("/api/behaviours", params={"tag": "payments"})
    body = r.json()
    assert body["total"] == 1
    assert body["behaviours"][0]["id"] == "b1"


def test_behaviours_filter_by_owner_and_route(client_as_owner, repo):
    _make_behaviour(repo, "b1", owner="roger", route="/cart")
    _make_behaviour(repo, "b2", owner="ama", route="/checkout")
    r = client_as_owner.get("/api/behaviours", params={"owner": "roger", "route": "/cart"})
    assert r.json()["total"] == 1


def test_deleted_behaviours_are_hidden_by_default(client_as_owner, repo):
    b = _make_behaviour(repo, "b1")
    repo.put_behaviour(type(b)(**{**b.__dict__, "status": "deleted"}))
    r = client_as_owner.get("/api/behaviours")
    assert r.json()["total"] == 0
    r2 = client_as_owner.get("/api/behaviours", params={"status": "deleted"})
    assert r2.json()["total"] == 1


def test_creating_a_behaviour_requires_text_and_a_route(client_as_owner):
    r = client_as_owner.post("/api/behaviours", json={"text": "", "route": "/checkout"})
    assert r.status_code == 400
    r2 = client_as_owner.post("/api/behaviours", json={"text": "Something must hold", "route": ""})
    assert r2.status_code == 400


def test_creating_a_behaviour(client_as_owner, repo):
    r = client_as_owner.post(
        "/api/behaviours", json={"text": "Checkout total updates live", "route": "/checkout"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "Checkout total updates live"
    assert repo.behaviours_for_workspace("ws1")


def test_a_reader_cannot_create_a_behaviour(client_as_reader):
    r = client_as_reader.post("/api/behaviours", json={"text": "x", "route": "/y"})
    assert r.status_code == 403


def test_a_demo_session_create_is_a_no_op(client_demo):
    r = client_demo.post("/api/behaviours", json={"text": "x", "route": "/y"})
    assert r.json() == {"demo": True, "persisted": False}


def test_updating_a_behaviour(client_as_owner, repo):
    _make_behaviour(repo, "b1")
    r = client_as_owner.patch("/api/behaviours/b1", json={"status": "paused"})
    assert r.status_code == 200
    assert repo.behaviours_for_workspace("ws1")[0].status == "paused"


def test_updating_a_missing_behaviour_is_404(client_as_owner):
    r = client_as_owner.patch("/api/behaviours/nope", json={"status": "paused"})
    assert r.status_code == 404


def test_deleting_a_behaviour_is_owner_only(client_as_approver, repo):
    _make_behaviour(repo, "b1")
    r = client_as_approver.delete("/api/behaviours/b1")
    assert r.status_code == 403


def test_owner_deleting_a_behaviour_soft_deletes_it(client_as_owner, repo):
    _make_behaviour(repo, "b1")
    r = client_as_owner.delete("/api/behaviours/b1")
    assert r.status_code == 200
    row = repo.behaviours_for_workspace("ws1")[0]
    assert row.status == "deleted"
    assert row.id == "b1"  # tombstoned, not gone -- Store has no delete
