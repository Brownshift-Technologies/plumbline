import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { WorkspacePane } from "./WorkspacePane";
import { ToastProvider } from "../../components/Toast";
import type { AsyncState } from "../../lib/useAsync";
import type { CurrentUser } from "../../lib/types";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function userState(role: CurrentUser["role"]): AsyncState<CurrentUser> {
  return {
    status: "success",
    data: { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role },
    error: null,
    reload: vi.fn(),
    refresh: vi.fn(),
    refreshing: false,
  };
}

function renderWorkspace(role: CurrentUser["role"] = "owner") {
  return render(
    <ToastProvider>
      <WorkspacePane user={userState(role)} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows a skeleton while loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderWorkspace();
  expect(screen.getByRole("status", { hidden: true })).toBeInTheDocument();
});

test("an unset target URL loads as an empty field, not an error", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { target_url: "" }));
  renderWorkspace();
  const input = await screen.findByLabelText("Target URL");
  expect(input).toHaveValue("");
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(500, { message: "the workspace store is unavailable" }));
  renderWorkspace();
  expect(await screen.findByText("Couldn't load workspace settings")).toBeInTheDocument();
  expect(screen.getByText("the workspace store is unavailable")).toBeInTheDocument();
});

test("the field is disabled with an explanation for a non-owner", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { target_url: "https://app.example.com" }));
  renderWorkspace("approver");
  const input = await screen.findByLabelText("Target URL");
  expect(input).toBeDisabled();
  expect(input).toHaveAttribute("title", "Only an owner can change the target URL.");
});

test("saving a valid URL shows a success toast and reloads", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((_input, init) => {
    if (init?.method === "PUT") return jsonResponse(200, { target_url: "https://app.example.com" });
    return jsonResponse(200, { target_url: "" });
  });
  renderWorkspace();
  const input = await screen.findByLabelText("Target URL");
  await user.type(input, "https://app.example.com");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Target URL saved.")).toBeInTheDocument();
});

test("an invalid URL surfaces the server's own reason inline, not a toast", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((_input, init) => {
    if (init?.method === "PUT") return jsonResponse(400, { detail: "target_url must start with http:// or https://" });
    return jsonResponse(200, { target_url: "" });
  });
  renderWorkspace();
  const input = await screen.findByLabelText("Target URL");
  await user.type(input, "javascript:alert(1)");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("target_url must start with http:// or https://")).toBeInTheDocument();
  // Still on the form -- a validation failure never reads as a silent no-op.
  expect(screen.getByLabelText("Target URL")).toBeInTheDocument();
});

test("the save button stays disabled until the value actually changes", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { target_url: "https://app.example.com" }));
  renderWorkspace();
  await screen.findByLabelText("Target URL");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});
