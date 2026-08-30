"""Task 12f: Oracle -- differential testing across two live environments.

Every other agent in this fleet asserts against something a human or a
model decided was correct in advance. Oracle needs no such oracle of
correctness: it drives the SAME route on two environments (staging versus
production, this branch versus main -- `ctx.browsers[baseline_env]` and
`ctx.browsers[candidate_env]`, the two-driver context Task 9's fix round 1
added `AgentContext.browsers` specifically for) and reports where the two
disagree. That catches the class of regression nobody wrote an assertion
for, which is the same insight Antithesis is built on.

**Comparison is structural, never pixels.** A pixel diff on a dynamic app
is noise -- ads, A/B buckets, animation frame timing, font rendering all
differ between two otherwise-identical environments, and noise is what
kills a differential-testing tool in practice. This module compares four
things per route instead: the accessibility tree (`a11y()`, role+name
pairs), visible text, the response status, and a coarse "network shape"
(request paths). All four already exist on `BrowserDriver` or its
`snapshot()` for other agents' benefit; nothing here needs a new capture.

**Volatility is masked before comparison, by rule.** `_mask` strips
timestamps, UUID/session-id-shaped strings, CSRF-token assignments, and
`$`-prefixed live pricing -- the brief's own list -- from both sides
BEFORE they are ever compared, plus whatever extra `volatile` regex a
caller configures (Oracle's own constructor argument). Skipping this is
the single most common reason differential testing fails in the field: a
report that cries wolf on every run's own footer timestamp trains
everyone to ignore it, including the one time it is right.

**Ranked by blast radius, never by discovery order.** `_blast_radius`
weighs a divergence by its TYPE (a missing/changed element outranks a
changed status which outranks a text wobble... except one case: a 5xx
appearing where the other side is still 2xx is scored as critical
UNCONDITIONALLY, regardless of route -- a candidate that started 500-ing
somewhere is never "low priority" just because the route's name doesn't
look important) and by whether the ROUTE itself matches a critical-path
pattern (`checkout`, `payment`, `billing`, `cart`) -- a changed button
label on `/checkout/payment` outranks a changed footer year on `/about`
even though both are, mechanically, "an element's name changed".

**A divergence is a finding, never an auto-patch.** `Oracle`'s own
`SCOPES` entry (`gateway/policy.py`) is `{"browser.read", "graph.read"}` --
no write tool of any kind. Two environments differing is information, not
a defect on its own; only a human (or a downstream Surgeon acting on a
Triager finding derived from an actual test failure, never from this
module directly) knows which side is right. This module never calls
`ctx.repo.put_finding`/`put_patch`/anything -- it only returns data.

**Consolidated, never one-entry-per-symptom.** Fix round 1: a reviewer
built a 20-route total-outage scenario (the candidate environment simply
down) and the pre-fix version returned 160 divergences -- a status change,
a text change, a network change, and up to five element changes, PER
ROUTE, all saying the same one thing twenty different ways. The brief's
own acid test is "one useful finding or five hundred useless ones", and a
tool that cries wolf at that volume trains everyone to ignore it, the same
failure mode `_mask`'s volatility handling above exists to prevent for a
single route. Two levels of roll-up fix this, both applied before ranking:

1. **Per-route**: `_compare_route` checks status FIRST and, if it
   diverges, returns immediately -- text/a11y/network are never even
   compared for that route. A page returning 500 has no meaningful DOM to
   diff against a 200 page's; the sub-comparisons would only ever add
   noise once the status itself has already said everything.
2. **Environment-wide**: `_rollup` groups the per-route divergences that
   remain by their exact `(type, baseline, candidate)` signature and, when
   the SAME signature recurs across at least half of the routes compared
   (and more than one), collapses that whole group into ONE
   `"environment_wide:<type>"` finding naming every affected route, rather
   than reporting the identical fact once per route. A genuinely isolated
   divergence -- the two-divergence blast-radius test below, where
   `/checkout/payment` and `/footer` diverge on DIFFERENT signatures --
   never meets that majority threshold and is reported exactly as before.

A route made unreachable outright (`BrowserGotoError` -- the same "this
route doesn't resolve at all" shape `agents/cartographer.py` already
treats as a routing fact, not a crash) is folded into the same
status-comparison path via `_goto_status`, which reports a synthetic
`status="unreachable"` rather than letting the exception propagate and
abort the whole batch over one dead route on one side.

Two gateway calls, neither looped: `graph.read` for the route list once,
`browser.read` for the whole route-by-route diff pass once (matching every
other batched crawl/audit in this fleet).
"""

import re

from agents.base import AgentResult
from agents.browser import BrowserGotoError

MAX_ROUTES = 50
# A signature shared by at least this fraction of the routes compared (and
# by more than one route) is treated as an environment-wide fact, not a
# per-route coincidence -- see `_rollup` and the module docstring.
ROLLUP_MAJORITY = 0.5

_CRITICAL_ROUTE = re.compile(r"checkout|payment|billing|cart", re.I)

# The brief's own volatile list: timestamps, session/CSRF-token-shaped
# strings, and live pricing. Applied to both sides before any comparison,
# unconditionally -- a caller's own `volatile` patterns are additive, never
# a replacement for these.
_DEFAULT_VOLATILE = [
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    re.compile(r"\bsess[_-]?[A-Za-z0-9]{6,}\b", re.I),
    re.compile(r"csrf[-_]?token[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._-]+", re.I),
    re.compile(r"\$\d+(?:\.\d{2})?"),
]


def _mask(text: str, extra: list[re.Pattern]) -> str:
    if not text:
        return text
    masked = text
    for pattern in (*_DEFAULT_VOLATILE, *extra):
        masked = pattern.sub("%VOLATILE%", masked)
    return masked


def _a11y_set(elements: list[dict], extra: list[re.Pattern]) -> frozenset:
    return frozenset((e.get("role", ""), _mask(e.get("name", ""), extra)) for e in elements)


def _blast_radius(route: str, kind: str, baseline, candidate) -> int:
    base = {
        "status_code_changed": 60, "missing_element": 50, "added_element": 30,
        "text_changed": 20, "network_changed": 15,
    }.get(kind, 10)
    if kind == "status_code_changed":
        codes = {c for c in (baseline, candidate) if isinstance(c, int)}
        if any(c >= 500 for c in codes):
            base += 200  # unconditional: a 5xx anywhere outranks route weighting entirely
    if _CRITICAL_ROUTE.search(route or ""):
        base *= 2
    return base


def _severity(blast_radius: int) -> str:
    if blast_radius >= 150:
        return "critical"
    if blast_radius >= 60:
        return "high"
    if blast_radius >= 20:
        return "medium"
    return "low"


def _divergence(route: str, kind: str, baseline, candidate) -> dict:
    radius = _blast_radius(route, kind, baseline, candidate)
    return {
        "route": route, "type": kind, "baseline": baseline, "candidate": candidate,
        "severity": _severity(radius), "blast_radius": radius,
    }


def _goto_status(driver, route: str) -> dict:
    """Navigate `driver` to `route` and return its snapshot -- or, if the
    route is entirely unreachable on this environment at all
    (`BrowserGotoError`), a synthetic snapshot reporting
    `status="unreachable"` so the rest of this module needs no special
    case for a side that never loaded. This is what lets a "candidate is
    completely down" scenario (every route refuses to connect, rather than
    connecting and returning a 5xx) fold into the exact same status-
    comparison and roll-up path as an ordinary status-code change."""
    try:
        driver.goto(route)
    except BrowserGotoError:
        return {"status": "unreachable"}
    return driver.snapshot()


def _compare_route(route: str, baseline, candidate, extra: list[re.Pattern]) -> list[dict]:
    b_snap = _goto_status(baseline, route)
    c_snap = _goto_status(candidate, route)

    # Status is checked FIRST and, if it diverges, ends this route's
    # comparison right here -- see the module docstring's roll-up section.
    # A page that isn't even returning the same status has no meaningful
    # DOM/network shape to diff against the other side's; comparing them
    # anyway is exactly the "up to four findings that all say one thing"
    # shape a reviewer demonstrated at 20-route scale.
    b_status, c_status = b_snap.get("status", 200), c_snap.get("status", 200)
    if b_status != c_status:
        return [_divergence(route, "status_code_changed", b_status, c_status)]

    divergences = []

    b_text = _mask(b_snap.get("text", ""), extra)
    c_text = _mask(c_snap.get("text", ""), extra)
    if b_text != c_text:
        divergences.append(_divergence(route, "text_changed", b_snap.get("text", ""), c_snap.get("text", "")))

    b_a11y = _a11y_set(baseline.a11y(), extra)
    c_a11y = _a11y_set(candidate.a11y(), extra)
    for role, name in sorted(b_a11y - c_a11y):
        divergences.append(_divergence(route, "missing_element", {"role": role, "name": name}, None))
    for role, name in sorted(c_a11y - b_a11y):
        divergences.append(_divergence(route, "added_element", None, {"role": role, "name": name}))

    b_net = sorted(b_snap.get("network", []))
    c_net = sorted(c_snap.get("network", []))
    if b_net != c_net:
        divergences.append(_divergence(route, "network_changed", b_net, c_net))

    return divergences


def _signature(d: dict) -> tuple:
    # `repr(...)` on baseline/candidate rather than the raw values: some
    # divergence payloads are dicts/lists (`missing_element`,
    # `network_changed`), which are not hashable and cannot be dict keys
    # directly -- their repr is a stable enough proxy for "the same fact".
    return (d["type"], repr(d["baseline"]), repr(d["candidate"]))


def _rollup(divergences: list[dict], total_routes: int) -> list[dict]:
    """Collapse an environment-wide divergence -- the exact same `(type,
    baseline, candidate)` triple recurring across a majority of the routes
    compared -- into ONE finding, rather than reporting the identical fact
    once per route. See the module docstring's fix-round-1 note for the
    160-divergence total-outage scenario this exists to prevent.

    A signature shared by only one route (however severe) is never rolled
    up -- roll-up is specifically for "this is a fact about the
    ENVIRONMENT", never a way to hide a single route's own real
    divergence."""
    if total_routes < 2 or not divergences:
        return divergences

    groups: dict[tuple, list[dict]] = {}
    for d in divergences:
        groups.setdefault(_signature(d), []).append(d)

    rolled: list[dict] = []
    for group in groups.values():
        routes = sorted({d["route"] for d in group})
        if len(routes) > 1 and len(routes) >= max(2, total_routes * ROLLUP_MAJORITY):
            sample = group[0]
            rolled.append({
                "route": "*", "type": f"environment_wide:{sample['type']}",
                "baseline": sample["baseline"], "candidate": sample["candidate"],
                "severity": "critical",
                "blast_radius": max(d["blast_radius"] for d in group),
                "affected_routes": routes,
            })
        else:
            rolled.extend(group)
    return rolled


class Oracle:
    name = "oracle"

    def __init__(self, baseline_env: str, candidate_env: str, volatile: list[str] | None = None):
        self.baseline_env = baseline_env
        self.candidate_env = candidate_env
        self.volatile = [re.compile(p, re.I) for p in (volatile or [])]

    def run(self, ctx) -> AgentResult:
        routes = ctx.gateway.call(
            ctx.workspace_id, self.name, "graph.read",
            target=f"routes for {ctx.workspace_id}",
            fn=lambda: ctx.repo.routes_for_workspace(ctx.workspace_id),
        )
        paths = [r.path for r in routes][:MAX_ROUTES] or ["/"]
        baseline = ctx.browsers[self.baseline_env]
        candidate = ctx.browsers[self.candidate_env]

        def compare_all():
            divergences = []
            for path in paths:
                divergences += _compare_route(path, baseline, candidate, self.volatile)
            return divergences

        divergences = ctx.gateway.call(
            ctx.workspace_id, self.name, "browser.read",
            target=f"diff of {len(paths)} route(s): {self.baseline_env} vs {self.candidate_env}",
            fn=compare_all,
        )
        divergences = _rollup(divergences, len(paths))
        divergences.sort(key=lambda d: (-d["blast_radius"], d["route"], d["type"]))

        return AgentResult(
            summary=f"Compared {len(paths)} route(s); {len(divergences)} divergence(s)",
            detail=f"{self.baseline_env} vs {self.candidate_env}.",
            data={"divergences": divergences, "compared": len(paths)},
        )
