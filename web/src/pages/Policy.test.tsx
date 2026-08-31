import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Policy } from "./Policy";
import { ToastProvider } from "../components/Toast";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderPolicy() {
  return render(
    <ToastProvider>
      <Policy />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows shaped skeleton rows while loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderPolicy();
  const skeletonRows = document.querySelectorAll("tr[data-skeleton-row]");
  expect(skeletonRows.length).toBeGreaterThan(0);
  expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
  expect(screen.queryByText("Loading gate decisions…")).not.toBeInTheDocument();
});

test("shows a real reason when nothing has hit a gate yet", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, []));
  renderPolicy();
  expect(await screen.findByText("No gate decisions yet")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(500, { message: "the policy log is unavailable" }));
  renderPolicy();
  expect(await screen.findByText("Couldn't load gate decisions")).toBeInTheDocument();
  expect(screen.getByText("the policy log is unavailable")).toBeInTheDocument();
});

test("a decision shows a human label and the rule that produced it, not a raw status", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [
      { time: Date.now() / 1000, agent: "Surgeon", call: "pr.merge storefront#2211", target: "src/checkout/payment-client.ts", rule: "payments/* -> human", decision: "blocked" },
    ]),
  );
  renderPolicy();
  expect(await screen.findByText("Blocked")).toBeInTheDocument();
  expect(screen.getByText("payments/* -> human")).toBeInTheDocument();
  expect(screen.queryByText("blocked")).not.toBeInTheDocument();
});
