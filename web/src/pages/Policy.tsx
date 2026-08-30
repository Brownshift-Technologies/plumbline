import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Table, type TableColumn } from "../components/Table";

interface Decision {
  time: string;
  agent: string;
  call: string;
  rule: string;
  decision: { kind: "fail" | "warn" | "pass"; label: string };
}

const DECISIONS: Decision[] = [
  { time: "14:06", agent: "Surgeon", call: "pr.merge storefront#2211", rule: "payments/* → human", decision: { kind: "fail", label: "Blocked" } },
  { time: "13:48", agent: "Chaos", call: "env.write prod-eu-west-1", rule: "staging only", decision: { kind: "fail", label: "Blocked" } },
  { time: "13:12", agent: "Triager", call: "artefact.read har/4469", rule: "redact card numbers", decision: { kind: "warn", label: "Redacted" } },
  { time: "11:30", agent: "Author", call: "repo.write specs/checkout/*", rule: "within scope", decision: { kind: "pass", label: "Allowed" } },
];

const columns: TableColumn<Decision>[] = [
  { key: "time", header: "Time", label: "Time", primary: true, width: "96px", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{d.time}</span> },
  { key: "agent", header: "Agent", label: "Agent", width: "132px", render: (d) => d.agent },
  { key: "call", header: "Call", label: "Call", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{d.call}</span> },
  { key: "rule", header: "Rule", label: "Rule", width: "190px", render: (d) => <span className="mono" style={{ color: "var(--muted)" }}>{d.rule}</span> },
  { key: "decision", header: "Decision", label: "Decision", width: "112px", render: (d) => <Pill kind={d.decision.kind} dot={false}>{d.decision.label}</Pill> },
];

export function Policy() {
  return (
    <div className="body">
      <div className="pagehead">
        <h1>Policy &amp; gates</h1>
        <p>What each agent is allowed to touch, and where a human has to sign.</p>
      </div>
      <Panel title="Gate decisions today" headerExtra={<span style={{ fontSize: 13, color: "var(--faint)" }}>{DECISIONS.filter((d) => d.decision.label === "Blocked").length} blocked</span>} style={{ marginTop: 18 }}>
        <Table columns={columns} rows={DECISIONS} getRowKey={(d) => `${d.time}-${d.agent}`} />
      </Panel>
    </div>
  );
}
