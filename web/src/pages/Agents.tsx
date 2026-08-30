import { useEffect } from "react";
import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { useCurrentUser } from "../lib/useCurrentUser";
import type { AgentStatus } from "../lib/types";

const STATE_KIND: Record<string, PillKind> = {
  idle: "grey",
  working: "info",
  gated: "warn",
  paused: "grey",
};

const STATE_LABEL: Record<string, string> = {
  idle: "Idle",
  working: "Working",
  gated: "At a gate",
  paused: "Paused",
};

const LIVE_REFRESH_MS = 5000;

export function Agents() {
  const { show } = useToast();
  const { data: user } = useCurrentUser();
  const agents = useAsync<AgentStatus[]>(() => api.get<AgentStatus[]>("/agents"), []);

  useEffect(() => {
    if (agents.status !== "success") return;
    const id = window.setInterval(() => agents.reload(), LIVE_REFRESH_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agents.status]);

  const canManage = user?.role === "owner";
  const rows = agents.status === "success" ? agents.data ?? [] : [];

  async function pause(name?: string) {
    try {
      await api.post("/agents/pause", name ? { agent: name } : undefined);
      show(name ? `${name} paused.` : "All agents paused.");
      agents.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't pause.");
    }
  }

  async function resume(name: string) {
    try {
      await api.post("/agents/resume", { agent: name });
      show(`${name} resumed.`);
      agents.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't resume.");
    }
  }

  const columns: TableColumn<AgentStatus>[] = [
    { key: "name", header: "Agent", label: "Agent", primary: true, width: "150px", render: (a) => <b>{a.name}</b> },
    { key: "version", header: "Version", label: "Version", width: "70px", render: (a) => <span className="mono" style={{ color: "var(--muted)" }}>{a.version}</span> },
    { key: "tools", header: "Tools it may use", label: "Tools", render: (a) => <span className="mono" style={{ color: "var(--muted)" }}>{a.tools.join(" · ")}</span> },
    { key: "model", header: "Model", label: "Model", width: "150px", render: (a) => <span className="mono" style={{ color: a.model ? "var(--muted)" : "var(--faint)" }}>{a.model ?? "—"}</span> },
    { key: "queue", header: "Queue", label: "Queue", width: "78px", render: (a) => <span className="n" style={{ color: "var(--muted)" }}>{a.queue}</span> },
    { key: "state", header: "State", label: "State", width: "118px", render: (a) => <Pill kind={STATE_KIND[a.state] ?? "grey"} dot={false}>{STATE_LABEL[a.state] ?? a.state}</Pill> },
    {
      key: "actions",
      header: "",
      label: "Actions",
      width: "90px",
      render: (a) =>
        a.state === "paused" ? (
          <Button size="sm" onClick={() => resume(a.name)} disabled={!canManage} title={canManage ? undefined : "Only an owner can resume an agent."}>
            Resume
          </Button>
        ) : (
          <Button size="sm" onClick={() => pause(a.name)} disabled={!canManage} title={canManage ? undefined : "Only an owner can pause an agent."}>
            Pause
          </Button>
        ),
    },
  ];

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Agents</h1>
          <p>Seven specialists. Each one has a scoped set of tools and nothing more.</p>
        </div>
        <span className="sp" />
        <Button size="sm" onClick={() => pause()} disabled={!canManage} title={canManage ? undefined : "Only an owner can pause the fleet."}>
          Pause all
        </Button>
      </div>
      <Panel style={{ marginTop: 18 }}>
        {agents.status === "loading" && <EmptyState variant="loading" title="Checking on the fleet…" />}
        {agents.status === "error" && (
          <EmptyState
            variant="error"
            title="Couldn't load the agent registry"
            description={agents.error}
            actions={<Button onClick={agents.reload}>Retry</Button>}
          />
        )}
        {agents.status === "success" && rows.length === 0 && (
          <EmptyState variant="empty" icon="i-agents" title="No agents registered" description="The fleet hasn't been provisioned for this workspace yet." />
        )}
        {agents.status === "success" && rows.length > 0 && <Table columns={columns} rows={rows} getRowKey={(a) => a.name} />}
      </Panel>
    </div>
  );
}
