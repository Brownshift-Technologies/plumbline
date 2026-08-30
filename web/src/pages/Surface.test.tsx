import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Surface } from "./Surface";
import { ToastProvider } from "../components/Toast";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderSurface() {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <Surface />
      </MemoryRouter>
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
  renderSurface();
  expect(screen.getByText("Mapping the repository…")).toBeInTheDocument();
});

test("shows a real reason and a next action when nothing has been mapped", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me")
      ? jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" })
      : jsonResponse(200, { routes: [], fully_covered: 0, partly_covered: 0, uncovered: 0, mapped_at: Date.now() / 1000 }),
  );
  renderSurface();
  expect(await screen.findByText("No routes mapped yet")).toBeInTheDocument();
  expect(screen.getByText(/Connect a repository, then run Cartographer/)).toBeInTheDocument();
});

test("shows what failed and a retry when the surface map cannot be loaded", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me")
      ? jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" })
      : jsonResponse(500, { message: "the coverage index is being rebuilt" }),
  );
  renderSurface();
  expect(await screen.findByText("Couldn't load the surface map")).toBeInTheDocument();
  expect(screen.getByText("the coverage index is being rebuilt")).toBeInTheDocument();
});

test("the missing-routes button is disabled once every route is covered", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/auth/me")
      ? jsonResponse(200, { id: "u1", name: "Roger", is_demo: false, workspace_id: "ws1", role: "owner" })
      : jsonResponse(200, {
          routes: [{ id: "r1", path: "/", coverage_pct: 100, last_mapped: Date.now() / 1000 }],
          fully_covered: 1,
          partly_covered: 0,
          uncovered: 0,
          mapped_at: Date.now() / 1000,
        }),
  );
  renderSurface();
  const button = await screen.findByRole("button", { name: /Write the 0 missing/ });
  expect(button).toBeDisabled();
});
