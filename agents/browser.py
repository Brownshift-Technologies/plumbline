"""The browser every agent drives, and the fake that stands in for it.

`snapshot()` returning a title and a URL is not enough for the fleet this
module serves. The reason is Healer's entire job: a locator anchored to a
CSS class or a DOM path breaks on every restyle, so a suite anchored that
way rots faster than agents can repair it. An accessibility tree survives a
restyle, because it describes what an element *is* to a user -- a button
named "Pay", a heading at level 1 -- rather than where it happens to sit in
the markup today. That is why Healer's contract already demands
`getByRole`/`getByLabel`/`getByTestId`: `a11y()` is what supplies the
material those locators are built from, and `interactive()` is what
supplies the material that *cannot* build one at all (a clickable `<div>`
carries no ARIA role, so no `getByRole` call can ever reach it -- Healer's
answer there has to be something else, or a finding of its own).

Two implementations live here, deliberately asymmetric in how much they are
exercised:

- `FakeBrowser` is what every agent test in this codebase runs against,
  including the default `pytest` run. It is not a throwaway stub -- eleven
  agent tasks assert against it, so its seeding surface (`links`, `a11y`,
  `interactive`, `spec_results`, a per-page `error`) has to cover what all
  eleven need, not just what Task 9's own tests happen to check.
- `PlaywrightDriver` is the real thing, launched only by code paths gated
  behind an environment variable (see `tests/test_playwright_live.py`),
  exactly as Task 8b gated real OAuth exchanges behind
  `PLUMBLINE_LIVE_OAUTH_TESTS`. A suite that needs a browser to pass is a
  suite that breaks in CI the day the image doesn't ship one; the default
  suite here never launches Chromium.
"""

import copy
import re
from typing import Protocol


class BrowserDriver(Protocol):
    def goto(self, url: str) -> None: ...
    def snapshot(self) -> dict: ...
    def links(self) -> list[str]: ...
    def run_spec(self, path: str) -> dict: ...

    def a11y(self) -> list[dict]:
        """Flattened accessibility tree: `{ref, role, name, level, state,
        disabled}` per element, in document order.

        `ref` is a stable handle ("e1", "e2", ...) so Cartographer can diff
        two snapshots of the same route across runs and say which controls
        appeared, vanished, or were renamed -- the signal Healer needs to
        tell "the button moved" from "the button is gone". Stability here
        means: calling `a11y()` twice against the *same* loaded page returns
        the same ref for the same element, not that a ref survives
        navigation or a real page mutation -- neither driver promises that,
        and no caller in this codebase needs it to.
        """
        ...

    def interactive(self) -> list[dict]:
        """Elements that behave clickable but carry no ARIA role:
        `{ref, tag, reason}` per element.

        A `<div>` with an `onclick` and a pointer cursor is invisible to
        `a11y()` -- the accessibility tree has nothing to say about it,
        because as far as a screen reader is concerned it isn't a control at
        all -- but a user can still click it and a bug can still hide behind
        it. Cartographer counts these as surface to cover; they are also,
        independently, an accessibility finding worth surfacing on their
        own (an interactive element with no accessible name or role fails
        WCAG 4.1.2 outright).
        """
        ...


class BrowserGotoError(Exception):
    """Raised by `FakeBrowser.goto` for a page seeded with an `"error"` key,
    and the shape a real 404 or a real Playwright navigation timeout both
    collapse to by the time an agent's `run()` sees them.

    `PlaywrightDriver.goto` does not catch or wrap `page.goto`'s own
    exceptions (`playwright.sync_api.Error`/`TimeoutError`) into this type --
    it lets them propagate as themselves, so a caller that wants the real
    driver's failure detail still gets it. This class exists so a *test*,
    running against `FakeBrowser` with no browser installed, can exercise
    "the agent under test handles a navigation failure" without needing to
    know or care which of Playwright's own exception types a real 404 versus
    a real timeout happens to raise -- an agent's `run()` is expected to
    catch broadly (`except Exception`) around a `goto` it does not control
    the target of, exactly the way it must already catch broadly around
    `ctx.gateway.call`'s `fn()` failing (see `gateway/gateway.py`'s `try`
    around `fn()`). Distinguishing 404 from timeout is not this layer's job:
    the only fact an agent's crawl or run loop actually branches on is
    "did the navigation succeed", the same fact `goto` not raising already
    tells it.
    """

    def __init__(self, url: str, reason: str):
        super().__init__(f"goto {url!r} failed: {reason}")
        self.url = url
        self.reason = reason


_A11Y_DEFAULTS = {"name": "", "level": None, "state": {}, "disabled": False}
_INTERACTIVE_DEFAULTS = {"tag": "", "reason": ""}


def _normalise(entry: dict, defaults: dict) -> dict:
    """Fill in the fields a seeded a11y/interactive entry left out, so every
    entry `FakeBrowser` hands back carries the full shape regardless of how
    little the test that seeded it bothered to specify.

    A `ctx_*` fixture across the eleven agent tasks that wants to test one
    thing -- "there is a button named Pay" -- should be able to write
    `{"ref": "e1", "role": "button", "name": "Pay"}` and nothing else; an
    agent's `run()` reading `entry["disabled"]` or `entry["state"]` off that
    entry should not have to `.get(..., default)` defensively just because
    the test that seeded it did not care about those fields. Filling
    defaults here, once, is what keeps that convenience from becoming
    eleven copies of the same `.get()` boilerplate spread across the fleet's
    own agent code instead. Copied (not aliased) so a caller mutating the
    dict handed back cannot corrupt the fixture data a later call in the
    same test would still read -- the same discipline `core/fakes.py`'s
    `FakeSnapshot.to_dict` uses for exactly this reason.
    """
    merged = {**defaults, **copy.deepcopy(entry)}
    return merged


class FakeBrowser:
    """The browser every agent test drives instead of a real Chromium.

    `pages` maps a URL to a dict describing what that page has:
    `{"links": [...], "a11y": [...], "interactive": [...], "error": "..."}`
    -- every key optional; a URL not seeded at all, or a key not present on
    a page that is, reads as empty (see `test_fake_browser_reports_an_
    unknown_page_as_empty` and the goto-before-any-page behaviour below).
    That "unseeded is empty, not an error" choice is deliberate and matches
    `goto`ing to a page that genuinely has nothing on it (a Cartographer
    fixture describing a blank route it discovered but hasn't crawled the
    contents of yet) -- an empty page is a real, common state to model, not
    a mistake a test made.

    A page's `"error"` key is the one exception to "missing means empty": a
    seeded `{"error": "..."}` makes `goto` raise `BrowserGotoError` instead
    of navigating, standing in for a real 404 or a real navigation timeout
    so a Cartographer or Runner test can assert its agent handles a broken
    link without needing a real broken server to hit. The attempted URL is
    still recorded in `visited` before the error is raised -- attempting a
    navigation that then fails is still an attempt, the same way a real
    browser's history gains an entry for a page that 404s.

    `visited` is the full, in-order list of every URL passed to `goto`,
    error or not, duplicates included. This is what lets an agent test
    assert on crawl order (`assert b.visited == ["/", "/cart", "/catalog"]`)
    rather than only on the final page reached -- Cartographer's whole job
    is the order and completeness of a crawl, not just where it ends up.

    `spec_results` is keyed by spec path, not by page URL -- a spec run is
    not scoped to "the current page" the way `links`/`a11y`/`interactive`
    are, so it lives in its own dict rather than nested under `pages`. A
    path with no seeded result fails CLOSED: `{"passed": False, "error":
    "no result seeded for <path>"}`, not a silent `{"passed": True}`. This
    deliberately departs from the simplest possible fake (return `True` for
    anything unseeded) because Runner's entire job is reporting whether a
    spec actually passed -- a fake that hands back a false-positive "passed"
    for a spec no test ever configured would let a bug in Runner's own path
    handling (the wrong path built, a typo, a spec dropped off the run)
    sail through as a green result instead of a loud, obvious failure in
    the test that exercises it. Every other fail-closed default in this
    codebase (`gateway/policy.py`'s missing-target gate, `gateway/gateway.py`'s
    missing-target check) makes the same call for the same reason: an
    ambiguous "we don't know" must never collapse to "allowed"/"passed".
    """

    def __init__(self, pages: dict, spec_results: dict | None = None):
        self._pages = pages
        self._spec_results = spec_results or {}
        self._at: str | None = None
        self.visited: list[str] = []

    def goto(self, url: str) -> None:
        self.visited.append(url)
        page = self._pages.get(url, {})
        if "error" in page:
            self._at = url
            raise BrowserGotoError(url, page["error"])
        self._at = url

    def _page(self) -> dict:
        if self._at is None:
            return {}
        return self._pages.get(self._at, {})

    def snapshot(self) -> dict:
        """The whole seeded page description for the current URL, plus
        `title`/`url` so a caller that only wants those two (matching
        `PlaywrightDriver.snapshot`'s narrower real-world return) can read
        them without knowing which driver it's holding. `title` falls back
        to the current URL when a test didn't bother seeding one -- a
        fixture built only to exercise `links()`/`a11y()` should not also
        have to invent a page title nobody asked about.
        """
        if self._at is None:
            return {}
        page = self._page()
        return {"title": page.get("title", self._at), "url": self._at, **page}

    def links(self) -> list[str]:
        return list(self._page().get("links", []))

    def a11y(self) -> list[dict]:
        return [_normalise(e, _A11Y_DEFAULTS) for e in self._page().get("a11y", [])]

    def interactive(self) -> list[dict]:
        return [_normalise(e, _INTERACTIVE_DEFAULTS) for e in self._page().get("interactive", [])]

    def run_spec(self, path: str) -> dict:
        if path in self._spec_results:
            return copy.deepcopy(self._spec_results[path])
        return {"passed": False, "error": f"no result seeded for {path!r}"}


# --- the real driver ---------------------------------------------------
#
# Everything below launches an actual Chromium and is exercised only by
# `tests/test_playwright_live.py`, gated behind `PLUMBLINE_LIVE_BROWSER_
# TESTS` the same way Task 8b gated real OAuth. The default suite imports
# this class (so `from agents.browser import PlaywrightDriver` never fails
# collection) but never instantiates or starts it.

# Matches one line of `page.aria_snapshot(mode="ai")`'s output, e.g.:
#   heading "Title" [level=1] [ref=e5]
#   generic [active] [ref=e1]:
#   button "Disabled Button" [disabled] [ref=e2]
# Lines with no `[ref=...]` at all (a nested `- /url: /cart` detail line, a
# bare `- text: ...` run) do not match and are skipped -- see `a11y()`'s
# docstring for why "no ref" means "not part of the flattened tree".
_ARIA_LINE = re.compile(
    r'^-\s+(?P<role>[a-zA-Z][\w-]*)'
    r'(\s+"(?P<name>(?:[^"\\]|\\.)*)")?'
    r'(?P<attrs>(?:\s*\[[^\]]*\])*)'
    r'\s*:?\s*(?P<rest>.*)$'
)
_ATTR = re.compile(r"\[([^\]]*)\]")


def _parse_aria_snapshot(text: str) -> list[dict]:
    """Flatten Playwright's `page.aria_snapshot(mode="ai")` YAML-ish output
    into the same `{ref, role, name, level, state, disabled}` shape
    `FakeBrowser.a11y()` hands back, so agent code never has to know which
    driver produced the tree it's reading.

    Pure string parsing, no browser needed -- which is exactly why this is
    tested directly, offline, in the default suite (`tests/test_agent_
    base.py`) with real captured `aria_snapshot` output, rather than only
    indirectly through a live-browser test that would never run in CI.
    Indentation is intentionally not used to build parent/child links here:
    every element with a `[ref=...]` becomes one flat entry regardless of
    nesting depth, matching `a11y()`'s own flattened contract (Cartographer
    diffs a flat list of refs, not a tree).
    """
    entries = []
    for line in text.splitlines():
        match = _ARIA_LINE.match(line.strip())
        if not match:
            continue
        attrs = {}
        for raw in _ATTR.findall(match.group("attrs") or ""):
            if "=" in raw:
                key, _, value = raw.partition("=")
                attrs[key] = value
            else:
                attrs[raw] = True
        if "ref" not in attrs:
            continue
        level = attrs.pop("level", None)
        disabled = bool(attrs.pop("disabled", False))
        ref = attrs.pop("ref")
        state = {k: v for k, v in attrs.items()}
        entries.append(
            {
                "ref": ref,
                "role": match.group("role"),
                "name": match.group("name") or "",
                "level": int(level) if level is not None else None,
                "state": state,
                "disabled": disabled,
            }
        )
    return entries


# Elements a real ARIA snapshot already gives a semantic role to, and so
# should never also be reported by `interactive()` -- an `<a href>` or a
# `<button>` already has a role; the whole point of `interactive()` is the
# elements that DON'T (see the module docstring). `page.aria_snapshot`
# assigns every plain `<div>`/`<span>` the structural role "generic" or
# folds it to a bare "text" run regardless of whether it is clickable, so
# "has no real role" is not the same test as "aria_snapshot calls it
# generic" -- this JS walk is a second, independent signal (onclick/pointer
# cursor) rather than a filter over the first.
_INTERACTIVE_JS = """
() => {
    const semanticTags = new Set([
        "A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "OPTION",
    ]);
    const out = [];
    let i = 0;
    document.querySelectorAll("*").forEach((el) => {
        if (el.hasAttribute("role") || semanticTags.has(el.tagName)) return;
        const hasOnclick = el.hasAttribute("onclick") || typeof el.onclick === "function";
        const pointerCursor = window.getComputedStyle(el).cursor === "pointer";
        if (!hasOnclick && !pointerCursor) return;
        out.push({
            ref: "i" + (i++),
            tag: el.tagName.toLowerCase(),
            reason: hasOnclick ? "onclick" : "cursor:pointer",
        });
    });
    return out;
}
"""


class PlaywrightDriver:
    """The real driver: an actual headless Chromium, via Playwright's sync
    API. Every agent task builds against `FakeBrowser` instead; this class
    exists so the platform has something to run in Cloud Run once an agent
    is trusted enough to touch a real target site, and so a developer can
    opt into `tests/test_playwright_live.py` to check this class against a
    real page when Playwright's own APIs shift under it.
    """

    def __init__(self):
        self._pw = self._browser = self._page = None

    def start(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # chromium_sandbox=False is not an optimisation -- it is required.
        # Chromium's own sandbox needs a user namespace it cannot create
        # under AppArmor's unprivileged-user-namespace restriction (the
        # default on current Ubuntu-based images) or inside a Cloud Run
        # container (which runs as a non-root, unprivileged user with no
        # CAP_SYS_ADMIN). Without this flag, `launch()` fails outright in
        # both places this platform actually runs -- a developer's sandboxed
        # machine and Cloud Run itself -- not just in some hypothetical
        # locked-down environment. `test_the_real_driver_disables_the_
        # chromium_sandbox` in `tests/test_agent_base.py` asserts this stays
        # true via `inspect.getsource` rather than by launching a browser,
        # so the check runs in the default, offline suite -- the whole
        # point of a regression guard is that it runs every time, and
        # "every time" here has to mean CI, not just a developer's machine
        # that happens to have Chromium installed.
        self._browser = self._pw.chromium.launch(headless=True, chromium_sandbox=False)
        self._page = self._browser.new_page()

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def snapshot(self) -> dict:
        return {"title": self._page.title(), "url": self._page.url}

    def links(self) -> list[str]:
        return self._page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )

    def a11y(self) -> list[dict]:
        return _parse_aria_snapshot(self._page.aria_snapshot(mode="ai"))

    def interactive(self) -> list[dict]:
        return self._page.evaluate(_INTERACTIVE_JS)

    def run_spec(self, path: str) -> dict:
        # The driver drives one page; Task 11 (Runner) is the thing that
        # knows what a "spec" even is (a recorded flow, a Playwright test
        # file, an assertion list) and orchestrates the driver through it.
        # Implementing spec-running here would mean this class either
        # imports Runner's format or invents its own, both of which make
        # the driver -- the one thing every agent shares -- carry knowledge
        # that belongs to a single agent.
        raise NotImplementedError("Runner drives specs, not the driver")

    def stop(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
