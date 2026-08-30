import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SecurityPane } from "./SecurityPane";
import { ToastProvider } from "../../components/Toast";
import type { AsyncState } from "../../lib/useAsync";
import type { CurrentUser } from "../../lib/types";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function userState(overrides: Partial<CurrentUser> = {}): AsyncState<CurrentUser> {
  return {
    status: "success",
    data: { id: "u1", name: "Roger", email: "roger@acme.com", is_demo: false, workspace_id: "ws1", role: "owner", totp_enabled: false, ...overrides },
    error: null,
    reload: vi.fn(),
  };
}

function renderSecurity(overrides: Partial<CurrentUser> = {}) {
  return render(
    <ToastProvider>
      <SecurityPane user={userState(overrides)} onSignOut={vi.fn()} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows skeleton loading for sessions, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation((input) => (String(input).includes("/auth/sessions") ? new Promise(() => {}) : jsonResponse(200, {})));
  renderSecurity();
  expect(screen.getByText("Loading sessions…")).toBeInTheDocument();
});

test("shows what failed and a retry when sessions cannot be loaded", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/sessions") ? jsonResponse(500, { message: "the session store is unavailable" }) : jsonResponse(200, {}),
  );
  renderSecurity();
  expect(await screen.findByText("Couldn't load sessions")).toBeInTheDocument();
  expect(screen.getByText("the session store is unavailable")).toBeInTheDocument();
});

test("a non-current session can be signed out individually", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/sessions/s2")) return jsonResponse(200, { ok: true });
    if (url.includes("/auth/sessions")) {
      return jsonResponse(200, [
        { id: "s1", user_agent: "Chrome on Linux", ip_city: "Accra", current: true },
        { id: "s2", user_agent: "Safari on iPhone", ip_city: "Accra", current: false },
      ]);
    }
    return jsonResponse(200, {});
  });
  renderSecurity();

  expect(await screen.findByText("This device")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Sign out" }));
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/auth/sessions/s2"), expect.objectContaining({ method: "DELETE" }));
});

test("starting TOTP enrolment renders a scannable QR code and the manual key", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/totp/enrol")) {
      return jsonResponse(200, { otpauth_url: "otpauth://totp/Plumbline:roger@acme.com?secret=JBSWY3DPEHPK3PXP&issuer=Plumbline", secret: "JBSWY3DPEHPK3PXP" });
    }
    if (url.includes("/auth/sessions")) return jsonResponse(200, []);
    return jsonResponse(200, {});
  });
  renderSecurity();

  await user.click(await screen.findByRole("button", { name: "Two-factor authentication" }));

  expect(await screen.findByLabelText("Scan this QR code with your authenticator app")).toBeInTheDocument();
  expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
});

test("a wrong password confirmation is caught before any request is sent", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) => (String(input).includes("/auth/sessions") ? jsonResponse(200, []) : jsonResponse(200, {})));
  renderSecurity();

  await user.type(screen.getByLabelText("Current password"), "correct horse battery");
  await user.type(screen.getByLabelText("New password"), "a brand new passphrase");
  await user.type(screen.getByLabelText("Confirm new password"), "does not match");
  await user.click(screen.getByRole("button", { name: "Update password" }));

  expect(await screen.findByText("The new password and its confirmation don't match.")).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalledWith(expect.stringContaining("/auth/password"), expect.anything());
});
