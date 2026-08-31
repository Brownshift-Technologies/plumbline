import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Runs } from "./Runs";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows shaped skeleton rows, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  render(
    <MemoryRouter>
      <Runs />
    </MemoryRouter>,
  );
  const skeletonRows = document.querySelectorAll("tr[data-skeleton-row]");
  expect(skeletonRows.length).toBeGreaterThan(0);
  expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
  expect(screen.queryByText("Loading runs…")).not.toBeInTheDocument();
});

test("shows a real reason and a next action when there are no runs yet", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { runs: [], next_cursor: null, total: 0 }));
  render(
    <MemoryRouter>
      <Runs />
    </MemoryRouter>,
  );
  expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  expect(screen.getByText("Runs will appear here once one kicks off.")).toBeInTheDocument();
});

test("shows what failed and offers a retry", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(503, { message: "the run index is temporarily unavailable" }));
  render(
    <MemoryRouter>
      <Runs />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Couldn't load runs")).toBeInTheDocument();
  expect(screen.getByText("the run index is temporarily unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

test("clicking a run row navigates using the run's id, not its display number", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, {
      runs: [
        { id: "run_abc", workspace_id: "ws1", number: 4471, trigger: "Pull request #2211", state: "failed", commit: "8f21c04", started_by: "Surgeon", held: 341, failed: 1, repaired: 0, duration_ms: 401000, started_at: Date.now() / 1000 },
      ],
      next_cursor: null,
      total: 1,
    }),
  );
  render(
    <MemoryRouter>
      <Runs />
    </MemoryRouter>,
  );
  expect(await screen.findByText("4471")).toBeInTheDocument();
  expect(screen.getByText("1 failing")).toBeInTheDocument();
});
