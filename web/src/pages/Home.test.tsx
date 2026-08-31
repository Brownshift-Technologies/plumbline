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

test("shows shaped skeleton content before any response arrives, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );
  // Recent runs: shaped skeleton table rows, not a spinner tile.
  const skeletonRows = document.querySelectorAll("tr[data-skeleton-row]");
  expect(skeletonRows.length).toBeGreaterThan(0);
  // Attention card: shimmering placeholder lines, same story.
  expect(document.querySelectorAll(".skel").length).toBeGreaterThan(skeletonRows.length);
  // The "Checking what needs you…" text still exists for screen readers,
  // but only inside a visually-hidden live region -- never as visible copy
  // next to a spinner tile the way EmptyState's loading variant renders it.
  expect(screen.getByText("Checking what needs you…").closest(".visually-hidden")).toBeInTheDocument();
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

test("findings section shows what failed and a retry, not silently disappearing", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (routeFor(url) === "runs") return jsonResponse(200, { runs: [], next_cursor: null, total: 0 });
    if (routeFor(url) === "attention") return jsonResponse(200, []);
    if (routeFor(url) === "findings") return jsonResponse(500, { message: "the finding index is unavailable" });
    return jsonResponse(200, { is_demo: false, id: "u1", name: "Roger", role: "owner", workspace_id: "ws1" });
  });
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );

  expect(await screen.findByText("Couldn't load recent findings")).toBeInTheDocument();
  expect(screen.getByText("the finding index is unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

test("findings section shows a real reason when there are none, rather than an empty gap", async () => {
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

  expect(await screen.findByText("No findings yet. Runs that fail or need a repro will show up here.")).toBeInTheDocument();
});
