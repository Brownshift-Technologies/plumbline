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
answer there has to be something else, or a finding of its own). `headers()`
and `cookies()` exist for Auditor, whose mandate (CSP, HSTS,
`X-Frame-Options`, cookie flags) lives entirely in a `Response` object
neither `a11y()` nor `snapshot()` ever touches.

Two implementations live here, deliberately asymmetric in how much they are
exercised:

- `FakeBrowser` is what every agent test in this codebase runs against,
  including the default `pytest` run. It is not a throwaway stub -- eleven
  agent tasks assert against it, so its seeding surface (`links`, `a11y`,
  `interactive`, `headers`, `cookies`, `spec_results`, a per-page `error`)
  has to cover what all eleven need, not just what Task 9's own tests
  happen to check.
- `PlaywrightDriver` is the real thing, launched only by code paths gated
  behind an environment variable (see `tests/test_playwright_live.py`),
  exactly as Task 8b gated real OAuth exchanges behind
  `PLUMBLINE_LIVE_OAUTH_TESTS`. A suite that needs a browser to pass is a
  suite that breaks in CI the day the image doesn't ship one; the default
  suite here never launches Chromium. Its `start()` still takes an
  injectable `playwright_factory` so the ONE fact that cannot be allowed to
  regress silently -- the sandbox stays disabled -- is checked by asserting
  on the kwargs actually handed to `launch()`, not by scanning source text
  for a string that survives being deleted from the call it once guarded
  (see `tests/test_agent_base.py`'s fix-round-1 section for how that hole
  was found and closed).

Fix round 1 also replaced how both `a11y()` and `interactive()` compute
`ref`. The first cut used Playwright's own `[ref=eN]` numbering for a11y
and an incrementing JS counter for interactive -- both positional, and
proven (see the task report) to renumber every sibling below an insertion
point on the very next snapshot. `_stable_ref` below is what both now use
instead: a hash of what an element IS (role/name or tag/text, plus its
ancestors' own identities), never of where iteration happened to visit it.
"""

import copy
import hashlib
import json
import os
import pathlib
import re
from urllib.parse import urljoin
import shutil
import signal
import subprocess
import tempfile
import uuid
from typing import Protocol


_ABSOLUTE_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


class BrowserDriver(Protocol):
    def goto(self, url: str) -> None: ...
    def snapshot(self) -> dict: ...
    def links(self) -> list[str]: ...
    def run_spec(self, path: str) -> dict: ...

    def headers(self) -> dict:
        """The last navigation's HTTP response headers, lower-cased key by
        key the way `playwright.sync_api.Response.headers` already returns
        them. This is Auditor's whole way in to CSP, HSTS, and
        `X-Frame-Options`: none of those show up in `a11y()` or `snapshot()`
        -- they are never rendered, only sent -- so an agent whose mandate
        is exactly those headers needs its own accessor rather than being
        asked to scrape them out of a DOM that was never told about them.
        """
        ...

    def cookies(self) -> list[dict]:
        """Every cookie the browser is currently holding for the loaded
        page's context: `{name, value, domain, path, expires, httpOnly,
        secure, sameSite}` per entry, the same shape
        `BrowserContext.cookies()` already returns. Auditor's other half of
        its mandate -- a session cookie missing `Secure` or `HttpOnly` is a
        finding regardless of what the page renders.
        """
        ...

    def a11y(self) -> list[dict]:
        """Flattened accessibility tree: `{ref, role, name, level, state,
        disabled}` per element, in document order.

        `ref` is a content-derived, stable handle -- a hash of the
        element's role, its accessible name, and the chain of its
        ancestors' own role/name identities (NOT the numeric position of
        any of them). That is what lets Cartographer diff two SEPARATE
        navigations to the same route -- a fresh crawl today against last
        week's, or two calls in the same run -- and say which controls
        appeared, vanished, or were renamed: the ref for "the Pay button
        inside the checkout form" is the same value both times specifically
        BECAUSE it was never a position in either tree, and it changes only
        when what the element IS changes (rename, remove, or a real
        identity change), never when something unrelated shifts around it.
        A control renamed between two snapshots reads as its old ref
        vanishing and a new ref appearing, not as one ref moving -- treat
        that as "removed, then a different control added", which is the
        honest reading: `a11y()` has no way to know a rename from a
        removal-plus-addition, and neither should Healer.

        Two elements that are genuinely identical in every one of those
        terms (same role, same name, same parent) get an occurrence index
        scoped to that one parent, so the collision is between those two
        siblings only -- it costs precision locally between them, and does
        not ripple `ref` values for anything else in the tree the way a
        single tree-wide counter would.
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
        WCAG 4.1.2 outright). `ref` here follows the exact same
        content-derived, ancestor-scoped-occurrence contract `a11y()`'s
        does (see its docstring) -- built from tag, visible text, and
        ancestor identity rather than DOM traversal order, for the same
        reason: an element with no ARIA role has no accessible name to hash
        either, so its visible text is the next best stand-in for "what
        this element is".
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
    `{"links": [...], "a11y": [...], "interactive": [...], "headers": {...},
    "cookies": [...], "error": "..."}` -- every key optional; a URL not
    seeded at all, or a key not present on a page that is, reads as empty
    (see `test_fake_browser_reports_an_unknown_page_as_empty` and the
    goto-before-any-page behaviour below). That "unseeded is empty, not an
    error" choice is deliberate and matches `goto`ing to a page that
    genuinely has nothing on it (a Cartographer fixture describing a blank
    route it discovered but hasn't crawled the contents of yet) -- an empty
    page is a real, common state to model, not a mistake a test made.
    `FakeBrowser` never computes a ref -- unlike the real driver, whose refs
    are derived from role/name/ancestry (see `_stable_ref`), a test seeding
    `a11y`/`interactive` supplies its own `ref` values directly and they are
    returned verbatim (only defaulted/copied, never rehashed), because a
    test asserting "this specific ref appeared/vanished" needs to control
    that value itself, not have this class recompute it out from under it.

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

    A value in `spec_results` is normally a single `dict`, returned
    verbatim on every call for that path. Task 11b's addition: a `list` of
    dicts is instead a SEQUENCE, popped one result per call -- see
    `run_spec`'s own docstring for why Healer's "verify a repair by
    re-running the spec" contract needs that.
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

    def headers(self) -> dict:
        return dict(self._page().get("headers", {}))

    def cookies(self) -> list[dict]:
        return copy.deepcopy(self._page().get("cookies", []))

    def a11y(self) -> list[dict]:
        return [_normalise(e, _A11Y_DEFAULTS) for e in self._page().get("a11y", [])]

    def interactive(self) -> list[dict]:
        return [_normalise(e, _INTERACTIVE_DEFAULTS) for e in self._page().get("interactive", [])]

    def run_spec(self, path: str) -> dict:
        if path in self._spec_results:
            seeded = self._spec_results[path]
            # A `list` (added for Task 11b's Healer) is a SEQUENCE of
            # results for repeated calls against the same path, popped in
            # order -- the same "scripted responses, consumed one at a
            # time" idea `core.fakes.FakeModel` already uses for
            # `generate()`. Healer's whole contract is "draft a repair,
            # then re-run the spec to see if it now passes": that needs
            # `run_spec(path)` to answer differently the second time it is
            # called for the same path within one test, which a single
            # static dict value can never do. Once the list is down to its
            # last entry it keeps being returned rather than raising (unlike
            # `FakeModel`, which asserts on exhaustion) -- a spec a test
            # calls a third time was not a mistake the way a third
            # unscripted model call would be; the fixture just did not
            # bother scripting a distinct outcome past the second call.
            # A bare `dict` (every pre-Task-11b caller) is untouched:
            # `isinstance(seeded, list)` is False for it, so it falls
            # through to the same `deepcopy` return as always.
            if isinstance(seeded, list):
                if not seeded:
                    return {"passed": False, "error": f"no more results seeded for {path!r}"}
                result = seeded.pop(0) if len(seeded) > 1 else seeded[0]
                return copy.deepcopy(result)
            return copy.deepcopy(seeded)
        return {"passed": False, "error": f"no result seeded for {path!r}"}


# --- the real driver ---------------------------------------------------
#
# Everything below launches an actual Chromium and is exercised only by
# `tests/test_playwright_live.py`, gated behind `PLUMBLINE_LIVE_BROWSER_
# TESTS` the same way Task 8b gated real OAuth. The default suite imports
# this class (so `from agents.browser import PlaywrightDriver` never fails
# collection) but never instantiates or starts it. `_parse_aria_snapshot`
# and `_assign_interactive_refs` below are pure string/data-in-data-out
# functions, though, so THEY are exercised directly, offline, in the
# default suite with real captured input -- only launching an actual
# browser is gated.


def _stable_ref(ancestor_path: tuple[str, ...], key: str, occurrence: int, prefix: str) -> str:
    """A ref derived from what an element IS -- `key` (role+name for a11y,
    tag+text for interactive) together with `ancestor_path` (its ancestors'
    own such keys, root to parent, never a sibling index) -- rather than
    from wherever Playwright's own `[ref=eN]` numbering or a JS
    `querySelectorAll` counter happened to visit it this time.

    That distinction is the entire fix here: Playwright reassigns `[ref=eN]`
    by fresh traversal order on every `aria_snapshot()` call, so inserting
    one element renumbers every sibling below it on the very next snapshot
    (verified against a real Chromium: a "Pay" button's ref moved from `e2`
    to `e6` after inserting one unrelated button above it, with nothing
    about "Pay" itself having changed -- see the task report). A caller
    diffing two such snapshots would read that as "Pay disappeared and a
    new, unrelated control appeared in its place", the exact false signal
    Healer exists not to act on. Hashing `key`+`ancestor_path` instead means
    the "Pay" button's ref is a function of "a button named Pay, inside
    [whatever its ancestors are]" -- unaffected by anything happening to
    unrelated siblings, before or after it, anywhere else in the tree.

    `occurrence` is the one input that IS positional, deliberately narrow:
    two elements that hash identically otherwise (same role, same name,
    same immediate parent -- e.g. two "Add to cart" buttons in a product
    grid) get 0, 1, 2... in the order this parent's children were visited,
    so they still get distinct refs. Two snapshots of an unchanged page
    order those two buttons the same way both times (nothing about parsing
    order depends on the buttons' own identity), so this does not
    reintroduce positional instability -- it only disambiguates a genuine
    same-parent, same-identity collision, and a collision there costs
    precision between those two elements alone, not a renumbering of
    anything else in the tree.
    """
    digest = hashlib.sha1("\x1f".join((*ancestor_path, f"{key}#{occurrence}")).encode()).hexdigest()
    return f"{prefix}{digest[:12]}"


# Matches one line of `page.aria_snapshot(mode="ai")`'s output, e.g.:
#   heading "Title" [level=1] [ref=e5]
#   generic [active] [ref=e1]:
#   button "Disabled Button" [disabled] [ref=e2]
# Lines with no `[ref=...]` at all (a nested `- /url: /cart` detail line, a
# bare `- text: ...` run) are not emitted as entries -- see `a11y()`'s
# docstring for why "no ref" means "not part of the flattened tree" -- but
# they are still tracked on the ancestor stack below, since a structural
# node with no `[ref]` of its own can still be the parent of one that does.
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
    driver produced the tree it's reading -- with `ref` recomputed via
    `_stable_ref` rather than trusting the `[ref=eN]` Playwright already
    put in the text (see that function's docstring for why).

    Indentation (leading whitespace, tabs or spaces, counted verbatim
    rather than assumed to be exactly two spaces per level) is used ONLY to
    build the ancestor-path input to `_stable_ref` -- a stack of
    `(indent, key)` popped back to the current line's depth before each
    node computes its own path from what remains. It is deliberately not
    used for anything else: the OUTPUT stays a flat list keyed by content,
    not a tree, matching `a11y()`'s own flattened contract (Cartographer
    diffs a flat list of refs).
    """
    entries = []
    stack: list[tuple[int, str]] = []
    occurrence_counts: dict[tuple[tuple[str, ...], str], int] = {}

    for line in text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        match = _ARIA_LINE.match(line.strip())
        if not match:
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()
        ancestor_path = tuple(k for _, k in stack)

        role = match.group("role")
        name = match.group("name") or ""
        base_key = f"{role}:{name}"
        count_key = (ancestor_path, base_key)
        occurrence = occurrence_counts.get(count_key, 0)
        occurrence_counts[count_key] = occurrence + 1

        attrs = {}
        for raw in _ATTR.findall(match.group("attrs") or ""):
            if "=" in raw:
                attr_key, _, value = raw.partition("=")
                attrs[attr_key] = value
            else:
                attrs[raw] = True

        has_ref = "ref" in attrs
        attrs.pop("ref", None)
        level = attrs.pop("level", None)
        disabled = bool(attrs.pop("disabled", False))

        if has_ref:
            entries.append(
                {
                    "ref": _stable_ref(ancestor_path, base_key, occurrence, "a"),
                    "role": role,
                    "name": name,
                    "level": int(level) if level is not None else None,
                    "state": dict(attrs),
                    "disabled": disabled,
                }
            )

        # Pushed regardless of has_ref -- a bare "text:" run never has
        # children in this format so it wouldn't matter, but a structural
        # node without its own [ref] is still a legitimate ancestor for
        # whatever is nested under it, and dropping it here would collapse
        # two genuinely different parents into the same ancestor_path.
        stack.append((indent, f"{base_key}#{occurrence}"))

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
#
# Deliberately returns RAW records with no ref of its own: `ancestors` is
# each ancestor element's own `role-or-tag:aria-label-or-id` descriptor,
# root to parent, with no sibling index anywhere in it -- the same
# ancestor-path shape `_parse_aria_snapshot` builds from indentation.
# `_assign_interactive_refs` (below, pure, tested offline) turns this into
# the same content-derived `{ref, tag, reason}` shape `a11y()` uses, via
# the same `_stable_ref`. Keeping the hash out of the browser-evaluated JS
# is deliberate: a pure Python function over plain data can be unit tested
# with a hand-built fixture list, the same way `_parse_aria_snapshot` is,
# rather than only through a live-browser test.
_INTERACTIVE_JS = """
() => {
    const semanticTags = new Set([
        "A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "OPTION",
    ]);
    function descriptor(el) {
        const role = el.getAttribute("role");
        const label = el.getAttribute("aria-label") || el.id || "";
        return (role || el.tagName.toLowerCase()) + ":" + label;
    }
    function ancestorPath(el) {
        const path = [];
        let node = el.parentElement;
        while (node) {
            path.unshift(descriptor(node));
            node = node.parentElement;
        }
        return path;
    }
    const out = [];
    document.querySelectorAll("*").forEach((el) => {
        if (el.hasAttribute("role") || semanticTags.has(el.tagName)) return;
        const hasOnclick = el.hasAttribute("onclick") || typeof el.onclick === "function";
        const pointerCursor = window.getComputedStyle(el).cursor === "pointer";
        if (!hasOnclick && !pointerCursor) return;
        out.push({
            tag: el.tagName.toLowerCase(),
            reason: hasOnclick ? "onclick" : "cursor:pointer",
            text: (el.textContent || "").trim().slice(0, 40),
            ancestors: ancestorPath(el),
        });
    });
    return out;
}
"""


def _assign_interactive_refs(raw: list[dict]) -> list[dict]:
    """Turn `_INTERACTIVE_JS`'s raw per-element records (`tag`, `reason`,
    `text`, `ancestors`) into the final `{ref, tag, reason}` shape, with
    `ref` computed the same content-derived, ancestor-scoped-occurrence way
    `_parse_aria_snapshot` computes an a11y ref -- see `_stable_ref` and
    `a11y()`'s docstring for why. `key` here is `tag:text:reason` rather
    than `role:name`, because an interactive element has no ARIA role or
    accessible name at all (that is the entire reason it is in this list
    instead of `a11y()`'s) -- visible text is the closest available stand-in
    for "what this element is" without one.

    Pure: takes and returns plain data, no browser involved, so this is
    tested directly with a hand-built `raw` list rather than only through
    `tests/test_playwright_live.py`.
    """
    entries = []
    occurrence_counts: dict[tuple[tuple[str, ...], str], int] = {}
    for item in raw:
        ancestor_path = tuple(item.get("ancestors") or ())
        key = f"{item.get('tag', '')}:{item.get('text', '')}:{item.get('reason', '')}"
        count_key = (ancestor_path, key)
        occurrence = occurrence_counts.get(count_key, 0)
        occurrence_counts[count_key] = occurrence + 1
        entries.append(
            {
                "ref": _stable_ref(ancestor_path, key, occurrence, "i"),
                "tag": item.get("tag", ""),
                "reason": item.get("reason", ""),
            }
        )
    return entries


# --- run_spec: shells out to the Playwright TEST RUNNER (a separate Node
# CLI, `npx playwright test`), not the Python bindings `start()`/`goto()`
# above drive. `PlaywrightDriver.a11y()`/`goto()`/etc. talk to ONE page this
# process already has open via the Python sync API; a `.spec.ts` file is
# JS/TS source Author or a customer wrote for Playwright's own test runner,
# and the only thing that can execute it is that runner itself -- there is
# no Python API that runs an arbitrary Playwright TEST FILE, only ones that
# drive a single page by hand. Everything below this comment, up to
# `PlaywrightDriver.run_spec` itself, is pure, offline-testable string/data
# handling -- exercised directly by the default suite -- so only the actual
# subprocess launch is gated behind `PLUMBLINE_LIVE_RUN_SPEC_TESTS` (see
# `tests/test_playwright_run_spec_live.py`).
#
# ONE CONTRACT CORRECTION, load-bearing enough to say here rather than only
# in the task report: the fixed contract this module implements against
# describes `matcher` as populated from "whether the failure carried a
# `matcherResult` object" on Playwright's JSON reporter output. Verified
# against a real `npx playwright test --reporter=json` run (three separate
# Playwright versions were not tried; 1.62.1 -- the exact version already
# pinned in `web/package.json` -- was, deliberately, so this matches what
# the rest of this codebase already runs): **no such field exists** in the
# JSON reporter's `error` object, for ANY failure. `matcherResult` is a
# real property Playwright attaches to the `Error` instance an `expect()`
# failure throws (see `node_modules/playwright/lib/matchers/expect.js`),
# but it never survives the worker-process -> main-process IPC boundary --
# confirmed by writing a custom in-process reporter and inspecting the
# `TestError` it actually receives: `Object.keys(err)` is exactly
# `["message", "stack", "location", "snippet"]`, with `matcherResult`
# already stripped, for BOTH an `expect()` failure and a raw locator
# timeout. No reporter -- built-in or custom -- can ever see it; the loss
# happens before any reporter runs, not because `json` was the wrong
# reporter choice.
#
# `_classify_matcher` below is the best available STRUCTURED substitute,
# and it is still not string-matching prose the way `agents/runner.py`'s
# own documented fallback is: `expect()` failures are generated by
# Playwright's OWN matcher-hint formatter, which always renders the call
# signature `expect(receiver).matcherName(...)` as the first line of the
# message -- program-generated output, not authored wording, the same way
# a Python traceback's exception class name is structured even though it
# arrives as text. A raw action/locator failure never carries that prefix.
# Verified against four real, separately-run specs (see the task report
# for the full parsed dicts): a passing spec, `expect(locator).toHaveText`
# failing outright, `expect(locator).toBeVisible()` timing out (still
# `matcher=True` -- it went through `expect()`, even though the underlying
# wait itself timed out), a raw `locator.click()` timeout with no
# `expect()` involved at all (`matcher=False`), and a `locator.click()`
# strict-mode violation (also `matcher=False`, same reasoning).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_EXPECT_FAILURE = re.compile(r"^Error:\s*expect\(")
# The exact vocabulary a raw (non-`expect()`) Playwright action/locator
# failure uses -- `TimeoutError:` is the literal name of the error CLASS
# Playwright throws for an action/navigation timeout (never for an
# `expect()` timeout, which stays a plain `Error` prefixed `expect(...)`
# per the split above); "strict mode violation" and "no element matches"
# are the same two phrases `agents/runner.py`'s own fallback `_SELECTOR_
# ERROR` and `agents/healer.py`'s `_is_selector_drift` already trust, kept
# identical on purpose so all three agents read the same failure the same
# way when they each independently reach for a string signal.
_RAW_ACTION_FAILURE = re.compile(r"^TimeoutError:|strict mode violation|no element matches", re.I)


def _strip_ansi(text: str | None) -> str:
    return _ANSI_RE.sub("", text or "")


def _classify_matcher(message: str | None) -> bool | None:
    """`True` for an `expect()` failure, `False` for a raw action/locator
    failure, `None` when the message matches neither recognised shape --
    deliberately left unknown rather than guessed, so `agents/runner.py`'s
    `_classify` falls through to ITS OWN fallback regex over `error`
    (already reviewed, already accepted) instead of this function
    overclaiming confidence it does not have."""
    text = _strip_ansi(message)
    if _EXPECT_FAILURE.match(text):
        return True
    if _RAW_ACTION_FAILURE.search(text):
        return False
    return None


def _iter_test_results(suites: list[dict]):
    """Every test's LAST attempt (`results[-1]`, so a retried test reads by
    its final outcome, not its first), depth-first, across `test.describe`
    nesting -- Playwright's JSON reporter nests child suites under `suites`
    recursively for each `describe` block, so a flat `for spec in
    suites[0]["specs"]` (right for a file with no `describe` wrapper) silently
    drops every test inside one that has it."""
    for suite in suites:
        for spec in suite.get("specs", []):
            results = spec.get("tests", [{}])[0].get("results") or []
            if results:
                yield spec, results[-1]
        yield from _iter_test_results(suite.get("suites", []) or [])


def _console_text(result: dict) -> str:
    """The spec's own Node-side `console.log`/`console.error` output for
    this attempt, `[stdout]`/`[stderr]`-tagged -- real data straight off
    the JSON reporter's own `stdout`/`stderr` arrays (each entry a `{text}`
    dict, or `{buffer}` for binary output), needing no extra fixture or
    reporter of our own to produce. This is Node-side output only: a page's
    OWN browser-console messages (`page.on('console', ...)`) are not
    forwarded here unless the spec itself listens and re-logs them -- doing
    that generically, for a spec this driver did not author, would mean
    either rewriting the spec (ruled out -- see the module docstring on
    Surgeon never touching a spec file, which this driver holds itself to
    as well even though Runner is the one actually calling it) or a fixture
    file the spec would have to import, which an arbitrary customer repo
    has no guaranteed way to provide."""
    lines = []
    for stream in ("stdout", "stderr"):
        for entry in result.get(stream) or []:
            text = entry.get("text")
            if text is None and "buffer" in entry:
                text = f"<{len(entry['buffer'])} bytes of binary output, omitted>"
            if text:
                lines.append(f"[{stream}] {text.rstrip()}")
    return "\n".join(lines)


# The four options that cannot come from CLI flags at all (Playwright test's
# CLI has no `--no-sandbox`/`--video`/`--trace`/`--har` switches) and so must
# live in a config file `run_spec` generates fresh per call. `recordHar`
# MUST be nested under `contextOptions` -- verified against the real
# fixture list in `node_modules/playwright/lib/index.js`'s
# `_combinedContextOptions`: that fixture only forwards a small, explicit
# allow-list of top-level `use.*` keys (`acceptDownloads`, `bypassCSP`,
# `colorScheme`, ...) into the real `browser.newContext()` call, plus
# whatever is under `use.contextOptions` verbatim -- `recordHar` is not on
# that allow-list, so `use: { recordHar: {...} }` (the natural-looking
# spelling) is silently DROPPED, no error, no file, ever. Confirmed the hard
# way: it produced zero bytes on disk across a passing run, a failing run,
# and a run with real (non-mocked) network traffic, until nested under
# `contextOptions` instead -- see the task report for the three empty runs
# that found this.
_CONFIG_TEMPLATE = """\
import {{ defineConfig }} from '@playwright/test';
export default defineConfig({{
  testDir: {test_dir},
  outputDir: {output_dir},
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reportSlowTests: null,
  use: {{
    launchOptions: {{ args: ['--no-sandbox'], chromiumSandbox: false }},
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    contextOptions: {{ recordHar: {{ path: {har_path} }} }},
  }},
}});
"""


def _write_config(work_dir: pathlib.Path, output_dir: pathlib.Path, har_path: pathlib.Path) -> pathlib.Path:
    """Written INSIDE `work_dir` (the checkout), never outside it --
    verified the hard way that this is not optional: a config file placed
    anywhere else fails `import { defineConfig } from '@playwright/test'`
    with `Cannot find module '@playwright/test'`, because Node's own module
    resolution for that import walks up from the CONFIG FILE'S OWN
    location, not from the subprocess's `cwd`. Given a unique
    (`uuid4`-suffixed) name so two specs run concurrently by
    `agents/runner.py`'s `ThreadPoolExecutor` never collide on the same
    file, and removed by `PlaywrightDriver.run_spec`'s own `finally` before
    that method ever returns -- never left behind for `job/checkout.py`'s
    `RepoCheckout.commit_all` to accidentally sweep into a customer's own
    repo, the same discipline the contract's own non-negotiables ask of
    Surgeon for spec files.
    """
    config_path = work_dir / f".plumbline-run-spec-{uuid.uuid4().hex}.config.ts"
    config_path.write_text(_CONFIG_TEMPLATE.format(
        test_dir=json.dumps(str(work_dir)),
        output_dir=json.dumps(str(output_dir)),
        har_path=json.dumps(str(har_path)),
    ))
    return config_path


DEFAULT_SPEC_SUBPROCESS_TIMEOUT_S = 90.0
# Artefacts live OUTSIDE any checkout, on purpose: `job/checkout.py`'s
# `RepoCheckout.commit_all` is documented (Task 14g/contract) to be the one
# thing that turns "whatever is sitting in the checkout's working tree"
# into a real commit Surgeon pushes. A video/trace/har/console-log dropped
# INSIDE the checkout would be exactly that kind of accidental file by the
# time Surgeon runs later in the same batch -- there is no contract today
# promising `commit_all` ignores them. Keeping every artefact under the
# system temp dir instead means Runner's own promised shape is satisfied
# (a real path per kind, real files behind them) with zero risk of ever
# reaching a customer's pull request.
_ARTEFACT_ROOT = pathlib.Path(tempfile.gettempdir()) / "plumbline-artefacts"

# Where `Dockerfile.worker` installs the Node Playwright test runner for a
# checkout that brought no `@playwright/test` of its own -- see that
# Dockerfile's own comment on why this lives at a fixed path rather than a
# global npm install. Overridable so `tests/test_playwright_run_spec_live.py`
# can point it at this repo's own `web/node_modules` (already carrying a
# matching `@playwright/test` + cached Chromium revision) without needing a
# second, real `/opt` install just to run the opt-in suite.
PLAYWRIGHT_TEST_HOME_ENV = "PLUMBLINE_PLAYWRIGHT_TEST_HOME"
_DEFAULT_PLAYWRIGHT_TEST_HOME = "/opt/playwright-test/node_modules"


def _ensure_local_playwright_test(work_dir: pathlib.Path) -> pathlib.Path | None:
    """Symlinks a shared `@playwright/test` install into
    `work_dir/node_modules` when the checkout brought none of its own, and
    returns the symlink's path so `run_spec` can remove it afterward --
    `None` when nothing was created, either because the checkout already
    had its own install (left completely untouched: a customer's real
    `node_modules` is never something this driver renames, merges into, or
    deletes) or because no shared install is configured to fall back to.

    Two DIFFERENT `node_modules` copies of `@playwright/test` -- even at
    the identical version string -- do not interoperate: Playwright Test
    keeps an internal singleton keyed by module IDENTITY, not by version,
    and a spec loaded through one copy while the CLI process itself was
    resolved from a different one fails outright with "Playwright Test did
    not expect test() to be called here" (verified: pointing `NODE_PATH` at
    one install while `npx` resolved its own CLI from a second, separate
    one -- both genuinely version 1.62.1 -- produced exactly that error;
    see the task report). A symlinked `node_modules` avoids the failure
    mode entirely rather than working around it: there is only ever ONE
    physical install for both the CLI and the config/spec's own `import` to
    resolve, so there is nothing for them to disagree about.
    """
    local = work_dir / "node_modules"
    if local.exists():
        return None
    shared = pathlib.Path(os.environ.get(PLAYWRIGHT_TEST_HOME_ENV, _DEFAULT_PLAYWRIGHT_TEST_HOME))
    if not shared.is_dir():
        return None  # nothing to link to -- run_spec surfaces npx's own failure instead
    local.symlink_to(shared, target_is_directory=True)
    return local


class PlaywrightDriver:
    """The real driver: an actual headless Chromium, via Playwright's sync
    API. Every agent task builds against `FakeBrowser` instead; this class
    exists so the platform has something to run in Cloud Run once an agent
    is trusted enough to touch a real target site, and so a developer can
    opt into `tests/test_playwright_live.py` to check this class against a
    real page when Playwright's own APIs shift under it.

    `cwd` (contract addition): the checkout `run_spec` runs specs inside --
    `None` means "the process's own current directory", which is only ever
    right for a spec that needs no repo at all (nothing in this codebase's
    default suite exercises that path; every real run has a checkout).
    """

    def __init__(self, cwd: "pathlib.Path | None" = None, spec_timeout_s: float = DEFAULT_SPEC_SUBPROCESS_TIMEOUT_S):
        self._pw = self._browser = self._page = self._last_response = None
        self._base_url = ""
        self._cwd = cwd
        self._spec_timeout_s = spec_timeout_s

    def start(self, playwright_factory=None):
        """`playwright_factory`, when passed, replaces `sync_playwright`
        itself -- a zero-argument callable returning something with a
        `.start()` that yields an object with `.chromium.launch(...)`. This
        exists ENTIRELY so `tests/test_agent_base.py` can inject a
        recording double and assert on the real kwargs `launch()` receives,
        instead of asserting on source text.

        That distinction is not cosmetic. Fix round 1 found -- by literally
        deleting `chromium_sandbox=False` from the `launch()` call below,
        leaving this docstring's own mention of the flag in place, and
        re-running the old test -- that a test asserting
        `"chromium_sandbox=False" in inspect.getsource(...)` keeps passing
        even after the flag is gone from the call, as long as the STRING
        survives anywhere in the function body (a comment is enough). A
        guard that stays green after the thing it guards is deleted is
        worse than no guard: it is false confidence in the one place this
        build cannot afford it, since the flag's absence is invisible until
        a real Cloud Run container fails to start. `test_agent_base.py`'s
        `test_start_passes_chromium_sandbox_false_to_the_real_launch_call`
        drives THIS parameter with a recording double instead, so the
        assertion is on behaviour Chromium itself would see, not on prose
        near the call.
        """
        if playwright_factory is None:
            from playwright.sync_api import sync_playwright

            playwright_factory = sync_playwright

        self._pw = playwright_factory().start()
        # chromium_sandbox=False is not an optimisation -- it is required.
        # Chromium's own sandbox needs a user namespace it cannot create
        # under AppArmor's unprivileged-user-namespace restriction (the
        # default on current Ubuntu-based images) or inside a Cloud Run
        # container (which runs as a non-root, unprivileged user with no
        # CAP_SYS_ADMIN). Without this flag, `launch()` fails outright in
        # both places this platform actually runs -- a developer's sandboxed
        # machine and Cloud Run itself -- not just in some hypothetical
        # locked-down environment.
        self._browser = self._pw.chromium.launch(headless=True, chromium_sandbox=False)
        self._page = self._browser.new_page()

    def goto(self, url: str) -> None:
        # The Response is kept (not discarded the way the pre-fix-round
        # version did) specifically so headers()/cookies() below have
        # something to read -- Auditor's entire mandate lives in this
        # object and nowhere else `PlaywrightDriver` touches.
        self._last_response = self._page.goto(
            self._absolute(url), wait_until="domcontentloaded"
        )

    def _absolute(self, url: str) -> str:
        """Resolve a route path against the site already being visited.

        Agents navigate by ROUTE PATH -- `agents/auditor.py` walks
        `Route.path` values straight from the graph and calls
        `ctx.browser.goto("/")`. Playwright has no base URL of its own, so
        that reached `Page.goto("/")` and raised `Protocol error
        (Page.navigate): Cannot navigate to invalid URL`. Auditor died on
        its first route of every real run.

        The base is remembered from the last absolute navigation, which
        `job/worker.py` always performs first (it navigates the fresh
        driver to the workspace's `target_url` before handing it to any
        agent). Falling back to the page's current URL covers a driver
        someone navigated by hand.
        """
        if _ABSOLUTE_URL.match(url):
            self._base_url = url
            return url
        base = self._base_url or ""
        if not base:
            current = getattr(self._page, "url", "") or ""
            if current.startswith(("http://", "https://")):
                base = current
        if not base:
            raise ValueError(
                f"cannot resolve the relative URL {url!r}: no absolute page has been "
                "visited yet, so there is no origin to resolve it against"
            )
        return urljoin(base, url)

    def snapshot(self) -> dict:
        return {"title": self._page.title(), "url": self._page.url}

    def links(self) -> list[str]:
        return self._page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )

    def headers(self) -> dict:
        if self._last_response is None:
            return {}
        return dict(self._last_response.headers)

    def cookies(self) -> list[dict]:
        return list(self._page.context.cookies())

    def a11y(self) -> list[dict]:
        return _parse_aria_snapshot(self._page.aria_snapshot(mode="ai"))

    def interactive(self) -> list[dict]:
        return _assign_interactive_refs(self._page.evaluate(_INTERACTIVE_JS))

    def run_spec(self, path: str) -> dict:
        """Shell out to `npx playwright test <path> --reporter=json`
        (`cwd` set to the checkout, per the contract) and parse its JSON
        reporter -- never the human-readable output. Returns the fixed
        contract shape `agents/runner.py`'s `_classify` already parses;
        never raises for a failing/timed-out spec (that is DATA, exactly
        like `FakeBrowser.run_spec` returning `{"passed": False, ...}`
        rather than raising) -- only when Playwright itself could not even
        load the spec (a syntax error, a missing import: point 5 of the
        brief) or the subprocess produced no parseable report at all, both
        of which are `agents/runner.py`'s own definition of `kind="crash"`
        (its `_run_one` maps a raised exception from `ctx.browser.run_spec`
        to exactly that -- see that module).
        """
        work_dir = self._cwd or pathlib.Path.cwd()
        run_dir = pathlib.Path(tempfile.mkdtemp(prefix="plumbline-run-spec-"))
        har_path = run_dir / "network.har"
        config_path = _write_config(work_dir, run_dir, har_path)
        node_modules_link = _ensure_local_playwright_test(work_dir)
        empty_artefacts = {"video": "", "trace": "", "har": "", "console": ""}
        try:
            try:
                proc = subprocess.Popen(
                    ["npx", "--yes", "playwright", "test", path,
                     f"--config={config_path}", "--reporter=json"],
                    cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True,
                    # A new session/process group so a hung `npx` (which
                    # itself spawns the actual `playwright` process, which
                    # spawns Chromium) can be killed as a GROUP -- point 3
                    # of the brief: killing only the direct child leaves
                    # Chromium orphaned and still running.
                    start_new_session=True,
                )
            except OSError as exc:
                # `npx` itself is not on PATH, or could not be executed at
                # all -- the runner could not run, full stop; nothing about
                # this spec's own content is knowable. Point 6: this is
                # exactly the raise case, not a failing-spec result.
                raise RuntimeError(f"could not launch the Playwright test runner: {exc}") from exc

            try:
                stdout, stderr = proc.communicate(timeout=self._spec_timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass  # already gone between the timeout firing and the kill
                stdout, stderr = proc.communicate()
                # A hang is DATA (point 6), the same "timeout" shape
                # `status == "timedOut"` already gives `_classify` --
                # Runner's own batch watchdog is a second, independent
                # layer above this one, not a reason to skip having one
                # here (see the brief: "the subprocess needs its own too").
                return {
                    "passed": False, "status": "timedOut", "matcher": None,
                    "error": f"spec did not complete within the driver's own "
                             f"{self._spec_timeout_s}s subprocess timeout (process group killed)",
                    "duration_ms": int(self._spec_timeout_s * 1000),
                    "artefacts": empty_artefacts,
                }

            try:
                report = json.loads(stdout)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"playwright produced no parseable JSON report for {path!r} "
                    f"(exit={proc.returncode}): {_strip_ansi(stderr)[:2000]}"
                ) from exc

            suites = report.get("suites") or []
            top_errors = report.get("errors") or []
            duration_ms = int(report.get("stats", {}).get("duration", 0))

            if not suites and top_errors:
                # Point 5: the spec failed to LOAD -- no test in it ever
                # ran, so there is no per-test result to classify at all.
                # Raising (rather than returning a "failed" dict) is what
                # lets `agents/runner.py`'s `_run_one` map this to
                # `kind="crash"`, distinct from a real assertion/selector
                # failure -- see this method's own docstring.
                raise RuntimeError(_strip_ansi(top_errors[0].get("message", "spec failed to load")))

            results = list(_iter_test_results(suites))
            if not results:
                raise RuntimeError(f"playwright reported no test results at all for {path!r}")

            passed = all(spec_result["status"] == "passed" for _, spec_result in results)
            if passed:
                return {
                    "passed": True, "status": "passed", "matcher": None,
                    "error": "", "duration_ms": duration_ms, "artefacts": empty_artefacts,
                }

            # The first non-passing test in document order is the one this
            # whole return value describes -- deterministic (never thread-
            # or completion-order dependent, there is only one process),
            # and consistent with `_write_config`'s own `workers: 1,
            # fullyParallel: false` keeping a multi-`test()` file's own
            # internal order stable run to run.
            _, chosen = next((r for r in results if r[1]["status"] != "passed"), results[0])
            status = "timedOut" if chosen["status"] == "timedOut" else "failed"
            error_obj = chosen.get("error") or (chosen.get("errors") or [{}])[0]
            message = error_obj.get("message", "") if isinstance(error_obj, dict) else ""

            return {
                "passed": False, "status": status,
                "matcher": None if status == "timedOut" else _classify_matcher(message),
                "error": _strip_ansi(message),
                "duration_ms": duration_ms,
                "artefacts": self._collect_artefacts(chosen, har_path),
            }
        finally:
            config_path.unlink(missing_ok=True)
            if node_modules_link is not None:
                node_modules_link.unlink(missing_ok=True)
            # Always -- passed, failed, timed out, or a crash raised above
            # -- this call's own private run_dir (outputDir + the HAR
            # target) never survives past this method returning. The
            # failing path has already copied out whatever it needs into
            # `_ARTEFACT_ROOT` by this point; a passing spec never wrote
            # anything worth keeping there in the first place (point 4).
            shutil.rmtree(run_dir, ignore_errors=True)

    def _collect_artefacts(self, result: dict, har_path: pathlib.Path) -> dict:
        """Point 4: only ever called for a FAILING spec (`run_spec` above
        never reaches this for a passing one), so the "342 passing specs
        must not write 1,368 files" half of the requirement is satisfied
        by never calling this at all on the passing path -- `video`/`trace`
        already cost nothing on disk for a passing spec on their own
        (`retain-on-failure`, Playwright's own doing), and the run's own
        private temp dir (`har_path`'s parent, `run_spec`'s `run_dir`) is
        deleted unconditionally by `run_spec`'s own `finally` once this
        method has copied out what it needs into `_ARTEFACT_ROOT` -- the
        transient write-then-clean the OS sees either way is the exact
        same shape `retain-on-failure` itself uses internally for
        video/trace, not a departure from it.

        `attachments` (real per-test paths for `video`/`trace`, straight
        off the JSON reporter -- verified against a real failing run, see
        the task report) is preferred over reconstructing Playwright's own
        output-folder naming scheme by hand, which is both undocumented
        and, unlike `attachments`, not something this driver has any
        contract-level promise will stay stable across a Playwright
        version bump.
        """
        dest = _ARTEFACT_ROOT / f"{uuid.uuid4().hex}"
        dest.mkdir(parents=True, exist_ok=True)
        artefacts = {"video": "", "trace": "", "har": "", "console": ""}
        for attachment in result.get("attachments") or []:
            name = attachment.get("name")
            src = attachment.get("path")
            if name in ("video", "trace") and src and pathlib.Path(src).is_file():
                target = dest / pathlib.Path(src).name
                shutil.copyfile(src, target)
                artefacts[name] = str(target)

        if har_path.is_file():
            target = dest / "network.har"
            shutil.copyfile(har_path, target)
            artefacts["har"] = str(target)

        console = _console_text(result)
        if console:
            target = dest / "console.log"
            target.write_text(console)
            artefacts["console"] = str(target)
        return artefacts

    def stop(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
