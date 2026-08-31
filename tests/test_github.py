"""Task 14g: `app.github.GitHubApp` -- minimum permissions, token
caching/refresh, the never-write-to-main discipline of `open_pull_request`,
and the constant-time webhook signature check."""

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github import PERMISSIONS, GitHubApp, verify_webhook_signature

# Generated once for the whole module -- RSA keygen is the one genuinely
# slow operation in this file (tens of milliseconds), and every test here
# needs the SAME key only to sign/verify a JWT shape, never to talk to a
# real GitHub App.
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_KEY_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def test_the_app_requests_only_the_minimum_permissions():
    assert PERMISSIONS == {"contents": "read", "pull_requests": "write", "checks": "write"}


def test_it_never_holds_contents_write_on_the_default_branch():
    assert PERMISSIONS["contents"] == "read"


def test_an_installation_token_is_cached_and_refreshed_on_expiry():
    calls = []

    def request(method, url, *, token=None, body=None, accept=None):
        calls.append((method, url, body))
        return {"token": f"ghs_fake{len(calls)}", "expires_at": "2020-01-01T00:10:00Z"}

    clock = [1577836800.0]  # 2020-01-01T00:00:00Z
    app = GitHubApp("12345", _PRIVATE_KEY_PEM, request=request, now=lambda: clock[0])

    first = app.installation_token("inst_1")
    second = app.installation_token("inst_1")  # still fresh -- no second mint
    assert first == second
    assert len(calls) == 1
    assert calls[0][2]["permissions"] == PERMISSIONS

    clock[0] += 700  # past the 00:10:00Z expiry above
    third = app.installation_token("inst_1")
    assert third != first
    assert len(calls) == 2


def test_a_token_never_reaches_a_ledger_entry(ledger):
    def request(method, url, *, token=None, body=None, accept=None):
        return {"token": "ghs_realISTICtoken1234567890abcdef", "expires_at": "2099-01-01T00:00:00Z"}

    app = GitHubApp("12345", _PRIVATE_KEY_PEM, request=request)
    token = app.installation_token("inst_1")
    assert token.startswith("ghs_")

    ledger.append("ws1", "surgeon", "pr.open", {"note": f"minted token {token} for the PR"})
    import json
    serialised = json.dumps(ledger.entries("ws1"))
    assert token not in serialised
    assert "[GITHUB_TOKEN]" in serialised


def test_opening_a_pull_request_targets_a_new_branch_not_the_default():
    calls = []

    def request(method, url, *, token=None, body=None, accept=None):
        calls.append((method, url, body))
        if "git/ref/heads/main" in url:
            return {"object": {"sha": "base-sha-123"}}
        if url.endswith("/git/refs"):
            return {"ref": body["ref"]}
        if "/contents/" in url:
            return {"content": {}}
        if url.endswith("/pulls"):
            return {"html_url": "https://github.com/acme/storefront/pull/42"}
        return {"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}

    app = GitHubApp("12345", _PRIVATE_KEY_PEM, request=request)
    app.bind("acme/storefront", "inst_1")

    url = app.open_pull_request(
        "acme/storefront", "plumbline/fix-checkout", "Fix checkout", "body",
        {"src/checkout/payment-client.ts": "// fixed"}, default_branch="main",
    )
    assert url == "https://github.com/acme/storefront/pull/42"

    ref_creation = next(c for c in calls if c[1].endswith("/git/refs"))
    assert ref_creation[2]["ref"] == "refs/heads/plumbline/fix-checkout"
    assert ref_creation[2]["sha"] == "base-sha-123"

    content_write = next(c for c in calls if "/contents/" in c[1])
    assert content_write[2]["branch"] == "plumbline/fix-checkout"
    assert content_write[2]["branch"] != "main"

    # Nothing in this whole call sequence ever wrote to refs/heads/main.
    assert not any(c[0] in ("POST", "PUT", "PATCH") and "heads/main" in c[1] for c in calls)


def test_the_signature_compare_is_constant_time(monkeypatch):
    calls = []
    import hmac as hmac_module
    real_compare = hmac_module.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr("app.github.hmac.compare_digest", spy)
    body = b'{"ok":true}'
    good = "sha256=" + __import__("hmac").new(b"secret", body, __import__("hashlib").sha256).hexdigest()
    verify_webhook_signature("secret", body, good)
    assert len(calls) == 1  # the comparison went through hmac.compare_digest, not `==`


def test_a_webhook_signature_verifies_when_correct():
    body = b'{"ok":true}'
    good = "sha256=" + __import__("hmac").new(b"secret", body, __import__("hashlib").sha256).hexdigest()
    assert verify_webhook_signature("secret", body, good) is True


def test_a_webhook_signature_fails_when_wrong():
    body = b'{"ok":true}'
    assert verify_webhook_signature("secret", body, "sha256=deadbeef") is False


def test_a_missing_webhook_signature_fails_closed():
    assert verify_webhook_signature("secret", b"{}", None) is False
    assert verify_webhook_signature("secret", b"{}", "not-even-prefixed") is False
