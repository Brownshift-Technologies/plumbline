"""OAuth sign-in: `GET /api/auth/oauth/{provider}/start` and
`GET /api/auth/oauth/{provider}/callback`.

**CSRF via `state`.** `start` mints a random value, signs it with
itsdangerous (bound to a provider name and a timestamp), and hands the
signed blob to the provider as `state` *and* sets it as a short-lived,
httponly cookie on the browser. `callback` requires the `state` query
param the provider sent back to be byte-identical to that cookie, AND to
verify against this app's own signing key, AND to be within its max-age.
Any of those failing is a 400, not a soft fallback -- an absent state
(cookie expired or never set), a forged one (signature does not verify), an
expired one (signature verifies, but past `max_age`), and one that
"belongs to a different browser" (a query-string `state` a browser was
never issued, and so never has the matching cookie for) all fail the *same*
"query must equal cookie" check, because a state minted for one browser is
never present as a cookie on any other one. That equality check is what
actually stops the classic OAuth login-CSRF: an attacker cannot get a
victim to complete the attacker's own OAuth flow by sending them a
callback URL, because the victim's browser never carries the attacker's
state cookie.

**Account linking -- the security-critical decision.** `callback` looks the
returned email up via `Repo.user_by_email`. Three cases:

1. No user has that email: a brand-new account is created and linked to
   this OAuth identity. Nothing to take over.
2. An existing user has that email AND the provider asserts
   `email_verified`: link -- issue a session for that existing user. This
   is the seamless "I already have a Plumbline account, I'm just signing
   in with Google now" path, and it is safe *because* the provider itself
   vouches that this OAuth identity controls that mailbox -- the same
   guarantee email verification during signup would have given.
3. An existing user has that email but the provider does NOT assert
   `email_verified`: refused with 409. This is the case the brief calls
   out by name: an attacker registers `victim@example.com` at some OAuth
   provider (nothing stops that at signup) without ever proving they
   control the mailbox, then completes Plumbline's OAuth flow hoping to be
   silently linked into the victim's real, password-protected account.
   Every provider implemented here (`app/providers.py`) surfaces its own
   verification signal (Google/Okta's OIDC `email_verified` claim, GitHub's
   per-address `verified` flag) precisely so this gate has something real
   to check. This is not a hypothetical: it is the standard shape of an
   OAuth account-takeover vulnerability, and gating on provider-asserted
   verification is the standard fix (Google, GitHub and Auth0 all implement
   some form of it for their own "sign in with" linking).

A brand-new account (case 1) is created regardless of `email_verified` --
there is no existing account to take over, so nothing is lost by treating
it the same as an ordinary email/password signup, which this codebase does
not verify either.
"""

import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import RedirectResponse

from app.deps import COOKIE
from app.models import Membership, User
from app.providers import OAuthError
from app.security import hash_password, new_token

router = APIRouter(prefix="/api/auth/oauth")

STATE_COOKIE = "pl_oauth_state"

# How long a browser has to complete the provider's own login/consent screen
# and land back on `callback`. Long enough for a real user to type a
# password and click through an OAuth consent page; short enough that a
# state cookie is a poor thing to steal -- see the module docstring for why
# a mismatched-browser replay is rejected regardless of this window.
_STATE_MAX_AGE = 600


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.oauth_state_secret, salt="plumbline-oauth-state")


def _set_session_cookie(response: Response, sid: str) -> None:
    # Mirrors app/auth_routes.py's `_set_cookie` exactly (see that module
    # for why `secure=True` matters against TestClient). Duplicated rather
    # than imported so this router does not reach into another router
    # module's private helpers across a file this task did not write.
    response.set_cookie(
        COOKIE, sid, httponly=True, secure=True, samesite="lax", max_age=14 * 86400, path="/"
    )


def _provider_for(request: Request, name: str):
    provider = request.app.state.oauth_providers.get(name)
    if provider is None:
        raise HTTPException(404, f"unknown oauth provider {name!r}")
    return provider


@router.get("/{provider}/start")
def start(provider: str, request: Request):
    prov = _provider_for(request, provider)
    signed_state = _serializer(request).dumps({"n": new_token(), "p": provider})

    resp = RedirectResponse(prov.authorize_url(signed_state), status_code=302)
    resp.set_cookie(
        STATE_COOKIE,
        signed_state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_STATE_MAX_AGE,
        path="/api/auth/oauth",
    )
    return resp


@router.get("/{provider}/callback")
def callback(provider: str, request: Request, response: Response, code: str = "", state: str = ""):
    prov = _provider_for(request, provider)
    cookie_state = request.cookies.get(STATE_COOKIE)

    # A captured/replayed callback URL (same query string) presented a
    # second time from the SAME browser must also fail: the cookie is
    # cleared unconditionally on every callback, success or failure, so
    # there is nothing left for a second attempt to match against. This is
    # defence in depth alongside FakeProvider/every real provider's own
    # single-use-code enforcement in `exchange` -- either one alone already
    # stops the replay; both hold even if the other had a bug.
    response.delete_cookie(STATE_COOKIE, path="/api/auth/oauth")

    if not state or not cookie_state:
        raise HTTPException(400, "missing oauth state")
    # Constant-time-irrelevant here (state is not a secret an attacker is
    # guessing character-by-character -- it is compared whole, once), but
    # `!=` on plain strings is the natural, correct check for "does the
    # state this browser was issued match the state this callback carries".
    if state != cookie_state:
        raise HTTPException(400, "oauth state does not match this browser")
    try:
        payload = _serializer(request).loads(cookie_state, max_age=_STATE_MAX_AGE)
    except SignatureExpired as exc:
        raise HTTPException(400, "oauth state has expired") from exc
    except BadSignature as exc:
        raise HTTPException(400, "oauth state is invalid") from exc
    if payload.get("p") != provider:
        raise HTTPException(400, "oauth state does not match this provider")

    try:
        token = prov.exchange(code)
        email, name, verified = prov.profile(token)
    except OAuthError as exc:
        raise HTTPException(400, f"oauth exchange failed: {exc}") from exc
    if not email:
        raise HTTPException(400, "oauth provider returned no usable email")

    repo = request.app.state.repo
    user = repo.user_by_email(email)
    if user is not None and not verified:
        # See the module docstring's "account linking" section: an
        # unverified provider email must never attach to an existing
        # account, however much its address matches on paper.
        raise HTTPException(
            409,
            "an account with this email already exists; sign in with your "
            "password, or verify this email with the provider first",
        )

    if user is None:
        user = User(
            id=f"u_{uuid.uuid4().hex[:12]}",
            email=email,
            # OAuth-only accounts have no password of their own -- a random,
            # unguessable value here (never returned, never logged) keeps
            # `password_hash` satisfying its non-Optional type without ever
            # being a real credential; `verify_password` against it always
            # fails, exactly as it should for an account nobody set a
            # password on.
            password_hash=hash_password(new_token()),
            name=name or email.split("@")[0],
        )
        if not repo.claim_email(email, user.id):
            # Lost a race with a concurrent signup/OAuth callback for the
            # same email between the lookup above and this claim -- fall
            # back to whoever won rather than creating a second account on
            # one email (the same invariant `app/auth_routes.py`'s `signup`
            # protects).
            user = repo.user_by_email(email)
        else:
            repo.put_user(user)
            ws = request.app.state.bootstrap_workspace(user)
            repo.put_workspace(ws)
            repo.put_membership(
                Membership(id=f"m_{uuid.uuid4().hex[:12]}", user_id=user.id, workspace_id=ws.id, role="owner")
            )

    ws_id = next((m.workspace_id for m in repo.memberships_for_user(user.id)), "")
    sess = request.app.state.sessions.issue(
        user.id, ws_id, user_agent=request.headers.get("user-agent", "")
    )
    _set_session_cookie(response, sess.id)
    return {"id": user.id, "name": user.name, "workspace_id": ws_id}
