import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Panel } from "../components/Panel";
import { Pill, type PillKind } from "../components/Pill";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { SkeletonBlock, SkeletonLines } from "../components/Skeleton";
import { Diff } from "../components/Diff";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { useCurrentUser } from "../lib/useCurrentUser";
import { connectRunStream, type StreamStatus } from "../lib/sse";
import { isDemoWrite, demoWriteMessage } from "../lib/demo";
import { formatClock, formatDuration, relativeTime } from "../lib/time";
import { routes } from "../lib/routes";
import type { Finding, Patch, Run, RunDetailResponse, RunStep } from "../lib/types";

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
  if (run.state === "running") return "Running";
  if (run.state === "queued") return "Queued";
  return "All held";
}

function behaviourSummary(run: Run): string {
  const parts = [`${run.held} held`];
  if (run.failed) parts.push(`${run.failed} failed`);
  if (run.repaired) parts.push(`${run.repaired} repaired`);
  return parts.join(" · ");
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

  const runQuery = useAsync<RunDetailResponse>(() => api.get<RunDetailResponse>(`/runs/${runId}`), [runId]);

  const [steps, setSteps] = useState<RunStep[]>([]);
  const [finalRun, setFinalRun] = useState<Run | null>(null);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  const [lastAnnouncedStep, setLastAnnouncedStep] = useState<string>("");
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
        setLastAnnouncedStep(`${step.agent}: ${step.summary}`);
      },
      onFinished: setFinalRun,
      onStatusChange: setStreamStatus,
    });
  }, [runId]);

  const run = finalRun ?? runQuery.data?.run ?? null;
  // `finding_id` is a sibling of `run` in the `GET /runs/{id}` envelope,
  // not a field on the run itself -- and the "finished" SSE event (what
  // populates `finalRun`) never carries it at all (see lib/sse.ts), so
  // this always reads off the initial fetch, independent of whichever
  // source `run` above is currently drawing from.
  const findingId = runQuery.data?.finding_id ?? null;

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
  const [changesOpen, setChangesOpen] = useState(false);
  const [changesNote, setChangesNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const gatedStep = steps.find((s) => s.agent === "surgeon" && s.outcome === "gated");
  const isGated = Boolean(gatedStep) || patch?.gate_state === "awaiting_approval";

  let approveDisabledReason: string | null = null;
  if (user?.is_demo) {
    // Mirrors app/finding_routes.py's _check_approve_permission, which
    // returns early for a demo session: it holds no Membership row, so
    // /api/auth/me reports role "reader", but it is the sole de-facto
    // owner of its own sandbox workspace. Without this, the API allowed
    // the approval and the UI disabled the button that triggers it --
    // "Readers cannot approve a patch" on the demo's whole hero moment.
    approveDisabledReason = null;
  } else if (user?.role === "reader") {
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
      const res = await api.post<{ already_approved?: boolean; demo?: boolean; persisted?: boolean }>(
        `/findings/${findingId}/patch/approve`,
      );
      if (isDemoWrite(res)) {
        show(demoWriteMessage(res, "this is where the pull request would merge"));
      } else {
        show(res.already_approved ? "Already approved." : "Patch approved. Merging the pull request.");
      }
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
      const res = await api.post(`/findings/${findingId}/patch/reject`, { note: rejectNote.trim() });
      show(isDemoWrite(res) ? demoWriteMessage(res, "this patch would be rejected") : "Patch rejected. The finding stays open.");
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
      const res = await api.post(`/findings/${findingId}/patch/changes`, { note: changesNote.trim() || undefined });
      show(isDemoWrite(res) ? demoWriteMessage(res, "changes would be requested from Surgeon") : "Requested changes. Surgeon will try again.");
      setChangesOpen(false);
      setChangesNote("");
      patchQuery.reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Couldn't request changes.");
    }
  }

  async function onCancel() {
    if (!run) return;
    try {
      const res = await api.post(`/runs/${run.id}/cancel`);
      show(isDemoWrite(res) ? demoWriteMessage(res, "this run would be cancelled") : "Run cancelled.");
      runQuery.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't cancel this run.");
    }
  }

  if (runQuery.status === "loading" && !run) {
    return (
      <div className="body">
        <div className="pagehead" aria-busy="true">
          <span className="visually-hidden" role="status">
            Loading run {runId}…
          </span>
          <SkeletonBlock width={90} height={28} style={{ marginBottom: 14 }} />
          <SkeletonBlock width="40%" height={26} style={{ marginBottom: 10 }} />
          <SkeletonBlock width="60%" height={15} />
        </div>
        <Panel title="Reasoning chain" style={{ marginTop: 20 }}>
          <div style={{ padding: "12px 16px" }}>
            <SkeletonLines count={4} />
          </div>
        </Panel>
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
            <p className="n" style={{ marginTop: 4, fontSize: 13.5, color: "var(--muted)" }}>
              {behaviourSummary(run)}
            </p>
            {user?.is_demo && (
              <p style={{ marginTop: 4, fontSize: 13, color: "var(--muted)" }}>
                Replayed from the demo's seeded fixture, not a real browser run against a live app.
              </p>
            )}
          </div>
          <span className="sp" />
          <Button>Replay deterministically</Button>
          {(run.state === "queued" || run.state === "running") && (
            <Button variant="dang" onClick={onCancel}>
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
                {finding?.seed && <span>·</span>}
                {finding?.seed && <span>Seed <span className="mono">{finding.seed}</span></span>}
                <span>·</span>
                <span>This patch needs an owner's sign-off before it can merge.</span>
              </div>
            </div>
          </div>
        </div>
      )}

      <h2>What the agents did</h2>
      {/* A visually-hidden live region announces each new step as it streams
          in, without re-announcing the whole chain on every render. */}
      <div className="visually-hidden" role="status" aria-live="polite">
        {lastAnnouncedStep}
      </div>
      <Panel
        title="Reasoning chain"
        headerExtra={
          <span style={{ fontSize: 13, color: "var(--faint)" }}>
            {steps.length} step{steps.length === 1 ? "" : "s"} · {STREAM_LABEL[streamStatus]}
          </span>
        }
      >
        {steps.length === 0 && runQuery.status === "loading" ? (
          <div style={{ padding: "12px 16px" }} aria-busy="true">
            <span className="visually-hidden" role="status">Loading the reasoning chain…</span>
            <SkeletonLines count={4} />
          </div>
        ) : steps.length === 0 ? (
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
              {patchQuery.status === "loading" && (
                <div aria-busy="true">
                  <span className="visually-hidden" role="status">Loading the proposed patch…</span>
                  <SkeletonLines count={6} widths={["100%", "92%", "88%", "70%", "95%", "60%"]} />
                </div>
              )}
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
                  {patch.verified && (
                    <p style={{ marginTop: 12, fontSize: 13.5, color: "var(--muted)", lineHeight: 1.6 }}>
                      <Icon name="i-check" size="xs" /> Verified: re-run against the same seed and
                      latency, then the full suite. The patch reverts itself if either check fails.
                    </p>
                  )}
                  {actionError && (
                    <p role="alert" style={{ marginTop: 10, fontSize: 13.5, color: "var(--fail)" }}>
                      {actionError}
                    </p>
                  )}
                  <div className="acts">
                    <Button
                      variant="pri"
                      onClick={onApprove}
                      disabled={Boolean(approveDisabledReason) || approving}
                      aria-describedby={approveDisabledReason ? "approve-disabled-reason" : undefined}
                    >
                      {approving ? "Approving…" : "Approve and merge"}
                    </Button>
                    <Button onClick={() => setChangesOpen((o) => !o)} aria-expanded={changesOpen}>
                      Request changes
                    </Button>
                    <Button variant="dang" onClick={() => setRejectOpen((o) => !o)} aria-expanded={rejectOpen}>
                      Reject
                    </Button>
                  </div>
                  {approveDisabledReason && (
                    <p id="approve-disabled-reason" style={{ marginTop: 8, fontSize: 13, color: "var(--muted)" }}>
                      {approveDisabledReason}
                    </p>
                  )}
                  {changesOpen && (
                    <div style={{ marginTop: 12 }}>
                      <label htmlFor="changes-note" style={{ fontSize: 13, fontWeight: 600, color: "var(--ink2)" }}>
                        What should change? (optional)
                      </label>
                      <textarea
                        id="changes-note"
                        value={changesNote}
                        onChange={(e) => setChangesNote(e.target.value)}
                        style={{ width: "100%", marginTop: 6, padding: 9, border: "1px solid var(--line)", borderRadius: 7, minHeight: 60 }}
                      />
                      <div style={{ marginTop: 8 }}>
                        <Button onClick={onRequestChanges}>Send to Surgeon</Button>
                      </div>
                    </div>
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
                        aria-describedby="reject-note-hint"
                        aria-invalid={rejectNote.length > 0 && rejectNote.trim().length < 10}
                        style={{ width: "100%", marginTop: 6, padding: 9, border: "1px solid var(--line)", borderRadius: 7, minHeight: 60 }}
                      />
                      <p id="reject-note-hint" role="alert" style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 4 }}>
                        {rejectNote.trim().length < 10
                          ? `${10 - rejectNote.trim().length} more character${10 - rejectNote.trim().length === 1 ? "" : "s"} needed.`
                          : "Ready to submit."}
                      </p>
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
