import { API_BASE, api } from "./api";
import type { RunDetail, RunStep } from "./types";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "polling" | "closed";

export interface RunStreamHandlers {
  /** Called once per step, in the order the server sends them (replay, then live). */
  onStep: (step: RunStep) => void;
  /** Called once, when the run reaches a terminal state. */
  onFinished: (run: RunDetail) => void;
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
      .get<RunDetail>(`/runs/${runId}`)
      .then((run) => {
        if (closed) return;
        for (const step of run.steps) emitStep(step);
        if (isTerminal(run.state)) {
          handlers.onFinished(run);
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
        handlers.onFinished(JSON.parse((event as MessageEvent).data));
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
