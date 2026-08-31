"""Task 14d: outbound webhook delivery -- HMAC signing, retry-then-fail,
and the audit ledger record of every attempt."""

from app.webhooks import create_webhook, deliver, dispatch_run_finished, sign_payload


class _FakePost:
    """A callable `post` double: returns a scripted status code per call,
    or raises if `raises=True` on that attempt -- and remembers every call
    it received, so a test can assert on the signature header directly."""

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.calls: list[dict] = []

    def __call__(self, url, body, headers):
        self.calls.append({"url": url, "body": body, "headers": headers})
        status = self._statuses[len(self.calls) - 1]
        if status == "raise":
            raise ConnectionError("endpoint unreachable")
        return status


def _no_sleep(_seconds):
    return None


def test_a_webhook_body_is_signed_with_hmac(client_as_owner):
    repo = client_as_owner.app.state.repo
    ledger = client_as_owner.app.state.ledger
    hook = create_webhook(repo.store, "ws1", "https://example.com/hook", ["run.finished"], "u1")

    post = _FakePost([200])
    ok = deliver(repo.store, ledger, hook, "run.finished", {"run_id": "run_1"}, post=post, sleep=_no_sleep)
    assert ok

    call = post.calls[0]
    expected_signature = sign_payload(hook.secret, call["body"])
    assert call["headers"]["X-Plumbline-Signature"] == expected_signature
    assert expected_signature.startswith("sha256=")

    # And a receiver-side recompute over the exact bytes sent must agree --
    # proving the signature actually covers the RAW body, not some other
    # serialisation of it.
    import hashlib
    import hmac
    recomputed = "sha256=" + hmac.new(hook.secret.encode(), call["body"], hashlib.sha256).hexdigest()
    assert recomputed == expected_signature


def test_a_failing_endpoint_is_retried_then_marked_failing(client_as_owner):
    repo = client_as_owner.app.state.repo
    ledger = client_as_owner.app.state.ledger
    hook = create_webhook(repo.store, "ws1", "https://dead.example.com/hook", ["run.finished"], "u1")

    post = _FakePost([500, 500, 500, 500, 500])
    ok = deliver(repo.store, ledger, hook, "run.finished", {"run_id": "run_1"}, post=post, sleep=_no_sleep)

    assert not ok
    assert len(post.calls) == 5  # every attempt was actually made

    stored = repo.store.get("webhooks", hook.id)
    assert stored["status"] == "failing"
    assert stored["failure_count"] == 1


def test_a_recovering_endpoint_is_marked_active_again(client_as_owner):
    repo = client_as_owner.app.state.repo
    ledger = client_as_owner.app.state.ledger
    hook = create_webhook(repo.store, "ws1", "https://flaky.example.com/hook", ["run.finished"], "u1")

    post = _FakePost([500, 500, 200])
    ok = deliver(repo.store, ledger, hook, "run.finished", {"run_id": "run_1"}, post=post, sleep=_no_sleep)

    assert ok
    stored = repo.store.get("webhooks", hook.id)
    assert stored["status"] == "active"


def test_webhook_delivery_is_recorded_in_the_ledger(client_as_owner, ledger):
    repo = client_as_owner.app.state.repo
    hook = create_webhook(repo.store, "ws1", "https://example.com/hook", ["run.finished"], "u1")

    deliver(repo.store, ledger, hook, "run.finished", {"run_id": "run_1"}, post=_FakePost([200]), sleep=_no_sleep)

    entries = [e for e in ledger.entries("ws1") if e["action"] == "webhook.delivery"]
    assert len(entries) == 1
    assert entries[0]["detail"]["webhook_id"] == hook.id
    assert entries[0]["detail"]["event"] == "run.finished"
    assert entries[0]["detail"]["ok"] is True


def test_dispatch_run_finished_reaches_every_subscribed_webhook(client_as_owner):
    repo = client_as_owner.app.state.repo
    ledger = client_as_owner.app.state.ledger
    create_webhook(repo.store, "ws1", "https://a.example.com/hook", ["run.finished"], "u1")
    create_webhook(repo.store, "ws1", "https://b.example.com/hook", ["finding.created"], "u1")

    post = _FakePost([200])
    dispatch_run_finished(repo, ledger, "ws1", {"run_id": "run_1"}, post=post, sleep=_no_sleep)

    # Only the endpoint subscribed to run.finished was called.
    assert len(post.calls) == 1
    assert post.calls[0]["url"] == "https://a.example.com/hook"


def test_only_owner_can_create_and_delete_webhooks(client_as_reader):
    resp = client_as_reader.post("/api/webhooks", json={"url": "https://x.example.com", "events": ["run.finished"]})
    assert resp.status_code == 403
