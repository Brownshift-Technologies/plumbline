import { useState } from "react";
import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { useCurrentUser } from "../lib/useCurrentUser";
import { formatClock } from "../lib/time";
import type { PolicyDecision, PolicyRulesResponse } from "../lib/types";

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

function RulesEditor({ rules, canEdit }: { rules: PolicyRulesResponse; canEdit: boolean }) {
  const { show } = useToast();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(() => JSON.stringify(rules.rules, null, 2));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    setError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      setError("That isn't valid JSON.");
      return;
    }
    setSaving(true);
    try {
      await api.put("/policy/rules", { rules: parsed });
      show("Gate rules updated.");
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save these rules.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel
      title="Gate rules"
      headerExtra={
        <span style={{ fontSize: 13, color: "var(--faint)" }}>
          version {rules.version}
          {canEdit && (
            <Button size="sm" style={{ marginLeft: 10 }} onClick={() => setEditing((e) => !e)}>
              {editing ? "Cancel" : "Edit"}
            </Button>
          )}
        </span>
      }
      style={{ marginTop: 18 }}
    >
      <div style={{ padding: "14px 16px" }}>
        {editing ? (
          <>
            {error && (
              <p role="alert" style={{ color: "var(--fail)", fontSize: 13.5, marginBottom: 8 }}>
                {error}
              </p>
            )}
            <label htmlFor="rules-json" style={{ position: "absolute", left: -9999 }}>
              Gate rules JSON
            </label>
            <textarea
              id="rules-json"
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="mono"
              style={{ width: "100%", minHeight: 220, padding: 10, border: "1px solid var(--line)", borderRadius: 7 }}
            />
            <div style={{ marginTop: 10 }}>
              <Button variant="pri" onClick={save} disabled={saving}>
                {saving ? "Saving…" : "Save rules"}
              </Button>
            </div>
          </>
        ) : rules.rules.length === 0 ? (
          <p style={{ fontSize: 13.5, color: "var(--muted)" }}>
            No custom gate rules configured. Every agent uses the default gates (payments and billing routes require a
            human, Chaos may only touch staging).
          </p>
        ) : (
          <pre className="mono" style={{ fontSize: 12.5, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(rules.rules, null, 2)}
          </pre>
        )}
      </div>
    </Panel>
  );
}

export function Policy() {
  const { data: user } = useCurrentUser();
  const decisions = useAsync<PolicyDecision[]>(() => api.get<PolicyDecision[]>("/policy/decisions"), []);
  const rules = useAsync<PolicyRulesResponse>(() => api.get<PolicyRulesResponse>("/policy/rules"), []);

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
        {decisions.status === "loading" && <EmptyState variant="loading" title="Loading gate decisions…" />}
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

      {rules.status === "success" && rules.data && <RulesEditor rules={rules.data} canEdit={user?.role === "owner"} />}
      {rules.status === "error" && (
        <Panel title="Gate rules" style={{ marginTop: 18 }}>
          <EmptyState
            variant="error"
            title="Couldn't load gate rules"
            description={rules.error}
            actions={<Button onClick={rules.reload}>Retry</Button>}
          />
        </Panel>
      )}
    </div>
  );
}
