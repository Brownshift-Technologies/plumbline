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

export interface RunDetail extends Run {
  steps: RunStep[];
  finding_id?: string | null;
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

export interface RouteCoverage {
  id: string;
  path: string;
  coverage_pct: number;
  last_mapped: number;
}

export interface SurfaceSummary {
  routes: RouteCoverage[];
  fully_covered: number;
  partly_covered: number;
  uncovered: number;
  mapped_at: number;
}

export interface AgentStatus {
  name: string;
  version: string;
  tools: string[];
  model: string | null;
  queue: number;
  state: "idle" | "working" | "gated" | "paused" | string;
}

export interface PolicyDecision {
  time: number;
  agent: string;
  call: string;
  target: string;
  rule: string;
  decision: "allowed" | "blocked" | "redacted" | string;
}

export interface PolicyRule {
  tool: string;
  pattern?: string;
  allow_only?: string[];
  effect: "human" | "deny" | "allow";
}

export interface PolicyRulesResponse {
  version: number;
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
