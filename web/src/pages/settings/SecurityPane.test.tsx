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
    refresh: vi.fn(),
    refreshing: false,
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

test("shows shaped skeleton rows for sessions, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation((input) => (String(input).includes("/auth/sessions") ? new Promise(() => {}) : jsonResponse(200, {})));
  renderSecurity();
  expect(document.querySelectorAll("tr[data-skeleton-row]").length).toBeGreaterThan(0);
  // No visible spinner tile -- EmptyState's loading variant (a centred icon
  // with no row shape) is not what renders here.
  expect(document.querySelector(".tile")).not.toBeInTheDocument();
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

test("confirming a TOTP enrolment code succeeds and reloads the account", async () => {
  const user = userEvent.setup();
  const reload = vi.fn();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/totp/enrol")) {
      return jsonResponse(200, { otpauth_url: "otpauth://totp/Plumbline:roger@acme.com?secret=JBSWY3DPEHPK3PXP&issuer=Plumbline", secret: "JBSWY3DPEHPK3PXP" });
    }
    if (url.includes("/auth/totp/verify")) return jsonResponse(200, { ok: true });
    if (url.includes("/auth/sessions")) return jsonResponse(200, []);
    return jsonResponse(200, {});
  });
  render(
    <ToastProvider>
      <SecurityPane user={{ ...userState(), reload }} onSignOut={vi.fn()} />
    </ToastProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "Two-factor authentication" }));
  await screen.findByLabelText("Scan this QR code with your authenticator app");
  await user.type(screen.getByLabelText("6-digit code from your app"), "123456");
  await user.click(screen.getByRole("button", { name: "Confirm" }));

  expect(await screen.findByText("Two-factor authentication enabled.")).toBeInTheDocument();
  expect(reload).toHaveBeenCalled();
});

test("removing TOTP requires a current code and reloads the account on success", async () => {
  const user = userEvent.setup();
  const reload = vi.fn();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/totp") && !url.includes("verify") && !url.includes("enrol")) return jsonResponse(200, { ok: true });
    if (url.includes("/auth/sessions")) return jsonResponse(200, []);
    return jsonResponse(200, {});
  });
  render(
    <ToastProvider>
      <SecurityPane user={{ ...userState({ totp_enabled: true }), reload }} onSignOut={vi.fn()} />
    </ToastProvider>,
  );

  const removeButton = await screen.findByRole("button", { name: "Remove two-factor authentication" });
  expect(removeButton).toBeDisabled();
  await user.type(screen.getByLabelText("Current code, to remove two-factor"), "654321");
  expect(removeButton).not.toBeDisabled();
  await user.click(removeButton);

  expect(await screen.findByText("Two-factor authentication removed.")).toBeInTheDocument();
  expect(reload).toHaveBeenCalled();
});

test("sign out everywhere else signs out every non-current session", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/sessions/s2") || url.includes("/auth/sessions/s3")) return jsonResponse(200, { ok: true });
    if (url.includes("/auth/sessions")) {
      return jsonResponse(200, [
        { id: "s1", user_agent: "Chrome on Linux", ip_city: "Accra", current: true },
        { id: "s2", user_agent: "Safari on iPhone", ip_city: "Accra", current: false },
        { id: "s3", user_agent: "Firefox on macOS", ip_city: "London", current: false },
      ]);
    }
    return jsonResponse(200, {});
  });
  renderSecurity();

  await user.click(await screen.findByRole("button", { name: "Sign out everywhere else" }));

  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/auth/sessions/s2"), expect.objectContaining({ method: "DELETE" }));
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/auth/sessions/s3"), expect.objectContaining({ method: "DELETE" }));
  expect(await screen.findByText("Signed out everywhere else.")).toBeInTheDocument();
});

test("a demo session changing its password is told nothing was saved, not given the real success toast", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/password") ? jsonResponse(200, { demo: true, persisted: false }) : jsonResponse(200, []),
  );
  renderSecurity({ is_demo: true });

  await user.type(screen.getByLabelText("Current password"), "correct horse battery");
  await user.type(screen.getByLabelText("New password"), "a brand new passphrase");
  await user.type(screen.getByLabelText("Confirm new password"), "a brand new passphrase");
  await user.click(screen.getByRole("button", { name: "Update password" }));

  expect(await screen.findByText(/Nothing was saved/)).toBeInTheDocument();
});
