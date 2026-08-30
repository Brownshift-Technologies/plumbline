"""Task 9: the agent base protocol and the fakeable browser driver.

Every test here runs offline -- no browser, no network. `PlaywrightDriver`
is imported (so a broken import would fail collection for every later agent
task immediately), and its `start()` is exercised against a recording
double rather than a real Chromium; see `tests/test_playwright_live.py` for
the opt-in suite that actually launches one.

Fix round 1: the original `test_the_real_driver_disables_the_chromium_
sandbox` asserted `"chromium_sandbox=False" in inspect.getsource(...)`. The
reviewer deleted the flag from the real `launch()` call, left the
docstring's own mention of it in place, re-ran that test, and it still
passed -- a guard that survives the deletion of the thing it guards. It is
replaced below by `test_start_passes_chromium_sandbox_false_to_the_real_
launch_call`, which injects a recording double for `sync_playwright` and
asserts on the kwargs actually handed to `launch()`. Verified the way the
reviewer did: with `chromium_sandbox=False` stripped from `agents/browser.py`'s
real call, this test fails (`AssertionError`); with it restored, it passes.
See the task report for the exact commands run.
"""

import pytest

from agents.base import Agent, AgentContext, AgentResult
from agents.browser import (
    BrowserGotoError,
    FakeBrowser,
    PlaywrightDriver,
    _assign_interactive_refs,
    _parse_aria_snapshot,
)


# --- AgentResult / AgentContext / Agent --------------------------------


def test_agent_result_defaults_to_a_bare_ok_summary():
    result = AgentResult(summary="crawled 12 routes")
    assert result.summary == "crawled 12 routes"
    assert result.detail == ""
    assert result.outcome == "ok"
    assert result.data == {}


def test_agent_result_is_frozen():
    result = AgentResult(summary="x")
    with pytest.raises(Exception):
        result.summary = "y"


def test_two_agent_results_do_not_share_a_data_dict():
    # field(default_factory=dict) is what this guards: a `= {}` default
    # would make every AgentResult() with no explicit `data` alias the same
    # mutable dict.
    a, b = AgentResult(summary="a"), AgentResult(summary="b")
    a.data["k"] = "v"
    assert b.data == {}


def test_agent_context_holds_every_collaborator_by_reference():
    sentinel = object()
    ctx = AgentContext(
        workspace_id="ws1", run_id="r1", gateway=sentinel, model=sentinel,
        browser=sentinel, repo=sentinel,
    )
    assert ctx.workspace_id == "ws1"
    assert ctx.run_id == "r1"
    assert ctx.gateway is sentinel
    assert ctx.model is sentinel
    assert ctx.browser is sentinel
    assert ctx.repo is sentinel


def test_agent_context_browsers_defaults_to_an_empty_dict():
    ctx = AgentContext(
        workspace_id="ws1", run_id="r1", gateway=None, model=None,
        browser=None, repo=None,
    )
    assert ctx.browsers == {}


def test_agent_context_can_hold_named_additional_browsers_for_oracle():
    # Oracle's whole job is diffing two live environments -- it needs two
    # open drivers at once, not the one `browser` field every other agent
    # gets. See AgentContext.browsers's docstring for why this is a named
    # mapping rather than a second fixed field.
    staging, prod = object(), object()
    ctx = AgentContext(
        workspace_id="ws1", run_id="r1", gateway=None, model=None,
        browser=prod, repo=None, browsers={"staging": staging, "prod": prod},
    )
    assert ctx.browser is prod
    assert ctx.browsers["staging"] is staging
    assert ctx.browsers["prod"] is prod


def test_two_agent_contexts_do_not_share_a_browsers_dict():
    # field(default_factory=dict), same guard as AgentResult.data above.
    a = AgentContext(workspace_id="ws1", run_id="r1", gateway=None, model=None,
                      browser=None, repo=None)
    b = AgentContext(workspace_id="ws1", run_id="r1", gateway=None, model=None,
                      browser=None, repo=None)
    a.browsers["staging"] = object()
    assert b.browsers == {}


def test_agent_protocol_is_satisfied_structurally_not_nominally():
    class Cartographer:
        name = "cartographer"

        def run(self, ctx: AgentContext) -> AgentResult:
            return AgentResult(summary="ok")

    agent: Agent = Cartographer()
    assert agent.name == "cartographer"
    assert isinstance(agent.run(None), AgentResult)


# --- FakeBrowser: the exact tests the brief specifies -------------------


def test_fake_browser_returns_the_pages_it_was_seeded_with():
    b = FakeBrowser({"/": {"links": ["/cart", "/catalog"]}})
    b.goto("/")
    assert sorted(b.links()) == ["/cart", "/catalog"]


def test_fake_browser_reports_an_unknown_page_as_empty():
    b = FakeBrowser({})
    b.goto("/nope")
    assert b.links() == []


def test_the_fake_returns_the_accessibility_tree_it_was_seeded_with():
    b = FakeBrowser({"/": {"a11y": [{"ref": "e1", "role": "button", "name": "Pay"}]}})
    b.goto("/")
    assert b.a11y()[0]["name"] == "Pay"


def test_refs_are_stable_across_two_snapshots_of_one_page():
    # Trivially true for FakeBrowser -- a seeded ref is returned verbatim,
    # never recomputed (see FakeBrowser's docstring) -- kept as a basic
    # pass-through regression check. The real content-derived-ref
    # guarantee this is a stand-in for is exercised properly below, against
    # `_parse_aria_snapshot`/`_assign_interactive_refs`, with tests that
    # actually change the input between two calls.
    b = FakeBrowser({"/": {"a11y": [{"ref": "e1", "role": "button", "name": "Pay"}]}})
    b.goto("/")
    assert [e["ref"] for e in b.a11y()] == [e["ref"] for e in b.a11y()]


def test_interactive_reports_clickables_with_no_aria_role():
    b = FakeBrowser({"/": {"interactive": [{"ref": "e9", "tag": "div", "reason": "onclick"}]}})
    b.goto("/")
    assert b.interactive()[0]["tag"] == "div"


# --- FakeBrowser: the rest of what the fleet needs -----------------------


def test_a11y_entries_are_normalised_with_defaults_for_omitted_fields():
    b = FakeBrowser({"/": {"a11y": [{"ref": "e1", "role": "button", "name": "Pay"}]}})
    b.goto("/")
    entry = b.a11y()[0]
    assert entry["level"] is None
    assert entry["state"] == {}
    assert entry["disabled"] is False


def test_interactive_entries_are_normalised_too():
    b = FakeBrowser({"/": {"interactive": [{"ref": "e9"}]}})
    b.goto("/")
    entry = b.interactive()[0]
    assert entry["tag"] == ""
    assert entry["reason"] == ""


def test_a11y_before_any_goto_is_empty_not_an_error():
    b = FakeBrowser({"/": {"a11y": [{"ref": "e1", "role": "button", "name": "Pay"}]}})
    assert b.a11y() == []
    assert b.interactive() == []
    assert b.links() == []
    assert b.snapshot() == {}
    assert b.headers() == {}
    assert b.cookies() == []


def test_a_page_with_an_empty_seeded_a11y_tree_is_distinct_from_unseeded():
    # A page can genuinely have nothing accessible on it (a blank route);
    # that must read the same as a page nobody described a11y for at all.
    b = FakeBrowser({"/blank": {"a11y": []}})
    b.goto("/blank")
    assert b.a11y() == []


def test_goto_records_every_url_visited_in_order_including_repeats():
    b = FakeBrowser({"/": {"links": ["/cart"]}, "/cart": {"links": []}})
    b.goto("/")
    b.goto("/cart")
    b.goto("/")
    assert b.visited == ["/", "/cart", "/"]


def test_goto_to_a_page_seeded_with_an_error_raises_and_still_records_the_visit():
    b = FakeBrowser({"/gone": {"error": "404 not found"}})
    with pytest.raises(BrowserGotoError) as exc_info:
        b.goto("/gone")
    assert exc_info.value.url == "/gone"
    assert "404" in str(exc_info.value)
    assert b.visited == ["/gone"]


def test_run_spec_returns_the_seeded_result_for_a_known_path():
    b = FakeBrowser({}, spec_results={"specs/checkout.spec.ts": {"passed": False, "failures": 1}})
    result = b.run_spec("specs/checkout.spec.ts")
    assert result == {"passed": False, "failures": 1}


def test_run_spec_fails_closed_for_a_path_that_was_never_seeded():
    # Deliberately NOT {"passed": True} -- see FakeBrowser's docstring.
    # A Runner test that forgets to seed a path should see a loud failure,
    # not a silent, false-positive green.
    b = FakeBrowser({})
    result = b.run_spec("specs/nonexistent.spec.ts")
    assert result["passed"] is False
    assert "nonexistent.spec.ts" in result["error"]


def test_run_spec_result_is_a_copy_not_a_shared_reference():
    seeded = {"passed": True, "notes": []}
    b = FakeBrowser({}, spec_results={"s.spec.ts": seeded})
    result = b.run_spec("s.spec.ts")
    result["notes"].append("mutated")
    assert seeded["notes"] == []


def test_snapshot_includes_seeded_page_data_plus_title_and_url():
    b = FakeBrowser({"/": {"links": ["/cart"], "title": "Storefront"}})
    b.goto("/")
    snap = b.snapshot()
    assert snap["title"] == "Storefront"
    assert snap["url"] == "/"
    assert snap["links"] == ["/cart"]


def test_snapshot_falls_back_to_the_url_as_title_when_none_was_seeded():
    b = FakeBrowser({"/": {"links": []}})
    b.goto("/")
    assert b.snapshot()["title"] == "/"


def test_a11y_and_interactive_return_copies_a_caller_cannot_corrupt_the_fixture_with():
    pages = {"/": {"a11y": [{"ref": "e1", "role": "button", "name": "Pay"}]}}
    b = FakeBrowser(pages)
    b.goto("/")
    entry = b.a11y()[0]
    entry["name"] = "corrupted"
    assert b.a11y()[0]["name"] == "Pay"


# --- FakeBrowser: headers() and cookies(), for Auditor (fix round 1) -----


def test_fake_browser_returns_the_response_headers_it_was_seeded_with():
    b = FakeBrowser({"/": {"headers": {"x-frame-options": "DENY", "content-security-policy": "default-src 'self'"}}})
    b.goto("/")
    headers = b.headers()
    assert headers["x-frame-options"] == "DENY"
    assert headers["content-security-policy"] == "default-src 'self'"


def test_fake_browser_returns_the_cookies_it_was_seeded_with():
    b = FakeBrowser({"/": {"cookies": [{"name": "session", "secure": False, "httpOnly": True}]}})
    b.goto("/")
    cookie = b.cookies()[0]
    assert cookie["name"] == "session"
    assert cookie["secure"] is False


def test_headers_and_cookies_default_to_empty_for_an_unseeded_page():
    b = FakeBrowser({})
    b.goto("/nope")
    assert b.headers() == {}
    assert b.cookies() == []


def test_cookies_returned_are_a_copy_not_a_shared_reference():
    seeded = [{"name": "session", "value": "abc"}]
    b = FakeBrowser({"/": {"cookies": seeded}})
    b.goto("/")
    b.cookies()[0]["value"] = "corrupted"
    assert seeded[0]["value"] == "abc"


# --- the aria_snapshot(mode="ai") parser, offline ------------------------
#
# Captured verbatim from real `page.aria_snapshot(mode="ai")` calls against
# a real headless Chromium (see the task report), so this exercises the
# actual output shape without needing a browser in CI.


def test_parses_a_captured_aria_snapshot_into_flat_entries():
    text = (
        '- generic [active] [ref=e1]:\n'
        '  - button "Pay" [ref=e2]\n'
        '  - link "Cart" [ref=e3] [cursor=pointer]:\n'
        '    - /url: /cart\n'
        '  - generic [ref=e4] [cursor=pointer]: Fake button\n'
        '  - heading "Title" [level=1] [ref=e5]\n'
    )
    entries = _parse_aria_snapshot(text)
    assert len(entries) == 5
    assert len({e["ref"] for e in entries}) == 5  # all distinct

    pay = next(e for e in entries if e["name"] == "Pay")
    assert pay["role"] == "button"
    assert pay["level"] is None
    assert pay["disabled"] is False

    heading = next(e for e in entries if e["role"] == "heading")
    assert heading["name"] == "Title"
    assert heading["level"] == 1

    cart = next(e for e in entries if e["role"] == "link")
    assert cart["state"] == {"cursor": "pointer"}


def test_parses_disabled_and_boolean_state_attributes():
    text = (
        '- generic [active] [ref=e1]:\n'
        '  - button "Disabled Button" [disabled] [ref=e2]\n'
        '  - checkbox "Agree" [checked] [ref=e3]\n'
        '  - button "Toggle" [expanded] [ref=e4]\n'
    )
    entries = {e["name"]: e for e in _parse_aria_snapshot(text)}
    assert entries["Disabled Button"]["disabled"] is True
    assert entries["Agree"]["state"] == {"checked": True}
    assert entries["Toggle"]["state"] == {"expanded": True}


def test_lines_with_no_ref_are_skipped_not_raised_on():
    text = (
        '- generic [ref=e2]:\n'
        '  - paragraph [ref=e3]: Some paragraph text\n'
        '  - text: plain span\n'
        '  - combobox [ref=e6]:\n'
        '    - option "A" [selected]\n'
    )
    entries = _parse_aria_snapshot(text)
    assert len(entries) == 3
    assert {e["role"] for e in entries} == {"generic", "paragraph", "combobox"}


def test_parses_empty_text_to_an_empty_list():
    assert _parse_aria_snapshot("") == []


# --- ref stability under real mutation (fix round 1) ---------------------
#
# The pre-fix version trusted Playwright's own `[ref=eN]` numbering, which
# is positional: proven against a real Chromium (see the task report) that
# inserting one unrelated button above "Pay" moved Pay's ref from e2 to e6,
# with nothing about Pay itself having changed. `_parse_aria_snapshot` now
# recomputes `ref` as a hash of role+name+ancestor-identity via
# `_stable_ref`, which these tests exercise directly by feeding it two
# related snapshots and checking what should, and should not, move.


def test_a_ref_survives_an_element_being_inserted_above_it():
    before = (
        '- generic [ref=e1]:\n'
        '  - button "Pay" [ref=e2]\n'
        '  - link "Cart" [ref=e3]\n'
    )
    after = (
        '- generic [ref=e1]:\n'
        '  - button "New" [ref=e2]\n'
        '  - button "Pay" [ref=e3]\n'
        '  - link "Cart" [ref=e4]\n'
    )
    pay_before = next(e for e in _parse_aria_snapshot(before) if e["name"] == "Pay")
    pay_after = next(e for e in _parse_aria_snapshot(after) if e["name"] == "Pay")
    assert pay_before["ref"] == pay_after["ref"]

    cart_before = next(e for e in _parse_aria_snapshot(before) if e["name"] == "Cart")
    cart_after = next(e for e in _parse_aria_snapshot(after) if e["name"] == "Cart")
    assert cart_before["ref"] == cart_after["ref"]


def test_a_ref_survives_a_restyle_that_changes_classes_but_not_role_or_name():
    # A restyle shows up in aria_snapshot text (if at all) as an attribute
    # like [cursor=pointer] appearing or disappearing -- role and name are
    # untouched. ref is derived only from role/name/ancestry, so it must
    # not move even though `state` does.
    before = '- generic [ref=e1]:\n  - link "Cart" [ref=e2]\n'
    after = '- generic [ref=e1]:\n  - link "Cart" [ref=e2] [cursor=pointer]\n'
    cart_before = _parse_aria_snapshot(before)[1]
    cart_after = _parse_aria_snapshot(after)[1]
    assert cart_before["ref"] == cart_after["ref"]
    assert cart_before["state"] != cart_after["state"]


def test_a_removed_control_is_the_only_ref_that_disappears():
    before = (
        '- generic [ref=e1]:\n'
        '  - button "Pay" [ref=e2]\n'
        '  - button "Extra" [ref=e3]\n'
        '  - link "Cart" [ref=e4]\n'
    )
    after = (
        '- generic [ref=e1]:\n'
        '  - button "Pay" [ref=e2]\n'
        '  - link "Cart" [ref=e3]\n'
    )
    refs_before = {e["name"]: e["ref"] for e in _parse_aria_snapshot(before)}
    refs_after = {e["name"]: e["ref"] for e in _parse_aria_snapshot(after)}
    assert refs_before["Pay"] == refs_after["Pay"]
    assert refs_before["Cart"] == refs_after["Cart"]
    assert "Extra" in refs_before and "Extra" not in refs_after


def test_two_identical_siblings_get_distinct_stable_refs():
    text = (
        '- generic [ref=e1]:\n'
        '  - button "Add" [ref=e2]\n'
        '  - button "Add" [ref=e3]\n'
    )
    adds = [e for e in _parse_aria_snapshot(text) if e["name"] == "Add"]
    refs = [e["ref"] for e in adds]
    assert len(refs) == len(set(refs)) == 2

    # And the occurrence assignment itself is stable/deterministic across
    # two identical parses -- not a source of nondeterminism reintroduced
    # by the disambiguating counter.
    assert refs == [e["ref"] for e in _parse_aria_snapshot(text) if e["name"] == "Add"]


def test_a_renamed_control_reads_as_gone_plus_new_not_as_moved():
    before = '- generic [ref=e1]:\n  - button "Pay" [ref=e2]\n'
    after = '- generic [ref=e1]:\n  - button "Pay Now" [ref=e2]\n'
    # Scoped to the button only -- the wrapping "generic" container's own
    # identity (role "generic", no name, no ancestors) is unchanged between
    # the two texts, so it correctly keeps the same ref; that is not the
    # thing under test here.
    pay_before = next(e for e in _parse_aria_snapshot(before) if e["role"] == "button")
    pay_after = next(e for e in _parse_aria_snapshot(after) if e["role"] == "button")
    # No ref survives the rename -- "Pay"'s ref vanished and "Pay Now" got a
    # brand new one, which is the honest reading: a11y() has no way to
    # distinguish a rename from a remove-then-add, and neither should
    # whatever diffs two of these snapshots.
    assert pay_before["ref"] != pay_after["ref"]


# --- interactive() ref assignment, offline (fix round 1) ------------------
#
# `_assign_interactive_refs` takes the raw, ref-less records
# `_INTERACTIVE_JS` would return from a real page and assigns the same
# content-derived refs `_parse_aria_snapshot` does -- exercised here with
# hand-built input, no browser needed.


def _raw(tag, text, reason="onclick", ancestors=()):
    return {"tag": tag, "text": text, "reason": reason, "ancestors": list(ancestors)}


def test_interactive_ref_survives_an_element_inserted_above_it():
    before = [_raw("div", "Pay"), _raw("div", "Cart", reason="cursor:pointer")]
    after = [_raw("div", "New"), _raw("div", "Pay"), _raw("div", "Cart", reason="cursor:pointer")]

    def refs_by_text(raw):
        return {r["text"]: e["ref"] for r, e in zip(raw, _assign_interactive_refs(raw))}

    refs_before, refs_after = refs_by_text(before), refs_by_text(after)
    assert refs_before["Pay"] == refs_after["Pay"]
    assert refs_before["Cart"] == refs_after["Cart"]


def test_interactive_two_identical_siblings_get_distinct_stable_refs():
    raw = [_raw("div", "Add to cart"), _raw("div", "Add to cart")]
    entries = _assign_interactive_refs(raw)
    refs = [e["ref"] for e in entries]
    assert len(refs) == len(set(refs)) == 2


def test_interactive_a_removed_element_is_the_only_ref_that_disappears():
    before = [_raw("div", "Pay"), _raw("div", "Extra"), _raw("div", "Cart")]
    after = [_raw("div", "Pay"), _raw("div", "Cart")]
    refs_before = {e["ref"] for e in _assign_interactive_refs(before)}
    refs_after = {e["ref"] for e in _assign_interactive_refs(after)}
    assert refs_after < refs_before
    assert len(refs_before - refs_after) == 1


def test_assign_interactive_refs_fills_defaults_for_missing_keys():
    entries = _assign_interactive_refs([{}])
    assert entries[0]["tag"] == ""
    assert entries[0]["reason"] == ""
    assert entries[0]["ref"]


# --- the sandbox flag, verified by behaviour not source text (fix round 1) -


class _RecordingBrowserType:
    """Stands in for Playwright's `BrowserType` (`pw.chromium`). Records
    exactly the kwargs `PlaywrightDriver.start` hands to `launch()` --
    which is the one fact fix round 1 needs proven, since a source-text
    assertion kept passing after the real flag was deleted (see this
    module's docstring)."""

    def __init__(self):
        self.launch_kwargs: dict | None = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return _RecordingBrowser()


class _RecordingBrowser:
    def new_page(self):
        return object()

    def close(self):
        pass


class _RecordingPlaywright:
    def __init__(self):
        self.chromium = _RecordingBrowserType()

    def stop(self):
        pass


class _RecordingContextManager:
    """Stands in for `sync_playwright()`'s return value -- the thing
    `PlaywrightDriver.start` calls `.start()` on."""

    def __init__(self, playwright):
        self._playwright = playwright

    def start(self):
        return self._playwright


def test_start_passes_chromium_sandbox_false_to_the_real_launch_call():
    playwright = _RecordingPlaywright()
    driver = PlaywrightDriver()

    driver.start(playwright_factory=lambda: _RecordingContextManager(playwright))

    assert playwright.chromium.launch_kwargs is not None, "launch() was never called"
    assert playwright.chromium.launch_kwargs["chromium_sandbox"] is False, (
        "Chromium cannot launch locally or in Cloud Run with the sandbox on."
    )
    assert playwright.chromium.launch_kwargs["headless"] is True


def test_start_wires_up_the_page_from_the_launched_browser():
    playwright = _RecordingPlaywright()
    driver = PlaywrightDriver()
    driver.start(playwright_factory=lambda: _RecordingContextManager(playwright))
    assert driver._page is not None
    assert driver._pw is playwright
