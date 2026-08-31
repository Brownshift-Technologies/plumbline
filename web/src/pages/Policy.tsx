import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { formatClock } from "../lib/time";
import type { PolicyDecisionEntry, PolicyDecisionsResponse } from "../lib/types";

const DECISION_KIND: Record<string, PillKind> = {
  allowed: "pass",
  blocked: "fail",
  redacted: "warn",
};

const DECISION_LABEL: Record<string, string> = {
  allowed: "Allowed",
  blocked: "Blocked",
  redacted: "Redacted",
};

export function Policy() {
  const decisions = useAsync<PolicyDecisionsResponse>(() => api.get<PolicyDecisionsResponse>("/policy/decisions"), []);

  const rows = decisions.status === "success" ? decisions.data?.decisions ?? [] : [];
  const blockedCount = rows.filter((d) => d.detail.decision === "blocked").length;

  const columns: TableColumn<PolicyDecisionEntry>[] = [
    { key: "at", header: "Time", label: "Time", primary: true, width: "96px", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{formatClock(d.at)}</span> },
    { key: "actor", header: "Agent", label: "Agent", width: "132px", render: (d) => d.actor },
    { key: "action", header: "Call", label: "Call", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{d.action}</span> },
    { key: "target", header: "Target", label: "Target", width: "190px", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{d.detail.target ?? "—"}</span> },
    { key: "decision", header: "Decision", label: "Decision", width: "112px", render: (d) => <Pill kind={DECISION_KIND[d.detail.decision] ?? "grey"} dot={false}>{DECISION_LABEL[d.detail.decision] ?? d.detail.decision}</Pill> },
  ];

  return (
    <div className="body">
      <div className="pagehead">
        <h1>Policy &amp; gates</h1>
        <p>What each agent is allowed to touch, and where a human has to sign.</p>
      </div>
      <Panel
        title="Gate decisions today"
        headerExtra={decisions.status === "success" && <span style={{ fontSize: 13, color: "var(--faint)" }}>{blockedCount} blocked</span>}
        style={{ marginTop: 18 }}
      >
        {decisions.status === "loading" && (
          <Table columns={columns} rows={[]} getRowKey={(d) => `${d.seq}`} skeletonRows={5} caption="Loading gate decisions" />
        )}
        {decisions.status === "error" && (
          <EmptyState
            variant="error"
            title="Couldn't load gate decisions"
            description={decisions.error}
            actions={<Button onClick={decisions.reload}>Retry</Button>}
          />
        )}
        {decisions.status === "success" && rows.length === 0 && (
          <EmptyState variant="empty" icon="i-shield" title="No gate decisions yet" description="Nothing has hit a policy gate today." />
        )}
        {decisions.status === "success" && rows.length > 0 && (
          <Table columns={columns} rows={rows} getRowKey={(d) => `${d.seq}`} />
        )}
      </Panel>
    </div>
  );
}
