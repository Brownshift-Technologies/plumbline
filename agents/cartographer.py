"""Task 10: Cartographer -- the agent that maps a site's reachable surface.

Every other agent that reasons about "the app" starts from what
Cartographer wrote: Author picks routes to cover from `Route` rows this
agent creates, Runner and Sentinel report against paths this agent found,
Healer's repairs assume a route still exists at all. Get the crawl wrong
here -- miss a route, double-count one, or wedge on a cycle -- and every
downstream agent inherits the mistake silently, with no way to tell a
missed route from a route that genuinely does not exist.

The crawl itself is a plain breadth-first walk from `/`, capped at
`MAX_ROUTES` so a pathological site (or a crawl trap) cannot run forever --
`seen` is the loop guard, checked before a page is ever visited, not after.
The WHOLE walk runs inside one `browser.read` gateway call (fix round 1):
an earlier version of this file called `gateway.call` once PER PAGE, which
for a 300-route crawl means 300 transactional ledger appends contending on
one per-workspace head document -- the exact problem this module's own
`write_all` argues against, just applied to reads instead of a write. The
crawl's real navigations still happen one at a time against `ctx.browser`
(nothing here pretends a real browser can visit 300 pages atomically); what
moved is only where the LEDGER entry is drawn -- once for "we crawled the
site", not once per page crawled.

Four things past the bare walk are worth calling out, because a real site
is not the clean tree the given spec's own test fixtures model:

1. **A link that redirects to a route already reachable another way must
   not become a second, phantom route.** `FakeBrowser.snapshot()`'s `url`
   key -- and a real `PlaywrightDriver`'s `page.url` after a real 302 --
   report where navigation actually LANDED, which is not always the href
   that was followed to get there. `run()` compares the two: when they
   differ, the requested path is treated as an ALIAS of the landing page,
   not a route of its own, and the landing page is queued (if not already
   seen) so its own, real content still gets crawled on its own visit --
   never borrowed from the alias page that redirected to it.
2. **A discovered href is filtered through `_internal_href`, not a bare
   `.startswith("/")` check** (fix round 1, then hardened in fix round 2).
   `"/"` alone is not enough: `"//evil-cdn.example.com/x".startswith("/")`
   is ALSO `True` -- that is a protocol-relative URL, browsers resolve it
   against the CURRENT scheme onto a completely different host, and
   nothing upstream (a real `PlaywrightDriver.links()` hands back the raw
   href, unfiltered) would have caught an agent walking off the customer's
   site onto an attacker-chosen one. `javascript:`, `mailto:`, `tel:`, and
   `data:` hrefs are excluded for a different reason -- none of them is a
   route at all, crawling one would either do nothing, open a mail client,
   or hand Cartographer a data: URI to "visit". Round 1 checked the raw
   href against these rules directly, which tests a DIFFERENT string than
   the one a browser actually fetches: `"/\\evil.com"` (WHATWG treats `\`
   as `/` for http/https, so this resolves exactly like `//evil.com`) and
   `"/\t/evil.com"` / a zero-width-space variant (the WHATWG URL parser
   strips ASCII tab/CR/LF before parsing, collapsing this to `//evil.com`
   too) both slipped past round 1's filter unchanged. `_internal_href` now
   normalises the way a browser's URL parser does -- strip, then fold
   backslashes, then check -- before applying any of these rules. See
   `_internal_href`.
3. **A fragment never triggers a new page load.** `/catalog#reviews` and
   `/catalog` are the same route by construction -- an in-page anchor jump,
   never a fresh navigation -- so the fragment is stripped (inside
   `_internal_href`) before a discovered link is ever queued or compared
   against `seen`. A query string is NOT stripped the same way:
   `/catalog?category=shoes` can be materially different content from
   `/catalog` (a filtered view a customer can reach and a bug can hide
   in), and collapsing it away would silently drop real surface from the
   map. Fragments are always equivalent; query strings are not assumed to
   be.
4. **A route this crawl cannot reach must not take the whole crawl down.**
   An app with a login wall where most links 401, or a page that is
   genuinely gone, raises `BrowserGotoError` (see `agents/browser.py`) --
   caught here specifically, one path at a time, INSIDE the batched crawl,
   so one broken or unauthenticated link costs the crawl exactly that one
   route, not every route still queued behind it. A `GatewayError` -- a
   policy DENIAL of the whole `browser.read` call, not a per-page fact
   about the site -- is never something this module catches: it is raised
   by `ctx.gateway.call` itself, before the crawl closure is ever entered,
   and propagates straight out of `run()` the way any other caller of a
   denied tool would see it.

The write at the end is the one gateway call every route in one run shares
-- see `write_all` below for why a per-route write would be the wrong
shape entirely, not just a slower one.
"""

import uuid
from urllib.parse import urljoin, urlsplit

from agents.base import AgentResult
from agents.browser import BrowserGotoError
from app.models import Route

MAX_ROUTES = 300

# Never a route on THIS app -- see point 2 in the module docstring. Matched
# against the START of the normalised, lower-cased href -- `_internal_href`
# lower-cases a SEPARATE copy for this comparison only; the path it
# eventually returns keeps the href's original case.
_NON_ROUTE_SCHEMES = ("javascript:", "mailto:", "tel:", "data:")

# Characters the WHATWG URL parser strips from anywhere in the input
# BEFORE it ever looks at scheme or authority -- ASCII tab, CR, LF, and
# (browsers extend the spec's C0-control stripping to this one Unicode
# character in practice) the zero-width space. A crawler that filters the
# raw href is filtering a string the browser never actually parses; an
# attacker inserts one of these between the two slashes of what would
# otherwise be an obviously-external `//host` and the naive filter never
# sees it.
_STRIP_CHARS = ("\t", "\r", "\n", "​")


def _normalise_href(href: str) -> str:
    """The one normalisation step both `_internal_href` and (Tier 2)
    `validate_target_url` build on: strip every character in
    `_STRIP_CHARS` from anywhere in the string, then fold every backslash
    to a forward slash. Pulled out of `_internal_href` unchanged (fix
    round 2's own logic, verbatim) so `validate_target_url` reuses this
    exact normalisation instead of writing a second one -- see that
    function's own docstring for why a target URL needs the identical
    browser-parser-order treatment a discovered href does."""
    normalised = href
    for ch in _STRIP_CHARS:
        normalised = normalised.replace(ch, "")
    return normalised.replace("\\", "/")


def _origin_of(url: str) -> str:
    """`"scheme://host[:port]"` for an already-valid absolute `url` -- no
    trailing slash, no path, lower-cased for case-insensitive comparison
    (scheme and host are case-insensitive per RFC 3986; a path is not,
    which is exactly why this helper never touches one). The one shared
    definition of "origin" `Cartographer.run` and `_internal_href` both
    compare against, so the two can never quietly disagree about what
    counts as "the same site"."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _path_of(url: str, origin: str) -> str:
    """The origin-relative path (+ query, never a fragment) `url` names,
    for comparing a browser's REPORTED landing url (`snapshot()["url"]`,
    always absolute for a real `PlaywrightDriver`) back against the
    relative path this crawl queued it under. `url` that is already
    relative (no scheme/netloc -- `FakeBrowser`'s own convention for a
    seeded redirect target, e.g. `test_a_route_that_redirects_...`'s
    `"url": "/catalog"`) is returned unchanged: there is nothing to strip.
    An absolute `url` whose own origin does not match `origin` is ALSO
    returned unchanged (not rejected here -- point 1 in the module
    docstring only ever asks "did this redirect somewhere new", and an
    off-origin redirect target is exactly that; `_internal_href` is what
    later refuses to ever QUEUE it)."""
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    if f"{parsed.scheme}://{parsed.netloc}".lower() != origin:
        return url
    path = parsed.path or "/"
    return path + (f"?{parsed.query}" if parsed.query else "")


def _internal_href(href: str, origin: str | None = None) -> str | None:
    """The normalised, same-origin path `href` names, or `None` if it is
    not a route this crawl should ever queue.

    Fix round 2: normalise the way a browser's URL parser does BEFORE
    applying any rule, not after -- checking the raw href tests a
    different string than the one that actually gets fetched. In order:
    strip every character in `_STRIP_CHARS` from anywhere in the string
    (not just the edges -- `"/\t/evil.com"` has the tab in the middle,
    exactly where it needs to be to hide `//evil.com` from a naive
    prefix check); fold every backslash to a forward slash (WHATWG: for
    http/https and the other "special" schemes, `\` is equivalent to `/`
    everywhere in the URL, not only at the start -- so `"/\evil.com"`
    resolves identically to `"//evil.com"`, and a `\` that survives
    folding inside an otherwise-ordinary path, e.g. `"/a\b"` -> `"/a/b"`,
    is exactly what a browser would also do with it, not a special case).
    Only THEN do the existing `//`, scheme, and fragment checks below run,
    against the normalised string.

    Order matters within the checks themselves too: a protocol-relative
    URL (`//host/path`) MUST be rejected before the bare
    `stripped.startswith("/")` check, because it would otherwise pass that
    check -- a leading `//` reads as "internal" under a naive prefix test,
    but a real browser resolves it against the current scheme onto
    `host`, not onto this app. Checked before the scheme list too, since
    `//` is itself effectively a scheme marker (protocol-relative), not a
    path.

    Deliberately NOT percent-decoded: `"%2f%2fevil.com"` stays same-origin
    on purpose, because browsers do not percent-decode a relative
    reference before resolving it against the current document -- treating
    an encoded `//` as if it were a literal one would reject hrefs that
    are genuinely internal (a path segment that happens to contain an
    encoded slash is not a host boundary at all).

    Tier 2 (2026-08-30 contract, item 5): `origin`, when the caller knows
    one (Cartographer.run does, once `Workspace.target_url` is set), is
    what lets a FULLY-QUALIFIED same-origin href (`"https://acme.com/
    dashboard"` written out in full on acme.com's own page) resolve as
    internal -- something round 1 and round 2 both correctly rejected
    outright, because neither ever had an origin to resolve a bare path
    against and "doesn't start with /" was the only test available. This
    is strictly an EXTENSION of the existing rules, never a replacement:
    every check above still runs first, unchanged, against the same
    normalised string; `origin` only ever gets consulted for an href that
    survives all of them but still doesn't start with `/` -- and even
    then, `urljoin` resolves it and the RESULT's own origin is compared,
    never the raw string, so a backslash- or tab-hidden off-origin href
    (folded to `//evil.com` above) never reaches this branch at all: it
    was already rejected by the `//` check three paragraphs up, origin or
    no origin.
    """
    if not href:
        return None
    normalised = _normalise_href(href)

    if normalised.startswith("//"):
        return None
    lowered = normalised.lower()
    if any(lowered.startswith(scheme) for scheme in _NON_ROUTE_SCHEMES):
        return None
    stripped = normalised.split("#", 1)[0]  # point 3: a fragment is never a new page
    if stripped.startswith("/"):
        return stripped
    if origin is None:
        return None
    resolved = urljoin(origin + "/", stripped)
    if _origin_of(resolved) != origin:
        return None
    parsed = urlsplit(resolved)
    return (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")


def validate_target_url(url: str) -> str:
    """`""` when `url` is a usable `Workspace.target_url`; otherwise a
    plain-English reason a customer sees inline on the settings form
    (Tier 2 contract, item 3: "a malformed target discovered at run time
    costs a whole run; discovered at save time it costs a form error").

    Reuses `_normalise_href` -- the exact strip-then-fold-backslashes
    treatment `_internal_href` applies to a discovered link -- rather
    than writing a second normalisation, per the contract's own
    instruction. A target URL is not a relative href, so the checks past
    normalisation are necessarily different: `_internal_href` decides
    same-origin-or-not for a path found ON a page; this decides whether
    the string itself is even a valid ORIGIN to crawl in the first place
    -- `http`/`https` only (rejects `javascript:`, `file:`, `data:`, and
    everything else Cartographer already refuses to treat as a route,
    plus every scheme neither of those two ever needed an opinion on),
    and a real host (`http://` alone, or `http:///path`, parses with an
    empty `hostname` -- a scheme with nothing to resolve against is not
    an origin). `urlsplit` -- not a bespoke parser -- is what decides
    "off-origin": a normalised string that parses to a DIFFERENT origin
    than a naive read of the input suggests (a userinfo trick like
    `"http://trusted.example@evil.com/"`, whose real host is `evil.com`,
    not `trusted.example`) is caught by reading `.hostname`, never the
    raw netloc, the same discipline `_origin_of` uses everywhere else in
    this module.
    """
    normalised = _normalise_href((url or "").strip())
    if not normalised:
        return "set a target URL before running the fleet"
    if normalised.startswith("//"):
        return "must include an http:// or https:// scheme"
    parsed = urlsplit(normalised)
    if parsed.scheme not in ("http", "https"):
        return "must start with http:// or https://"
    if not parsed.hostname:
        return "must include a host, e.g. https://app.example.com"
    return ""


class Cartographer:
    name = "cartographer"

    def run(self, ctx) -> AgentResult:
        # Tier 2 (2026-08-30 contract, item 2): a workspace that exists but
        # has never had `target_url` set is the exact failure shape this
        # build has fought all the way through -- crawl nothing from "/",
        # a bare path with no origin, and report a green run. Checked
        # BEFORE the gateway is ever called (there is nothing to audit --
        # no read happened) and BEFORE a single browser call, so a
        # misconfigured workspace costs one cheap repo read, not a
        # started-then-abandoned crawl. `workspace is None` (no row seeded
        # at all) is deliberately NOT this case -- that is every agent
        # unit test in this file, none of which seeds a `Workspace`
        # (`tests/agent_fixtures.py`'s own documented convention: a
        # missing workspace means "use the defaults", not "misconfigured")
        # -- so only an ACTUAL workspace with a genuinely empty
        # `target_url` trips this.
        workspace = ctx.repo.workspace(ctx.workspace_id)
        if workspace is not None and not workspace.target_url:
            return AgentResult(
                summary="Cartographer needs a target URL",
                detail=(
                    "Workspace.target_url is not set, so there is nothing to crawl. "
                    "Set it in Settings → Workspace before running the fleet."
                ),
                outcome="error",
                data={"routes": [], "new": 0, "unreachable": []},
            )
        origin = _origin_of(workspace.target_url) if workspace is not None and workspace.target_url else None

        known = {r.path for r in ctx.repo.routes_for_workspace(ctx.workspace_id)}

        def crawl():
            seen: set[str] = set()
            routes: list[str] = []
            elements_by_route: dict[str, tuple[tuple[str, str, str], ...]] = {}
            unreachable: list[str] = []
            queue = ["/"]

            while queue and len(seen) < MAX_ROUTES:
                path = queue.pop(0)
                if path in seen:
                    continue
                seen.add(path)

                # Tier 2, item 5: once an origin is known, every
                # navigation happens in ABSOLUTE terms -- `urljoin`
                # against the workspace's own origin, never a bare
                # relative path handed straight to the driver (a real
                # `PlaywrightDriver.goto` has no base URL configured and
                # would simply fail to resolve one). `queue`/`seen`/
                # `routes` themselves stay relative-path-shaped
                # throughout -- unchanged from before this task, and
                # exactly what `Route.path` already stores -- only the
                # string actually handed to `ctx.browser.goto` changes.
                target = urljoin(origin + "/", path.lstrip("/")) if origin else path
                try:
                    ctx.browser.goto(target)
                except BrowserGotoError:
                    # A login wall or a broken link costs this one route,
                    # not the rest of the crawl still queued behind it --
                    # see point 4 in the module docstring.
                    unreachable.append(path)
                    continue

                snapshot = ctx.browser.snapshot()
                landed = snapshot.get("url", target) or target
                canonical = _path_of(landed, origin) if origin else landed
                if canonical != path:
                    # `path` redirected elsewhere -- see point 1. Not a
                    # route of its own; queue the real target instead,
                    # unless it is already known or already waiting.
                    if canonical not in seen and canonical not in queue:
                        queue.append(canonical)
                    continue

                routes.append(path)
                elements_by_route[path] = tuple(
                    (e.get("ref", ""), e.get("role", ""), e.get("name", ""))
                    for e in ctx.browser.a11y()
                )
                for href in ctx.browser.links():
                    href = _internal_href(href, origin)
                    if href and href not in seen and href not in queue:
                        queue.append(href)

            return routes, elements_by_route, unreachable

        # ONE gateway call for the WHOLE crawl, not one per page -- see the
        # module docstring's fix-round-1 note. `target` is necessarily
        # static (not "N routes crawled") because we do not know how many
        # pages this walk will touch until it is already done.
        routes, elements_by_route, unreachable = ctx.gateway.call(
            ctx.workspace_id, self.name, "browser.read",
            target=f"full site crawl (cap {MAX_ROUTES})", fn=crawl,
        )

        routes.sort()

        # ONE gateway call for the whole write, not one per route. Every
        # gateway call appends to the audit ledger, and the ledger's append is
        # transactional against a single per-workspace head document -- 47
        # routes would mean 47 serialised transactions, each contending on the
        # same document, for a write that is logically one act ("mapped the
        # surface"). It would also bury the ledger in noise nobody reads.
        def write_all():
            for path in routes:
                ctx.repo.put_route(Route(
                    id=f"rt_{uuid.uuid4().hex[:12]}", workspace_id=ctx.workspace_id,
                    path=path, coverage_pct=0, elements=elements_by_route.get(path, ())))
            return len(routes)

        ctx.gateway.call(ctx.workspace_id, self.name, "graph.write",
                          target=f"{len(routes)} routes", fn=write_all)

        new = len([p for p in routes if p not in known])
        detail = f"{new} new since the last run." if new else "No new routes."
        if unreachable:
            detail += f" {len(unreachable)} unreachable (login wall or broken link)."

        return AgentResult(
            summary=f"Mapped {len(routes)} routes",
            detail=detail,
            data={"routes": routes, "new": new, "unreachable": unreachable})
