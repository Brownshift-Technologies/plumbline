import { API_BASE, api } from "./api";
import type { Run, RunDetailResponse, RunStep } from "./types";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "polling" | "closed";

export interface RunStreamHandlers {
  /** Called once per step, in the order the server sends them (replay, then live). */
  onStep: (step: RunStep) => void;
  /**
   * Called once, when the run reaches a terminal state. The `finished` SSE
   * event carries `_run_json(current)` (`app/run_routes.py`) -- a bare
   * `Run`, with no `steps` or `finding_id` of its own -- so this is a flat
   * `Run`, not the `{run, steps, finding_id}` shape `GET /runs/{id}` sends.
   */
  onFinished: (run: Run) => void;
  onStatusChange?: (status: StreamStatus) => void;
}

const MAX_CONSECUTIVE_FAILURES = 3;
const POLL_INTERVAL_MS = 4000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 8000;

function isTerminal(state: string): boolean {
  return ["passed", "failed", "unstable", "cancelled"].includes(state);
}

/**
 * Live-updates a run's reasoning chain over SSE (`GET /api/runs/{id}/stream`),
 * reconnecting with backoff on drop and falling back to polling
 * `GET /api/runs/{id}` after three consecutive failures to connect. The
 * server replays every step already recorded on connect (task 14a's
 * contract), so a fresh connection or a reconnect never loses history --
 * `onStep` is only ever called with steps not yet seen, deduplicated by id.
 *
 * Returns a disposer that tears down whichever transport (SSE or polling)
 * is currently active.
 */
export function connectRunStream(runId: string, handlers: RunStreamHandlers): () => void {
  let es: EventSource | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let failures = 0;
  let closed = false;
  const seenStepIds = new Set<string>();

  function setStatus(s: StreamStatus) {
    handlers.onStatusChange?.(s);
  }

  function emitStep(step: RunStep) {
    if (seenStepIds.has(step.id)) return;
    seenStepIds.add(step.id);
    handlers.onStep(step);
  }

  function teardownSse() {
    es?.close();
    es = null;
  }

  function pollOnce() {
    if (closed) return;
    api
      .get<RunDetailResponse>(`/runs/${runId}`)
      .then((data) => {
        if (closed) return;
        for (const step of data.steps) emitStep(step);
        if (isTerminal(data.run.state)) {
          handlers.onFinished(data.run);
          setStatus("closed");
          return;
        }
        pollTimer = setTimeout(pollOnce, POLL_INTERVAL_MS);
      })
      .catch(() => {
        if (closed) return;
        pollTimer = setTimeout(pollOnce, POLL_INTERVAL_MS);
      });
  }

  function startPolling() {
    teardownSse();
    setStatus("polling");
    pollOnce();
  }

  function scheduleReconnect() {
    failures += 1;
    if (failures >= MAX_CONSECUTIVE_FAILURES) {
      startPolling();
      return;
    }
    setStatus("reconnecting");
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** (failures - 1), RECONNECT_MAX_MS);
    reconnectTimer = setTimeout(connect, delay);
  }

  function connect() {
    if (closed) return;
    setStatus(failures === 0 ? "connecting" : "reconnecting");
    let source: EventSource;
    try {
      source = new EventSource(`${API_BASE}/runs/${runId}/stream`, { withCredentials: true });
    } catch {
      scheduleReconnect();
      return;
    }
    es = source;

    source.addEventListener("step", (event) => {
      failures = 0;
      try {
        emitStep(JSON.parse((event as MessageEvent).data));
      } catch {
        // A malformed event must not take the stream down.
      }
    });

    source.addEventListener("finished", (event) => {
      try {
        // `_run_events` (`app/run_routes.py`) always drains every unsent
        // step as its own "step" event, in the same generator iteration,
        // before it ever yields "finished" -- so by construction there is
        // no step this payload could carry that emitStep hasn't already
        // seen. The payload itself is a bare `Run` (`_run_json`): no
        // `steps` field to replay from at all.
        const finalRun: Run = JSON.parse((event as MessageEvent).data);
        handlers.onFinished(finalRun);
      } catch {
        // fall through; polling/reconnect below still applies if this repeats
      }
      teardownSse();
      setStatus("closed");
    });

    source.onopen = () => {
      failures = 0;
      setStatus("live");
    };

    source.onerror = () => {
      teardownSse();
      if (closed) return;
      scheduleReconnect();
    };
  }

  connect();

  return () => {
    closed = true;
    teardownSse();
    if (pollTimer) clearTimeout(pollTimer);
    if (reconnectTimer) clearTimeout(reconnectTimer);
  };
}
