"""Task 14d: outbound webhook delivery -- `run.finished`, `finding.created`,
`patch.ready`, `patch.approved`.

**Signed, so a receiver can verify it came from us.** Every delivery's raw
JSON body is HMAC-SHA256 signed with the endpoint's own `Webhook.secret`
(generated once, at creation, alongside the endpoint -- see
`app/models.py`'s `Webhook`) and sent as `X-Plumbline-Signature:
sha256=<hex>`. A receiver recomputes the same HMAC over the raw bytes it
received and compares -- exactly the verification discipline
`app/github_routes.py`'s `POST /api/github/webhook` (Task 14g) uses in
the other direction, for inbound GitHub webhooks.

**Retries, then marked failing.** `deliver` attempts up to
`_MAX_ATTEMPTS` (5) times with exponential backoff (`_BACKOFF_SECONDS`),
calling the injectable `sleep` between attempts rather than a bare
`time.sleep` so a test exercises the real retry COUNT without spending
real wall-clock seconds. Every attempt -- success or failure -- is
recorded in the workspace's audit ledger (`ledger.append`, action
`webhook.delivery`), so "did you call us?" has a real, queryable answer.
Once every attempt has failed, the endpoint's `Webhook.status` flips to
`"failing"` and `failure_count` is bumped; the UI (a later task) is what
surfaces that.

**Where this is actually triggered from.** `app/main.py`'s `_on_event`
(already the Pub/Sub receiver for `job/worker.py`'s `publish_event(...,
"run.finished", ...)` -- see that module's own docstring) calls
`dispatch_run_finished` below whenever a push payload matches the
`run.finished` shape. `finding.created`/`patch.ready`/`patch.approved`
have no publisher wired anywhere in this codebase yet -- nothing owned by
this task publishes them (they would need a call site inside
`app/finding_routes.py`/`agents/triager.py`/`agents/surgeon.py`, all
outside this task's file list) -- so `dispatch_event` below is a complete,
tested, general-purpose mechanism exposed as `app.state.dispatch_webhook`
for whichever future change adds those call sites, not a promise that
those three events fire in production today. Flagged here and in the
task report rather than silently assumed done.
"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.deps import current_session, demo_refusal, require_write_role
from app.models import Webhook

router = APIRouter(prefix="/api/webhooks")

_VALID_EVENTS = frozenset({"run.finished", "finding.created", "patch.ready", "patch.approved"})
_MAX_ATTEMPTS = 5
_BACKOFF_SECONDS = (0, 1, 2, 4, 8)


def sign_payload(secret: str, raw_body: bytes) -> str:
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _default_post(url: str, body: bytes, headers: dict) -> int:
    """The real `post` -- `urllib.request`, not `requests`, for the same
    reason `app/providers.py`'s real OAuth providers avoid it: `requests`
    is a dev-only dependency (`pyproject.toml`'s `[project.optional-
    dependencies].dev`), so a production import of this module must not
    need it. Never called by the default test suite -- every test supplies
    its own `post=` double."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 -- customer-configured endpoint
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def create_webhook(store, workspace_id: str, url: str, events: list[str], created_by: str) -> Webhook:
    hook = Webhook(
        id=f"wh_{uuid.uuid4().hex[:12]}", workspace_id=workspace_id, url=url,
        secret=f"whsec_{uuid.uuid4().hex}", events=tuple(events), created_by=created_by,
    )
    store.put("webhooks", hook.id, hook.__dict__)
    return hook


def list_webhooks(store, workspace_id: str) -> list[Webhook]:
    rows = store.query("webhooks", "workspace_id", "==", workspace_id)
    return sorted((Webhook(**r) for r in rows), key=lambda w: w.created_at, reverse=True)


def webhooks_for_event(store, workspace_id: str, event: str) -> list[Webhook]:
    return [w for w in list_webhooks(store, workspace_id) if event in w.events]


def delete_webhook(store, workspace_id: str, webhook_id: str) -> bool:
    row = store.get("webhooks", webhook_id)
    if not row or row["workspace_id"] != workspace_id:
        return False
    # No delete in Store (see core/store.py) -- tombstone with an empty
    # workspace_id, the same pattern Repo.delete_session uses, so
    # `webhooks_for_event`'s query can never match it again.
    store.put("webhooks", webhook_id, {**row, "workspace_id": "", "status": "deleted"})
    return True


def deliver(store, ledger, webhook: Webhook, event: str, payload: dict, *, post=_default_post, sleep=time.sleep) -> bool:
    """Deliver one event to one endpoint, retrying with backoff. `post` is
    `(url, body_bytes, headers) -> status_code`, injectable so the default
    offline test suite never opens a real socket -- a `FakeHTTP` double
    supplies it (see `tests/test_webhooks.py`); a live opt-in test file
    would use `urllib.request` the way `app/providers.py`'s real OAuth
    providers do. Returns whether delivery ultimately succeeded.
    """
    body = json.dumps({"event": event, "data": payload}, sort_keys=True).encode()
    signature = sign_payload(webhook.secret, body)
    headers = {"Content-Type": "application/json", "X-Plumbline-Signature": signature}

    last_status = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            sleep(_BACKOFF_SECONDS[attempt])
        try:
            last_status = post(webhook.url, body, headers)
            ok = 200 <= last_status < 300
        except Exception as exc:  # noqa: BLE001 -- a customer endpoint can fail any way it likes
            last_status = str(exc)
            ok = False

        ledger.append(
            webhook.workspace_id, f"webhook:{webhook.id}", "webhook.delivery",
            {"webhook_id": webhook.id, "event": event, "attempt": attempt + 1, "status": last_status, "ok": ok},
        )
        if ok:
            row = store.get("webhooks", webhook.id) or webhook.__dict__
            store.put("webhooks", webhook.id, {**row, "status": "active", "failure_count": 0})
            return True

    row = store.get("webhooks", webhook.id) or webhook.__dict__
    store.put("webhooks", webhook.id, {
        **row, "status": "failing", "failure_count": row.get("failure_count", 0) + 1,
    })
    return False


def dispatch_event(repo, ledger, workspace_id: str, event: str, payload: dict, *, post=_default_post, sleep=time.sleep) -> None:
    for webhook in webhooks_for_event(repo.store, workspace_id, event):
        deliver(repo.store, ledger, webhook, event, payload, post=post, sleep=sleep)


def dispatch_run_finished(repo, ledger, workspace_id: str, payload: dict, *, post=_default_post, sleep=time.sleep) -> None:
    dispatch_event(repo, ledger, workspace_id, "run.finished", payload, post=post, sleep=sleep)


class CreateWebhookBody(BaseModel):
    url: str = Field(min_length=1)
    events: list[str] = Field(min_length=1)


def _webhook_json(w: Webhook) -> dict:
    return {
        "id": w.id, "url": w.url, "events": list(w.events), "status": w.status,
        "failure_count": w.failure_count, "created_at": w.created_at,
    }


@router.post("")
def create_webhook_route(
    body: CreateWebhookBody, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if sess.is_demo:
        # Outbound delivery (`deliver`, above) posts to a URL the visitor
        # supplies -- a real HTTP call to somewhere outside this codebase's
        # control, no matter how harmless creating the row alone would be.
        return demo_refusal("Webhooks would deliver to a real URL outside the demo, so creating one isn't available.")
    unknown = [e for e in body.events if e not in _VALID_EVENTS]
    if unknown:
        raise HTTPException(400, f"unknown event(s) {unknown} -- choose from {sorted(_VALID_EVENTS)}")
    repo = request.app.state.repo
    hook = create_webhook(repo.store, sess.workspace_id, body.url, body.events, sess.user_id)
    # `secret` is included exactly once, in this response -- the same
    # shown-once discipline `app/api_keys.py`'s create_key uses.
    return {**_webhook_json(hook), "secret": hook.secret}


@router.get("")
def list_webhooks_route(request: Request, sess=Depends(current_session)):
    repo = request.app.state.repo
    hooks = [h for h in list_webhooks(repo.store, sess.workspace_id) if h.status != "deleted"]
    return {"webhooks": [_webhook_json(h) for h in hooks]}


@router.delete("/{webhook_id}")
def delete_webhook_route(
    webhook_id: str, request: Request, sess=Depends(current_session),
    _role=Depends(require_write_role("owner")),
):
    if sess.is_demo:
        return demo_refusal("There's no real webhook in the demo to delete.")
    repo = request.app.state.repo
    if not delete_webhook(repo.store, sess.workspace_id, webhook_id):
        raise HTTPException(404, "no such webhook")
    return {"ok": True}
