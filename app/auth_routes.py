"""Session-cookie auth: sign up, sign in, sign out, demo entry, password
change, and session inventory/revocation.

OAuth, TOTP enrolment/verification, and password reset are Task 8b -- this
module is deliberately just the password + demo path. Every write here
goes through `Repo`/`SessionService` rather than touching Firestore
directly, and every response shape a test in `tests/test_auth_routes.py`
or `tests/test_demo_mode.py` checks is chosen to be exactly what a
frontend needs and nothing it has to reverse-engineer from an HTTP status
code alone.
"""

import base64
import dataclasses
import time
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr

from app.deps import COOKIE, current_session, demo_refusal
from app.models import Membership, User
from app.security import hash_password, verify_password
from app.sessions import DEMO_TTL_SECONDS

router = APIRouter(prefix="/api/auth")

# A signed-up account needs at least this many characters. 12 rather than a
# smaller number matches NIST SP 800-63B's guidance that length, not
# composition rules (forced digits/symbols), is what actually resists
# offline guessing -- and it is the same floor `app/security.py`'s
# argon2 cost is tuned to make expensive to brute-force per guess.
_MIN_PASSWORD_LEN = 12

# A syntactically valid argon2id hash of a fixed, unguessable password,
# computed once at import time -- not per request. `signin` runs
# `verify_password` against this whenever the email lookup misses, so a
# nonexistent-account attempt costs the same wall-clock time as a
# wrong-password attempt against a real account: both call `_ph.verify`
# once. Without this, "no such user" would return measurably faster than
# "wrong password for a user that exists" (no argon2 hash to compute
# against), and that timing gap is a user-enumeration oracle -- an
# attacker learns which emails have accounts without guessing a single
# password. Carried into this task from Tasks 6/7's review (Ruling 32).
_DUMMY_HASH = hash_password("this-is-not-a-real-account-do-not-reuse-it")

# The single 401 body every failed sign-in returns, whether the email does
# not exist or the password is wrong for one that does. Two different
# messages ("no such user" vs "wrong password") would themselves be a
# user-enumeration oracle even with matched timing -- this closes both
# channels, not just the timing one.
_BAD_CREDENTIALS = "that email and password do not match"


class SignUp(BaseModel):
    email: EmailStr
    password: str
    name: str


class SignIn(BaseModel):
    email: EmailStr
    password: str


class ChangePassword(BaseModel):
    current: str
    new: str


def _set_cookie(response: Response, sid: str, max_age: int = 14 * 86400) -> None:
    # `max_age` has to be able to follow the session's own TTL. A demo
    # sandbox lives as long as its cookie (there are no demo credentials to
    # sign back in with, so the cookie is the only handle on it), and a
    # 14-day cookie over a year-long session would have quietly orphaned
    # every sandbox after a fortnight -- the workspace still in Firestore,
    # its owner unable to reach it.
    response.set_cookie(
        COOKIE, sid, httponly=True, secure=True, samesite="lax", max_age=max_age, path="/"
    )


@router.post("/signup")
def signup(body: SignUp, request: Request, response: Response):
    repo = request.app.state.repo
    # Case-insensitive: `Repo.user_by_email` lower-cases its query, and
    # `Repo.put_user` lower-cases what it stores (Task 2's review), so this
    # check and the account it might collide with agree on casing no
    # matter which case either request used.
    if repo.user_by_email(body.email):
        raise HTTPException(409, "that email already has an account")
    if len(body.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(400, f"use at least {_MIN_PASSWORD_LEN} characters")

    user = User(
        id=f"u_{uuid.uuid4().hex[:12]}",
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
    )
    # Transactional claim, not just the cheap check above: two signups for
    # the same email racing each other can both pass that check before
    # either writes -- see Repo.claim_email for why that would otherwise
    # leave two ambiguous accounts on one email instead of one clean 409.
    if not repo.claim_email(body.email, user.id):
        raise HTTPException(409, "that email already has an account")
    repo.put_user(user)

    ws = request.app.state.bootstrap_workspace(user)
    repo.put_workspace(ws)
    repo.put_membership(
        Membership(id=f"m_{uuid.uuid4().hex[:12]}", user_id=user.id, workspace_id=ws.id, role="owner")
    )

    sess = request.app.state.sessions.issue(user.id, ws.id, user_agent=request.headers.get("user-agent", ""))
    _set_cookie(response, sess.id)
    return {"id": user.id, "name": user.name, "workspace_id": ws.id}


@router.post("/signin")
def signin(body: SignIn, request: Request, response: Response):
    repo = request.app.state.repo
    user = repo.user_by_email(body.email)
    if not user:
        # Burn the same argon2 cost a real-account wrong-password attempt
        # would pay, then fail with the identical body -- see _DUMMY_HASH.
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(401, _BAD_CREDENTIALS)
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(401, _BAD_CREDENTIALS)

    ws_id = next((m.workspace_id for m in repo.memberships_for_user(user.id)), "")
    sess = request.app.state.sessions.issue(
        user.id, ws_id, user_agent=request.headers.get("user-agent", "")
    )
    _set_cookie(response, sess.id)
    return {"id": user.id, "name": user.name, "workspace_id": ws_id}


def _touch_demo_workspace(request: Request, workspace_id: str) -> None:
    """Mark a sandbox as still in use, so the sweep leaves it alone.

    Without this a returning visitor's sandbox would still be collected a
    year after it was *created*, however recently they last opened it.
    """
    repo = request.app.state.repo
    workspace = repo.workspace(workspace_id)
    if workspace is not None:
        repo.put_workspace(dataclasses.replace(workspace, last_seen_at=time.time()))


def _live_demo_session(request: Request):
    """The caller's own demo session, if they still have a usable one.

    Returns None -- so the caller mints a fresh sandbox -- when there is no
    cookie, when the session has expired or been revoked, when it belongs
    to a real account rather than a demo, or when the workspace it points
    at is gone. That last check matters: a session outliving its workspace
    would land the visitor in an empty shell of a dashboard with every
    query returning nothing, which reads exactly like data loss.
    """
    sid = request.cookies.get(COOKIE)
    if not sid:
        return None
    sess = request.app.state.sessions.resolve(sid)
    if sess is None or not sess.is_demo:
        return None
    if request.app.state.repo.workspace(sess.workspace_id) is None:
        return None
    return sess


@router.post("/demo")
def demo(request: Request, response: Response):
    # Every demo visitor gets their OWN sandbox workspace -- a fresh copy
    # of the seeded fixture that session can fully write to -- rather than
    # sharing one read-only `config.demo_workspace_id` with every other
    # visitor. Isolation is then automatic: every route already scopes by
    # `sess.workspace_id`, so this is the only place that needs to know a
    # new id is being minted at all.
    #
    # A returning visitor comes BACK to the sandbox they already built.
    # The demo is meant to behave like an account: whatever you created
    # last time -- behaviours, runs, an approved patch -- is still there.
    # Re-seeding on every click would have silently thrown all of it away
    # and handed back an empty-looking copy, which is indistinguishable
    # from "my work vanished". The cookie is the only handle on a demo
    # sandbox (there are no demo credentials), so it is what we resolve.
    existing = _live_demo_session(request)
    if existing is not None:
        _touch_demo_workspace(request, existing.workspace_id)
        _set_cookie(response, existing.id, max_age=DEMO_TTL_SECONDS)
        return {
            "id": "demo",
            "name": "Demo visitor",
            "workspace_id": existing.workspace_id,
            "is_demo": True,
            "returning": True,
        }

    ws_id = f"ws_demo_{uuid.uuid4().hex[:20]}"
    request.app.state.seed_demo_workspace(ws_id)
    # Opportunistic, bounded cleanup of sandboxes nobody can reach any more
    # -- after seeding this one, never before: a slow or failed sweep must
    # never be the reason a visitor's own demo entry fails. Only runs when
    # a NEW sandbox is minted, so a returning visitor pays nothing for it.
    request.app.state.sweep_expired_demo_workspaces()
    sess = request.app.state.sessions.issue("demo", ws_id, is_demo=True)
    _set_cookie(response, sess.id, max_age=DEMO_TTL_SECONDS)
    return {
        "id": "demo",
        "name": "Demo visitor",
        "workspace_id": ws_id,
        "is_demo": True,
        "returning": False,
    }


@router.post("/signout")
def signout(request: Request, response: Response, sess=Depends(current_session)):
    request.app.state.sessions.revoke(sess.id)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(body: ChangePassword, request: Request, sess=Depends(current_session)):
    # A demo session (`user_id == "demo"`) has no `User` row at all -- see
    # `app/deps.py`'s `current_user` docstring -- so there is no account
    # here to change the password of, sandbox or not. Unlike the
    # workspace-scoped writes this task turned into real writes (behaviours,
    # gate rules, the gated patch, ...), this genuinely has nothing to act
    # on, so it stays a refusal, just with a reason instead of the old
    # unconditional "nothing was saved".
    if sess.is_demo:
        return demo_refusal("Demo sessions don't have a real account to change the password of.")

    repo = request.app.state.repo
    user = repo.user(sess.user_id)
    if not user or not verify_password(body.current, user.password_hash):
        raise HTTPException(400, "your current password is not correct")
    if len(body.new) < _MIN_PASSWORD_LEN:
        raise HTTPException(400, f"use at least {_MIN_PASSWORD_LEN} characters")

    repo.put_user(type(user)(**{**user.__dict__, "password_hash": hash_password(body.new)}))
    # Every *other* device signs out; the device that just proved knowledge
    # of the new password (this request's own session) stays signed in.
    request.app.state.sessions.revoke_all_except(user.id, sess.id)
    return {"ok": True, "other_sessions_ended": True}


@router.get("/me")
def me(request: Request, sess=Depends(current_session)):
    if sess.is_demo:
        return {
            "id": "demo",
            "name": "Demo visitor",
            "is_demo": True,
            "workspace_id": sess.workspace_id,
            "role": "reader",
            "photo_url": "",
        }
    user = request.app.state.repo.user(sess.user_id)
    if not user:
        # The account behind a still-live, non-demo session was deleted.
        # Treat exactly like any other unresolved session (see
        # app/deps.py's current_user for the same call) rather than 500ing.
        raise HTTPException(401, "not signed in")
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "is_demo": False,
        "workspace_id": sess.workspace_id,
        "role": request.app.state.repo.role_of(user.id, sess.workspace_id),
        "photo_url": user.photo_url,
    }


MAX_PHOTO_BYTES = 256 * 1024
_PHOTO_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp", "image/gif": "gif"}


@router.post("/photo")
async def upload_photo(request: Request, photo: UploadFile = File(...), sess=Depends(current_session)):
    """Set the caller's profile photo.

    Stored inline as a `data:` URI on the `User` row -- see
    `app/models.py`'s `photo_url` for why that rather than a bucket.

    The content type is taken from `imghdr`-style sniffing of the bytes,
    NOT from the client's `Content-Type` header: a header is attacker
    controlled, and echoing it straight back into a `data:` URI that the
    browser then renders is how `image/svg+xml` (which executes script)
    gets served from our own origin. Only the four raster formats in
    `_PHOTO_TYPES` are accepted, and SVG is deliberately not one of them.
    """
    if sess.is_demo:
        return demo_refusal("Demo sessions don't have a real account to set a photo on.")

    raw = await photo.read()
    if not raw:
        raise HTTPException(400, "that file is empty")
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(
            413, f"that photo is {len(raw) // 1024} KB -- the limit is {MAX_PHOTO_BYTES // 1024} KB"
        )

    kind = _sniff_image(raw)
    if kind is None:
        raise HTTPException(400, "that file is not a PNG, JPEG, WebP or GIF image")

    repo = request.app.state.repo
    user = repo.user(sess.user_id)
    if user is None:
        raise HTTPException(401, "not signed in")
    encoded = base64.b64encode(raw).decode("ascii")
    repo.put_user(dataclasses.replace(user, photo_url=f"data:{kind};base64,{encoded}"))
    return {"ok": True, "bytes": len(raw)}


@router.delete("/photo")
def remove_photo(request: Request, sess=Depends(current_session)):
    if sess.is_demo:
        return demo_refusal("Demo sessions don't have a real account to remove a photo from.")
    repo = request.app.state.repo
    user = repo.user(sess.user_id)
    if user is None:
        raise HTTPException(401, "not signed in")
    repo.put_user(dataclasses.replace(user, photo_url=""))
    return {"ok": True}


def _sniff_image(raw: bytes) -> str | None:
    """Identify an image from its magic bytes, ignoring any client header.

    Returns the MIME type, or None for anything not in `_PHOTO_TYPES`.
    """
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


@router.get("/sessions")
def sessions_list(request: Request, sess=Depends(current_session)):
    return [
        {"id": s.id, "user_agent": s.user_agent, "ip_city": s.ip_city, "current": s.id == sess.id}
        for s in request.app.state.sessions.list_for_user(sess.user_id)
    ]


@router.delete("/sessions/{sid}")
def session_revoke(sid: str, request: Request, sess=Depends(current_session)):
    # Scoped to the caller's own sessions: list_for_user(sess.user_id) is the
    # authorisation check, not just a convenience filter. Without it, any
    # signed-in user could pass an arbitrary session id from a different
    # account and revoke someone else's session -- a horizontal privilege
    # escalation, and a realistic one, since session ids appear in this same
    # router's own GET /sessions response for the caller's own sessions and
    # an attacker only has to guess or observe one belonging to someone else.
    owned_ids = {s.id for s in request.app.state.sessions.list_for_user(sess.user_id)}
    if sid not in owned_ids:
        raise HTTPException(404, "no such session")
    request.app.state.sessions.revoke(sid)
    return {"ok": True}
