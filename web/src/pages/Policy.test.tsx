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

function route(url: string) {
  if (url.includes("/policy/decisions")) return "decisions";
  if (url.includes("/policy/rules")) return "rules";
  return "me";
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows skeleton loading for gate decisions, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderPolicy();
  expect(screen.getByText("Loading gate decisions…")).toBeInTheDocument();
});

test("shows a real reason when nothing has hit a gate yet", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const which = route(String(input));
    if (which === "decisions") return jsonResponse(200, []);
    if (which === "rules") return jsonResponse(200, { version: 14, rules: [] });
    return jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" });
  });
  renderPolicy();
  expect(await screen.findByText("No gate decisions yet")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const which = route(String(input));
    if (which === "decisions") return jsonResponse(500, { message: "the policy log is unavailable" });
    if (which === "rules") return jsonResponse(200, { version: 14, rules: [] });
    return jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" });
  });
  renderPolicy();
  expect(await screen.findByText("Couldn't load gate decisions")).toBeInTheDocument();
  expect(screen.getByText("the policy log is unavailable")).toBeInTheDocument();
});

test("a decision shows a human label and the rule that produced it, not a raw status", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const which = route(String(input));
    if (which === "decisions") {
      return jsonResponse(200, [
        { time: Date.now() / 1000, agent: "Surgeon", call: "pr.merge storefront#2211", target: "src/checkout/payment-client.ts", rule: "payments/* -> human", decision: "blocked" },
      ]);
    }
    if (which === "rules") return jsonResponse(200, { version: 14, rules: [] });
    return jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" });
  });
  renderPolicy();
  expect(await screen.findByText("Blocked")).toBeInTheDocument();
  expect(screen.getByText("payments/* -> human")).toBeInTheDocument();
  expect(screen.queryByText("blocked")).not.toBeInTheDocument();
});
