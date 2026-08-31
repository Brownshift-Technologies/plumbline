import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { Table, type TableColumn } from "../components/Table";
import { EmptyState } from "../components/EmptyState";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { formatDuration, relativeTime } from "../lib/time";
import { routes } from "../lib/routes";
import type { Run, RunListResponse } from "../lib/types";

const RESULT_KIND: Record<string, PillKind> = {
  passed: "pass",
  failed: "fail",
  unstable: "warn",
  cancelled: "grey",
  queued: "grey",
  running: "info",
};

function behaviourSummary(run: Run): string {
  const parts = [`${run.held} held`];
  if (run.failed) parts.push(`${run.failed} failed`);
  if (run.repaired) parts.push(`${run.repaired} repaired`);
  return parts.join(" · ");
}

function resultLabel(run: Run): string {
  if (run.state === "failed") return `${run.failed} failing`;
  if (run.state === "unstable") return `${run.failed} unstable`;
  if (run.state === "cancelled") return "Cancelled";
  if (run.state === "running") return "Running";
  if (run.state === "queued") return "Queued";
  return "All held";
}

const STATE_OPTIONS = [
  { value: "", label: "All states" },
  { value: "queued", label: "Queued" },
  { value: "running", label: "Running" },
  { value: "passed", label: "Passed" },
  { value: "failed", label: "Failed" },
  { value: "unstable", label: "Unstable" },
  { value: "cancelled", label: "Cancelled" },
];

type SortKey = "number" | "duration";

export function Runs() {
  const navigate = useNavigate();
  const [state, setState] = useState("");
  const [trigger, setTrigger] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [sort, setSort] = useState<SortKey>("number");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const cursor = cursorStack[cursorStack.length - 1];

  const runs = useAsync<RunListResponse>(() => {
    const params = new URLSearchParams();
    params.set("limit", "20");
    params.set("sort", sort);
    params.set("order", order);
    if (cursor) params.set("cursor", cursor);
    if (state) params.set("state", state);
    if (trigger.trim()) params.set("trigger", trigger.trim());
    return api.get<RunListResponse>(`/runs?${params.toString()}`);
  }, [state, trigger, sort, order, cursor]);

  function toggleSort(key: SortKey) {
    if (sort === key) {
      setOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSort(key);
      setOrder("desc");
    }
    setCursorStack([null]);
  }

  function nextPage() {
    if (runs.status === "success" && runs.data?.next_cursor) {
      setCursorStack((s) => [...s, runs.data!.next_cursor]);
    }
  }

  function prevPage() {
    setCursorStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
  }

  const columns: TableColumn<Run>[] = [
    {
      key: "id",
      header: (
        <button type="button" onClick={() => toggleSort("number")}>
          Run <Icon name="i-sort" size="xs" />
        </button>
      ),
      label: "Run",
      primary: true,
      width: "74px",
      render: (r) => <span className="mono">{r.number}</span>,
    },
    { key: "trigger", header: "Trigger", label: "Trigger", render: (r) => r.trigger },
    { key: "result", header: "Result", label: "Result", width: "116px", render: (r) => <Pill kind={RESULT_KIND[r.state] ?? "grey"}>{resultLabel(r)}</Pill> },
    { key: "behaviours", header: "Behaviours", label: "Behaviours", width: "154px", render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{behaviourSummary(r)}</span> },
    {
      key: "duration",
      header: (
        <button type="button" onClick={() => toggleSort("duration")}>
          Duration <Icon name="i-sort" size="xs" />
        </button>
      ),
      label: "Duration",
      width: "104px",
      render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{formatDuration(r.duration_ms)}</span>,
    },
    { key: "finished", header: "Finished", label: "Finished", width: "120px", render: (r) => <span style={{ color: "var(--muted)" }}>{relativeTime(r.started_at)}</span> },
    { key: "who", header: "Started by", label: "Started by", width: "136px", render: (r) => r.started_by },
  ];

  const rows = runs.status === "success" ? runs.data?.runs ?? [] : [];

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Runs</h1>
          <p>Every run against acme / storefront.</p>
        </div>
        <span className="sp" />
        <Button size="sm" onClick={() => setFilterOpen((o) => !o)} aria-expanded={filterOpen}>
          <Icon name="i-filter" size="xs" /> Filter
        </Button>
        <Button variant="pri" size="sm" onClick={() => navigate(routes.home)}>
          <Icon name="i-plus" size="xs" /> New run
        </Button>
      </div>

      {filterOpen && (
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", padding: "10px 0" }}>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="run-state">State</label>
            <select
              id="run-state"
              value={state}
              onChange={(e) => {
                setState(e.target.value);
                setCursorStack([null]);
              }}
              style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 7, background: "var(--white)" }}
            >
              {STATE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="run-trigger">Trigger contains</label>
            <input
              id="run-trigger"
              type="text"
              value={trigger}
              onChange={(e) => {
                setTrigger(e.target.value);
                setCursorStack([null]);
              }}
              placeholder="checkout, pull request #…"
            />
          </div>
        </div>
      )}

      <Panel style={{ marginTop: 18 }}>
        {runs.status === "loading" && (
          <Table columns={columns} rows={[]} getRowKey={(r) => r.id} skeletonRows={6} caption="Loading runs" />
        )}
        {runs.status === "error" && (
          <EmptyState
            variant="error"
            title="Couldn't load runs"
            description={runs.error}
            actions={<Button onClick={runs.reload}>Retry</Button>}
          />
        )}
        {runs.status === "success" && rows.length === 0 && (
          <EmptyState
            variant="empty"
            title={state || trigger ? "No runs match these filters" : "No runs yet"}
            description={
              state || trigger
                ? "Try clearing the state or trigger filter."
                : "Runs will appear here once one kicks off."
            }
            actions={
              state || trigger ? (
                <Button
                  onClick={() => {
                    setState("");
                    setTrigger("");
                    setCursorStack([null]);
                  }}
                >
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        )}
        {runs.status === "success" && rows.length > 0 && (
          <>
            <Table columns={columns} rows={rows} getRowKey={(r) => r.id} onRowClick={(r) => navigate(routes.run(r.id))} />
            <div className="pager">
              Showing {rows.length} of {runs.data?.total ?? rows.length} runs
              <span className="sp" />
              <button className="pbtn" onClick={prevPage} disabled={cursorStack.length <= 1} aria-label="Previous page">
                <Icon name="i-chev-l" size="xs" />
              </button>
              <button className="pbtn" onClick={nextPage} disabled={!runs.data?.next_cursor} aria-label="Next page">
                <Icon name="i-chev-r" size="xs" />
              </button>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
