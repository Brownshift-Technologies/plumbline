import { useNavigate } from "react-router-dom";
import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { SkeletonBlock, SkeletonLines } from "../components/Skeleton";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { useCurrentUser } from "../lib/useCurrentUser";
import { isDemoWrite, demoWriteMessage } from "../lib/demo";
import { routes } from "../lib/routes";
import type { SurfaceSummary } from "../lib/types";

function coverageColor(pct: number): string {
  if (pct === 0) return "var(--fail)";
  if (pct < 65) return "var(--warn)";
  return "var(--pass)";
}

export function Surface() {
  const navigate = useNavigate();
  const { show } = useToast();
  const { data: user } = useCurrentUser();
  const surface = useAsync<SurfaceSummary>(() => api.get<SurfaceSummary>("/surface"), []);

  const canRemap = user?.role === "owner" || user?.role === "approver";
  const uncovered = surface.status === "success" ? surface.data?.routes.filter((r) => r.coverage_pct === 0) ?? [] : [];

  async function onRemap() {
    try {
      const res = await api.post("/surface/remap");
      show(isDemoWrite(res) ? demoWriteMessage("the repository would be re-mapped") : "Re-mapping the repository.");
      surface.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't start a re-map.");
    }
  }

  async function onWriteMissing() {
    try {
      const res = await api.post<{ id: string; demo?: boolean; persisted?: boolean }>("/runs", {
        trigger: `Write behaviours for ${uncovered.length} uncovered route${uncovered.length === 1 ? "" : "s"}`,
        routes: uncovered.map((r) => r.path),
      });
      if (isDemoWrite(res)) {
        show(demoWriteMessage("behaviours would be written for the missing routes"));
        return;
      }
      show("Writing behaviours for the missing routes.");
      if (res.id) navigate(routes.run(res.id));
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't start that run.");
    }
  }

  if (surface.status === "loading") {
    return (
      <div className="body">
        <div className="pagehead" aria-busy="true">
          <span className="visually-hidden" role="status">Loading the surface map…</span>
          <SkeletonBlock width="35%" height={26} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 330px", gap: 14, marginTop: 18 }}>
          <div className="panel" style={{ padding: "16px 17px" }}>
            <SkeletonLines count={6} />
          </div>
          <div className="panel" style={{ padding: "16px 17px" }}>
            <SkeletonLines count={3} />
          </div>
        </div>
      </div>
    );
  }
  if (surface.status === "error") {
    return (
      <div className="body">
        <div className="pagehead">
          <h1>Surface map</h1>
        </div>
        <EmptyState
          variant="error"
          title="Couldn't load the surface map"
          description={surface.error}
          actions={<Button onClick={surface.reload}>Retry</Button>}
        />
      </div>
    );
  }

  const data = surface.data;
  if (!data || data.routes.length === 0) {
    return (
      <div className="body">
        <div className="pagehead">
          <h1>Surface map</h1>
          <p>Nothing has been mapped yet.</p>
        </div>
        <Panel>
          <EmptyState
            variant="empty"
            icon="i-map"
            title="No routes mapped yet"
            description="Connect a repository, then run Cartographer, to see coverage by route here."
            actions={<Button variant="pri" onClick={onRemap} disabled={!canRemap}>Map now</Button>}
          />
        </Panel>
      </div>
    );
  }

  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Surface map</h1>
          <p>
            {data.routes.length} routes found. {data.fully_covered + data.partly_covered} have at least one behaviour
            written against them.
          </p>
        </div>
        <span className="sp" />
        <Button
          size="sm"
          onClick={onRemap}
          disabled={!canRemap}
          title={canRemap ? undefined : "Only an owner or approver can re-map."}
          aria-describedby={canRemap ? undefined : "remap-disabled-reason"}
        >
          Re-map
        </Button>
        <Button
          variant="pri"
          size="sm"
          onClick={onWriteMissing}
          disabled={uncovered.length === 0}
          title={uncovered.length === 0 ? "Every route already has a behaviour." : undefined}
          aria-describedby={uncovered.length === 0 ? "write-missing-disabled-reason" : undefined}
        >
          Write the {uncovered.length} missing
        </Button>
        {!canRemap && <span id="remap-disabled-reason" className="visually-hidden">Only an owner or approver can re-map.</span>}
        {uncovered.length === 0 && <span id="write-missing-disabled-reason" className="visually-hidden">Every route already has a behaviour.</span>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 330px", gap: 14, marginTop: 18 }}>
        <Panel title="Routes by coverage" headerExtra={<span style={{ fontSize: 13, color: "var(--faint)" }}>Mapped {new Date(data.mapped_at * 1000).toLocaleString()}</span>}>
          <div style={{ padding: "6px 16px 14px" }}>
            {[...data.routes].sort((a, b) => a.coverage_pct - b.coverage_pct).map((r) => {
              const color = coverageColor(r.coverage_pct);
              return (
                <div
                  key={r.id}
                  style={{ display: "grid", gridTemplateColumns: "1fr 150px 52px", gap: 14, alignItems: "center", padding: "9px 0", borderBottom: "1px solid var(--line2)" }}
                >
                  <code className="mono">{r.path}</code>
                  <span style={{ height: 6, borderRadius: 3, background: "#EDEBE7", position: "relative", overflow: "hidden" }}>
                    <i style={{ position: "absolute", inset: "0 auto 0 0", width: `${Math.max(r.coverage_pct, 2)}%`, background: color, borderRadius: 3, display: "block" }} />
                  </span>
                  <span className="mono n" style={{ textAlign: "right", color: "var(--muted)" }}>{r.coverage_pct}%</span>
                </div>
              );
            })}
          </div>
        </Panel>
        <Panel title="Summary">
          <div style={{ padding: "14px 16px", display: "grid", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Pill kind="pass" dot={false}>{data.fully_covered}</Pill>
              <span style={{ fontSize: 14 }}>Fully covered</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Pill kind="warn" dot={false}>{data.partly_covered}</Pill>
              <span style={{ fontSize: 14 }}>Partly covered</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Pill kind="fail" dot={false}>{data.uncovered}</Pill>
              <span style={{ fontSize: 14 }}>No behaviour at all</span>
            </div>
            <div style={{ marginTop: 6, paddingTop: 13, borderTop: "1px solid var(--line2)", fontSize: 13.5, color: "var(--muted)", lineHeight: 1.6 }}>
              Coverage says what was measured. It is not a promise about the {data.uncovered} routes nobody has written
              a behaviour for yet.
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
