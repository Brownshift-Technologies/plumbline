import { useNavigate, useParams } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { routes } from "../lib/routes";

const TIMELINE = [
  { t: "14:02:11", title: "Cartographer mapped 47 routes", detail: "12 new since run 4469.", tag: "33s", kind: "grey" as const },
  { t: "14:03:19", title: "Chaos injected 240ms of latency on payments-api", detail: "The provider's p99 is 210ms.", tag: "48s", kind: "warn" as const },
  { t: "14:04:07", title: "Runner saw two charges", detail: "Two POST /v1/charges with different idempotency keys.", tag: "failed", kind: "fail" as const },
  { t: "14:06:29", title: "Surgeon opened the pull request and stopped", detail: "Policy will not let an agent merge under payments/*.", tag: "at gate", kind: "info" as const },
];

export interface RunDetailProps {
  loading?: boolean;
  error?: string;
}

export function RunDetail({ loading, error }: RunDetailProps = {}) {
  const { runId } = useParams();
  const navigate = useNavigate();

  if (loading) {
    return (
      <div className="body">
        <EmptyState variant="loading" title={`Loading run ${runId}…`} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="body">
        <EmptyState variant="error" title="Couldn't load this run" description={error} />
      </div>
    );
  }

  return (
    <div className="body">
      <div className="pagehead">
        <Button size="sm" style={{ marginBottom: 14 }} onClick={() => navigate(routes.runs)}>
          <Icon name="i-back" size="xs" /> Back
        </Button>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h1>Run {runId}</h1>
              <Pill kind="fail">1 failing</Pill>
            </div>
            <p>
              Pull request #2211 · retry idempotency · commit{" "}
              <span className="mono">8f21c04</span> · finished 22 minutes ago
            </p>
          </div>
          <span className="sp" />
          <Button>Replay deterministically</Button>
          <Button variant="pri">Approve patch</Button>
        </div>
      </div>

      <h2>What the agents did</h2>
      <Panel title="Reasoning chain" headerExtra={<span style={{ fontSize: 13, color: "var(--faint)" }}>4 of 7 steps shown</span>}>
        <div className="tl">
          {TIMELINE.map((row, i) => (
            <div className="tl-row" key={row.t}>
              <span className="t">{row.t}</span>
              <span className="g">
                <i />
                {i < TIMELINE.length - 1 && <u />}
              </span>
              <span className="c">
                <b>{row.title}</b>
                <p>{row.detail}</p>
              </span>
              <Pill kind={row.kind} dot={false}>
                {row.tag}
              </Pill>
            </div>
          ))}
        </div>
      </Panel>

      <h2>Proposed patch</h2>
      <Panel
        title={<span className="mono" style={{ fontSize: 13 }}>src/checkout/payment-client.ts</span>}
        headerExtra={
          <>
            <Pill kind="pass" dot={false}>+7</Pill>
            <Pill kind="fail" dot={false}>−2</Pill>
          </>
        }
      >
        <div style={{ padding: "14px 16px" }}>
          <div className="diff">
            <div className="hunk">{"@@ -118,9 +118,14 @@ async function submitCharge(cart, provider) {"}</div>
            <div className="ctx">   const key = idempotencyKeyFor(cart);</div>
            <div className="del">-  void persistIdempotencyKey(key);</div>
            <div className="add">+  await persistIdempotencyKey(key);</div>
            <div className="add">+    idempotencyKey: key,</div>
            <div className="ctx">{" }"}</div>
          </div>
          <div className="acts">
            <Button variant="pri">Approve and merge</Button>
            <Button>Request changes</Button>
            <Button variant="dang">Reject</Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}
