"""Task 12d: Sentinel -- turns a production incident into a permanent
regression test, or a finding for a human when it cannot.

Everything else in this fleet tests what someone thought to test.
Sentinel starts from what actually broke: an incoming batch of
`Incident` rows (app/models.py) -- a Sentry-shaped payload, a Cloud
Logging error group, a support ticket, already ingested into this
workspace's `incidents` collection by whatever webhook or importer put
them there. This module's job starts at "given these incidents", not at
"given a raw Sentry payload" -- ingestion into `Incident` rows is a
separate concern this task does not own.

Four things worth calling out, because a real incident feed is nothing
like a clean one-row-per-bug fixture:

1. **Clustering, not counting.** `_dedup_key` normalises a message by
   collapsing every digit run to `<n>` and every UUID-shaped run to `<id>`
   before pairing it with the incident's own route -- 4,000 occurrences of
   "order 48213 failed at 2026-08-30T12:00:03Z" and "order 91847 failed at
   2026-08-30T14:22:11Z" collapse to the SAME cluster key, because the only
   thing that matters for "is this one behaviour" is the shape of the
   failure, never the id or timestamp riding along inside it. Counting the
   raw strings instead would write one behaviour per near-duplicate
   message -- the opposite of what this agent exists to do.
2. **Reproduce before writing, always.** `_reproduce` drives the browser to
   the clustered route and checks whether the failure still occurs (a
   `BrowserGotoError` on that route stands in for "the error recurs" --
   see `agents/browser.py`'s own docstring for why a seeded page `error`
   is the fixture-friendly shape of a real 500/crash). A cluster that does
   NOT reproduce is never written as a green test masquerading as
   coverage -- it is recorded as a `Finding` with
   `status="not_reproducible"` for a human to look at instead. This is the
   same discipline `agents/healer.py` and `agents/surgeon.py` already
   follow: nothing in this fleet writes a passing artefact for a failure
   it never actually witnessed happening.
3. **An unmapped route is flagged, never silently dropped.** An incident
   whose route (after normalising its URL through `core.urls.route_of` --
   see that module for why a URL has to be read the way a browser would
   before it is trusted at all) is not in `ctx.repo.routes_for_workspace`
   is left out of clustering/reproduction entirely and instead recorded as
   its own `Finding` (`status="unmapped"`) naming the route -- the signal
   a human or the next Cartographer crawl needs to close the gap, rather
   than an incident this run just quietly never mentioned again.
4. **PII is handled structurally, not by this module remembering to
   scrub it.** Every incident is read through the single `telemetry.read`
   gateway call below, as PLAIN DICTS (`app.models.to_dict`, not the
   frozen `Incident` objects themselves -- `core.guards.redact_deep` only
   walks dict/list/tuple/str, so a raw dataclass would sail through
   unredacted). `Gateway.call` redacts the RESULT of every `.read` tool
   automatically (see `gateway/gateway.py`'s own docstring, point 5) --
   by the time this module ever looks at `inc["message"]` or
   `inc["stack"]`, a card number or an email address embedded in a
   production error has already become `[CARD]`/`[EMAIL]`.

Two gateway calls per run, neither looped:

1. `telemetry.read` -- reads every open incident for the workspace, once.
2. `repo.write:specs` -- persists every reproduced cluster's spec+behaviour
   AND every unreproducible/unmapped cluster's `Finding`, in one call for
   the whole batch (Sentinel's `SCOPES` entry has no separate scope for
   `Finding` writes -- see `gateway/policy.py` -- so this bookkeeping rides
   the one write scope Sentinel does hold, the same way
   `agents/surgeon.py` bundles its own Finding-status update into
   `repo.write:src` rather than inventing a scope this task's brief never
   asked for). Also marks every incident this run acted on `status=
   "clustered"` so a later Sentinel run does not re-process the same
   occurrences forever.
"""

import re
import uuid

from agents.base import AgentResult
from agents.browser import BrowserGotoError
from app.models import Behaviour, Finding, to_dict
from core.urls import route_of

_DIGITS = re.compile(r"\d+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_WHITESPACE = re.compile(r"\s+")


def _dedup_key(message: str, route: str) -> str:
    """Normalised message + route -- see the module docstring's point 1.
    Order matters: UUIDs are collapsed before the bare digit-run pass,
    since a digit-run collapse alone would leave the hex letters of a
    UUID's non-numeric segments behind as noise that still varies between
    two occurrences of the same underlying failure."""
    text = _UUID.sub("<id>", message or "")
    text = _DIGITS.sub("<n>", text)
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return f"{route}|{text}"


def _reproduce(ctx, route: str) -> bool:
    try:
        ctx.browser.goto(route)
    except BrowserGotoError:
        return True
    return False


def _spec_path(route: str, key: str) -> str:
    slug = route.strip("/").replace("/", "-") or "home"
    return f"specs/sentinel-{slug}-{abs(hash(key)) % 100000}.spec.ts"


def _prompt(cluster: dict) -> str:
    return (
        f"Write exactly one Playwright test(...) block that reproduces a "
        f"real production incident on route {cluster['route']!r}.\n"
        f"Error signature: {cluster['message']!r}\n"
        "The test should navigate to the route and assert the failure "
        "recurs (a rejected/failed navigation, an error state visible on "
        "the page, or a specific broken behaviour). Use await for every "
        "action. Return only the test(...) block."
    )


class Sentinel:
    name = "sentinel"

    def run(self, ctx) -> AgentResult:
        def read_incidents():
            return [
                to_dict(i) for i in ctx.repo.incidents_for_workspace(ctx.workspace_id)
                if i.status == "open"
            ]

        raw_incidents = ctx.gateway.call(
            ctx.workspace_id, self.name, "telemetry.read",
            target=f"open incidents for {ctx.workspace_id}", fn=read_incidents,
        )

        if not raw_incidents:
            return AgentResult(
                summary="No open incidents", outcome="ok",
                data={"incidents": [], "reproduced": 0, "behaviours_written": []},
            )

        known_routes = {r.path for r in ctx.repo.routes_for_workspace(ctx.workspace_id)}

        clusters: dict[str, dict] = {}
        unmapped: dict[str, dict] = {}
        for inc in raw_incidents:
            route = route_of(inc.get("url", ""))
            if route not in known_routes:
                key = f"unmapped|{route}"
                c = unmapped.setdefault(key, {"route": route, "message": inc.get("message", ""),
                                               "ids": [], "count": 0})
                c["ids"].append(inc["id"])
                c["count"] += inc.get("count", 1)
                continue
            key = _dedup_key(inc.get("message", ""), route)
            c = clusters.setdefault(key, {"route": route, "message": inc.get("message", ""),
                                           "stack": inc.get("stack", ""), "ids": [], "count": 0})
            c["ids"].append(inc["id"])
            c["count"] += inc.get("count", 1)

        reproduced, not_reproducible = [], []
        for key, cluster in clusters.items():
            (reproduced if _reproduce(ctx, cluster["route"]) else not_reproducible).append((key, cluster))

        payload = {"messages": " ".join(c["message"] for c in clusters.values()),
                   "unmapped_routes": " ".join(c["route"] for c in unmapped.values())}

        def write_all():
            written = []
            for key, cluster in reproduced:
                text = ctx.model.generate(_prompt(cluster))
                spec_path = _spec_path(cluster["route"], key)
                ctx.repo.put_spec(ctx.workspace_id, spec_path, text)
                ctx.repo.put_behaviour(Behaviour(
                    id=f"bh_sentinel_{uuid.uuid4().hex[:12]}", workspace_id=ctx.workspace_id,
                    text=f"Regression (from production): {cluster['message'][:180]}",
                    route=cluster["route"], spec_path=spec_path, tags=("sentinel", "incident"),
                ))
                written.append(spec_path)
            for key, cluster in not_reproducible:
                ctx.repo.put_finding(Finding(
                    id=f"fnd_sentinel_{uuid.uuid4().hex[:12]}", workspace_id=ctx.workspace_id,
                    title=f"Could not reproduce: {cluster['message'][:180]}",
                    route=cluster["route"], found_by=self.name, status="not_reproducible",
                    severity="medium", repro_count=0,
                ))
            for key, cluster in unmapped.items():
                ctx.repo.put_finding(Finding(
                    id=f"fnd_sentinel_{uuid.uuid4().hex[:12]}", workspace_id=ctx.workspace_id,
                    title=f"Incident on an unmapped route -- needs a re-crawl: {cluster['message'][:120]}",
                    route=cluster["route"], found_by=self.name, status="unmapped", severity="medium",
                ))
            all_ids = [i for c in clusters.values() for i in c["ids"]] + \
                      [i for c in unmapped.values() for i in c["ids"]]
            for inc in ctx.repo.incidents_for_workspace(ctx.workspace_id):
                if inc.id in all_ids:
                    ctx.repo.put_incident(type(inc)(**{**inc.__dict__, "status": "clustered"}))
            return written

        behaviours_written = ctx.gateway.call(
            ctx.workspace_id, self.name, "repo.write:specs",
            target=f"{len(clusters) + len(unmapped)} cluster(s)", payload=payload, fn=write_all,
        )

        incidents_out = (
            [{"route": c["route"], "message": c["message"], "count": c["count"], "status": "reproduced"}
             for _, c in reproduced]
            + [{"route": c["route"], "message": c["message"], "count": c["count"], "status": "not_reproducible"}
               for _, c in not_reproducible]
            + [{"route": c["route"], "message": c["message"], "count": c["count"], "status": "unmapped"}
               for c in unmapped.values()]
        )

        detail = (f"{len(reproduced)} reproduced and written, "
                  f"{len(not_reproducible)} not reproducible, "
                  f"{len(unmapped)} on an unmapped route.")

        return AgentResult(
            summary=f"Processed {len(raw_incidents)} incident(s) into {len(clusters) + len(unmapped)} cluster(s)",
            detail=detail,
            data={
                "incidents": incidents_out,
                "reproduced": len(reproduced),
                "behaviours_written": behaviours_written,
            },
        )
