import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { formatClock } from "../lib/time";
import type { PolicyDecision } from "../lib/types";

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
  const decisions = useAsync<PolicyDecision[]>(() => api.get<PolicyDecision[]>("/policy/decisions"), []);

  const rows = decisions.status === "success" ? decisions.data ?? [] : [];
  const blockedCount = rows.filter((d) => d.decision === "blocked").length;

  const columns: TableColumn<PolicyDecision>[] = [
    { key: "time", header: "Time", label: "Time", primary: true, width: "96px", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{formatClock(d.time)}</span> },
    { key: "agent", header: "Agent", label: "Agent", width: "132px", render: (d) => d.agent },
    { key: "call", header: "Call", label: "Call", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{d.call}</span> },
    { key: "rule", header: "Rule", label: "Rule", width: "190px", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{d.rule}</span> },
    { key: "decision", header: "Decision", label: "Decision", width: "112px", render: (d) => <Pill kind={DECISION_KIND[d.decision] ?? "grey"} dot={false}>{DECISION_LABEL[d.decision] ?? d.decision}</Pill> },
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
          <Table columns={columns} rows={[]} getRowKey={(d) => `${d.time}-${d.agent}`} skeletonRows={5} caption="Loading gate decisions" />
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
          <Table columns={columns} rows={rows} getRowKey={(d) => `${d.time}-${d.agent}-${d.call}`} />
        )}
      </Panel>
    </div>
  );
}
