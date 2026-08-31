"""Opt-in: launches a real headless Chromium and drives `PlaywrightDriver`
against it, instead of `FakeBrowser`. Skipped by default so the default
`pytest`/`pytest tests/` run never needs a browser installed -- see
`agents/browser.py`'s module docstring for why that split exists at all,
and `tests/test_oauth_live.py` for the identical pattern Task 8b used for
real OAuth exchanges.

Run explicitly, on a machine with Playwright's Chromium installed
(`playwright install chromium`):

    PLUMBLINE_LIVE_BROWSER_TESTS=1 pytest tests/test_playwright_live.py
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("PLUMBLINE_LIVE_BROWSER_TESTS"),
    reason="opt-in: set PLUMBLINE_LIVE_BROWSER_TESTS=1 and a locally installed Chromium to run",
)

_HTML = """<!doctype html><html><body>
<button id="pay">Pay</button>
<a href="/cart">Cart</a>
<div onclick="void(0)" style="cursor:pointer">Fake button</div>
<h1>Title</h1>
</body></html>"""


@pytest.fixture
def driver():
    from agents.browser import PlaywrightDriver

    d = PlaywrightDriver()
    d.start()
    try:
        yield d
    finally:
        d.stop()


def test_a11y_reports_the_real_page_s_accessibility_tree(driver):
    driver._page.set_content(_HTML)
    entries = {e["name"]: e for e in driver.a11y() if e["name"]}
    assert entries["Pay"]["role"] == "button"
    assert entries["Title"]["role"] == "heading"
    assert entries["Title"]["level"] == 1


def test_refs_are_stable_across_two_calls_on_the_same_real_page(driver):
    driver._page.set_content(_HTML)
    first = [e["ref"] for e in driver.a11y()]
    second = [e["ref"] for e in driver.a11y()]
    assert first == second


def test_interactive_finds_the_div_with_no_aria_role(driver):
    driver._page.set_content(_HTML)
    tags = [e["tag"] for e in driver.interactive()]
    assert "div" in tags


def test_links_reports_real_anchor_hrefs(driver):
    driver._page.set_content(_HTML)
    assert driver.links() == ["/cart"]


def test_goto_a_real_page_and_snapshot_it(driver):
    driver.goto("data:text/html," + _HTML.replace("\n", ""))
    snap = driver.snapshot()
    assert snap["url"].startswith("data:")


# `run_spec` used to raise NotImplementedError, and a test here asserted
# exactly that. Tier 2 implemented it, and this file never noticed: it is
# opt-in, so the stale assertion sat green-by-absence until the suite was
# actually run. Real coverage now lives in test_playwright_run_spec_live.py,
# which drives four real fixture specs through real Chromium.


# --- fix round 1 -----------------------------------------------------------


def test_a_ref_survives_a_real_dom_mutation_that_inserts_a_sibling_above_it(driver):
    # This is the exact scenario that broke Playwright's own [ref=eN]
    # numbering (proven against this same real Chromium -- see the task
    # report: "Pay"'s ref moved from e2 to e6 after inserting one unrelated
    # button above it). _parse_aria_snapshot's content-derived ref must not
    # move here.
    driver._page.set_content(
        '<button id="pay">Pay</button><a href="/cart">Cart</a>'
    )
    before = {e["name"]: e["ref"] for e in driver.a11y()}
    driver._page.set_content(
        '<button id="new">New</button><button id="pay">Pay</button><a href="/cart">Cart</a>'
    )
    after = {e["name"]: e["ref"] for e in driver.a11y()}
    assert before["Pay"] == after["Pay"]
    assert before["Cart"] == after["Cart"]


def test_interactive_refs_are_stable_across_two_calls_on_the_same_real_page(driver):
    driver._page.set_content(_HTML)
    first = [e["ref"] for e in driver.interactive()]
    second = [e["ref"] for e in driver.interactive()]
    assert first == second


def test_headers_reflects_a_real_response_header(driver):
    driver._page.route("**/*", lambda route: route.fulfill(
        status=200, headers={"X-Frame-Options": "DENY"}, body="<html></html>",
    ))
    driver.goto("https://example.test/")
    assert driver.headers().get("x-frame-options") == "DENY"


def test_cookies_reflects_a_real_set_cookie_response_header(driver):
    driver._page.route("**/*", lambda route: route.fulfill(
        status=200, headers={"Set-Cookie": "session=abc123; Path=/"}, body="<html></html>",
    ))
    driver.goto("https://example.test/")
    names = [c["name"] for c in driver.cookies()]
    assert "session" in names


def test_headers_is_empty_before_any_goto(driver):
    assert driver.headers() == {}
