import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";

interface Finding {
  title: string;
  route: string;
  foundBy: string;
  status: { kind: "info" | "fail" | "warn" | "grey"; label: string };
  age: string;
}

const FINDINGS: Finding[] = [
  { title: "A retried payment charges the customer twice", route: "/checkout/payment", foundBy: "Chaos", status: { kind: "info", label: "Patch ready" }, age: "22 min" },
  { title: "Changing a password doesn't end other sessions", route: "/account/security", foundBy: "Chaos", status: { kind: "fail", label: "Triaged" }, age: "2 days" },
  { title: "Cart total drifts a cent when currency changes", route: "/cart", foundBy: "Runner", status: { kind: "warn", label: "Tolerance" }, age: "3 days" },
];

const columns: TableColumn<Finding>[] = [
  { key: "title", header: "Finding", label: "Finding", primary: true, render: (f) => f.title },
  { key: "route", header: "Route", label: "Route", width: "170px", render: (f) => <span className="mono" style={{ color: "var(--muted)" }}>{f.route}</span> },
  { key: "foundBy", header: "Found by", label: "Found by", width: "130px", render: (f) => f.foundBy },
  { key: "status", header: "Status", label: "Status", width: "128px", render: (f) => <Pill kind={f.status.kind} dot={false}>{f.status.label}</Pill> },
  { key: "age", header: "Age", label: "Age", width: "96px", render: (f) => <span style={{ color: "var(--muted)" }}>{f.age}</span> },
];

export interface FindingsProps {
  loading?: boolean;
  error?: string;
}

export function Findings({ loading, error }: FindingsProps = {}) {
  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Findings</h1>
          <p>{FINDINGS.length} open. Ordered by how much of the product they touch.</p>
        </div>
        <span className="sp" />
        <Button size="sm">
          <Icon name="i-filter" size="xs" /> Filter
        </Button>
      </div>
      <Panel style={{ marginTop: 18 }}>
        {loading ? (
          <EmptyState variant="loading" title="Loading findings…" />
        ) : error ? (
          <EmptyState variant="error" title="Couldn't load findings" description={error} />
        ) : FINDINGS.length === 0 ? (
          <EmptyState variant="empty" title="No open findings" description="Nothing is currently failing." />
        ) : (
          <Table columns={columns} rows={FINDINGS} getRowKey={(f) => f.title} />
        )}
      </Panel>
    </div>
  );
}
