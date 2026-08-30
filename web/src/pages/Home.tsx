import { useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Table, type TableColumn } from "../components/Table";
import { routes } from "../lib/routes";

interface RunRow {
  id: string;
  trigger: string;
  result: { kind: "fail" | "pass" | "warn"; label: string };
  behaviours: string;
  duration: string;
  who: string;
}

const RECENT_RUNS: RunRow[] = [
  {
    id: "4471",
    trigger: "Pull request #2211 · retry idempotency",
    result: { kind: "fail", label: "1 failing" },
    behaviours: "341 held · 1 failed",
    duration: "6m 41s",
    who: "Surgeon",
  },
  {
    id: "4470",
    trigger: "Pull request #2210 · checkout nav refactor",
    result: { kind: "pass", label: "All held" },
    behaviours: "338 held · 4 repaired",
    duration: "5m 52s",
    who: "Roger K.",
  },
  {
    id: "4469",
    trigger: "Scheduled · nightly chaos sweep",
    result: { kind: "warn", label: "2 unstable" },
    behaviours: "340 held · 2 unstable",
    duration: "11m 08s",
    who: "Chaos",
  },
];

const columns: TableColumn<RunRow>[] = [
  { key: "id", header: "Run", label: "Run", primary: true, width: "74px", render: (r) => <span className="mono">{r.id}</span> },
  { key: "trigger", header: "Trigger", label: "Trigger", render: (r) => r.trigger },
  { key: "result", header: "Result", label: "Result", width: "116px", render: (r) => <Pill kind={r.result.kind}>{r.result.label}</Pill> },
  { key: "behaviours", header: "Behaviours", label: "Behaviours", width: "154px", render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{r.behaviours}</span> },
  { key: "duration", header: "Duration", label: "Duration", width: "104px", render: (r) => <span className="n" style={{ color: "var(--muted)" }}>{r.duration}</span> },
  { key: "who", header: "Started by", label: "Started by", width: "136px", render: (r) => r.who },
];

export function Home() {
  const navigate = useNavigate();

  return (
    <section>
      <div className="hero">
        <h1>Good evening, Roger. What should we put under test?</h1>
        <div className="promptwrap">
          <div className="prompt">
            <div className="ph">
              <Icon name="i-spark" size="s" /> Describe a behaviour
            </div>
            <textarea placeholder="A customer who retries a slow payment should only be charged once" />
            <div className="pf">
              <span className="sp" />
              <button type="button" className="ib" title="Dictate">
                <Icon name="i-mic" size="s" />
              </button>
              <button type="button" className="ib send" title="Write and run this behaviour">
                <Icon name="i-up" size="s" />
              </button>
            </div>
          </div>
        </div>
        <p className="disc">
          Plumbline writes and runs the test. It never merges anything without your
          approval.
        </p>
      </div>

      <div className="body">
        <h2 style={{ marginTop: 6 }}>Recent runs</h2>
        <Panel
          title="Last 24 hours"
          headerExtra={
            <a href="#" className="lnk" onClick={(e) => { e.preventDefault(); navigate(routes.runs); }}>
              View all
            </a>
          }
        >
          <Table
            columns={columns}
            rows={RECENT_RUNS}
            getRowKey={(r) => r.id}
            onRowClick={(r) => navigate(routes.run(r.id))}
          />
        </Panel>
      </div>
    </section>
  );
}
