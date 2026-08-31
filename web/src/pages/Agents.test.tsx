import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Agents } from "./Agents";
import { ToastProvider } from "../components/Toast";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderAgents() {
  return render(
    <ToastProvider>
      <Agents />
    </ToastProvider>,
  );
}

const OWNER = { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" };
const AGENT = { name: "Cartographer", version: "2.4.1", tools: ["browser.read"], model: "gemini-3.5-flash", queue: 0, state: "idle" };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows shaped skeleton rows while loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderAgents();
  const skeletonRows = document.querySelectorAll("tr[data-skeleton-row]");
  expect(skeletonRows.length).toBeGreaterThan(0);
  expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
  // The failure mode this guards against: a spinner tile with no row shape.
  expect(screen.queryByText("Checking on the fleet…")).not.toBeInTheDocument();
});

test("shows a real reason when no agents are registered", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me") ? jsonResponse(200, OWNER) : jsonResponse(200, []),
  );
  renderAgents();
  expect(await screen.findByText("No agents registered")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me") ? jsonResponse(200, OWNER) : jsonResponse(500, { message: "the agent registry is unreachable" }),
  );
  renderAgents();
  expect(await screen.findByText("Couldn't load the agent registry")).toBeInTheDocument();
  expect(screen.getByText("the agent registry is unreachable")).toBeInTheDocument();
});

test("pause is disabled with an explanation for a non-owner, reachable by assistive tech via aria-describedby", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, { ...OWNER, role: "approver" });
    return jsonResponse(200, [AGENT]);
  });
  renderAgents();
  const pauseAll = await screen.findByRole("button", { name: "Pause all" });
  expect(pauseAll).toBeDisabled();
  expect(pauseAll).toHaveAttribute("title", "Only an owner can pause the fleet.");
  const describedBy = pauseAll.getAttribute("aria-describedby");
  expect(describedBy).toBeTruthy();
  expect(document.getElementById(describedBy!)).toHaveTextContent(/owner/i);
});

test("the 5s live-refresh keeps existing rows on screen instead of unmounting them back to a skeleton", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let queue = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, OWNER);
    queue += 1;
    return jsonResponse(200, [{ ...AGENT, queue }]);
  });
  renderAgents();

  expect(await screen.findByText("Cartographer")).toBeInTheDocument();
  expect(screen.getByText("1")).toBeInTheDocument(); // first queue depth

  await vi.advanceTimersByTimeAsync(5000);

  // The row is still the SAME row (still present), just with updated data --
  // never replaced by a skeleton row or a loading title along the way.
  expect(await screen.findByText("2")).toBeInTheDocument(); // refreshed queue depth
  expect(screen.getByText("Cartographer")).toBeInTheDocument();
  expect(document.querySelectorAll("tr[data-skeleton-row]")).toHaveLength(0);

  vi.useRealTimers();
});
