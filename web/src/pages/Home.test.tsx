import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Home } from "./Home";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function routeFor(url: string) {
  if (url.includes("/findings?status=patch_ready")) return "attention";
  if (url.includes("/findings")) return "findings";
  if (url.includes("/runs")) return "runs";
  if (url.includes("/auth/me")) return "me";
  return "unknown";
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows skeleton loading rows before any response arrives", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );
  expect(screen.getByText("Loading recent runs…")).toBeInTheDocument();
  expect(screen.getByText("Checking what needs you…")).toBeInTheDocument();
});

test("shows a real reason and a next action when there are no runs yet", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (routeFor(url) === "runs") return jsonResponse(200, { runs: [], next_cursor: null, total: 0 });
    if (routeFor(url) === "attention") return jsonResponse(200, []);
    if (routeFor(url) === "findings") return jsonResponse(200, []);
    return jsonResponse(200, { is_demo: false, id: "u1", name: "Roger", role: "owner", workspace_id: "ws1" });
  });
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );

  expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  expect(
    screen.getByText(/Describe a behaviour above, or connect a repository/),
  ).toBeInTheDocument();
});

test("shows what failed and a retry when loading recent runs fails", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (routeFor(url) === "runs") return jsonResponse(500, { message: "the run index is unavailable" });
    if (routeFor(url) === "attention") return jsonResponse(200, []);
    if (routeFor(url) === "findings") return jsonResponse(200, []);
    return jsonResponse(200, { is_demo: false, id: "u1", name: "Roger", role: "owner", workspace_id: "ws1" });
  });
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );

  expect(await screen.findByText("Couldn't load recent runs")).toBeInTheDocument();
  expect(screen.getByText("the run index is unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});
