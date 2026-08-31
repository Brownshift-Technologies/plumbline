import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MembersPane } from "./MembersPane";
import { ToastProvider } from "../../components/Toast";
import type { AsyncState } from "../../lib/useAsync";
import type { CurrentUser } from "../../lib/types";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function userState(role: CurrentUser["role"], id = "u1"): AsyncState<CurrentUser> {
  return {
    status: "success",
    data: { id, name: "Roger", is_demo: false, workspace_id: "ws1", role },
    error: null,
    reload: vi.fn(),
    refresh: vi.fn(),
    refreshing: false,
  };
}

function renderMembers(role: CurrentUser["role"] = "owner", id = "u1") {
  return render(
    <ToastProvider>
      <MembersPane user={userState(role, id)} />
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
  renderMembers();
  expect(document.querySelectorAll("tr[data-skeleton-row]").length).toBeGreaterThan(0);
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

test("role changes and removal are disabled with an explanation for a non-owner -- disabled, never hidden", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [{ id: "m1", user_id: "u2", name: "Ama Owusu", email: "ama@acme.com", role: "approver" }]),
  );
  renderMembers("approver");

  const removeButton = await screen.findByRole("button", { name: "Remove" });
  expect(removeButton).toBeDisabled();
  expect(removeButton).toHaveAttribute("title", "Only an owner can remove a member.");

  // The role control stays a real, operable <select> -- disabled and
  // explained, not swapped out for a static pill or hidden entirely.
  const roleSelect = screen.getByLabelText("Role for Ama Owusu");
  expect(roleSelect).toBeInTheDocument();
  expect(roleSelect).toBeDisabled();
  expect(roleSelect).toHaveAttribute("title", "Only an owner can change roles.");
});

test("both disabled controls are reachable by assistive tech via aria-describedby, not just a title attribute", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [{ id: "m1", user_id: "u2", name: "Ama Owusu", email: "ama@acme.com", role: "approver" }]),
  );
  renderMembers("approver");

  const roleSelect = await screen.findByLabelText("Role for Ama Owusu");
  const roleDescribedBy = roleSelect.getAttribute("aria-describedby");
  expect(roleDescribedBy).toBeTruthy();
  expect(document.getElementById(roleDescribedBy!)).toHaveTextContent("Only an owner can change roles.");

  const removeButton = screen.getByRole("button", { name: "Remove" });
  const removeDescribedBy = removeButton.getAttribute("aria-describedby");
  expect(removeDescribedBy).toBeTruthy();
  expect(document.getElementById(removeDescribedBy!)).toHaveTextContent("Only an owner can remove a member.");
});

test("an owner can change another member's role and remove them", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [{ id: "m1", user_id: "u2", name: "Ama Owusu", email: "ama@acme.com", role: "approver" }]),
  );
  renderMembers("owner");

  const removeButton = await screen.findByRole("button", { name: "Remove" });
  expect(removeButton).not.toBeDisabled();
  expect(screen.getByLabelText("Role for Ama Owusu")).not.toBeDisabled();
});

test("an owner cannot demote or remove themselves -- each control carries its OWN correct reason, not a shared one", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(200, [{ id: "m1", user_id: "u1", name: "Roger", email: "roger@acme.com", role: "owner" }]),
  );
  renderMembers("owner", "u1"); // the rendered member IS the current user

  const roleSelect = await screen.findByLabelText("Role for Roger");
  expect(roleSelect).toBeDisabled();
  expect(roleSelect).toHaveAttribute("title", "You cannot change your own role.");

  const removeButton = screen.getByRole("button", { name: "Remove" });
  expect(removeButton).toBeDisabled();
  // The exact bug this guards against: the Remove button showing the ROLE
  // explanation instead of its own.
  expect(removeButton).toHaveAttribute("title", "You cannot remove yourself.");
  expect(removeButton).not.toHaveAttribute("title", "You cannot change your own role.");
});

test("a demo session inviting a member is told nothing was saved, not given the real success toast", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/members/invite") ? jsonResponse(200, { demo: true, persisted: false }) : jsonResponse(200, []),
  );
  renderMembers("owner");

  await screen.findByText("Just you, for now");
  await user.click(screen.getByRole("button", { name: /Invite/ }));
  await user.type(screen.getByLabelText("Email to invite"), "new@acme.com");
  await user.click(screen.getByRole("button", { name: "Send invite" }));

  expect(await screen.findByText(/Nothing was saved/)).toBeInTheDocument();
});
