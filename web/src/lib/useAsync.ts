import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type AsyncStatus = "loading" | "success" | "error";

export interface AsyncState<T> {
  status: AsyncStatus;
  data: T | null;
  error: string | null;
  /** Re-runs `fn` from scratch: `status` goes back to "loading" first, so the caller's loading UI (a skeleton) replaces whatever was showing. Use for user-initiated retries and filter/param changes. */
  reload: () => void;
  /**
   * Re-runs `fn` WITHOUT resetting `status` to "loading" -- the previous
   * `data` stays visible while the request is in flight, and only updates
   * once it resolves. `refreshing` flags that a background fetch is under
   * way, for a screen that wants to show it without hiding the content.
   * Built for polling a live-ish view (Agents' queue depth) where flipping
   * back to a loading skeleton every few seconds would unmount the very
   * rows the poll exists to keep current.
   */
  refresh: () => void;
  refreshing: boolean;
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Couldn't reach the server.";
}

/**
 * Runs `fn` on mount and whenever `deps` change, exposing a
 * loading/success/error tri-state plus a manual `reload`. Every screen's
 * three states (loading, empty, error) are meant to be derived from this:
 * `status === "loading"` -> skeleton, `status === "error"` -> the retry
 * panel, `status === "success" && data is empty` -> the empty state.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [status, setStatus] = useState<AsyncStatus>("loading");
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [tick, setTick] = useState(0);

  // `fn` is a fresh closure every render (it captures whatever params the
  // caller built this call with); refresh() must call today's `fn`, not the
  // one captured when the effect below last ran, so it's kept in a ref
  // rather than the effect's own dependency array.
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const hasDataRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setError(null);
    fnRef.current()
      .then((result) => {
        if (cancelled) return;
        hasDataRef.current = true;
        setData(result);
        setStatus("success");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(messageOf(err));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  const refresh = useCallback(() => {
    setRefreshing(true);
    fnRef.current()
      .then((result) => {
        hasDataRef.current = true;
        setData(result);
        setStatus("success");
        setError(null);
      })
      .catch((err: unknown) => {
        // A background refresh that fails degrades to the error state only
        // if there was nothing on screen to keep showing; if the previous
        // fetch already succeeded, the stale data stays visible rather
        // than being yanked for a retry panel mid-poll.
        if (!hasDataRef.current) {
          setError(messageOf(err));
          setStatus("error");
        }
      })
      .finally(() => setRefreshing(false));
  }, []);

  return { status, data, error, reload, refresh, refreshing };
}
