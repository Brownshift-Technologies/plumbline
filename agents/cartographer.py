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
Three things past the bare walk are worth calling out, because a real site
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
2. **A fragment never triggers a new page load.** `/catalog#reviews` and
   `/catalog` are the same route by construction -- an in-page anchor jump,
   never a fresh navigation -- so the fragment is stripped before a
   discovered link is ever queued or compared against `seen`. A query
   string is NOT stripped the same way: `/catalog?category=shoes` can be
   materially different content from `/catalog` (a filtered view a
   customer can reach and a bug can hide in), and collapsing it away would
   silently drop real surface from the map. Fragments are always
   equivalent; query strings are not assumed to be.
3. **A route this crawl cannot reach must not take the whole crawl down.**
   An app with a login wall where most links 401, or a page that is
   genuinely gone, raises `BrowserGotoError` (see `agents/browser.py`) --
   caught here specifically, one path at a time, so one broken or
   unauthenticated link costs the crawl exactly that one route, not every
   route still queued behind it. A `GatewayError` -- a policy DENIAL, not a
   navigation failure -- is deliberately NOT swallowed the same way: that
   is a decision about what Cartographer is allowed to do, not a fact
   about the site, and hiding it as "just another unreachable page" would
   turn a real authorisation problem invisible.

The write at the end is the one gateway call every route in one run shares
-- see `write_all` below for why a per-route write would be the wrong
shape entirely, not just a slower one.
"""

import uuid

from agents.base import AgentResult
from agents.browser import BrowserGotoError
from app.models import Route
from gateway.gateway import GatewayError

MAX_ROUTES = 300


class Cartographer:
    name = "cartographer"

    def run(self, ctx) -> AgentResult:
        known = {r.path for r in ctx.repo.routes_for_workspace(ctx.workspace_id)}
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

            def visit(p=path):
                ctx.browser.goto(p)
                snapshot = ctx.browser.snapshot()
                return {
                    # `snapshot()` always carries "url" (see FakeBrowser
                    # and PlaywrightDriver's own snapshot()), but `.get`
                    # with a fallback to `p` costs nothing and means a
                    # driver that ever omitted it would still behave as
                    # "no redirect happened" rather than crashing here.
                    "canonical": snapshot.get("url", p),
                    "links": ctx.browser.links(),
                    "elements": tuple(
                        (e.get("ref", ""), e.get("role", ""), e.get("name", ""))
                        for e in ctx.browser.a11y()
                    ),
                }

            try:
                result = ctx.gateway.call(
                    ctx.workspace_id, self.name, "browser.read", target=path, fn=visit
                ) or {}
            except GatewayError:
                # A policy decision, not a broken page -- see point 3 in
                # the module docstring. Must surface, not be folded into
                # "unreachable" alongside a genuine 401.
                raise
            except BrowserGotoError:
                unreachable.append(path)
                continue

            canonical = result.get("canonical") or path
            if canonical != path:
                # `path` redirected elsewhere -- see point 1 above. Not a
                # route of its own; queue the real target instead, unless
                # it is already known or already waiting to be visited.
                if canonical not in seen and canonical not in queue:
                    queue.append(canonical)
                continue

            routes.append(path)
            elements_by_route[path] = result.get("elements", ())
            for href in result.get("links", []):
                href = href.split("#", 1)[0]  # point 2: a fragment is never a new page
                if href.startswith("/") and href not in seen and href not in queue:
                    queue.append(href)

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
