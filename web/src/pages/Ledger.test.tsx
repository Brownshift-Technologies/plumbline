import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Ledger } from "./Ledger";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows shaped skeleton rows while loading, not a spinner over a blank page", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  render(<Ledger />);
  const skeletonRows = document.querySelectorAll("tr[data-skeleton-row]");
  expect(skeletonRows.length).toBeGreaterThan(0);
  expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
  expect(screen.queryByText("Loading the ledger…")).not.toBeInTheDocument();
});

test("shows a real reason when nothing has been recorded yet", async () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { entries: [], next_cursor: null }));
  render(<Ledger />);
  expect(await screen.findByText("Nothing recorded yet")).toBeInTheDocument();
});

test("shows what failed and a retry", async () => {
  vi.mocked(fetch).mockImplementation((input) =>
    String(input).includes("/ledger?") ? jsonResponse(500, { message: "the ledger head could not be read" }) : jsonResponse(200, { entries: [], next_cursor: null }),
  );
  render(<Ledger />);
  expect(await screen.findByText("Couldn't load the ledger")).toBeInTheDocument();
  expect(screen.getByText("the ledger head could not be read")).toBeInTheDocument();
});

test("verify chain reports intact with the number of entries checked", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/ledger/verify")) return jsonResponse(200, { intact: true, checked: 4812 });
    return jsonResponse(200, { entries: [], next_cursor: null });
  });
  render(<Ledger />);
  await user.click(await screen.findByRole("button", { name: "Verify chain" }));
  expect(await screen.findByText("Chain intact · 4812 entries checked")).toBeInTheDocument();
});

test("verify chain reports tampering as clearly as it reports intact", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/ledger/verify")) return jsonResponse(200, { intact: false, checked: 4812 });
    return jsonResponse(200, { entries: [], next_cursor: null });
  });
  render(<Ledger />);
  await user.click(await screen.findByRole("button", { name: "Verify chain" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Chain tampered -- do not trust this ledger");
});

test("the export control is a real link to the CSV endpoint, not a JS stub", () => {
  vi.mocked(fetch).mockImplementation(() => jsonResponse(200, { entries: [], next_cursor: null }));
  render(<Ledger />);
  expect(screen.getByRole("link", { name: /Export/ })).toHaveAttribute("href", expect.stringContaining("/ledger.csv"));
});
