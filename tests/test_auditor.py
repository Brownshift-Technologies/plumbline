"""Task 12e: Auditor.

Each fixture seeds a single route "/" with exactly the a11y/interactive/
headers/cookies/scripts content needed to trigger (or not trigger) one
finding kind, via `make_ctx(pages=...)` plus a `Route` so `graph.read`
has something to hand back.
"""

import pytest

from agents.auditor import Auditor
from app.models import Route, Workspace
from gateway.gateway import GatewayError
from tests.agent_fixtures import make_ctx

_STRONG_HEADERS = {
    "content-security-policy": "default-src 'self'",
    "strict-transport-security": "max-age=63072000",
    "x-frame-options": "DENY",
}
_STRONG_COOKIES = [{"name": "session", "value": "x", "httpOnly": True, "secure": True, "sameSite": "Strict"}]


def _ctx(page: dict):
    ctx = make_ctx(pages={"/": page})
    ctx.repo.put_route(Route(id="rt1", workspace_id="ws1", path="/", coverage_pct=50))
    return ctx


@pytest.fixture
def ctx_unnamed_button():
    return _ctx({"a11y": [{"ref": "e1", "role": "button", "name": ""}],
                 "headers": _STRONG_HEADERS, "cookies": _STRONG_COOKIES})


@pytest.fixture
def ctx_clickable_div():
    return _ctx({"interactive": [{"ref": "e1", "tag": "div", "reason": "onclick, no role"}],
                 "headers": _STRONG_HEADERS, "cookies": _STRONG_COOKIES})


@pytest.fixture
def ctx_bad_headings():
    return _ctx({
        "a11y": [
            {"ref": "e1", "role": "heading", "name": "Title", "level": 1},
            {"ref": "e2", "role": "heading", "name": "Sub-sub", "level": 3},
        ],
        "headers": _STRONG_HEADERS, "cookies": _STRONG_COOKIES,
    })


@pytest.fixture
def ctx_no_csp():
    return _ctx({"headers": {"strict-transport-security": "max-age=1", "x-frame-options": "DENY"},
                 "cookies": _STRONG_COOKIES})


@pytest.fixture
def ctx_loose_cookie():
    return _ctx({"headers": _STRONG_HEADERS,
                 "cookies": [{"name": "sid", "value": "1", "httpOnly": False, "secure": False}]})


@pytest.fixture
def ctx_leaked_key():
    return _ctx({"headers": _STRONG_HEADERS, "cookies": _STRONG_COOKIES,
                 "scripts": ["const apiKey = 'AKIAABCDEFGHIJKLMNOP';"]})


@pytest.fixture
def ctx_any():
    return _ctx({"a11y": [{"ref": "e1", "role": "button", "name": ""}],
                 "headers": _STRONG_HEADERS, "cookies": _STRONG_COOKIES})


@pytest.fixture
def ctx_clean():
    return _ctx({
        "a11y": [{"ref": "e1", "role": "button", "name": "Pay now"},
                 {"ref": "e2", "role": "heading", "name": "Checkout", "level": 1}],
        "headers": _STRONG_HEADERS, "cookies": _STRONG_COOKIES,
    })


# --- from the brief -------------------------------------------------------


def test_a_button_with_no_accessible_name_is_a_finding(ctx_unnamed_button):
    out = Auditor().run(ctx_unnamed_button)
    assert any(f["type"] == "no_accessible_name" for f in out.data["a11y"])


def test_a_clickable_div_with_no_role_is_a_finding(ctx_clickable_div):
    out = Auditor().run(ctx_clickable_div)
    assert any(f["type"] == "clickable_without_role" for f in out.data["a11y"])


def test_a_skipped_heading_level_is_a_finding(ctx_bad_headings):
    out = Auditor().run(ctx_bad_headings)
    assert any(f["type"] == "skipped_heading_level" for f in out.data["a11y"])


def test_a_missing_csp_header_is_a_finding(ctx_no_csp):
    out = Auditor().run(ctx_no_csp)
    assert any(f["type"] == "missing_csp" for f in out.data["security"])


def test_a_cookie_without_httponly_is_a_finding(ctx_loose_cookie):
    out = Auditor().run(ctx_loose_cookie)
    assert any(f["type"] == "loose_cookie" for f in out.data["security"])


def test_an_api_key_shaped_string_in_served_js_is_a_finding(ctx_leaked_key):
    out = Auditor().run(ctx_leaked_key)
    assert any(f["type"] == "leaked_secret" for f in out.data["security"])


def test_it_never_issues_a_request_carrying_a_payload(ctx_any):
    Auditor().run(ctx_any)
    # Every navigation is exactly a known route's own path -- nothing
    # appended, nothing templated in from a finding or an error string.
    assert ctx_any.browser.visited == ["/"]
    for url in ctx_any.browser.visited:
        assert "'" not in url and "<" not in url and "=" not in url


def test_a_clean_page_produces_no_findings_not_a_low_score(ctx_clean):
    out = Auditor().run(ctx_clean)
    assert out.data["a11y"] == [] and out.data["security"] == []
    assert out.data["score"] == {"a11y": 100, "security": 100}


# --- extra: judgement calls and fleet-wide rules ---------------------------


def test_a_csp_with_unsafe_inline_is_flagged_as_weak():
    ctx = _ctx({"headers": {**_STRONG_HEADERS, "content-security-policy": "default-src 'self' 'unsafe-inline'"},
                "cookies": _STRONG_COOKIES})
    out = Auditor().run(ctx)
    assert any(f["type"] == "weak_csp" for f in out.data["security"])


def test_a_page_served_with_no_headers_at_all_flags_every_missing_check():
    ctx = _ctx({"headers": {}, "cookies": []})
    out = Auditor().run(ctx)
    kinds = {f["type"] for f in out.data["security"]}
    assert {"missing_csp", "missing_hsts", "missing_x_frame_options"} <= kinds


def test_a_denied_read_surfaces_as_an_error_not_a_silent_skip(ctx_any):
    ctx_any.repo.put_workspace(Workspace(
        id="ws1", name="Acme", repo="acme/storefront",
        gate_rules=({"tool": "browser.read", "pattern": "*", "effect": "deny"},)))
    with pytest.raises(GatewayError):
        Auditor().run(ctx_any)


def test_every_finding_names_the_exact_element_or_header(ctx_unnamed_button):
    out = Auditor().run(ctx_unnamed_button)
    for f in out.data["a11y"]:
        assert f["element"] and f["message"] != "improve accessibility"
