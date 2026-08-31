"""Member management routes -- the last Critical open in the build.

**Whose defect this is.** Task 14c's brief carried an early ruling that
removing a member must revoke every session that member holds, but its
own interface list never named a single member-management route. The
14c implementer read the brief, found no member routes to build, and
correctly declined to invent `GET /api/members`/`POST /api/members/
invite`/`PATCH`/`DELETE /api/members/{id}` out of scope -- that is the
right call for an implementer to make, not a gap in their work. The gap
is here: the frontend (`web/src/pages/settings/MembersPane.tsx`) already
ships a working Remove/Invite/role-change UI pointed at these paths, and
until this module exists every one of those controls hits a 404. This
file is that missing task, built to the same "removal revokes every
session" ruling 14c was always supposed to carry.

**Removal revokes every session, in the same operation.** A `Membership`
row is not the access boundary in this codebase -- a `Session` is (see
`app/deps.py`'s `current_session`). A member removed from a workspace
while their session cookie is still live would keep every read and write
that cookie's role allowed until it happened to expire on its own, which
is exactly backwards for what "removed" is supposed to mean. `remove_
member` below tombstones the `Membership` AND calls `SessionService.
revoke_all(user_id)` before it returns -- not queued, not eventual, the
same request. `revoke_all` (not `revoke_all_except`) is the right
primitive here: there is no session to spare the way a password change
spares the session that just proved the old password (`app/account_
routes.py`), because the whole point of this action is that the person
holding every one of this user's sessions no longer belongs in this
workspace. This is also the FIRST user-removal primitive this codebase
has -- no route before this one has ever revoked another person's
sessions as a side effect of anything -- so it is written to be complete
rather than a partial version something else has to finish later.

**Membership rows tombstone, they do not delete.** `core/store.py` never
exposes delete (see that module's own docstring); `Repo.delete_session`
already establishes the pattern this file reuses verbatim: overwrite the
row with its identifying foreign keys blanked (`user_id=""`,
`workspace_id=""`) rather than removing the document. `members_of`
queries on `workspace_id == wid`, so a tombstoned membership silently
stops matching that query forever -- no new `Repo` method was needed to
make "removed" real, and a second `DELETE` on an already-removed
membership id correctly 404s the same way a second `DELETE
/api/auth/sessions/{sid}` does once a session is revoked, rather than
needing its own "already removed" branch.

**The last owner is a hard floor, checked by counting owners, not
members.** A workspace that reaches zero owners can never change its own
gate rules, invite anyone, or fix this again -- it is permanently frozen,
and nothing else in this product can recover it (there is no superadmin
override anywhere in this codebase). `_owner_count` counts CURRENT owner
rows in the target workspace, live, on every `PATCH`/`DELETE` that could
reduce that count -- never a cached number, never "assume there are
enough". Because a single owner is definitionally the only one who could
ever be the target of "demote/remove the last owner" (nobody else holds
the `owner` role to attempt it on), this one check covers both the
self-demotion and self-removal case and the "someone else tries to
demote the sole owner" case identically, with no separate `membership.
user_id == sess.user_id` branch needed. Two owners demoting/removing each
other stays allowed all the way down to one, which is the intended floor,
not zero.

**The IDOR this file deliberately does not repeat.** `DELETE /api/auth/
sessions/{sid}` was previously found to accept any session id at all and
only checked ownership as an afterthought -- fixed by scoping the lookup
to `sessions.list_for_user(sess.user_id)` before ever touching it (see
that route in `app/auth_routes.py`). `_get_membership_or_404` below makes
the identical mistake structurally impossible: it looks a membership id
up ONLY inside `repo.members_of(sess.workspace_id)` -- the caller's own
workspace -- so a membership id that is perfectly real in a different
workspace is indistinguishable from one that does not exist anywhere,
and returns 404, never 403. A 403 would confirm the id is real just
scoped elsewhere; that is the exact enumeration oracle this route must
not become.

**Inviting an email already in this workspace is 409, not a duplicate
row.** Checked by resolving each active membership's user and comparing
emails case-insensitively (matching `Repo.put_user`'s own lower-casing
choke point) -- not a second index, since `members_of` is already the
full membership list for one workspace and this codebase's workspaces
are not expected to carry thousands of members.

**Inviting an email with no existing account.** This codebase has no
invite-email delivery (`app/account_routes.py`'s module docstring
already establishes "log it, don't fake it" for the identical gap around
password-reset delivery) and no separate "pending invite" concept --
`User`/`Membership` are the only two shapes this task has to work with.
So an invite to a brand-new email creates the `User` row immediately,
with a real argon2 hash of an unguessable, freshly generated, never-
persisted-in-plaintext token as its password (matching `app/auth_routes.
py`'s `_DUMMY_HASH` discipline of "a real hash of a secret nobody holds"
rather than an empty string a future bug could mistake for "no password
required") -- the invited person sets their own usable password later
through the exact same password-reset flow any other account uses, never
through this route. `Repo.claim_email`'s transactional claim (the same
one `signup` uses) closes the race between two invites, or an invite
racing a signup, for the same email; losing that race means someone
else's account already exists under it by the time this request
committed, so this re-reads by email and attaches the new membership to
THAT account rather than creating a second one.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.deps import current_session, require_write_role
from app.models import Membership, User
from app.security import hash_password

router = APIRouter(prefix="/api/members")

_VALID_ROLES = ("owner", "approver", "reader")


class InviteBody(BaseModel):
    email: EmailStr
    role: str


class RoleBody(BaseModel):
    role: str


def _member_json(repo, m: Membership) -> dict:
    user = repo.user(m.user_id)
    return {
        "id": m.id,
        "user_id": m.user_id,
        "name": user.name if user else m.user_id,
        "email": user.email if user else "",
        "role": m.role,
    }


def _active_members(repo, workspace_id: str) -> list[Membership]:
    # A removed member's row is a tombstone with workspace_id="" (see the
    # module docstring), which `members_of`'s own `workspace_id == wid`
    # query already excludes -- nothing here filters twice.
    return repo.members_of(workspace_id)


def _owner_count(repo, workspace_id: str) -> int:
    return sum(1 for m in _active_members(repo, workspace_id) if m.role == "owner")


def _get_membership_or_404(repo, workspace_id: str, membership_id: str) -> Membership:
    m = next((m for m in _active_members(repo, workspace_id) if m.id == membership_id), None)
    if m is None:
        raise HTTPException(404, "no such member")
    return m


@router.get("")
def list_members(request: Request, sess=Depends(current_session)):
    # Read-only, open to every role including a demo session (whose own
    # `role_of` would be `None` -- see `app/deps.py`'s `current_user`
    # docstring for why a demo session holds no real membership at all):
    # an approver or reader can see who is on the team even though only
    # an owner can change it (contract point 3).
    repo = request.app.state.repo
    rows = sorted(_active_members(repo, sess.workspace_id), key=lambda m: m.id)
    return [_member_json(repo, m) for m in rows]


@router.post("/invite")
def invite_member(
    body: InviteBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    # A real write in a demo session's own sandbox too -- creates a
    # Membership (and, for a brand-new email, a User) row scoped to
    # `sess.workspace_id`. It never sends anything: this codebase has no
    # invite-email delivery for ANY session, demo or not (see the module
    # docstring's "Inviting an email with no existing account" section),
    # so there is nothing here that reaches outside the sandbox to refuse.
    if body.role not in _VALID_ROLES:
        raise HTTPException(400, f"role must be one of {_VALID_ROLES}")

    repo = request.app.state.repo
    email = body.email.lower()

    for m in _active_members(repo, sess.workspace_id):
        existing_user = repo.user(m.user_id)
        if existing_user and existing_user.email == email:
            raise HTTPException(409, "that email already has a membership in this workspace")

    user = repo.user_by_email(email)
    if user is None:
        user = User(
            id=f"u_{uuid.uuid4().hex[:12]}",
            email=email,
            password_hash=hash_password(uuid.uuid4().hex),
            name=email.split("@", 1)[0],
        )
        if repo.claim_email(email, user.id):
            repo.put_user(user)
        else:
            # Lost the race -- someone else's account now owns this email
            # (a concurrent invite or signup). Attach to that one instead
            # of creating a second, orphaned User document.
            user = repo.user_by_email(email)

    membership = Membership(
        id=f"m_{uuid.uuid4().hex[:12]}", user_id=user.id, workspace_id=sess.workspace_id, role=body.role,
    )
    repo.put_membership(membership)
    request.app.state.ledger.append(
        sess.workspace_id, sess.user_id, "member.invited",
        {"membership_id": membership.id, "user_id": user.id, "email": email, "role": body.role},
    )
    return _member_json(repo, membership)


@router.patch("/{membership_id}")
def change_role(
    membership_id: str, body: RoleBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if body.role not in _VALID_ROLES:
        raise HTTPException(400, f"role must be one of {_VALID_ROLES}")

    repo = request.app.state.repo
    # Scoped to the caller's own workspace -- see the module docstring's
    # IDOR section. A membership id real in another workspace 404s here,
    # exactly like one that does not exist anywhere.
    membership = _get_membership_or_404(repo, sess.workspace_id, membership_id)

    if membership.role == "owner" and body.role != "owner" and _owner_count(repo, sess.workspace_id) <= 1:
        raise HTTPException(409, "the last owner cannot be demoted -- promote another member to owner first")

    updated = type(membership)(**{**membership.__dict__, "role": body.role})
    repo.put_membership(updated)
    request.app.state.ledger.append(
        sess.workspace_id, sess.user_id, "member.role_changed",
        {
            "membership_id": membership.id, "user_id": membership.user_id,
            "old_role": membership.role, "new_role": body.role,
        },
    )
    return _member_json(repo, updated)


@router.delete("/{membership_id}")
def remove_member(
    membership_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    repo = request.app.state.repo
    membership = _get_membership_or_404(repo, sess.workspace_id, membership_id)

    if membership.role == "owner" and _owner_count(repo, sess.workspace_id) <= 1:
        raise HTTPException(409, "the last owner cannot be removed -- promote another member to owner first")

    # Tombstone first, then revoke -- see the module docstring for why
    # both happen in this one request rather than either being deferred.
    tombstoned = type(membership)(**{**membership.__dict__, "user_id": "", "workspace_id": ""})
    repo.put_membership(tombstoned)
    request.app.state.sessions.revoke_all(membership.user_id)
    request.app.state.ledger.append(
        sess.workspace_id, sess.user_id, "member.removed",
        {"membership_id": membership.id, "user_id": membership.user_id},
    )
    return {"ok": True}
