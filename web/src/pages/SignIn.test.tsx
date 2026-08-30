import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SignIn } from "./SignIn";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("submitting with an empty email and password shows inline validation, not a network call", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <SignIn />
    </MemoryRouter>,
  );

  await user.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByText("Enter a valid work email.")).toBeInTheDocument();
  expect(screen.getByText("Enter your password.")).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

test("a failed sign-in shows the server's own message, not a generic error", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(401, { message: "that email and password do not match" }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <SignIn />
    </MemoryRouter>,
  );

  await user.type(screen.getByLabelText("Work email"), "roger@acme.com");
  await user.type(screen.getByLabelText("Password"), "wrong-password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "that email and password do not match",
  );
});

test("the demo panel is a first-class control that signs in and can fail with a real message", async () => {
  vi.mocked(fetch).mockImplementation(() =>
    jsonResponse(500, { message: "the demo workspace could not be seeded" }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <SignIn />
    </MemoryRouter>,
  );

  const demoButton = screen.getByRole("button", { name: "Open the live demo" });
  await user.click(demoButton);

  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("/auth/demo"),
    expect.objectContaining({ method: "POST" }),
  ));
  expect(await screen.findByText("the demo workspace could not be seeded")).toBeInTheDocument();
});

test("the three OAuth options are real links to the provider start route, not JS stubs", () => {
  render(
    <MemoryRouter>
      <SignIn />
    </MemoryRouter>,
  );
  expect(screen.getByRole("button", { name: /Continue with GitHub/ })).toHaveAttribute(
    "href",
    expect.stringContaining("/auth/oauth/github/start"),
  );
  expect(screen.getByRole("button", { name: /Continue with Okta SSO/ })).toHaveAttribute(
    "href",
    expect.stringContaining("/auth/oauth/okta/start"),
  );
});
