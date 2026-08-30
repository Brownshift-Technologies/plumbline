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
import re
from typing import Protocol


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
            return copy.deepcopy(self._spec_results[path])
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


class PlaywrightDriver:
    """The real driver: an actual headless Chromium, via Playwright's sync
    API. Every agent task builds against `FakeBrowser` instead; this class
    exists so the platform has something to run in Cloud Run once an agent
    is trusted enough to touch a real target site, and so a developer can
    opt into `tests/test_playwright_live.py` to check this class against a
    real page when Playwright's own APIs shift under it.
    """

    def __init__(self):
        self._pw = self._browser = self._page = self._last_response = None

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
        self._last_response = self._page.goto(url, wait_until="domcontentloaded")

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
