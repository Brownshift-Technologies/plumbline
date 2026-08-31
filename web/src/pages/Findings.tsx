import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/EmptyState";
import { Table, type TableColumn } from "../components/Table";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { relativeTime } from "../lib/time";
import { routes } from "../lib/routes";
import type { Finding, FindingsResponse } from "../lib/types";

const STATUS_KIND: Record<string, PillKind> = {
  patch_ready: "info",
  triaged: "fail",
  needs_repro: "warn",
  tolerance: "warn",
  accepted: "grey",
};

const STATUS_LABEL: Record<string, string> = {
  patch_ready: "Patch ready",
  triaged: "Triaged",
  needs_repro: "Needs repro",
  tolerance: "Tolerance",
  accepted: "Accepted",
};

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "patch_ready", label: "Patch ready" },
  { value: "triaged", label: "Triaged" },
  { value: "needs_repro", label: "Needs repro" },
  { value: "tolerance", label: "Tolerance" },
  { value: "accepted", label: "Accepted" },
];

export function Findings() {
  const navigate = useNavigate();
  const { show } = useToast();
  const [status, setStatus] = useState("");
  const [route, setRoute] = useState("");
  const [foundBy, setFoundBy] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);

  const findings = useAsync<FindingsResponse>(() => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (route.trim()) params.set("route", route.trim());
    if (foundBy.trim()) params.set("found_by", foundBy.trim());
    const qs = params.toString();
    return api.get<FindingsResponse>(`/findings${qs ? `?${qs}` : ""}`);
  }, [status, route, foundBy]);

  const rows = findings.status === "success" ? findings.data?.findings ?? [] : [];
  const filtersActive = Boolean(status || route || foundBy);

  const columns: TableColumn<Finding>[] = [
    { key: "title", header: "Finding", label: "Finding", primary: true, render: (f) => f.title },
    { key: "route", header: "Route", label: "Route", width: "170px", render: (f) => <span className="mono" style={{ color: "var(--muted)" }}>{f.route}</span> },
    { key: "foundBy", header: "Found by", label: "Found by", width: "130px", render: (f) => f.found_by },
    { key: "status", header: "Status", label: "Status", width: "128px", render: (f) => <Pill kind={STATUS_KIND[f.status] ?? "grey"} dot={false}>{STATUS_LABEL[f.status] ?? f.status}</Pill> },
    { key: "age", header: "Age", label: "Age", width: "96px", render: (f) => <span style={{ color: "var(--muted)" }}>{relativeTime(f.at)}</span> },
  ];

  function onRowClick(f: Finding) {
    if (f.run_id) navigate(routes.run(f.run_id));
    else show("No run is linked to this finding yet.");
  }

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Findings</h1>
          {/* Counts OPEN findings, matching the sidebar badge, which reads
              /api/summary and excludes accepted ones. This said
              `rows.length` -- every row including the accepted ones -- so
              the page claimed 7 while the nav next to it said 6. */}
          <p>
            {rows.filter((f) => f.status !== "accepted").length} open. Ordered by how much of
            the product they touch.
          </p>
        </div>
        <span className="sp" />
        <Button size="sm" onClick={() => setFilterOpen((o) => !o)} aria-expanded={filterOpen}>
          <Icon name="i-filter" size="xs" /> Filter
        </Button>
      </div>

      {filterOpen && (
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", padding: "10px 0" }}>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="finding-status">Status</label>
            <select
              id="finding-status"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 7, background: "var(--white)" }}
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="finding-route">Route</label>
            <input id="finding-route" type="text" value={route} onChange={(e) => setRoute(e.target.value)} placeholder="/checkout" />
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="finding-foundby">Found by</label>
            <input id="finding-foundby" type="text" value={foundBy} onChange={(e) => setFoundBy(e.target.value)} placeholder="Chaos" />
          </div>
        </div>
      )}

      <Panel style={{ marginTop: 18 }}>
        {findings.status === "loading" && (
          <Table columns={columns} rows={[]} getRowKey={(f) => f.id} skeletonRows={5} caption="Loading findings" />
        )}
        {findings.status === "error" && (
          <EmptyState
            variant="error"
            title="Couldn't load findings"
            description={findings.error}
            actions={<Button onClick={findings.reload}>Retry</Button>}
          />
        )}
        {findings.status === "success" && rows.length === 0 && (
          <EmptyState
            variant="empty"
            title={filtersActive ? "No findings match these filters" : "No open findings"}
            description={filtersActive ? "Try a different status, route or agent." : "Nothing is currently failing."}
            actions={
              filtersActive ? (
                <Button
                  onClick={() => {
                    setStatus("");
                    setRoute("");
                    setFoundBy("");
                  }}
                >
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        )}
        {findings.status === "success" && rows.length > 0 && (
          <Table columns={columns} rows={rows} getRowKey={(f) => f.id} onRowClick={onRowClick} />
        )}
      </Panel>
    </div>
  );
}
