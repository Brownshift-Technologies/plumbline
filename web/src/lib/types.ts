/**
 * Shared response shapes for the Plumbline API. Field names mirror
 * `app/models.py`'s frozen dataclasses (the backend's source of truth for
 * what a document contains) so the client and server never quietly drift.
 */

export type Role = "owner" | "approver" | "reader";

export interface CurrentUser {
  id: string;
  name: string;
  email?: string;
  is_demo: boolean;
  workspace_id: string;
  role: Role;
  /** Profile photo as a `data:` URI, or "" for none -- render initials. */
  photo_url?: string;
  /**
   * Whether this user has a CONFIRMED (not merely pending) TOTP secret --
   * see app/models.py's User.totp_secret vs totp_pending_secret. Not part
   * of Task 8a's documented `/me` response; the client treats it as
   * "unknown" (undefined) rather than "false" when absent, so it only
   * blocks Approve on a positive signal, never on a field the backend
   * simply hasn't added yet. See RunDetail's gate-disabled logic.
   */
  totp_enabled?: boolean;
  /**
   * Undefined (not `false`) unless the backend actually reports it. The
   * design mockup shows one fixed "Verified" state, but that is not a data
   * contract Task 8a's documented `/me` response promises -- ProfilePane
   * renders the badge only when this is explicitly `true`, a distinct
   * "Not verified" pill when explicitly `false`, and neither when unknown.
   */
  email_verified?: boolean;
}

export interface Run {
  id: string;
  workspace_id: string;
  number: number;
  trigger: string;
  state: "queued" | "running" | "passed" | "failed" | "unstable" | "cancelled" | string;
  commit: string;
  started_by: string;
  held: number;
  failed: number;
  repaired: number;
  duration_ms: number;
  started_at: number;
}

export interface RunStep {
  id: string;
  run_id: string;
  agent: string;
  summary: string;
  detail: string;
  outcome: "ok" | "warn" | "fail" | "gated" | "degraded" | string;
  duration_ms: number;
  at: number;
}

/**
 * `GET /api/runs/{id}`'s actual shape (`app/run_routes.py`'s `get_run`) --
 * the run itself nested under `run`, sitting alongside `steps` and
 * `finding_id` as siblings, NOT a flat merge of `Run`'s fields with `steps`
 * tacked on. That flat shape is what a *`finished` SSE event* carries
 * instead (`_run_json(current)`, a bare `Run` with no `steps`/`finding_id`
 * at all) -- two different payloads for two different transports; see
 * `lib/sse.ts`.
 */
export interface RunDetailResponse {
  run: Run;
  steps: RunStep[];
  finding_id: string | null;
}

export interface RunListResponse {
  runs: Run[];
  next_cursor: string | null;
  total: number;
}

export interface Finding {
  id: string;
  workspace_id: string;
  title: string;
  route: string;
  found_by: string;
  status: "triaged" | "needs_repro" | "tolerance" | "patch_ready" | "accepted" | string;
  severity: string;
  seed: string;
  repro_count: number;
  at: number;
  run_id?: string | null;
}

/** `GET /api/findings` (`app/finding_routes.py`'s `list_findings`) wraps the list. */
export interface FindingsResponse {
  findings: Finding[];
  total: number;
}

export interface Patch {
  id: string;
  finding_id: string;
  diff: string;
  files: string[];
  added: number;
  removed: number;
  verified: boolean;
  pr_url: string;
  gate_state: "awaiting_approval" | "gated" | "approved" | "rejected" | "changes_requested" | string;
  gate_reason?: string;
}

export interface Behaviour {
  id: string;
  workspace_id: string;
  text: string;
  route: string;
  spec_path: string;
  tags: string[];
  owner: string;
  status: string;
}

/** `GET /api/behaviours` (`app/behaviour_routes.py`'s `list_behaviours`) wraps the list. */
export interface BehavioursResponse {
  behaviours: Behaviour[];
  total: number;
}

export interface RouteCoverage {
  id: string;
  path: string;
  coverage_pct: number;
  last_mapped: number;
}

/**
 * `GET /api/surface` (`app/surface_routes.py`'s `get_surface`) -- the real
 * response is `{routes, total, uncovered}`. It has never sent
 * `fully_covered`, `partly_covered` or `mapped_at`; `Surface.tsx` derives
 * those from `routes` itself rather than trusting fields the server does
 * not send.
 */
export interface SurfaceResponse {
  routes: RouteCoverage[];
  total: number;
  uncovered: number;
}

/**
 * One entry of `GET /api/agents`'s `agents` array
 * (`app/agent_routes.py`'s `_agent_status`). The real fields are `agent`,
 * `tools`, `queue_depth` and `state` -- there is no per-agent `version` or
 * `model` anywhere in the backend.
 */
export interface AgentStatus {
  agent: string;
  tools: string[];
  queue_depth: number;
  state: "idle" | "working" | "gated" | "paused" | string;
}

/** `GET /api/agents` (`app/agent_routes.py`'s `list_agents`) wraps the list. */
export interface AgentsResponse {
  agents: AgentStatus[];
  paused: boolean;
}

/**
 * One entry of `GET /api/policy/decisions`'s `decisions` array
 * (`app/agent_routes.py`'s `list_policy_decisions`) -- a raw ledger entry
 * (see `LedgerEntry`), not a bespoke shape: `actor` is the agent, `action`
 * is the tool call, and the decision itself lives in `detail`.
 */
export interface PolicyDecisionEntry {
  seq: number;
  at: number;
  actor: string;
  action: string;
  detail: {
    decision: "allowed" | "blocked" | "redacted" | string;
    reason?: string;
    target?: string;
    policy_version?: number;
  };
  signature: string;
}

/** `GET /api/policy/decisions` wraps the list. */
export interface PolicyDecisionsResponse {
  decisions: PolicyDecisionEntry[];
}

export interface PolicyRule {
  tool: string;
  pattern?: string;
  allow_only?: string[];
  effect: "human" | "deny" | "allow";
}

/**
 * `GET`/`PUT /api/policy/rules` (`app/agent_routes.py`) -- the version
 * field is named `policy_version`, not `version`.
 */
export interface PolicyRulesResponse {
  policy_version: number;
  rules: PolicyRule[];
}

export interface LedgerEntry {
  seq: number;
  at: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  signature: string;
}

export interface LedgerListResponse {
  entries: LedgerEntry[];
  next_cursor: string | null;
  total: number;
}

export interface LedgerVerifyResponse {
  intact: boolean;
  checked: number;
}

export interface BillingInfo {
  plan: string;
  price: number;
  interval: string;
  renews_at: number;
  runs_used: number;
  run_limit: number;
  seats_used: number;
  seat_limit: number;
  payment_method: string;
}

export interface Session {
  id: string;
  user_agent: string;
  ip_city: string;
  current: boolean;
}

export interface Member {
  id: string;
  user_id: string;
  name: string;
  email: string;
  role: Role;
  last_active?: number;
}

export interface TotpEnrollResponse {
  otpauth_url: string;
  secret: string;
}

export interface BillingInvoice {
  id: string;
  at: number;
  amount: number;
  status: string;
  url?: string;
}

/** `GET /api/summary` -- the counts the sidebar shows. Counts only, no rows. */
export interface SummaryResponse {
  runs: number;
  findings: number;
  behaviours: number;
  agents: number;
}
