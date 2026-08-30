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

Two gateway calls, neither looped: `graph.read` for the route list once,
`browser.read` for the whole route-by-route diff pass once (matching every
other batched crawl/audit in this fleet).
"""

import re

from agents.base import AgentResult

MAX_ROUTES = 50

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


def _compare_route(route: str, baseline, candidate, extra: list[re.Pattern]) -> list[dict]:
    baseline.goto(route)
    candidate.goto(route)
    b_snap, c_snap = baseline.snapshot(), candidate.snapshot()
    divergences = []

    b_status, c_status = b_snap.get("status", 200), c_snap.get("status", 200)
    if b_status != c_status:
        divergences.append(_divergence(route, "status_code_changed", b_status, c_status))

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
        divergences.sort(key=lambda d: (-d["blast_radius"], d["route"], d["type"]))

        return AgentResult(
            summary=f"Compared {len(paths)} route(s); {len(divergences)} divergence(s)",
            detail=f"{self.baseline_env} vs {self.candidate_env}.",
            data={"divergences": divergences, "compared": len(paths)},
        )
