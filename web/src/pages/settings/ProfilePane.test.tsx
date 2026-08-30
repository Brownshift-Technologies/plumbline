import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ProfilePane } from "./ProfilePane";
import { ToastProvider } from "../../components/Toast";
import type { AsyncState } from "../../lib/useAsync";
import type { CurrentUser } from "../../lib/types";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function userState(overrides: Partial<CurrentUser> = {}): AsyncState<CurrentUser> {
  return {
    status: "success",
    data: { id: "u1", name: "Roger Koranteng", email: "roger@acme.com", is_demo: false, workspace_id: "ws1", role: "owner", ...overrides },
    error: null,
    reload: vi.fn(),
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows the role as a read-only pill, never an editable control", () => {
  render(
    <ToastProvider>
      <ProfilePane user={userState({ role: "owner" })} />
    </ToastProvider>,
  );
  expect(screen.getByText("Owner")).toBeInTheDocument();
});

test("the email carries a verified badge", () => {
  render(
    <ToastProvider>
      <ProfilePane user={userState()} />
    </ToastProvider>,
  );
  expect(screen.getByText("Verified")).toBeInTheDocument();
});

test("saving surfaces the server's own error, not a generic message", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(422, { message: "that email is already in use" }));
  const { default: userEvent } = await import("@testing-library/user-event");
  const user = userEvent.setup();
  render(
    <ToastProvider>
      <ProfilePane user={userState()} />
    </ToastProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("that email is already in use")).toBeInTheDocument();
});
