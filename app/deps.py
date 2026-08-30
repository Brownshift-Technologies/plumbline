"""FastAPI dependencies shared by every authenticated route in Plumbline.

Every dependency here reads off `request.app.state`, not a module-level
singleton -- `app.main.build_app` can construct more than one app per
process (each test in `tests/conftest.py` gets its own, backed by its own
`FakeFirestore`), and a module-level `sessions`/`repo` would let one test's
app answer with another test's data. Depending on `request.app.state`
instead means every dependency here is automatically correct for whichever
app instance actually served the request.

`current_session` is the one dependency that reads the cookie and resolves
it against `SessionService.resolve`, which is itself careful to reject a
tombstoned (revoked) session on its field values rather than on Python
truthiness (see `app/sessions.py`'s module docstring). Every other
dependency here builds on `current_session` rather than re-reading the
cookie, so there is exactly one place that turns a cookie into "signed in
or not".
"""

from fastapi import Depends, HTTPException, Request

COOKIE = "pl_session"


def current_session(request: Request):
    """Resolve `pl_session` to a live `Session`, or 401.

    A missing cookie and an unresolvable one (expired, revoked, forged,
    or simply never issued) are indistinguishable to the caller on
    purpose: both come back as the same 401, so a client cannot use this
    endpoint to probe which session ids exist.
    """
    sid = request.cookies.get(COOKIE)
    sess = request.app.state.sessions.resolve(sid) if sid else None
    if not sess:
        raise HTTPException(401, "not signed in")
    return sess


def current_user(request: Request, sess=Depends(current_session)):
    """The signed-in `User`, or `None` for a demo session.

    A demo session's `user_id` ("demo") deliberately has no row in `users`
    -- there is no account to look up -- so `repo.user` returning `None`
    for it is expected, not an error. Only a *non*-demo session whose user
    lookup misses (the account was deleted out from under a still-live
    session) is treated as unauthenticated.
    """
    user = request.app.state.repo.user(sess.user_id)
    if not user and not sess.is_demo:
        raise HTTPException(401, "not signed in")
    return user


def require_role(*roles):
    """Dependency factory: 403 unless the caller's role in their session's
    workspace is one of `roles`. `roles` is fixed at route-definition time
    (`Depends(require_role("owner"))`), never derived from request data --
    a caller cannot widen what a route accepts by sending a different role
    in the body."""

    def check(request: Request, sess=Depends(current_session)):
        role = request.app.state.repo.role_of(sess.user_id, sess.workspace_id)
        if role not in roles:
            raise HTTPException(403, f"needs one of {roles}")
        return role

    return check


def is_demo(sess=Depends(current_session)) -> bool:
    """Whether the current session is a demo visitor. Routes that accept
    writes from anyone use this to short-circuit into the
    `{"demo": True, "persisted": False}` no-op shape instead of touching
    Firestore -- see `app/auth_routes.py`'s `change_password` for the
    reference implementation every write-capable route after this task
    follows."""
    return sess.is_demo
