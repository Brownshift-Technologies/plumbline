import { renderHook, waitFor, act } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { useAsync } from "./useAsync";

test("reload resets status to loading so a skeleton replaces the old content", async () => {
  let call = 0;
  const fn = vi.fn(async () => {
    call += 1;
    return `result-${call}`;
  });
  const { result } = renderHook(() => useAsync(fn, []));
  await waitFor(() => expect(result.current.status).toBe("success"));
  expect(result.current.data).toBe("result-1");

  act(() => result.current.reload());
  expect(result.current.status).toBe("loading");
  await waitFor(() => expect(result.current.status).toBe("success"));
  expect(result.current.data).toBe("result-2");
});

test("refresh keeps the previous data on screen instead of flipping status back to loading", async () => {
  let call = 0;
  const fn = vi.fn(async () => {
    call += 1;
    return `result-${call}`;
  });
  const { result } = renderHook(() => useAsync(fn, []));
  await waitFor(() => expect(result.current.status).toBe("success"));
  expect(result.current.data).toBe("result-1");

  act(() => result.current.refresh());
  // Status never drops back to loading, and the stale data is still there
  // while the refresh is in flight.
  expect(result.current.status).toBe("success");
  expect(result.current.data).toBe("result-1");
  expect(result.current.refreshing).toBe(true);

  await waitFor(() => expect(result.current.refreshing).toBe(false));
  expect(result.current.data).toBe("result-2");
  expect(result.current.status).toBe("success");
});

test("a failed refresh keeps showing the last good data rather than replacing it with an error panel", async () => {
  let call = 0;
  const fn = vi.fn(async () => {
    call += 1;
    if (call === 1) return "good-data";
    throw new Error("network blip");
  });
  const { result } = renderHook(() => useAsync(fn, []));
  await waitFor(() => expect(result.current.data).toBe("good-data"));

  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.refreshing).toBe(false));

  expect(result.current.status).toBe("success");
  expect(result.current.data).toBe("good-data");
});
