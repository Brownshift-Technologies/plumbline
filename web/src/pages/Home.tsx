import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Icon, type IconName } from "../components/Icon";
import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { SkeletonLines } from "../components/Skeleton";
import { Table, type TableColumn } from "../components/Table";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { useCurrentUser } from "../lib/useCurrentUser";
import { isDemoWrite, demoWriteMessage } from "../lib/demo";
import { greeting, relativeTime, formatDuration } from "../lib/time";
import { routes } from "../lib/routes";
import type { Finding, FindingsResponse, Run, RunListResponse } from "../lib/types";

const START_TILES: { key: string; label: string; sub: string; icon: IconName; bg: string; fg: string }[] = [
  { key: "behaviour", label: "Behaviour", sub: "One thing that must hold", icon: "i-checkbox", bg: "var(--brand-w)", fg: "var(--brand)" },
  { key: "suite", label: "Suite", sub: "Group behaviours together", icon: "i-layers", bg: "var(--pass-w)", fg: "var(--pass)" },
  { key: "chaos", label: "Chaos run", sub: "Break it on purpose", icon: "i-bolt", bg: "var(--violet-w)", fg: "var(--violet)" },
  { key: "schedule", label: "Schedule", sub: "Run on a cadence", icon: "i-cal", bg: "var(--warn-w)", fg: "var(--warn)" },
  { key: "import", label: "Import", sub: "Bring existing Playwright", icon: "i-import", bg: "#F1EFEA", fg: "var(--muted)" },
];

const RESULT_KIND: Record<string, PillKind> = {
  passed: "pass",
  failed: "fail",
  unstable: "warn",
  cancelled: "grey",
  queued: "grey",
  running: "info",
};

function resultLabel(run: Run): string {
  if (run.state === "failed") return `${run.failed} failing`;
  if (run.state === "unstable") return `${run.failed} unstable`;
  if (run.state === "cancelled") return "Cancelled";
  if (run.state === "running" || run.state === "queued") return "In progress";
  return "All held";
}

function behaviourSummary(run: Run): string {
  const parts = [`${run.held} held`];
  if (run.failed) parts.push(`${run.failed} failed`);
  if (run.repaired) parts.push(`${run.repaired} repaired`);
  return parts.join(" · ");
}

const STATUS_KIND: Record<string, PillKind> = {
  patch_ready: "info",
  triaged: "fail",
  needs_repro: "warn",
  tolerance: "warn",
  accepted: "grey",
};

const STATUS_LABEL: Record<string, string> = {
  patch_ready: "Patch ready",
  triaged: "Failing",
  needs_repro: "Needs repro",
  tolerance: "Tolerance",
  accepted: "Accepted",
};

const runColumns: TableColumn<Run>[] = [
  { key: "id", header: "Run", label: "Run", primary: true, width: "74px", render: (r) => <span className="mono">{r.number}</span> },
  { key: "trigger", header: "Trigger", label: "Trigger", render: (r) => r.trigger },
  {
    key: "result",
    header: "Result",
    label: "Result",
    width: "116px",
    render: (r) => <Pill kind={RESULT_KIND[r.state] ?? "grey"}>{resultLabel(r)}</Pill>,
  },
  {
    key: "behaviours",
    header: "Behaviours",
    label: "Behaviours",
    width: "154px",
    render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{behaviourSummary(r)}</span>,
  },
  { key: "duration", header: "Duration", label: "Duration", width: "104px", render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{formatDuration(r.duration_ms)}</span> },
  { key: "who", header: "Started by", label: "Started by", width: "136px", render: (r) => r.started_by },
];

export function Home() {
  const navigate = useNavigate();
  const { data: user } = useCurrentUser();
  const [prompt, setPrompt] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [demoNotice, setDemoNotice] = useState<string | null>(null);

  const attention = useAsync<Finding[]>(
    () => api.get<FindingsResponse>("/findings?status=patch_ready").then((r) => r.findings),
    [],
  );
  const findings = useAsync<Finding[]>(
    () => api.get<FindingsResponse>("/findings?limit=3").then((r) => r.findings),
    [],
  );
  const runs = useAsync<RunListResponse>(() => api.get<RunListResponse>("/runs?limit=4"), []);

  async function startRun(trigger: string) {
    setCreateError(null);
    setDemoNotice(null);
    setCreating(true);
    try {
      const res = await api.post<{ id: string; demo?: boolean; persisted?: boolean }>("/runs", { trigger });
      setPrompt("");
      if (isDemoWrite(res)) {
        setDemoNotice(demoWriteMessage("this run would start"));
        runs.reload();
      } else if (res.id) {
        navigate(routes.run(res.id));
        return;
      } else {
        runs.reload();
      }
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Couldn't start that run.");
    } finally {
      setCreating(false);
    }
  }

  function onPromptSubmit(e: FormEvent) {
    e.preventDefault();
    if (!prompt.trim() || creating) return;
    void startRun(prompt.trim());
  }

  const attentionFinding = attention.status === "success" ? attention.data?.[0] : undefined;
  const runRows = runs.status === "success" ? runs.data?.runs ?? [] : [];

  return (
    <section>
      <div className="hero">
        <h1>
          {greeting()}
          {user ? `, ${user.name.split(" ")[0]}` : ""}. What should we put under test?
        </h1>
        <form className="promptwrap" onSubmit={onPromptSubmit}>
          <div className="prompt">
            <div className="ph">
              <Icon name="i-spark" size="s" /> Describe a behaviour
            </div>
            <label htmlFor="prompt-input" style={{ position: "absolute", left: -9999 }}>
              Describe a behaviour
            </label>
            <textarea
              id="prompt-input"
              placeholder="A customer who retries a slow payment should only be charged once"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={creating}
            />
            <div className="pf">
              <span className="sp" />
              <button type="button" className="ib" title="Dictate">
                <Icon name="i-mic" size="s" />
              </button>
              <button
                type="submit"
                className="ib send"
                title="Write and run this behaviour"
                disabled={creating || !prompt.trim()}
              >
                <Icon name="i-up" size="s" />
              </button>
            </div>
          </div>
        </form>
        {createError && (
          <p role="alert" style={{ marginTop: 10, fontSize: 13.5, color: "var(--fail)" }}>
            {createError}
          </p>
        )}
        {demoNotice && (
          <p role="status" style={{ marginTop: 10, fontSize: 13.5, color: "var(--brand)" }}>
            {demoNotice}
          </p>
        )}
        <p className="disc">
          Plumbline writes and runs the test. It never merges anything without your
          approval.
        </p>
      </div>

      <div className="body">
        <h2 style={{ marginTop: 6 }}>Start from scratch</h2>
        <div className="grid5">
          {START_TILES.map((tile) => (
            <button
              key={tile.key}
              type="button"
              className="card"
              onClick={() => document.getElementById("prompt-input")?.focus()}
            >
              <span className="tile" style={{ background: tile.bg }}>
                <span style={{ color: tile.fg, display: "flex" }}>
                  <Icon name={tile.icon} />
                </span>
              </span>
              <span>
                <b>{tile.label}</b>
                <span>{tile.sub}</span>
              </span>
            </button>
          ))}
        </div>

        <h2>Needs your attention</h2>
        {attention.status === "loading" && (
          <div className="panel" style={{ marginBottom: 13, padding: "16px 17px" }} aria-busy="true">
            <span className="visually-hidden" role="status">Checking what needs you…</span>
            <SkeletonLines count={3} widths={["30%", "60%", "45%"]} />
          </div>
        )}
        {attention.status === "error" && (
          <div className="panel" style={{ marginBottom: 13 }}>
            <EmptyState
              variant="error"
              title="Couldn't load what needs your attention"
              description={attention.error}
              actions={<Button onClick={attention.reload}>Retry</Button>}
            />
          </div>
        )}
        {attention.status === "success" && !attentionFinding && (
          <div className="panel" style={{ marginBottom: 13, padding: "20px 16px", fontSize: 13.5, color: "var(--muted)" }}>
            Nothing needs you right now. Every patch is either merged or still running.
          </div>
        )}
        {attentionFinding && (
          <div className="attn" style={{ marginBottom: 13 }}>
            <div className="attn-in">
              <span style={{ color: "var(--violet)", marginTop: 2 }}>
                <Icon name="i-star" label="Needs attention" />
              </span>
              <div style={{ flex: 1 }}>
                <div className="eyebrow">Waiting on you since {new Date(attentionFinding.at * 1000).toLocaleTimeString()}</div>
                <h3>{attentionFinding.title}</h3>
                <div className="meta">
                  <Pill kind="fail">Failing</Pill>
                  <span className="mono">{attentionFinding.route}</span>
                  <span>·</span>
                  <span>Reproduced {attentionFinding.repro_count} of {attentionFinding.repro_count}</span>
                </div>
                <div className="acts">
                  <Button variant="pri" onClick={() => navigate(routes.run(attentionFinding.run_id ?? attentionFinding.id))}>
                    Review patch
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}

        {findings.status === "loading" && (
          <div className="grid3" aria-busy="true">
            <span className="visually-hidden" role="status">Loading findings…</span>
            {[0, 1, 2].map((i) => (
              <div key={i} className="card" style={{ display: "block" }}>
                <SkeletonLines count={3} widths={["90%", "50%", "70%"]} />
              </div>
            ))}
          </div>
        )}
        {findings.status === "error" && (
          <div className="panel">
            <EmptyState
              variant="error"
              title="Couldn't load recent findings"
              description={findings.error}
              actions={<Button onClick={findings.reload}>Retry</Button>}
            />
          </div>
        )}
        {findings.status === "success" && (findings.data?.length ?? 0) === 0 && (
          <div className="panel" style={{ padding: "20px 16px", fontSize: 13.5, color: "var(--muted)" }}>
            No findings yet. Runs that fail or need a repro will show up here.
          </div>
        )}
        {findings.status === "success" && findings.data && findings.data.length > 0 && (
          <div className="grid3">
            {findings.data.slice(0, 3).map((f) => (
              <button
                key={f.id}
                className="card"
                style={{ display: "block" }}
                onClick={() => navigate(routes.findings)}
              >
                <h4 style={{ fontSize: 14.5, fontWeight: 600, lineHeight: 1.35 }}>{f.title}</h4>
                <div style={{ marginTop: 9, display: "flex", alignItems: "center", gap: 8 }}>
                  <Pill kind={STATUS_KIND[f.status] ?? "grey"}>{STATUS_LABEL[f.status] ?? f.status}</Pill>
                  <span className="mono" style={{ color: "var(--muted)" }}>{f.route}</span>
                </div>
                <div style={{ marginTop: 11, paddingTop: 10, borderTop: "1px solid var(--line2)", fontSize: 12.5, color: "var(--faint)" }}>
                  Found by {f.found_by} · {relativeTime(f.at)}
                </div>
              </button>
            ))}
          </div>
        )}

        <h2>Recent runs</h2>
        <Panel
          title="Last 24 hours"
          headerExtra={
            <a href="#" className="lnk" onClick={(e) => { e.preventDefault(); navigate(routes.runs); }}>
              View all
            </a>
          }
        >
          {runs.status === "loading" && (
            <Table columns={runColumns} rows={[]} getRowKey={(r) => r.id} skeletonRows={4} caption="Loading recent runs" />
          )}
          {runs.status === "error" && (
            <EmptyState
              variant="error"
              title="Couldn't load recent runs"
              description={runs.error}
              actions={<Button onClick={runs.reload}>Retry</Button>}
            />
          )}
          {runs.status === "success" && runRows.length === 0 && (
            <EmptyState
              variant="empty"
              title="No runs yet"
              description="Describe a behaviour above, or connect a repository, to kick off your first run."
            />
          )}
          {runs.status === "success" && runRows.length > 0 && (
            <Table
              columns={runColumns}
              rows={runRows}
              getRowKey={(r) => r.id}
              onRowClick={(r) => navigate(routes.run(r.id))}
            />
          )}
        </Panel>
      </div>
    </section>
  );
}
