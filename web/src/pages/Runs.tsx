import { useNavigate } from "react-router-dom";
import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { Table, type TableColumn } from "../components/Table";
import { EmptyState } from "../components/EmptyState";
import { routes } from "../lib/routes";

interface RunRow {
  id: string;
  trigger: string;
  result: { kind: "fail" | "pass" | "warn" | "grey"; label: string };
  behaviours: string;
  duration: string;
  finished: string;
  who: string;
}

const RUNS: RunRow[] = [
  { id: "4471", trigger: "Pull request #2211 · retry idempotency", result: { kind: "fail", label: "1 failing" }, behaviours: "341 held · 1 failed", duration: "6m 41s", finished: "22 min ago", who: "Surgeon" },
  { id: "4470", trigger: "Pull request #2210 · checkout nav refactor", result: { kind: "pass", label: "All held" }, behaviours: "338 held · 4 repaired", duration: "5m 52s", finished: "47 min ago", who: "Roger K." },
  { id: "4469", trigger: "Scheduled · nightly chaos sweep", result: { kind: "warn", label: "2 unstable" }, behaviours: "340 held · 2 unstable", duration: "11m 08s", finished: "11 hours ago", who: "Chaos" },
];

const columns: TableColumn<RunRow>[] = [
  { key: "id", header: "Run", label: "Run", primary: true, width: "74px", render: (r) => <span className="mono">{r.id}</span> },
  { key: "trigger", header: "Trigger", label: "Trigger", render: (r) => r.trigger },
  { key: "result", header: "Result", label: "Result", width: "116px", render: (r) => <Pill kind={r.result.kind}>{r.result.label}</Pill> },
  { key: "behaviours", header: "Behaviours", label: "Behaviours", width: "154px", render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{r.behaviours}</span> },
  { key: "duration", header: "Duration", label: "Duration", width: "104px", render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{r.duration}</span> },
  { key: "finished", header: "Finished", label: "Finished", width: "120px", render: (r) => <span style={{ color: "var(--muted)" }}>{r.finished}</span> },
  { key: "who", header: "Started by", label: "Started by", width: "136px", render: (r) => r.who },
];

export interface RunsProps {
  loading?: boolean;
  error?: string;
}

export function Runs({ loading, error }: RunsProps = {}) {
  const navigate = useNavigate();

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Runs</h1>
          <p>Every run against acme / storefront.</p>
        </div>
        <span className="sp" />
        <Button size="sm">
          <Icon name="i-filter" size="xs" /> Filter
        </Button>
        <Button variant="pri" size="sm">
          <Icon name="i-plus" size="xs" /> New run
        </Button>
      </div>
      <Panel style={{ marginTop: 18 }}>
        {loading ? (
          <EmptyState variant="loading" title="Loading runs…" />
        ) : error ? (
          <EmptyState variant="error" title="Couldn't load runs" description={error} />
        ) : RUNS.length === 0 ? (
          <EmptyState variant="empty" title="No runs yet" description="Runs will appear here once one kicks off." />
        ) : (
          <Table
            columns={columns}
            rows={RUNS}
            getRowKey={(r) => r.id}
            onRowClick={(r) => navigate(routes.run(r.id))}
          />
        )}
      </Panel>
    </div>
  );
}
