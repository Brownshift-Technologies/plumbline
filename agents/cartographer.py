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
   `.startswith("/")` check** (fix round 1). `"/"` alone is not enough:
   `"//evil-cdn.example.com/x".startswith("/")` is ALSO `True` -- that is a
   protocol-relative URL, browsers resolve it against the CURRENT scheme
   onto a completely different host, and nothing upstream (a real
   `PlaywrightDriver.links()` hands back the raw href, unfiltered) would
   have caught an agent walking off the customer's site onto an
   attacker-chosen one. `javascript:`, `mailto:`, `tel:`, and `data:` hrefs
   are excluded for a different reason -- none of them is a route at all,
   crawling one would either do nothing, open a mail client, or hand
   Cartographer a data: URI to "visit". See `_internal_href`.
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

from agents.base import AgentResult
from agents.browser import BrowserGotoError
from app.models import Route

MAX_ROUTES = 300

# Never a route on THIS app -- see point 2 in the module docstring. Matched
# case-insensitively against the START of the href; a real browser treats
# "JavaScript:" and "javascript:" identically.
_NON_ROUTE_SCHEMES = ("javascript:", "mailto:", "tel:", "data:")


def _internal_href(href: str) -> str | None:
    """The normalised, same-origin path `href` names, or `None` if it is
    not a route this crawl should ever queue.

    Order matters: a protocol-relative URL (`//host/path`) MUST be
    rejected before the bare `href.startswith("/")` check below, because
    it would otherwise pass that check -- a leading `//` reads as
    "internal" under a naive prefix test, but a real browser resolves it
    against the current scheme onto `host`, not onto this app. Checked
    before the scheme list too, since `//` is itself effectively a scheme
    marker (protocol-relative), not a path.
    """
    if not href or href.startswith("//"):
        return None
    lowered = href.lower()
    if any(lowered.startswith(scheme) for scheme in _NON_ROUTE_SCHEMES):
        return None
    stripped = href.split("#", 1)[0]  # point 3: a fragment is never a new page
    return stripped if stripped.startswith("/") else None


class Cartographer:
    name = "cartographer"

    def run(self, ctx) -> AgentResult:
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

                try:
                    ctx.browser.goto(path)
                except BrowserGotoError:
                    # A login wall or a broken link costs this one route,
                    # not the rest of the crawl still queued behind it --
                    # see point 4 in the module docstring.
                    unreachable.append(path)
                    continue

                snapshot = ctx.browser.snapshot()
                canonical = snapshot.get("url", path) or path
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
                    href = _internal_href(href)
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
