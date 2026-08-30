import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Findings } from "./Findings";
import { ToastProvider } from "../components/Toast";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderFindings() {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <Findings />
      </MemoryRouter>
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows skeleton loading rows, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderFindings();
  expect(screen.getByText("Loading findings…")).toBeInTheDocument();
});

test("shows a real reason and a next action when there are no open findings", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, []));
  renderFindings();
  expect(await screen.findByText("No open findings")).toBeInTheDocument();
  expect(screen.getByText("Nothing is currently failing.")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(502, { message: "the finding index timed out" }));
  renderFindings();
  expect(await screen.findByText("Couldn't load findings")).toBeInTheDocument();
  expect(screen.getByText("the finding index timed out")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

test("status is shown as a pill with a human label, not the raw status code", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [
      { id: "f1", workspace_id: "ws1", title: "A retried payment charges the customer twice", route: "/checkout/payment", found_by: "Chaos", status: "patch_ready", severity: "high", seed: "0x1", repro_count: 5, at: Date.now() / 1000 },
    ]),
  );
  renderFindings();
  expect(await screen.findByText("Patch ready")).toBeInTheDocument();
  expect(screen.queryByText("patch_ready")).not.toBeInTheDocument();
});
