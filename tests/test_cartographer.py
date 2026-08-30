"""Task 10: Cartographer.

Built on `tests.agent_fixtures.make_ctx` (see that module's docstring),
not a bespoke `_ctx` helper -- every later agent test in this codebase
shares that one factory, and Cartographer's own tests should be the first
example of that convention, not an exception to it.
"""

import pytest

from agents.cartographer import Cartographer
from app.models import Workspace
from gateway.gateway import GatewayError
from tests.agent_fixtures import make_ctx


def test_it_finds_every_reachable_route():
    ctx = make_ctx(pages={
        "/": {"links": ["/cart", "/catalog"]},
        "/cart": {"links": ["/checkout"]},
        "/catalog": {"links": []},
        "/checkout": {"links": []},
    })
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/cart", "/catalog", "/checkout"]


def test_it_does_not_loop_on_a_cycle():
    ctx = make_ctx(pages={"/": {"links": ["/a"]}, "/a": {"links": ["/"]}})
    assert Cartographer().run(ctx).data["routes"] == ["/", "/a"]


def test_it_writes_the_routes_it_found():
    ctx = make_ctx(pages={"/": {"links": ["/cart"]}, "/cart": {"links": []}})
    Cartographer().run(ctx)
    assert {r.path for r in ctx.repo.routes_for_workspace("ws1")} == {"/", "/cart"}


def test_it_writes_the_whole_surface_in_one_gateway_call(monkeypatch):
    ctx = make_ctx(pages={"/": {"links": ["/a", "/b"]}, "/a": {"links": []}, "/b": {"links": []}})
    calls = []
    real = ctx.gateway.call
    monkeypatch.setattr(ctx.gateway, "call",
                         lambda *a, **k: (calls.append(a[2]), real(*a, **k))[1])
    Cartographer().run(ctx)
    assert calls.count("graph.write") == 1, (
        "one ledger entry per route would serialise N transactions on one head "
        "document and bury the ledger in noise")


def test_it_counts_routes_it_had_not_seen_before():
    ctx = make_ctx(pages={"/": {"links": ["/cart"]}, "/cart": {"links": []}})
    assert Cartographer().run(ctx).data["new"] == 2
    assert Cartographer().run(ctx).data["new"] == 0


def test_it_captures_each_routes_accessible_elements():
    # Author (Task 11a) has no browser.read scope at all -- see
    # gateway/policy.py's SCOPES -- so this is the ONLY chance the graph
    # ever gets to record what is actually on a page. If Cartographer drops
    # this, Author's prompts go in blind.
    ctx = make_ctx(pages={"/": {
        "links": [],
        "a11y": [{"ref": "e1", "role": "button", "name": "Pay"}],
    }})
    Cartographer().run(ctx)
    route = ctx.repo.routes_for_workspace("ws1")[0]
    assert route.elements == (("e1", "button", "Pay"),)


# --- point 7: what a real app does to a crawler --------------------------


def test_a_route_that_redirects_to_one_already_seen_is_not_double_counted():
    # "/promo" 302s to "/catalog", which is also linked directly from "/"
    # and gets visited first. FakeBrowser models the redirect via a page's
    # own "url" key overriding snapshot()'s reported landing URL (see
    # BrowserDriver.a11y's docstring on refs and Cartographer's own module
    # docstring, point 1).
    ctx = make_ctx(pages={
        "/": {"links": ["/catalog", "/promo"]},
        "/catalog": {"links": []},
        "/promo": {"links": [], "url": "/catalog"},
    })
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/catalog"]
    assert "/promo" not in out.data["routes"]


def test_a_redirect_to_an_unlinked_page_still_discovers_that_page():
    # Here nothing links to "/catalog" directly -- the ONLY way to it is
    # through "/promo"'s redirect. A crawler that only special-cased
    # already-seen redirect targets would miss this page entirely.
    ctx = make_ctx(pages={
        "/": {"links": ["/promo"]},
        "/promo": {"links": [], "url": "/catalog"},
        "/catalog": {"links": []},
    })
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/catalog"]


def test_an_external_link_is_not_crawled_or_counted_as_a_route():
    ctx = make_ctx(pages={"/": {"links": ["https://stripe.com/checkout", "/cart"]},
                           "/cart": {"links": []}})
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/cart"]
    assert "https://stripe.com/checkout" not in ctx.browser.visited


def test_a_fragment_link_is_treated_as_the_same_route():
    # "#reviews" is an in-page anchor jump, never a fresh navigation -- it
    # must collapse onto "/catalog", not become a second route.
    ctx = make_ctx(pages={"/": {"links": ["/catalog", "/catalog#reviews"]},
                           "/catalog": {"links": []}})
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/catalog"]
    assert ctx.browser.visited.count("/catalog") == 1


def test_a_query_string_is_kept_as_a_distinct_route():
    # Unlike a fragment, a query string can genuinely change what renders
    # (a filtered catalog view) -- collapsing it away would silently drop
    # real, testable surface from the map.
    ctx = make_ctx(pages={
        "/": {"links": ["/catalog?category=shoes", "/catalog?category=hats"]},
        "/catalog?category=shoes": {"links": []},
        "/catalog?category=hats": {"links": []},
    })
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/catalog?category=hats", "/catalog?category=shoes"]


def test_a_login_walled_route_is_skipped_not_fatal():
    # Most links 401 behind a login wall this crawl has no session for.
    # One broken/unauthorised page must cost exactly that page, not the
    # rest of the crawl still queued behind it.
    ctx = make_ctx(pages={
        "/": {"links": ["/admin", "/public"]},
        "/admin": {"error": "401 Unauthorized"},
        "/public": {"links": []},
    })
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/public"]
    assert out.data["unreachable"] == ["/admin"]


def test_a_policy_denial_is_not_swallowed_as_an_unreachable_page():
    # Distinct from a 401 on the target SITE: this is Plumbline's own
    # policy refusing the call outright, and must surface loudly rather
    # than being folded into "just another broken link".
    ctx = make_ctx(pages={"/": {"links": []}})
    ctx.repo.put_workspace(Workspace(
        id="ws1", name="Acme", repo="acme/storefront",
        gate_rules=({"tool": "browser.read", "pattern": "*", "effect": "deny"},)))
    with pytest.raises(GatewayError):
        Cartographer().run(ctx)


# --- fix round 1 --------------------------------------------------------


def test_the_whole_crawl_is_one_gateway_call_not_one_per_page(monkeypatch):
    ctx = make_ctx(pages={
        "/": {"links": ["/a", "/b"]}, "/a": {"links": ["/c"]},
        "/b": {"links": []}, "/c": {"links": []},
    })
    calls = []
    real = ctx.gateway.call
    monkeypatch.setattr(ctx.gateway, "call",
                         lambda *a, **k: (calls.append(a[2]), real(*a, **k))[1])
    Cartographer().run(ctx)
    assert calls.count("browser.read") == 1, (
        "one ledger entry per page crawled would serialise N transactions on "
        "one head document -- the same problem this module's own write_all "
        "argues against, applied to reads")


def test_a_protocol_relative_url_is_not_treated_as_internal():
    # "//evil-cdn.example.com/x" starts with "/" -- a naive prefix check
    # reads that as internal and walks the crawler off the customer's site
    # onto an attacker-chosen host.
    ctx = make_ctx(pages={"/": {"links": ["//evil-cdn.example.com/x", "/cart"]},
                           "/cart": {"links": []}})
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/cart"]
    assert not any(v.startswith("//") for v in ctx.browser.visited)


def test_a_javascript_href_is_not_crawled():
    ctx = make_ctx(pages={"/": {"links": ["javascript:alert(1)", "/cart"]},
                           "/cart": {"links": []}})
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/cart"]


def test_a_mailto_href_is_not_crawled():
    ctx = make_ctx(pages={"/": {"links": ["mailto:sales@acme.com", "/cart"]},
                           "/cart": {"links": []}})
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/cart"]


def test_a_tel_href_is_not_crawled():
    ctx = make_ctx(pages={"/": {"links": ["tel:+14155550132", "/cart"]},
                           "/cart": {"links": []}})
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/cart"]


def test_a_data_uri_href_is_not_crawled():
    ctx = make_ctx(pages={"/": {"links": ["data:text/html,<h1>hi</h1>", "/cart"]},
                           "/cart": {"links": []}})
    out = Cartographer().run(ctx)
    assert out.data["routes"] == ["/", "/cart"]
