import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Diff } from "../components/Diff";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { useCurrentUser } from "../lib/useCurrentUser";
import { connectRunStream, type StreamStatus } from "../lib/sse";
import { formatClock, formatDuration, relativeTime } from "../lib/time";
import { routes } from "../lib/routes";
import type { Finding, Patch, RunDetail as RunDetailData, RunStep } from "../lib/types";

const RESULT_KIND: Record<string, PillKind> = {
  passed: "pass",
  failed: "fail",
  unstable: "warn",
  cancelled: "grey",
  queued: "grey",
  running: "info",
};

function resultLabel(run: RunDetailData): string {
  if (run.state === "failed") return `${run.failed} failing`;
  if (run.state === "unstable") return `${run.failed} unstable`;
  if (run.state === "cancelled") return "Cancelled";
  if (run.state === "running") return "Running";
  if (run.state === "queued") return "Queued";
  return "All held";
}

const STEP_KIND: Record<string, PillKind> = {
  ok: "grey",
  warn: "warn",
  fail: "fail",
  gated: "info",
  degraded: "warn",
};

const STEP_LABEL: Record<string, (step: RunStep) => string> = {
  gated: () => "at gate",
  degraded: () => "degraded",
  fail: () => "failed",
};

function stepTag(step: RunStep): string {
  const custom = STEP_LABEL[step.outcome]?.(step);
  if (custom) return custom;
  if (step.duration_ms) return formatDuration(step.duration_ms);
  return step.outcome;
}

const STREAM_LABEL: Record<StreamStatus, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  polling: "Live (polling)",
  closed: "Finished",
};

export function RunDetail() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const { show } = useToast();
  const { data: user } = useCurrentUser();

  const runQuery = useAsync<RunDetailData>(() => api.get<RunDetailData>(`/runs/${runId}`), [runId]);

  const [steps, setSteps] = useState<RunStep[]>([]);
  const [finalRun, setFinalRun] = useState<RunDetailData | null>(null);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  const seenStepIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    seenStepIds.current = new Set();
    setSteps([]);
    setFinalRun(null);
  }, [runId]);

  useEffect(() => {
    if (runQuery.status !== "success" || !runQuery.data) return;
    const fresh = runQuery.data.steps.filter((s) => !seenStepIds.current.has(s.id));
    fresh.forEach((s) => seenStepIds.current.add(s.id));
    if (fresh.length > 0) setSteps((prev) => [...prev, ...fresh].sort((a, b) => a.at - b.at));
  }, [runQuery.status, runQuery.data]);

  useEffect(() => {
    if (!runId) return;
    return connectRunStream(runId, {
      onStep: (step) => {
        if (seenStepIds.current.has(step.id)) return;
        seenStepIds.current.add(step.id);
        setSteps((prev) => [...prev, step].sort((a, b) => a.at - b.at));
      },
      onFinished: setFinalRun,
      onStatusChange: setStreamStatus,
    });
  }, [runId]);

  const run = finalRun ?? runQuery.data;
  const findingId = run?.finding_id ?? null;

  const findingQuery = useAsync<Finding | null>(
    () => (findingId ? api.get<Finding>(`/findings/${findingId}`) : Promise.resolve(null)),
    [findingId],
  );
  const patchQuery = useAsync<Patch | null>(
    () => (findingId ? api.get<Patch>(`/findings/${findingId}/patch`) : Promise.resolve(null)),
    [findingId],
  );
  const finding = findingQuery.data;
  const patch = patchQuery.data;

  const [approving, setApproving] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectNote, setRejectNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const gatedStep = steps.find((s) => s.agent === "surgeon" && s.outcome === "gated");
  const isGated = Boolean(gatedStep) || patch?.gate_state === "awaiting_approval";

  let approveDisabledReason: string | null = null;
  if (user?.role === "reader") {
    approveDisabledReason = "Readers cannot approve a patch. Ask an owner or approver.";
  } else if (isGated && user?.role !== "owner") {
    approveDisabledReason = "This patch is blocked at a gate. Only an owner can approve it.";
  } else if (isGated && user?.role === "owner" && user.totp_enabled === false) {
    approveDisabledReason = "This patch is blocked at a gate and needs two-factor authentication. Add it in Settings → Security.";
  }

  async function onApprove() {
    if (!findingId || approveDisabledReason) return;
    setApproving(true);
    setActionError(null);
    try {
      const res = await api.post<{ already_approved?: boolean }>(`/findings/${findingId}/patch/approve`);
      show(res.already_approved ? "Already approved." : "Patch approved. Merging the pull request.");
      patchQuery.reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Couldn't approve this patch.");
    } finally {
      setApproving(false);
    }
  }

  async function onReject() {
    if (!findingId || rejectNote.trim().length < 10) return;
    setActionError(null);
    try {
      await api.post(`/findings/${findingId}/patch/reject`, { note: rejectNote.trim() });
      show("Patch rejected. The finding stays open.");
      setRejectOpen(false);
      setRejectNote("");
      patchQuery.reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Couldn't reject this patch.");
    }
  }

  async function onRequestChanges() {
    if (!findingId) return;
    setActionError(null);
    try {
      await api.post(`/findings/${findingId}/patch/changes`, { note: rejectNote.trim() || undefined });
      show("Requested changes. Surgeon will try again.");
      patchQuery.reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Couldn't request changes.");
    }
  }

  if (runQuery.status === "loading" && !run) {
    return (
      <div className="body">
        <EmptyState variant="loading" title={`Loading run ${runId}…`} />
      </div>
    );
  }
  if (runQuery.status === "error" && !run) {
    return (
      <div className="body">
        <EmptyState
          variant="error"
          title="Couldn't load this run"
          description={runQuery.error}
          actions={<Button onClick={runQuery.reload}>Retry</Button>}
        />
      </div>
    );
  }
  if (!run) return null;

  return (
    <div className="body">
      <div className="pagehead">
        <Button size="sm" style={{ marginBottom: 14 }} onClick={() => navigate(routes.runs)}>
          <Icon name="i-back" size="xs" /> Back
        </Button>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h1>Run {run.number}</h1>
              <Pill kind={RESULT_KIND[run.state] ?? "grey"}>{resultLabel(run)}</Pill>
            </div>
            <p>
              {run.trigger} · commit <span className="mono">{run.commit || "—"}</span> ·{" "}
              {run.state === "running" || run.state === "queued" ? "started" : "finished"}{" "}
              {relativeTime(run.started_at)}
            </p>
          </div>
          <span className="sp" />
          <Button>Replay deterministically</Button>
          {(run.state === "queued" || run.state === "running") && (
            <Button
              variant="dang"
              onClick={() =>
                api
                  .post(`/runs/${run.id}/cancel`)
                  .then(() => {
                    show("Run cancelled.");
                    runQuery.reload();
                  })
                  .catch((err) => show(err instanceof Error ? err.message : "Couldn't cancel this run."))
              }
            >
              Cancel run
            </Button>
          )}
        </div>
      </div>

      {isGated && (
        <div className="attn" style={{ marginTop: 20 }}>
          <div className="attn-in">
            <span style={{ color: "var(--violet)", marginTop: 2 }}>
              <Icon name="i-star" label="Blocked at a gate" />
            </span>
            <div style={{ flex: 1 }}>
              <div className="eyebrow">Blocked at a gate</div>
              <h3>{finding?.title ?? "Waiting on a human approval"}</h3>
              {finding && (
                <p style={{ marginTop: 9, fontSize: 14, color: "var(--ink2)", lineHeight: 1.6, maxWidth: "80ch" }}>
                  {gatedStep?.detail}
                </p>
              )}
              <div className="meta">
                {finding && <span className="mono">{finding.route}</span>}
                {finding && <span>·</span>}
                {finding && <span>Reproduced {finding.repro_count} of {finding.repro_count}</span>}
                <span>·</span>
                <span>This patch needs an owner's sign-off before it can merge.</span>
              </div>
            </div>
          </div>
        </div>
      )}

      <h2>What the agents did</h2>
      <Panel
        title="Reasoning chain"
        headerExtra={
          <span style={{ fontSize: 13, color: "var(--faint)" }}>
            {steps.length} step{steps.length === 1 ? "" : "s"} · {STREAM_LABEL[streamStatus]}
          </span>
        }
      >
        {steps.length === 0 ? (
          <EmptyState
            variant={run.state === "queued" ? "empty" : "loading"}
            title={run.state === "queued" ? "Queued" : "Waiting for the first step…"}
            description={
              run.state === "queued"
                ? "No agent has picked this run up yet."
                : "An agent will appear here the moment it finishes its first step."
            }
          />
        ) : (
          <div className="tl">
            {steps.map((step, i) => (
              <div className="tl-row" key={step.id}>
                <span className="t">{formatClock(step.at)}</span>
                <span className="g">
                  <i />
                  {i < steps.length - 1 && <u />}
                </span>
                <span className="c">
                  <b>
                    {step.agent.charAt(0).toUpperCase() + step.agent.slice(1)}: {step.summary}
                  </b>
                  {step.detail && <p>{step.detail}</p>}
                </span>
                <Pill kind={STEP_KIND[step.outcome] ?? "grey"} dot={false}>
                  {stepTag(step)}
                </Pill>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {findingId && (
        <>
          <h2>Proposed patch</h2>
          <Panel
            title={patch?.files[0] ? <span className="mono" style={{ fontSize: 13 }}>{patch.files[0]}</span> : "Patch"}
            headerExtra={
              patch && (
                <>
                  <Pill kind="pass" dot={false}>+{patch.added}</Pill>
                  <Pill kind="fail" dot={false}>−{patch.removed}</Pill>
                  {patch.pr_url && (
                    <a href={patch.pr_url} className="lnk" target="_blank" rel="noreferrer">
                      Open on GitHub
                    </a>
                  )}
                </>
              )
            }
          >
            <div style={{ padding: "14px 16px" }}>
              {patchQuery.status === "loading" && <EmptyState variant="loading" title="Loading the proposed patch…" />}
              {patchQuery.status === "error" && (
                <EmptyState
                  variant="error"
                  title="Couldn't load the patch"
                  description={patchQuery.error}
                  actions={<Button onClick={patchQuery.reload}>Retry</Button>}
                />
              )}
              {patchQuery.status === "success" && !patch && (
                <EmptyState variant="empty" title="No patch yet" description="Surgeon hasn't proposed a fix for this finding." />
              )}
              {patch && (
                <>
                  <Diff patch={patch.diff} />
                  {actionError && (
                    <p role="alert" style={{ marginTop: 10, fontSize: 13.5, color: "var(--fail)" }}>
                      {actionError}
                    </p>
                  )}
                  <div className="acts">
                    <Button variant="pri" onClick={onApprove} disabled={Boolean(approveDisabledReason) || approving}>
                      {approving ? "Approving…" : "Approve and merge"}
                    </Button>
                    <Button onClick={onRequestChanges}>Request changes</Button>
                    <Button variant="dang" onClick={() => setRejectOpen((o) => !o)} aria-expanded={rejectOpen}>
                      Reject
                    </Button>
                  </div>
                  {approveDisabledReason && (
                    <p style={{ marginTop: 8, fontSize: 13, color: "var(--muted)" }}>{approveDisabledReason}</p>
                  )}
                  {rejectOpen && (
                    <div style={{ marginTop: 12 }}>
                      <label htmlFor="reject-note" style={{ fontSize: 13, fontWeight: 600, color: "var(--ink2)" }}>
                        Why is this being rejected? (at least 10 characters)
                      </label>
                      <textarea
                        id="reject-note"
                        value={rejectNote}
                        onChange={(e) => setRejectNote(e.target.value)}
                        style={{ width: "100%", marginTop: 6, padding: 9, border: "1px solid var(--line)", borderRadius: 7, minHeight: 60 }}
                      />
                      <div style={{ marginTop: 8 }}>
                        <Button variant="dang" onClick={onReject} disabled={rejectNote.trim().length < 10}>
                          Confirm reject
                        </Button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}
