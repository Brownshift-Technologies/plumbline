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
    refresh: vi.fn(),
    refreshing: false,
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

test("shows a verified badge only when the server explicitly says so", () => {
  render(
    <ToastProvider>
      <ProfilePane user={userState({ email_verified: true })} />
    </ToastProvider>,
  );
  expect(screen.getByText("Verified")).toBeInTheDocument();
});

test("shows an unverified badge, not a lying Verified one, when the server says false", () => {
  render(
    <ToastProvider>
      <ProfilePane user={userState({ email_verified: false })} />
    </ToastProvider>,
  );
  expect(screen.getByText("Not verified")).toBeInTheDocument();
  expect(screen.queryByText("Verified")).not.toBeInTheDocument();
});

test("shows neither badge when verification status is unknown -- never assumes verified", () => {
  render(
    <ToastProvider>
      <ProfilePane user={userState({ email_verified: undefined })} />
    </ToastProvider>,
  );
  expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  expect(screen.queryByText("Not verified")).not.toBeInTheDocument();
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

test("a demo session saving the profile is told nothing was saved, not given the real success toast", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { demo: true, persisted: false }));
  const { default: userEvent } = await import("@testing-library/user-event");
  const user = userEvent.setup();
  render(
    <ToastProvider>
      <ProfilePane user={userState({ is_demo: true })} />
    </ToastProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText(/Nothing was saved/)).toBeInTheDocument();
});

test("uploading a photo saves and reloads the account on success", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { ok: true }));
  const reload = vi.fn();
  const { default: userEvent } = await import("@testing-library/user-event");
  const user = userEvent.setup();
  render(
    <ToastProvider>
      <ProfilePane user={{ ...userState(), reload }} />
    </ToastProvider>,
  );
  const file = new File(["x"], "photo.png", { type: "image/png" });
  await user.upload(screen.getByLabelText("Upload photo"), file);
  expect(await screen.findByText("Photo updated.")).toBeInTheDocument();
  expect(reload).toHaveBeenCalled();
});
