import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MembersPane } from "./MembersPane";
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
  };
}

function renderMembers(role: CurrentUser["role"] = "owner") {
  return render(
    <ToastProvider>
      <MembersPane user={userState(role)} />
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
  renderMembers();
  expect(screen.getByText("Loading members…")).toBeInTheDocument();
});

test("shows a real reason when the workspace has only the current owner", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, []));
  renderMembers();
  expect(await screen.findByText("Just you, for now")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(500, { message: "the membership index is unavailable" }));
  renderMembers();
  expect(await screen.findByText("Couldn't load members")).toBeInTheDocument();
  expect(screen.getByText("the membership index is unavailable")).toBeInTheDocument();
});

test("role changes and removal are disabled with an explanation for a non-owner", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [{ id: "m1", user_id: "u2", name: "Ama Owusu", email: "ama@acme.com", role: "approver" }]),
  );
  renderMembers("approver");

  const removeButton = await screen.findByRole("button", { name: "Remove" });
  expect(removeButton).toBeDisabled();
  expect(removeButton).toHaveAttribute("title", "Only an owner can change roles or remove members.");
  // A role change control (a <select>) is replaced by a plain pill when
  // disabled, rather than being shown-but-inert.
  expect(screen.getByText("Approver")).toBeInTheDocument();
  expect(screen.queryByLabelText("Role for Ama Owusu")).not.toBeInTheDocument();
});

test("an owner can change another member's role and remove them", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [{ id: "m1", user_id: "u2", name: "Ama Owusu", email: "ama@acme.com", role: "approver" }]),
  );
  renderMembers("owner");

  const removeButton = await screen.findByRole("button", { name: "Remove" });
  expect(removeButton).not.toBeDisabled();
  expect(screen.getByLabelText("Role for Ama Owusu")).toBeInTheDocument();
});
