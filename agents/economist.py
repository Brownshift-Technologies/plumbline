"""Task 12g: Economist -- the suite's own health and cost, recommend-only.

Every other agent in this fleet adds tests. Nothing removes them, and a
testing product that only ever grows its own suite eventually costs more
than it saves -- the honest objection a buyer raises in year two.
Economist is the answer to "what does this cost me in twelve months", and
it answers with EVIDENCE, never with a delete.

**Holds no write scope at all -- `gateway/policy.py`'s own SCOPES entry
for `"economist"` is `{"graph.read", "repo.read"}`, no write tool of any
kind, and that is enforced in code no workspace rule can widen (see that
module's docstring). An agent that judges which tests are wasteful and can
ALSO delete them is one prompt-injected recommendation away from doing
exactly that; making it structurally incapable is the only version of
"recommends only" a customer should trust. `test_it_holds_no_write_scope`
asserts this directly against `SCOPES`, not just against this module's own
behaviour.

**No dedicated run-history store exists yet.** This task's brief asks for
signals (a green streak, a repair count, a typical duration, "asserts the
same thing as another test") that nothing else in this fleet currently
persists per-behaviour -- `Step`/`Run` (app/models.py) record run-level
aggregates, not per-spec history. Rather than inventing a new Firestore
collection this task was never scoped to design, Economist reads these
signals off `Behaviour.tags` (already a free-form `tuple[str, ...]` field)
using a small `"key:value"` convention documented in `_tag_values`/
`_tag_str` below: `"green_streak:40"`, `"repairs:5"`, `"duration_ms:9000"`,
`"asserts:checkout-total"`. This is a real, named gap -- flagged again in
this task's report -- rather than a silent assumption: a production
deployment needs Runner/Healer to actually WRITE these tags before
Economist's recommendations mean anything, which is outside this task's
own scope. A workspace with no tagged history at all is not an error case:
every category below defaults to "nothing to flag" rather than
mis-reading absent data as a signal (see `test_a_suite_with_no_history_
produces_no_recommendations`).

**Every recommendation carries both numbers.** `_recommendation` requires
`minutes_saved` and `coverage_cost` as arguments -- there is no code path
that can build one with only the runtime side of the tradeoff. `coverage_
cost` is the route's own `coverage_pct` divided across however many active
behaviours share that route (a rough proxy, not a real per-assertion
coverage measure -- the same honesty this module owes about the tags
convention above).

**A Sentinel-written behaviour is never considered at all**, for any
category, regardless of streak length -- filtered out of the candidate
pool before any of the four checks ever run. `Behaviour.tags` carries
`"sentinel"` for exactly this (see `agents/sentinel.py`); that test is the
permanent record of something that actually broke in production, and no
amount of "hasn't failed in months" makes it safe to flag for removal.

Two gateway calls, neither looped: `graph.read` for the route list,
`repo.read` for behaviours and incidents (the latter, via `core.urls.
route_of`, is what tells "never-failed" apart from "never-failed AND never
mattered" -- see the module-wide URL-normalisation rule in
`core/urls.py`).
"""

import re

from agents.base import AgentResult
from core.urls import route_of

MIN_GREEN_STREAK = 20
MIN_REPAIRS = 3
LOW_COVERAGE_PCT = 50
SLOW_PERCENTILE = 0.9

_TAG_KV = re.compile(r"^([a-z_]+):(-?\d+(?:\.\d+)?)$")


def _tag_values(tags: tuple[str, ...]) -> dict[str, float]:
    values = {}
    for tag in tags:
        m = _TAG_KV.match(tag)
        if m:
            values[m.group(1)] = float(m.group(2))
    return values


def _tag_str(tags: tuple[str, ...], prefix: str) -> str | None:
    for tag in tags:
        if tag.startswith(prefix):
            return tag[len(prefix):]
    return None


def _is_sentinel_written(behaviour) -> bool:
    return "sentinel" in behaviour.tags


def _recommendation(kind: str, behaviour, minutes_saved: float, coverage_cost: float, reason: str) -> dict:
    return {
        "type": kind, "spec_path": behaviour.spec_path, "route": behaviour.route,
        "reason": reason, "minutes_saved": round(minutes_saved, 2), "coverage_cost": round(coverage_cost, 2),
    }


class Economist:
    name = "economist"

    def run(self, ctx) -> AgentResult:
        routes = ctx.gateway.call(
            ctx.workspace_id, self.name, "graph.read",
            target=f"routes for {ctx.workspace_id}",
            fn=lambda: ctx.repo.routes_for_workspace(ctx.workspace_id),
        )

        def read_suite():
            return (
                ctx.repo.behaviours_for_workspace(ctx.workspace_id),
                ctx.repo.incidents_for_workspace(ctx.workspace_id),
            )

        behaviours, incidents = ctx.gateway.call(
            ctx.workspace_id, self.name, "repo.read",
            target=f"suite for {ctx.workspace_id}", fn=read_suite,
        )

        route_by_path = {r.path: r for r in routes}
        incident_routes = {route_of(i.url) for i in incidents}
        route_counts: dict[str, int] = {}
        for b in behaviours:
            if b.status == "active":
                route_counts[b.route] = route_counts.get(b.route, 0) + 1

        def coverage_cost(b) -> float:
            route = route_by_path.get(b.route)
            pct = route.coverage_pct if route else 0
            return pct / max(1, route_counts.get(b.route, 1))

        def minutes(b) -> float:
            return _tag_values(b.tags).get("duration_ms", 0.0) / 60000

        # Sentinel-written behaviours are excluded from the candidate pool
        # entirely -- see the module docstring.
        active = [b for b in behaviours if b.status == "active" and not _is_sentinel_written(b)]

        recommendations = []
        recommendations += self._never_failed(active, incident_routes, coverage_cost, minutes)
        recommendations += self._chronically_flaky(active, coverage_cost, minutes)
        recommendations += self._slow(active, route_by_path, coverage_cost, minutes)
        recommendations += self._redundant(active, coverage_cost, minutes)

        by_spec = {r["spec_path"]: r for r in recommendations}
        minutes_saved = round(sum(r["minutes_saved"] for r in by_spec.values()), 2)
        coverage_delta = round(-sum(r["coverage_cost"] for r in by_spec.values()), 2)

        return AgentResult(
            summary=f"{len(by_spec)} behaviour(s) flagged across {len(recommendations)} recommendation(s)",
            detail=f"{minutes_saved} minute(s) potentially saved; {coverage_delta} coverage point(s) at risk.",
            data={
                "recommendations": recommendations,
                "minutes_saved": minutes_saved,
                "coverage_delta": coverage_delta,
            },
        )

    def _never_failed(self, active, incident_routes, coverage_cost, minutes) -> list[dict]:
        out = []
        for b in active:
            streak = _tag_values(b.tags).get("green_streak", 0)
            if streak >= MIN_GREEN_STREAK and b.route not in incident_routes:
                out.append(_recommendation(
                    "never_failed", b, minutes(b), coverage_cost(b),
                    f"Green for {int(streak)} consecutive run(s); {b.route!r} has never had a "
                    "production incident -- low information for its ongoing cost.",
                ))
        return out

    def _chronically_flaky(self, active, coverage_cost, minutes) -> list[dict]:
        out = []
        for b in active:
            repairs = _tag_values(b.tags).get("repairs", 0)
            if repairs >= MIN_REPAIRS:
                out.append(_recommendation(
                    "chronically_flaky", b, minutes(b), coverage_cost(b),
                    f"Repaired by Healer {int(repairs)} time(s) -- costs more than it catches.",
                ))
        return out

    def _slow(self, active, route_by_path, coverage_cost, minutes) -> list[dict]:
        durations = sorted(_tag_values(b.tags).get("duration_ms", 0.0) for b in active)
        if not durations:
            return []
        cutoff = durations[int(SLOW_PERCENTILE * (len(durations) - 1))]
        out = []
        for b in active:
            duration = _tag_values(b.tags).get("duration_ms", 0.0)
            route = route_by_path.get(b.route)
            pct = route.coverage_pct if route else 0
            if duration > 0 and duration >= cutoff and pct < LOW_COVERAGE_PCT:
                out.append(_recommendation(
                    "slow", b, minutes(b), coverage_cost(b),
                    f"Top-decile duration ({round(minutes(b), 2)} min) for only {pct}% "
                    f"coverage on {b.route!r} -- not covering enough to justify the cost.",
                ))
        return out

    def _redundant(self, active, coverage_cost, minutes) -> list[dict]:
        groups: dict[tuple[str, str], list] = {}
        for b in active:
            fingerprint = _tag_str(b.tags, "asserts:")
            if fingerprint:
                groups.setdefault((b.route, fingerprint), []).append(b)
        out = []
        for (route, fingerprint), group in groups.items():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda x: x.id)
            keeper = ordered[0]
            for b in ordered[1:]:
                out.append(_recommendation(
                    "redundant", b, minutes(b), coverage_cost(b),
                    f"Asserts the same thing ({fingerprint!r}) on {route!r} as {keeper.spec_path!r}.",
                ))
        return out
