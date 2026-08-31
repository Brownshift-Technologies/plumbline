"""Two-factor enrolment and password reset -- the rest of Task 8b's session
surface, alongside `app/oauth_routes.py`.

**TOTP.** `POST /api/auth/totp/enrol` generates a fresh secret and stores it
as `User.totp_pending_secret`, never touching `totp_secret` -- see
`app/models.py`'s docstring for why that split exists at all: an unconfirmed
secret must never satisfy an approval gate (Task 14b reads `totp_secret`
for exactly that gate), and re-enrolling while already confirmed must not
be a way to blow away a working second factor before proving the new one.
`POST /api/auth/totp/verify` is the only thing that promotes pending to
confirmed, and only after a current code checks out against the *pending*
secret. `DELETE /api/auth/totp` requires a current code against the
*confirmed* secret, so a stolen session cookie alone cannot strip someone's
2FA.

Every one of those three also runs its code through `Repo.consume_totp_step`
-- the persisted, transactional replay counter (see that method's
docstring, and `app/security.py`'s `totp_step_for`) -- so a code captured
during enrolment-confirmation or deletion cannot be replayed against a
sibling Cloud Run instance either, exactly like a sign-in-time TOTP check
would need to.

**Password reset.** `POST /api/auth/reset/request` always returns the same
200/`{"ok": True}` whether or not the email exists -- `app/auth_routes.py`'s
`signin` already established why a differing response is an
account-enumeration oracle (Ruling 32 there); this route follows the same
principle, including doing the same *amount* of work (one token minted, one
Firestore write) on both paths, not just the same response shape, so the
two cases cost about the same wall-clock time too. The raw token is only
ever handed to `app.state.deliver_reset_email` (an injectable hook, mirroring
`app.state.seed_demo_if_missing`'s pattern in `app/main.py` -- this codebase
has no email/SMS provider wired yet, and building one is out of scope for
this task); it is never in the HTTP response, and only its SHA-256 is
stored (`Repo.put_password_reset`), so a leaked `password_resets` collection
does not hand out working reset links. `POST /api/auth/reset/confirm`
consumes the token via `Repo.consume_password_reset` (single-use,
transactional, checks expiry) and revokes every existing session for that
user -- a reset is what someone does when they believe they are
compromised, so nothing about "session cookie that was live before the
reset" survives it, including, deliberately, one an attacker holds.
"""

import time

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.deps import current_session, demo_refusal
from app.models import PasswordReset
from app.security import hash_password, hash_token, new_token, new_totp_secret, totp_step_for

router = APIRouter(prefix="/api/auth")

# Matches app/auth_routes.py's own floor -- see that module for the NIST
# SP 800-63B rationale. Redeclared here rather than imported: it is a
# module-private constant there (`_MIN_PASSWORD_LEN`), and this router is
# free-standing, not a submodule of auth_routes.
_MIN_PASSWORD_LEN = 12

# 30 minutes, per the brief -- long enough that a real user who has to go
# find the email has a fair shot, short enough that a token sitting unused
# in an inbox (or a leaked mail log) is only a narrow window of exposure.
_RESET_TTL_SECONDS = 30 * 60


class TotpVerify(BaseModel):
    code: str


class TotpRemove(BaseModel):
    code: str


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(BaseModel):
    token: str
    new_password: str


@router.post("/totp/enrol")
def totp_enrol(request: Request, sess=Depends(current_session)):
    # A demo session's user_id ("demo") has no User row at all -- see
    # app/deps.py's current_user docstring -- so there is no account here
    # to attach a TOTP secret to, sandbox or not.
    if sess.is_demo:
        return demo_refusal("Demo sessions don't have a real account to enrol two-factor authentication on.")
    repo = request.app.state.repo
    user = repo.user(sess.user_id)
    if not user:
        raise HTTPException(401, "not signed in")

    secret = new_totp_secret()
    # Only totp_pending_secret changes -- an already-confirmed totp_secret
    # (if any) is left exactly as it was. See the module docstring.
    repo.put_user(type(user)(**{**user.__dict__, "totp_pending_secret": secret}))
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Plumbline")
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/totp/verify")
def totp_verify(body: TotpVerify, request: Request, sess=Depends(current_session)):
    if sess.is_demo:
        return demo_refusal("Demo sessions don't have a real account to confirm two-factor authentication on.")
    repo = request.app.state.repo
    user = repo.user(sess.user_id)
    if not user:
        raise HTTPException(401, "not signed in")
    if not user.totp_pending_secret:
        raise HTTPException(400, "no pending TOTP enrolment to verify")

    step = totp_step_for(user.totp_pending_secret, body.code)
    if step is None or not repo.consume_totp_step(user.id, step):
        raise HTTPException(400, "that code is not valid")

    repo.put_user(
        type(user)(**{**user.__dict__, "totp_secret": user.totp_pending_secret, "totp_pending_secret": None})
    )
    return {"ok": True}


@router.delete("/totp")
def totp_remove(body: TotpRemove, request: Request, sess=Depends(current_session)):
    if sess.is_demo:
        return demo_refusal("Demo sessions don't have a real account to remove two-factor authentication from.")
    repo = request.app.state.repo
    user = repo.user(sess.user_id)
    if not user:
        raise HTTPException(401, "not signed in")
    if not user.totp_secret:
        raise HTTPException(400, "TOTP is not enrolled")

    step = totp_step_for(user.totp_secret, body.code)
    if step is None or not repo.consume_totp_step(user.id, step):
        raise HTTPException(400, "that code is not valid")

    repo.put_user(type(user)(**{**user.__dict__, "totp_secret": None, "totp_pending_secret": None}))
    return {"ok": True}


@router.post("/reset/request")
def reset_request(body: ResetRequest, request: Request):
    repo = request.app.state.repo
    user = repo.user_by_email(body.email)

    # Always mint a token and always write a row -- see the module
    # docstring's enumeration-resistance note. `user_id=""` when there is no
    # such user is a dead row nothing can ever be confirmed against
    # (`reset_confirm` below looks the resolved user up separately and 400s
    # when it is missing), the same tombstone shape `Repo.delete_session`
    # already uses for "this row resolves to nobody".
    token = new_token()
    repo.put_password_reset(
        PasswordReset(
            id=hash_token(token),
            user_id=user.id if user else "",
            expires_at=time.time() + _RESET_TTL_SECONDS,
        )
    )
    if user:
        request.app.state.deliver_reset_email(user.email, token)
    return {"ok": True}


@router.post("/reset/confirm")
def reset_confirm(body: ResetConfirm, request: Request):
    repo = request.app.state.repo
    # Checked BEFORE the token is consumed: this is client-controlled input
    # with nothing security-sensitive to leak either way, and consuming a
    # valid token first would let a simple too-short-password typo burn the
    # user's one reset link for nothing, forcing them back to
    # `reset/request` to get a new one over a mistake that had nothing to
    # do with the token at all.
    if len(body.new_password) < _MIN_PASSWORD_LEN:
        raise HTTPException(400, f"use at least {_MIN_PASSWORD_LEN} characters")

    reset = repo.consume_password_reset(hash_token(body.token))
    if not reset:
        raise HTTPException(400, "that reset link is invalid or has expired")
    user = repo.user(reset.user_id)
    if not user:
        # The token was genuinely once valid but resolves to nobody usable
        # now (a dead "no such email" row from reset_request, or an account
        # this Store has no way to hard-delete but has otherwise been
        # disabled). Same 400 either way -- not an oracle either, since the
        # caller already had to possess a real, unconsumed token to reach
        # this branch at all. The token IS already spent at this point --
        # unlike the password-length check above, there is no cheap way to
        # validate "does this token resolve to a real user" without first
        # consuming it (Repo.consume_password_reset's whole job is doing
        # that check and the consumption atomically), so a token pointed at
        # a dead user is simply lost rather than reusable. That is the
        # right failure mode for a token that never had a legitimate user
        # behind it in the first place.
        raise HTTPException(400, "that reset link is invalid or has expired")

    repo.put_user(type(user)(**{**user.__dict__, "password_hash": hash_password(body.new_password)}))
    # Every session, full stop -- see the module docstring for why this one
    # has no "except the caller's own" carve-out the way
    # app/auth_routes.py's change_password does.
    request.app.state.sessions.revoke_all(user.id)
    return {"ok": True}
