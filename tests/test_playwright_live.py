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


def test_run_spec_raises_not_implemented(driver):
    with pytest.raises(NotImplementedError):
        driver.run_spec("specs/anything.spec.ts")
