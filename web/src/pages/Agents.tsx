import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";
import { Table, type TableColumn } from "../components/Table";

interface Agent {
  name: string;
  version: string;
  tools: string;
  model: string;
  queue: number;
  state: { kind: "grey" | "info" | "warn"; label: string };
}

const AGENTS: Agent[] = [
  { name: "Cartographer", version: "2.4.1", tools: "browser.read · graph.write", model: "gemini-3.5-flash", queue: 0, state: { kind: "grey", label: "Idle" } },
  { name: "Author", version: "3.1.0", tools: "graph.read · repo.write:specs", model: "gemini-3.5-flash", queue: 3, state: { kind: "info", label: "Working" } },
  { name: "Chaos", version: "1.9.2", tools: "net.fault · env.write:staging", model: "gemini-3.5-flash", queue: 0, state: { kind: "grey", label: "Idle" } },
  { name: "Surgeon", version: "1.4.6", tools: "repo.write:src · pr.open", model: "gemini-3.5-flash", queue: 1, state: { kind: "warn", label: "At a gate" } },
];

const columns: TableColumn<Agent>[] = [
  { key: "name", header: "Agent", label: "Agent", primary: true, width: "150px", render: (a) => <b>{a.name}</b> },
  { key: "version", header: "Version", label: "Version", width: "70px", render: (a) => <span className="mono" style={{ color: "var(--muted)" }}>{a.version}</span> },
  { key: "tools", header: "Tools it may use", label: "Tools", render: (a) => <span className="mono" style={{ color: "var(--muted)" }}>{a.tools}</span> },
  { key: "model", header: "Model", label: "Model", width: "132px", render: (a) => <span className="mono" style={{ color: "var(--muted)" }}>{a.model}</span> },
  { key: "queue", header: "Queue", label: "Queue", width: "78px", render: (a) => <span className="n" style={{ color: "var(--muted)" }}>{a.queue}</span> },
  { key: "state", header: "State", label: "State", width: "118px", render: (a) => <Pill kind={a.state.kind} dot={false}>{a.state.label}</Pill> },
];

export function Agents() {
  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Agents</h1>
          <p>Seven specialists. Each one has a scoped set of tools and nothing more.</p>
        </div>
        <span className="sp" />
        <Button size="sm">Pause all</Button>
      </div>
      <Panel style={{ marginTop: 18 }}>
        <Table columns={columns} rows={AGENTS} getRowKey={(a) => a.name} />
      </Panel>
    </div>
  );
}
