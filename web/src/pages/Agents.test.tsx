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

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows skeleton loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderAgents();
  expect(screen.getByText("Checking on the fleet…")).toBeInTheDocument();
});

test("shows a real reason when no agents are registered", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me")
      ? jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" })
      : jsonResponse(200, []),
  );
  renderAgents();
  expect(await screen.findByText("No agents registered")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me")
      ? jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" })
      : jsonResponse(500, { message: "the agent registry is unreachable" }),
  );
  renderAgents();
  expect(await screen.findByText("Couldn't load the agent registry")).toBeInTheDocument();
  expect(screen.getByText("the agent registry is unreachable")).toBeInTheDocument();
});

test("pause is disabled with an explanation for a non-owner", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, { id: "u1", name: "Ama", is_demo: false, workspace_id: "ws1", role: "approver" });
    return jsonResponse(200, [{ name: "Cartographer", version: "2.4.1", tools: ["browser.read"], model: "gemini-3.5-flash", queue: 0, state: "idle" }]);
  });
  renderAgents();
  const pauseAll = await screen.findByRole("button", { name: "Pause all" });
  expect(pauseAll).toBeDisabled();
  expect(pauseAll).toHaveAttribute("title", "Only an owner can pause the fleet.");
});
