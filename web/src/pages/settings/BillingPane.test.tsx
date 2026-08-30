import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { BillingPane } from "./BillingPane";
import { ToastProvider } from "../../components/Toast";
import type { AsyncState } from "../../lib/useAsync";
import type { CurrentUser } from "../../lib/types";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function userState(role: CurrentUser["role"] = "owner"): AsyncState<CurrentUser> {
  return {
    status: "success",
    data: { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role },
    error: null,
    reload: vi.fn(),
  };
}

function renderBilling(role: CurrentUser["role"] = "owner") {
  return render(
    <ToastProvider>
      <BillingPane user={userState(role)} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows skeleton loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderBilling();
  expect(screen.getByText("Loading billing…")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(500, { message: "billing is temporarily unavailable" }));
  renderBilling();
  expect(await screen.findByText("Couldn't load billing")).toBeInTheDocument();
  expect(screen.getByText("billing is temporarily unavailable")).toBeInTheDocument();
});

test("both usage meters render with their limits", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/billing/invoices")) return jsonResponse(200, []);
    return jsonResponse(200, {
      plan: "Team",
      price: 240,
      interval: "month",
      renews_at: Date.now() / 1000,
      runs_used: 184,
      run_limit: 500,
      seats_used: 3,
      seat_limit: 5,
      payment_method: "Visa ending 4242",
    });
  });
  renderBilling();
  expect(await screen.findByText("184 / 500")).toBeInTheDocument();
  expect(screen.getByText("3 / 5")).toBeInTheDocument();
});

test("changing the plan is disabled with an explanation for a non-owner", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/billing/invoices")) return jsonResponse(200, []);
    return jsonResponse(200, {
      plan: "Team", price: 240, interval: "month", renews_at: Date.now() / 1000,
      runs_used: 1, run_limit: 500, seats_used: 1, seat_limit: 5, payment_method: "",
    });
  });
  renderBilling("approver");
  const changePlan = await screen.findByRole("button", { name: "Change plan" });
  expect(changePlan).toBeDisabled();
  expect(changePlan).toHaveAttribute("title", "Only an owner can change the plan.");
});
