import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

export type AsyncStatus = "loading" | "success" | "error";

export interface AsyncState<T> {
  status: AsyncStatus;
  data: T | null;
  error: string | null;
  reload: () => void;
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
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setError(null);
    fn()
      .then((result) => {
        if (cancelled) return;
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

  return { status, data, error, reload };
}
