"""Task 12e: Auditor -- accessibility and security findings from data the
fleet already captures on every route, for free.

Tasks 9/10 already snapshot the accessibility tree (`a11y()`) and the
role-less clickable set (`interactive()`) on every route Cartographer
visits, for Healer's benefit. This module reads the exact same two calls,
plus `headers()`/`cookies()` (also already on `BrowserDriver`, added for
this task's own benefit -- see `agents/browser.py`'s module docstring) and
answers a different question with them: not "what locator should Healer
use" but "is this control usable by a screen reader" and "is this response
safe by default".

**Auditor inspects. It never attacks.** Every browser call this module
makes is a plain read -- `goto`, `headers`, `cookies`, `a11y`,
`interactive`, `snapshot` -- against the route exactly as the graph names
it. Nothing here ever appends a query string, a header, or a body to a
request; nothing here ever constructs an exploit string and sends it
anywhere. That is not a stylistic choice: an agent this fleet, or a
customer, might point at a PRODUCTION deployment has to be safe to run
there by construction, not by discipline. `test_it_never_issues_a_request_
carrying_a_payload` asserts every URL this module ever visits is exactly a
known route's own path -- nothing appended, nothing templated in.

Findings carry a severity and the exact element or header, never a vague
"improve accessibility" -- see `_a11y_finding`/`_security_finding`, both of
which take the concrete offending value as a required argument, so there
is no code path that can produce a finding without one.

Two judgement calls beyond the brief's own four security checks:

- **A CSP that exists but is `unsafe-inline` is still a finding.** A
  present-but-toothless CSP is a common real-world middle state (an app
  that "has a CSP" in the sense of shipping the header, but one so loose
  it blocks nothing) -- treating "header present" as "check passed" would
  miss exactly the deployments most likely to need this agent's help.
- **A page served with NO headers at all** (an empty dict, not a header
  merely missing one directive) must not crash or short-circuit
  differently than a page with a partial header set -- every header check
  runs independently off `.get(...)`, so a headerless response produces
  every applicable finding at once rather than a single generic
  "something's wrong" entry or, worse, a silent skip.

One `graph.read` call (the known route list) and one `browser.read` call
(the whole audit pass across every route), neither looped per item -- the
same batching discipline `agents/cartographer.py`'s crawl and
`agents/oracle.py`'s comparison both use, for the same reason: the
audit-worthy act is "we audited the site", once, not once per route.
"""

import re

from agents.base import AgentResult

MAX_ROUTES = 50

# Roles a screen reader treats as an actionable control -- an element in
# this set with no accessible `name` fails WCAG 4.1.2 outright.
_NAMED_CONTROL_ROLES = frozenset({
    "button", "link", "checkbox", "radio", "combobox", "menuitem", "tab", "switch",
})
_FIELD_ROLES = frozenset({"textbox", "combobox", "checkbox", "radio", "searchbox"})

_SECRET = re.compile(
    r"(?:AKIA[0-9A-Z]{16})"
    r"|(?:(?:api[_-]?key|secret|token)\s*[:=]\s*['\"]([A-Za-z0-9_\-]{16,})['\"])",
    re.I,
)


def _a11y_finding(route: str, kind: str, severity: str, element: dict, message: str) -> dict:
    return {"type": kind, "severity": severity, "route": route, "element": element, "message": message}


def _security_finding(route: str, kind: str, severity: str, header: str, message: str) -> dict:
    return {"type": kind, "severity": severity, "route": route, "header": header, "message": message}


def _a11y_for_route(route: str, a11y: list[dict], interactive: list[dict]) -> list[dict]:
    findings = []
    last_level = 0
    for el in a11y:
        role, name = el.get("role", ""), el.get("name", "")
        if role == "img" and not name:
            findings.append(_a11y_finding(route, "missing_alt_text", "high", el,
                                           "Image has no alt text."))
        elif role in _FIELD_ROLES and not name:
            findings.append(_a11y_finding(route, "unlabelled_field", "high", el,
                                           f"Form field ({role}) has no accessible label."))
        elif role in _NAMED_CONTROL_ROLES and not name:
            findings.append(_a11y_finding(route, "no_accessible_name", "high", el,
                                           f"Interactive control ({role}) has no accessible name."))
        if role == "heading" and el.get("level"):
            level = el["level"]
            if last_level and level > last_level + 1:
                findings.append(_a11y_finding(
                    route, "skipped_heading_level", "medium", el,
                    f"Heading jumps from level {last_level} to {level}."))
            last_level = level
    for el in interactive:
        findings.append(_a11y_finding(
            route, "clickable_without_role", "high", el,
            f"Element <{el.get('tag', '?')}> is clickable but carries no ARIA role."))
    return findings


def _csp_findings(route: str, csp: str) -> list[dict]:
    if not csp:
        return [_security_finding(route, "missing_csp", "high", "content-security-policy",
                                   "No Content-Security-Policy header is set.")]
    if "unsafe-inline" in csp:
        return [_security_finding(route, "weak_csp", "medium", "content-security-policy",
                                   f"CSP allows 'unsafe-inline': {csp!r}.")]
    return []


def _security_for_route(route: str, headers: dict, cookies: list[dict], scripts: list[str]) -> list[dict]:
    findings = []
    lowered = {k.lower(): v for k, v in headers.items()}
    findings += _csp_findings(route, lowered.get("content-security-policy", ""))
    if not lowered.get("strict-transport-security"):
        findings.append(_security_finding(route, "missing_hsts", "medium", "strict-transport-security",
                                           "No Strict-Transport-Security header is set."))
    if not lowered.get("x-frame-options"):
        findings.append(_security_finding(route, "missing_x_frame_options", "medium", "x-frame-options",
                                           "No X-Frame-Options header is set (clickjacking risk)."))
    for cookie in cookies:
        missing = [flag for flag in ("secure", "httpOnly") if not cookie.get(flag)]
        if not cookie.get("sameSite"):
            missing.append("sameSite")
        if missing:
            findings.append(_security_finding(
                route, "loose_cookie", "high", cookie.get("name", "(unnamed)"),
                f"Cookie {cookie.get('name', '(unnamed)')!r} is missing {', '.join(missing)}."))
    for script in scripts:
        for match in _SECRET.finditer(script):
            findings.append(_security_finding(
                route, "leaked_secret", "critical", "(served JavaScript)",
                f"A key/secret-shaped string is embedded in served JS: {match.group(0)[:40]!r}."))
    return findings


def _score(findings: list[dict]) -> int:
    penalty = {"critical": 25, "high": 15, "medium": 8, "low": 3}
    return max(0, 100 - sum(penalty.get(f["severity"], 5) for f in findings))


class Auditor:
    name = "auditor"

    def run(self, ctx) -> AgentResult:
        routes = ctx.gateway.call(
            ctx.workspace_id, self.name, "graph.read",
            target=f"routes for {ctx.workspace_id}",
            fn=lambda: ctx.repo.routes_for_workspace(ctx.workspace_id),
        )
        paths = [r.path for r in routes][:MAX_ROUTES] or ["/"]

        def inspect_all():
            a11y_findings, security_findings, visited = [], [], []
            for path in paths:
                ctx.browser.goto(path)
                visited.append(path)
                a11y_findings += _a11y_for_route(path, ctx.browser.a11y(), ctx.browser.interactive())
                snapshot = ctx.browser.snapshot()
                security_findings += _security_for_route(
                    path, ctx.browser.headers(), ctx.browser.cookies(),
                    snapshot.get("scripts", []),
                )
            return a11y_findings, security_findings, visited

        a11y_findings, security_findings, visited = ctx.gateway.call(
            ctx.workspace_id, self.name, "browser.read",
            target=f"audit of {len(paths)} route(s)", fn=inspect_all,
        )

        score = {"a11y": _score(a11y_findings), "security": _score(security_findings)}
        detail = f"{len(a11y_findings)} accessibility finding(s), {len(security_findings)} security finding(s)."

        return AgentResult(
            summary=f"Audited {len(visited)} route(s)",
            detail=detail,
            data={"a11y": a11y_findings, "security": security_findings, "score": score},
        )
