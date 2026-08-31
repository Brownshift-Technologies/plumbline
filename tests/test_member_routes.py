"""Tests for `app/member_routes.py` -- see that module's docstring for the
full narrative of why this task exists (Task 14c's own carried ruling on
session revocation, missing from its interface list) and for the design
choices these tests hold it to: revoke-on-removal, the last-owner floor
counted by owners not members, the IDOR fix reused from `DELETE
/api/auth/sessions/{sid}`, and the demo no-op contract every other write
route in this codebase already follows.
"""

import uuid

from starlette.testclient import TestClient

from app.models import Membership, User
from app.security import hash_password


def _second_member(repo, sessions, *, role="approver", workspace_id="ws1"):
    """A second real user + membership + live session in `workspace_id`,
    built directly against `repo` the same way `tests/conftest.py`'s own
    `_member` fixture does -- but without swapping the shared `client`'s
    cookie, since these tests need the OWNER's client to act while
    independently holding this second person's session id."""
    user = User(
        id=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{role}-{uuid.uuid4().hex[:6]}@acme.com",
        password_hash=hash_password("correct horse battery"),
        name=role.title(),
    )
    repo.put_user(user)
    membership = Membership(
        id=f"m_{uuid.uuid4().hex[:8]}", user_id=user.id, workspace_id=workspace_id, role=role,
    )
    repo.put_membership(membership)
    sess = sessions.issue(user.id, workspace_id)
    return user, membership, sess


# --- removal revokes sessions -------------------------------------------


def test_removing_a_member_revokes_every_session_they_hold(client_as_owner, repo, sessions):
    user, membership, sess1 = _second_member(repo, sessions)
    sess2 = sessions.issue(user.id, "ws1")
    assert len(sessions.list_for_user(user.id)) == 2

    r = client_as_owner.delete(f"/api/members/{membership.id}")
    assert r.status_code == 200 and r.json() == {"ok": True}

    assert sessions.list_for_user(user.id) == []
    assert sessions.resolve(sess1.id) is None
    assert sessions.resolve(sess2.id) is None


def test_a_removed_member_cannot_use_a_cookie_issued_before_removal(client_as_owner, repo, sessions, app):
    # A distinct `TestClient` -- `client_as_owner` and the shared `client`
    # fixture it is built from are the SAME underlying object (see
    # `tests/conftest.py`'s `_member`), so reusing `client` here would
    # silently clobber the owner's own cookie instead of modelling a
    # second, independent browser.
    user, membership, sess = _second_member(repo, sessions)
    with TestClient(app, base_url="https://testserver") as other:
        other.cookies.set("pl_session", sess.id)
        assert other.get("/api/members").status_code == 200

        r = client_as_owner.delete(f"/api/members/{membership.id}")
        assert r.status_code == 200

        r2 = other.get("/api/members")
        assert r2.status_code == 401


# --- the last-owner floor -------------------------------------------------


def test_the_last_owner_cannot_remove_themselves(client_as_owner, repo):
    members = client_as_owner.get("/api/members").json()
    [self_membership] = members
    r = client_as_owner.delete(f"/api/members/{self_membership['id']}")
    assert r.status_code == 409
    assert repo.role_of(self_membership["user_id"], "ws1") == "owner"


def test_the_last_owner_cannot_demote_themselves(client_as_owner, repo):
    members = client_as_owner.get("/api/members").json()
    [self_membership] = members
    r = client_as_owner.patch(f"/api/members/{self_membership['id']}", json={"role": "approver"})
    assert r.status_code == 409
    assert repo.role_of(self_membership["user_id"], "ws1") == "owner"


def test_an_owner_can_demote_another_owner_when_two_exist(client_as_owner, repo, sessions):
    _, second_owner_membership, _ = _second_member(repo, sessions, role="owner")

    r = client_as_owner.patch(f"/api/members/{second_owner_membership.id}", json={"role": "approver"})
    assert r.status_code == 200
    assert r.json()["role"] == "approver"
    assert repo.role_of(second_owner_membership.user_id, "ws1") == "approver"
    # The acting owner is untouched, and the workspace still has exactly
    # one owner -- the floor was never crossed, just approached.
    assert sum(1 for m in repo.members_of("ws1") if m.role == "owner") == 1


# --- role scoping ----------------------------------------------------------


def test_an_approver_can_read_the_member_list_but_not_change_it(client_as_approver, repo, sessions):
    _, membership, _ = _second_member(repo, sessions, role="reader")

    r_get = client_as_approver.get("/api/members")
    assert r_get.status_code == 200
    assert len(r_get.json()) == 2

    assert client_as_approver.patch(f"/api/members/{membership.id}", json={"role": "approver"}).status_code == 403
    assert client_as_approver.delete(f"/api/members/{membership.id}").status_code == 403
    assert client_as_approver.post("/api/members/invite", json={"email": "new@acme.com", "role": "reader"}).status_code == 403


def test_a_reader_cannot_invite(client_as_reader):
    r = client_as_reader.post("/api/members/invite", json={"email": "new@acme.com", "role": "reader"})
    assert r.status_code == 403


# --- IDOR ------------------------------------------------------------------


def test_a_membership_id_from_another_workspace_is_404_not_403(client_as_owner, repo):
    other_user = User(
        id="u_other", email="other@other.com", password_hash=hash_password("correct horse battery"),
        name="Other",
    )
    repo.put_user(other_user)
    other_membership = Membership(id="m_other", user_id=other_user.id, workspace_id="ws-other", role="owner")
    repo.put_membership(other_membership)

    r_patch = client_as_owner.patch(f"/api/members/{other_membership.id}", json={"role": "reader"})
    assert r_patch.status_code == 404

    r_delete = client_as_owner.delete(f"/api/members/{other_membership.id}")
    assert r_delete.status_code == 404

    # Untouched -- a 404 here must not be a side effect that quietly
    # mutated a row this caller was never authorised to reach.
    assert repo.role_of(other_user.id, "ws-other") == "owner"


# --- input validation -------------------------------------------------------


def test_an_invalid_role_is_rejected(client_as_owner, repo, sessions):
    _, membership, _ = _second_member(repo, sessions, role="reader")

    r_patch = client_as_owner.patch(f"/api/members/{membership.id}", json={"role": "superadmin"})
    assert r_patch.status_code == 400

    r_invite = client_as_owner.post("/api/members/invite", json={"email": "new@acme.com", "role": "superadmin"})
    assert r_invite.status_code == 400


def test_inviting_an_existing_member_is_409(client_as_owner, repo):
    members = client_as_owner.get("/api/members").json()
    existing_email = members[0]["email"]

    r = client_as_owner.post("/api/members/invite", json={"email": existing_email.upper(), "role": "reader"})
    assert r.status_code == 409


# --- ledger ------------------------------------------------------------------


def test_removal_is_written_to_the_ledger_with_the_actor(client_as_owner, repo, sessions, ledger):
    user, membership, _ = _second_member(repo, sessions)
    actor_id = next(
        m["user_id"] for m in client_as_owner.get("/api/members").json() if m["role"] == "owner"
    )

    r = client_as_owner.delete(f"/api/members/{membership.id}")
    assert r.status_code == 200

    entries = [e for e in ledger.entries("ws1") if e["action"] == "member.removed"]
    assert len(entries) == 1
    assert entries[0]["actor"] == actor_id
    assert entries[0]["detail"]["membership_id"] == membership.id
    assert entries[0]["detail"]["user_id"] == user.id


def test_a_demo_session_gets_the_demo_response_not_a_403(client_demo):
    invite = client_demo.post("/api/members/invite", json={"email": "new@acme.com", "role": "reader"})
    assert invite.status_code == 200
    assert invite.json() == {"demo": True, "persisted": False}

    patch = client_demo.patch("/api/members/m_whatever", json={"role": "reader"})
    assert patch.status_code == 200
    assert patch.json() == {"demo": True, "persisted": False}

    delete = client_demo.delete("/api/members/m_whatever")
    assert delete.status_code == 200
    assert delete.json() == {"demo": True, "persisted": False}


# --- happy paths, for completeness ------------------------------------------


def test_invite_creates_a_membership_for_a_brand_new_email(client_as_owner, repo):
    r = client_as_owner.post("/api/members/invite", json={"email": "brandnew@acme.com", "role": "approver"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "brandnew@acme.com"
    assert body["role"] == "approver"
    assert repo.role_of(body["user_id"], "ws1") == "approver"


def test_invite_attaches_an_existing_user_to_a_new_workspace(client_as_owner, repo):
    other_user = User(
        id="u_elsewhere", email="elsewhere@acme.com",
        password_hash=hash_password("correct horse battery"), name="Elsewhere",
    )
    repo.put_user(other_user)

    r = client_as_owner.post("/api/members/invite", json={"email": "elsewhere@acme.com", "role": "reader"})
    assert r.status_code == 200
    assert r.json()["user_id"] == other_user.id
