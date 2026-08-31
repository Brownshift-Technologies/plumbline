"""Task 14d: `pk_live_` API keys -- generation, hashing, authentication,
role enforcement, and per-key rate limiting.

**Shown once, stored hashed.** `generate_key()` returns the raw
`pk_live_<32 chars>` value exactly once, at creation (`create_api_key`
below). Everything persisted (`ApiKey.key_hash`, `app/models.py`) is a
SHA-256 hex digest of it -- a leaked `api_keys` collection (a Firestore
export, a misconfigured rule, a compromised backup) yields no working
key, because there is nothing there to reverse: authenticating hashes
whatever the caller presents and looks up THAT, so presenting the stored
hash itself is just presenting a wrong value that also fails to match
(see `authenticate`). This is the identical discipline
`app/models.py`'s `PasswordReset.id` already uses for reset tokens, and
`app/security.py`'s `hash_password` uses for account passwords -- one
more credential in this codebase, one more place it is never held
reversibly.

**A key may never exceed the role it was issued with.** `ApiKey.role` is
fixed at creation and is the ONLY role ever consulted for a key-
authenticated request -- `require_api_role` below checks this field
directly, never `Repo.role_of` (that resolves a *user's* membership role,
which has nothing to do with what a machine credential was scoped to).
There is no path anywhere in this module, `app/public_routes.py`, or the
Task 14e MCP server that lets a request escalate past `ApiKey.role`.

**Rate limiting is per key, not per IP.** A customer's CI runners, a
shared NAT gateway, a corporate proxy -- all of that egresses from a
small number of IPs shared across many unrelated callers, so limiting by
IP would throttle a whole company because one of its pipelines is busy.
`check_rate_limit` below buckets strictly by `ApiKey.id`. The bucket
itself is a plain token bucket (`tokens`, refilled continuously at
`workspace.api_rate_limit_per_minute / 60` tokens/second, capped at that
same ceiling) persisted as one Firestore document per key
(`rate_limits/{key_id}`) -- deliberately NOT wrapped in a
`@firestore.transactional` retry the way `Repo.claim_run`/`claim_email`
are: those protect a count that must never be double-spent (a run
billed twice, an email claimed twice); a rate limiter reading a slightly
stale bucket under a genuine race merely admits, at worst, one extra
request in a tight window, which is the correct failure direction for a
gate whose entire job is "protect the service from load", not "produce
an exact count".
"""

import hashlib
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.deps import current_session, demo_refusal, require_write_role
from app.models import ApiKey

router = APIRouter(prefix="/api/keys")

_KEY_PREFIX = "pk_live_"
_KEY_BODY_LEN = 32
_KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_VALID_ROLES = ("owner", "approver", "reader")


def generate_key() -> str:
    """A fresh `pk_live_<32 random chars>` value. `secrets.choice` (a CSPRNG),
    never `random` -- this is a bearer credential, not a display id."""
    body = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_BODY_LEN))
    return f"{_KEY_PREFIX}{body}"


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_api_key(
    store, workspace_id: str, name: str, role: str, created_by: str,
    expires_in_days: float | None = None,
) -> tuple[ApiKey, str]:
    """Persist a new key and return `(ApiKey, raw_key)`. The raw value is
    handed back ONLY here, to the caller of THIS function, at the moment
    of creation -- nothing downstream (`GET /api/keys`, a ledger entry, a
    log line) ever sees it again. Callers that need the raw value later
    have none: that is the whole point."""
    raw = generate_key()
    expires_at = (time.time() + expires_in_days * 86400) if expires_in_days else None
    key = ApiKey(
        id=f"key_{secrets.token_hex(8)}",
        workspace_id=workspace_id,
        name=name,
        role=role,
        key_hash=hash_key(raw),
        prefix=raw[: len(_KEY_PREFIX) + 6],
        created_by=created_by,
        expires_at=expires_at,
    )
    store.put("api_keys", key.id, key.__dict__)
    return key, raw


def list_api_keys(store, workspace_id: str) -> list[ApiKey]:
    rows = store.query("api_keys", "workspace_id", "==", workspace_id)
    return sorted((ApiKey(**r) for r in rows), key=lambda k: k.created_at, reverse=True)


def revoke_api_key(store, workspace_id: str, key_id: str) -> bool:
    row = store.get("api_keys", key_id)
    if not row or row["workspace_id"] != workspace_id:
        return False
    key = ApiKey(**row)
    if key.revoked_at is not None:
        return True  # already revoked -- idempotent
    store.put("api_keys", key.id, {**row, "revoked_at": time.time()})
    return True


def authenticate(store, raw_key: str) -> ApiKey | None:
    """Resolve a raw `pk_live_...` value to its live `ApiKey`, or `None` for
    anything that does not authenticate: unknown, revoked, or expired.
    Hashes `raw_key` and looks up BY HASH -- so presenting the stored hash
    value itself (what a database leak would actually contain) hashes to a
    different digest and matches nothing. See the module docstring."""
    rows = store.query("api_keys", "key_hash", "==", hash_key(raw_key))
    if not rows:
        return None
    key = ApiKey(**rows[0])
    if key.revoked_at is not None:
        return None
    if key.expires_at is not None and key.expires_at <= time.time():
        return None
    return key


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


def check_rate_limit(store, api_key: ApiKey, limit_per_minute: int) -> RateLimitResult:
    """Token-bucket check for `api_key`, ceiling `limit_per_minute` (read off
    the key's own workspace -- see `app/models.py`'s `Workspace.
    api_rate_limit_per_minute`). Consumes one token on success; leaves the
    bucket untouched on a 429 so a caller that backs off and retries later
    is not double-charged for the rejected attempt."""
    rate = limit_per_minute / 60.0
    now = time.time()
    row = store.get("rate_limits", api_key.id)
    if row is None:
        tokens, updated_at = float(limit_per_minute), now
    else:
        tokens = min(float(limit_per_minute), row["tokens"] + (now - row["updated_at"]) * rate)
        updated_at = now

    if tokens < 1:
        deficit = 1 - tokens
        retry_after = max(1, int(deficit / rate) + 1)
        store.put("rate_limits", api_key.id, {"tokens": tokens, "updated_at": updated_at})
        return RateLimitResult(allowed=False, retry_after=retry_after)

    store.put("rate_limits", api_key.id, {"tokens": tokens - 1, "updated_at": updated_at})
    return RateLimitResult(allowed=True)


def current_api_key(request: Request) -> ApiKey:
    """FastAPI dependency: `Authorization: Bearer <pk_live_...>` -> a live
    `ApiKey`, checked against BOTH authentication and the workspace's rate
    limit -- 401 for a missing/invalid/expired/revoked key, 429 (with
    `Retry-After`) once the bucket is empty. Every `/v1/` route and the
    Task 14e MCP endpoints depend on this rather than `current_session`
    (`app/deps.py`): those two authentication schemes are deliberately
    kept apart -- a browser cookie and a `pk_live_` key are different
    credentials with different lifetimes and different holders (a human
    versus a customer's pipeline or agent), and collapsing them into one
    dependency would make it easy to accidentally accept one where the
    other was intended.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing or malformed Authorization header")
    raw = auth[len("Bearer "):].strip()

    repo = request.app.state.repo
    key = authenticate(repo.store, raw)
    if key is None:
        raise HTTPException(401, "invalid, expired, or revoked API key")

    workspace = repo.workspace(key.workspace_id)
    limit = workspace.api_rate_limit_per_minute if workspace else 60
    result = check_rate_limit(repo.store, key, limit)
    if not result.allowed:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(result.retry_after)})

    return key


def require_api_role(*roles: str):
    """Dependency factory, the API-key analogue of `app/deps.py`'s
    `require_role` -- 403 unless `current_api_key(...).role` is one of
    `roles`. A key's role is fixed at issuance (see the module docstring);
    this is the one place that boundary is enforced for every `/v1/`
    write and every Task 14e MCP write tool."""

    def check(key: ApiKey = Depends(current_api_key)) -> ApiKey:
        if key.role not in roles:
            raise HTTPException(403, f"this key's role ({key.role!r}) needs one of {roles}")
        return key

    return check


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1)
    role: str
    expires_in_days: float | None = None


def _key_json(k: ApiKey) -> dict:
    return {
        "id": k.id, "name": k.name, "role": k.role, "prefix": k.prefix,
        "created_by": k.created_by, "created_at": k.created_at,
        "expires_at": k.expires_at, "revoked_at": k.revoked_at,
    }


@router.post("")
def create_key(
    body: CreateKeyBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if sess.is_demo:
        # A `pk_live_` key authenticates `/v1/` requests against whichever
        # workspace it was issued for -- including this demo session's own
        # sandbox -- for as long as it stays valid, which is not bounded by
        # the 2-hour session TTL the way the sandbox itself is. Refused,
        # not turned into a real (if harmless) write, so a demo visitor
        # never walks away with a credential this codebase makes any
        # promise about outliving the session that created it.
        return demo_refusal("Creating an API key isn't available in the demo -- keys would outlive the sandbox.")
    if body.role not in _VALID_ROLES:
        raise HTTPException(400, f"role must be one of {_VALID_ROLES}")
    repo = request.app.state.repo
    key, raw = create_api_key(
        repo.store, sess.workspace_id, body.name, body.role, sess.user_id,
        expires_in_days=body.expires_in_days,
    )
    # `key` is included exactly once, in this response, and nowhere else --
    # see the module docstring. request.app.state.ledger.append records the
    # creation for audit, but only ever with the (already-hashed, never
    # reversible) key id, never the raw value.
    request.app.state.ledger.append(
        sess.workspace_id, sess.user_id, "api_key.create", {"key_id": key.id, "role": key.role},
    )
    return {**_key_json(key), "key": raw}


@router.get("")
def list_keys(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    keys = list_api_keys(repo.store, sess.workspace_id)
    return {"keys": [_key_json(k) for k in keys]}


@router.delete("/{key_id}")
def delete_key(
    key_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if sess.is_demo:
        return demo_refusal("There's no real API key in the demo to revoke.")
    repo = request.app.state.repo
    if not revoke_api_key(repo.store, sess.workspace_id, key_id):
        raise HTTPException(404, "no such key")
    request.app.state.ledger.append(
        sess.workspace_id, sess.user_id, "api_key.revoke", {"key_id": key_id},
    )
    return {"ok": True}
