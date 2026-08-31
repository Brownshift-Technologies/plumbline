import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Behaviours } from "./Behaviours";
import { ToastProvider } from "../components/Toast";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderBehaviours() {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <Behaviours />
      </MemoryRouter>
    </ToastProvider>,
  );
}

const ME_OWNER = { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" };

function makeBehaviour(id: string) {
  return { id, workspace_id: "ws1", text: "text", route: "/x", spec_path: "", tags: [], owner: "", status: "active" };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows shaped skeleton rows while loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderBehaviours();
  const skeletonRows = document.querySelectorAll("tr[data-skeleton-row]");
  expect(skeletonRows.length).toBeGreaterThan(0);
  expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
  expect(document.querySelector(".tile")).not.toBeInTheDocument();
});

test("a fresh workspace with no behaviours gets a distinct empty state from a filtered one", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me") ? jsonResponse(200, ME_OWNER) : jsonResponse(200, { behaviours: [], total: 0 }),
  );
  renderBehaviours();
  expect(await screen.findByText("No behaviours yet")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me") ? jsonResponse(200, ME_OWNER) : jsonResponse(500, { message: "the behaviour index is unavailable" }),
  );
  renderBehaviours();
  expect(await screen.findByText("Couldn't load behaviours")).toBeInTheDocument();
  expect(screen.getByText("the behaviour index is unavailable")).toBeInTheDocument();
});

test("filtering to nothing gives the real reason -- a count and what doesn't match -- not a bare 'no data'", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, ME_OWNER);
    if (url.includes("tag=payments")) return jsonResponse(200, { behaviours: [], total: 0 });
    return jsonResponse(200, { behaviours: Array.from({ length: 342 }, (_, i) => makeBehaviour(`b${i}`)), total: 342 });
  });
  renderBehaviours();

  await user.type(screen.getByLabelText("Tag"), "payments");

  expect(await screen.findByText("No filters match")).toBeInTheDocument();
  expect(
    await screen.findByText(/342 behaviours exist for this repository, but none are tagged/),
  ).toBeInTheDocument();
});

test("delete is disabled with an explanation for a role below owner", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, { ...ME_OWNER, role: "approver" });
    return jsonResponse(200, { behaviours: [makeBehaviour("b1")], total: 1 });
  });
  renderBehaviours();

  const deleteButton = await screen.findByRole("button", { name: "Delete" });
  expect(deleteButton).toBeDisabled();
  expect(deleteButton).toHaveAttribute("title", "Only an owner can delete a behaviour.");
});

test("a demo session creating a behaviour is told nothing was saved, not given the real success toast", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input, init) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, { ...ME_OWNER, is_demo: true });
    if (init?.method === "POST") return jsonResponse(200, { demo: true, persisted: false });
    return jsonResponse(200, { behaviours: [], total: 0 });
  });
  renderBehaviours();

  await screen.findByText("No behaviours yet");
  // Two "New behaviour" buttons render at once here (the pagehead's and the
  // empty state's own) -- either opens the same form.
  await user.click(screen.getAllByRole("button", { name: /New behaviour/ })[0]);
  await user.type(screen.getByLabelText("Behaviour"), "A customer who retries a slow payment should only be charged once");
  await user.type(screen.getByLabelText("Route"), "/checkout/payment");
  await user.click(screen.getByRole("button", { name: "Create behaviour" }));

  expect(await screen.findByText(/Nothing was saved/)).toBeInTheDocument();
});
