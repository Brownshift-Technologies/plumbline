import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { connectRunStream } from "./sse";

vi.mock("./api", () => ({
  API_BASE: "/api",
  api: { get: vi.fn() },
}));

// Imported after the mock so it resolves to the mocked module.
import { api } from "./api";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    this.listeners[type]?.forEach((cb) => cb({ data: JSON.stringify(data) } as MessageEvent));
  }
  triggerOpen() {
    this.onopen?.();
  }
  triggerError() {
    this.onerror?.();
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.mocked(api.get).mockReset();
});

test("replays steps in order and reports finished exactly once, deduplicating by id", () => {
  const onStep = vi.fn();
  const onFinished = vi.fn();
  const dispose = connectRunStream("r1", { onStep, onFinished });

  const es = FakeEventSource.instances[0];
  es.triggerOpen();
  const step = { id: "s1", agent: "cartographer", summary: "mapped 47 routes", detail: "", outcome: "ok", duration_ms: 300, at: 1, run_id: "r1" };
  es.emit("step", step);
  es.emit("step", step); // a reconnect replay of a step we already have
  es.emit("finished", { id: "r1", state: "passed", steps: [] });

  expect(onStep).toHaveBeenCalledTimes(1);
  expect(onFinished).toHaveBeenCalledTimes(1);
  dispose();
});

test("reconnects with backoff on drop, then falls back to polling after three consecutive failures", () => {
  const onStep = vi.fn();
  const onFinished = vi.fn();
  const onStatusChange = vi.fn();
  vi.mocked(api.get).mockResolvedValue({ id: "r1", state: "running", steps: [] });

  const dispose = connectRunStream("r1", { onStep, onFinished, onStatusChange });
  expect(FakeEventSource.instances).toHaveLength(1);

  FakeEventSource.instances[0].triggerError();
  expect(onStatusChange).toHaveBeenCalledWith("reconnecting");
  vi.advanceTimersByTime(1000);
  expect(FakeEventSource.instances).toHaveLength(2);

  FakeEventSource.instances[1].triggerError();
  vi.advanceTimersByTime(2000);
  expect(FakeEventSource.instances).toHaveLength(3);

  FakeEventSource.instances[2].triggerError();
  // The third consecutive failure switches transport to polling rather than
  // opening a fourth EventSource.
  expect(onStatusChange).toHaveBeenCalledWith("polling");
  expect(FakeEventSource.instances).toHaveLength(3);
  expect(api.get).toHaveBeenCalledWith("/runs/r1");

  dispose();
});

test("a live step resets the failure count so a later single drop reconnects instead of polling", () => {
  const onStatusChange = vi.fn();
  const dispose = connectRunStream("r1", { onStep: vi.fn(), onFinished: vi.fn(), onStatusChange });

  FakeEventSource.instances[0].triggerError();
  vi.advanceTimersByTime(1000);
  FakeEventSource.instances[1].triggerOpen(); // recovered
  FakeEventSource.instances[1].emit("step", { id: "s1", agent: "runner", summary: "ok", detail: "", outcome: "ok", duration_ms: 10, at: 1, run_id: "r1" });

  FakeEventSource.instances[1].triggerError();
  vi.advanceTimersByTime(1000);

  // Only a third EventSource for this single new failure -- not treated as
  // the second of a three-in-a-row streak, since the intervening success
  // reset the counter.
  expect(FakeEventSource.instances).toHaveLength(3);
  expect(onStatusChange).not.toHaveBeenCalledWith("polling");
  dispose();
});

test("disposing the stream tears down the EventSource and stops any pending reconnect", () => {
  const dispose = connectRunStream("r1", { onStep: vi.fn(), onFinished: vi.fn() });
  const es = FakeEventSource.instances[0];
  dispose();
  expect(es.closed).toBe(true);

  es.triggerError();
  vi.advanceTimersByTime(10000);
  // No new EventSource is opened after disposal.
  expect(FakeEventSource.instances).toHaveLength(1);
});

test("the finished event's own steps are run through the same dedup path, so a step that only ever appeared in the terminal payload is not dropped", () => {
  const onStep = vi.fn();
  const onFinished = vi.fn();
  const dispose = connectRunStream("r1", { onStep, onFinished });

  const es = FakeEventSource.instances[0];
  es.triggerOpen();
  const seenStep = { id: "s1", agent: "cartographer", summary: "mapped", detail: "", outcome: "ok", duration_ms: 100, at: 1, run_id: "r1" };
  const finalOnlyStep = { id: "s2", agent: "surgeon", summary: "opened the pull request", detail: "", outcome: "ok", duration_ms: 50, at: 2, run_id: "r1" };
  es.emit("step", seenStep);
  // The server finalises with BOTH steps in its payload -- s1 (already
  // streamed) and s2 (never sent as its own "step" event).
  es.emit("finished", { id: "r1", state: "passed", steps: [seenStep, finalOnlyStep] });

  expect(onStep).toHaveBeenCalledTimes(2);
  expect(onStep).toHaveBeenCalledWith(finalOnlyStep);
  expect(onFinished).toHaveBeenCalledTimes(1);
  dispose();
});
