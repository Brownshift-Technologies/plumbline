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
    refresh: vi.fn(),
    refreshing: false,
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

test("shows shimmering placeholder blocks while loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderBilling();
  expect(document.querySelectorAll(".skel").length).toBeGreaterThan(0);
  // No visible spinner tile -- EmptyState's loading variant is not what renders here.
  expect(document.querySelector(".tile")).not.toBeInTheDocument();
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

const BASE_BILLING = {
  plan: "Team", price: 240, interval: "month", renews_at: Date.now() / 1000,
  runs_used: 1, run_limit: 500, seats_used: 1, seat_limit: 5, payment_method: "",
};

test("invoice history shows a real reason when there are none yet", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/billing/invoices") ? jsonResponse(200, []) : jsonResponse(200, BASE_BILLING),
  );
  renderBilling();
  expect(await screen.findByText("No invoices yet.")).toBeInTheDocument();
});

test("invoice history degrades gracefully -- a missing endpoint reads as 'none on file', not a page-level error", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/billing/invoices") ? jsonResponse(404, { message: "not found" }) : jsonResponse(200, BASE_BILLING),
  );
  renderBilling();
  expect(await screen.findByText("No invoice history is available yet.")).toBeInTheDocument();
  // The rest of the pane (plan/usage) still rendered -- one footnote
  // section failing does not take down the whole screen.
  expect(screen.getByText("Team, billed month. Renews " + new Date(BASE_BILLING.renews_at * 1000).toLocaleDateString() + ".")).toBeInTheDocument();
});

test("invoice history lists a real invoice with a download link", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/billing/invoices")
      ? jsonResponse(200, [{ id: "inv1", at: Date.now() / 1000, amount: 24000, status: "paid", url: "https://example.com/inv1.pdf" }])
      : jsonResponse(200, BASE_BILLING),
  );
  renderBilling();
  expect(await screen.findByText("$240.00")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", "https://example.com/inv1.pdf");
});

test("a demo session changing plan is told nothing was saved, not given the real success toast", async () => {
  const user = (await import("@testing-library/user-event")).default.setup();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/billing/plan")) return jsonResponse(200, { demo: true, persisted: false });
    if (url.includes("/billing/invoices")) return jsonResponse(200, []);
    return jsonResponse(200, BASE_BILLING);
  });
  renderBilling("owner");
  await user.click(await screen.findByRole("button", { name: "Change plan" }));
  expect(await screen.findByText(/Nothing was saved/)).toBeInTheDocument();
});
