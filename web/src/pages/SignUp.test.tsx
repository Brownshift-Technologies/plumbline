import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { SignUp } from "./SignUp";
import { api } from "../lib/api";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <SignUp />
    </MemoryRouter>,
  );
}

describe("SignUp", () => {
  beforeEach(() => {
    navigate.mockReset();
    vi.restoreAllMocks();
  });

  it("creates the account and goes straight to the product", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ id: "u1" } as never);
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Your name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Work email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "a-long-enough-passphrase");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/auth/signup", {
        email: "ada@example.com",
        password: "a-long-enough-passphrase",
        name: "Ada Lovelace",
      }),
    );
    // Signup issues the cookie itself; a second sign-in step would be wrong.
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/"));
  });

  it("refuses a password the server would reject, without a round trip", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Your name"), "Ada");
    await user.type(screen.getByLabelText("Work email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Use at least 12 characters.")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("shows the server's reason when the email is taken", async () => {
    const { ApiError } = await import("../lib/api");
    vi.spyOn(api, "post").mockRejectedValue(
      new ApiError(409, "that email already has an account", null),
    );
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Your name"), "Ada");
    await user.type(screen.getByLabelText("Work email"), "taken@example.com");
    await user.type(screen.getByLabelText("Password"), "a-long-enough-passphrase");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "that email already has an account",
    );
    expect(navigate).not.toHaveBeenCalled();
  });

  it("offers a way back to sign in", () => {
    renderPage();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/signin");
  });
});
