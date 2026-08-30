import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";

const ROUTES: [string, number][] = [
  ["/", 100],
  ["/catalog", 96],
  ["/cart", 84],
  ["/checkout/payment", 58],
  ["/checkout/3ds", 0],
];

export function Surface() {
  return (
    <div className="body">
      <div className="pagehead" style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
        <div>
          <h1>Surface map</h1>
          <p>47 routes found. 38 have at least one behaviour written against them.</p>
        </div>
        <span className="sp" />
        <Button size="sm">Re-map</Button>
        <Button variant="pri" size="sm">Write the 9 missing</Button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 330px", gap: 14, marginTop: 18 }}>
        <Panel title="Routes by coverage">
          <div style={{ padding: "6px 16px 14px" }}>
            {ROUTES.map(([route, pct]) => {
              const color = pct === 0 ? "var(--fail)" : pct < 65 ? "var(--warn)" : "var(--pass)";
              return (
                <div
                  key={route}
                  style={{ display: "grid", gridTemplateColumns: "1fr 150px 52px", gap: 14, alignItems: "center", padding: "9px 0", borderBottom: "1px solid var(--line2)" }}
                >
                  <code className="mono">{route}</code>
                  <span style={{ height: 6, borderRadius: 3, background: "#EDEBE7", position: "relative", overflow: "hidden" }}>
                    <i style={{ position: "absolute", inset: "0 auto 0 0", width: `${Math.max(pct, 2)}%`, background: color, borderRadius: 3, display: "block" }} />
                  </span>
                  <span className="mono n" style={{ textAlign: "right", color: "var(--muted)" }}>{pct}%</span>
                </div>
              );
            })}
          </div>
        </Panel>
        <Panel title="Summary">
          <div style={{ padding: "14px 16px", display: "grid", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Pill kind="pass" dot={false}>24</Pill>
              <span style={{ fontSize: 14 }}>Fully covered</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Pill kind="warn" dot={false}>14</Pill>
              <span style={{ fontSize: 14 }}>Partly covered</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Pill kind="fail" dot={false}>9</Pill>
              <span style={{ fontSize: 14 }}>No behaviour at all</span>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
