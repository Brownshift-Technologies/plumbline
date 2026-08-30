"""Task 9: the agent base protocol and the fakeable browser driver.

Every test here runs offline -- no browser, no network. `PlaywrightDriver`
is imported (so a broken import would fail collection for every later agent
task immediately) but never started; see `tests/test_playwright_live.py`
for the opt-in suite that actually launches Chromium.
"""

import inspect

import pytest

from agents.base import Agent, AgentContext, AgentResult
from agents.browser import BrowserGotoError, FakeBrowser, PlaywrightDriver, _parse_aria_snapshot


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


def test_the_real_driver_disables_the_chromium_sandbox():
    src = inspect.getsource(PlaywrightDriver.start)
    assert "chromium_sandbox=False" in src, (
        "Chromium cannot launch locally or in Cloud Run with the sandbox on."
    )


def test_the_fake_returns_the_accessibility_tree_it_was_seeded_with():
    b = FakeBrowser({"/": {"a11y": [{"ref": "e1", "role": "button", "name": "Pay"}]}})
    b.goto("/")
    assert b.a11y()[0]["name"] == "Pay"


def test_refs_are_stable_across_two_snapshots_of_one_page():
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


# --- the aria_snapshot(mode="ai") parser, offline ------------------------
#
# Captured verbatim from a real `page.aria_snapshot(mode="ai")` call
# against a real headless Chromium (see the task report), so this exercises
# the actual output shape without needing a browser in CI.


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
    refs = [e["ref"] for e in entries]
    assert refs == ["e1", "e2", "e3", "e4", "e5"]

    pay = next(e for e in entries if e["ref"] == "e2")
    assert pay == {"ref": "e2", "role": "button", "name": "Pay", "level": None, "state": {}, "disabled": False}

    heading = next(e for e in entries if e["ref"] == "e5")
    assert heading["role"] == "heading"
    assert heading["level"] == 1

    cart = next(e for e in entries if e["ref"] == "e3")
    assert cart["state"] == {"cursor": "pointer"}


def test_parses_disabled_and_boolean_state_attributes():
    text = (
        '- generic [active] [ref=e1]:\n'
        '  - button "Disabled Button" [disabled] [ref=e2]\n'
        '  - checkbox "Agree" [checked] [ref=e3]\n'
        '  - button "Toggle" [expanded] [ref=e4]\n'
    )
    entries = {e["ref"]: e for e in _parse_aria_snapshot(text)}
    assert entries["e2"]["disabled"] is True
    assert entries["e3"]["state"] == {"checked": True}
    assert entries["e4"]["state"] == {"expanded": True}


def test_lines_with_no_ref_are_skipped_not_raised_on():
    text = (
        '- generic [ref=e2]:\n'
        '  - paragraph [ref=e3]: Some paragraph text\n'
        '  - text: plain span\n'
        '  - combobox [ref=e6]:\n'
        '    - option "A" [selected]\n'
    )
    entries = _parse_aria_snapshot(text)
    refs = [e["ref"] for e in entries]
    assert refs == ["e2", "e3", "e6"]


def test_parses_empty_text_to_an_empty_list():
    assert _parse_aria_snapshot("") == []
