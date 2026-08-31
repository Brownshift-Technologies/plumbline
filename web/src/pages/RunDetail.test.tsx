import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RunDetail } from "./RunDetail";
import { ToastProvider } from "../components/Toast";

class NoopEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener() {}
  close() {}
}

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderAt(runId: string) {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[`/runs/${runId}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetail />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

const ME = { id: "u1", name: "Ama", is_demo: false, workspace_id: "ws1", role: "approver" };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("EventSource", NoopEventSource as unknown as typeof EventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows a loading skeleton before the run arrives", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  renderAt("run_1");
  expect(screen.getByText("Loading run run_1…")).toBeInTheDocument();
});

test("shows what failed and a retry when the run cannot be loaded", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    if (String(input).includes("/auth/me")) return jsonResponse(200, ME);
    return jsonResponse(404, { message: "no run with that id" });
  });
  renderAt("run_missing");
  expect(await screen.findByText("Couldn't load this run")).toBeInTheDocument();
  expect(screen.getByText("no run with that id")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

test("a gated run reads as gated, not failed -- the product's core distinction", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, ME);
    if (url.includes("/findings/f1/patch")) {
      return jsonResponse(200, {
        id: "p1",
        finding_id: "f1",
        diff: "@@ -1,1 +1,1 @@\n-old\n+new\n",
        files: ["src/checkout/payment-client.ts"],
        added: 1,
        removed: 1,
        verified: true,
        pr_url: "",
        gate_state: "awaiting_approval",
      });
    }
    if (url.includes("/findings/f1")) {
      return jsonResponse(200, {
        id: "f1",
        workspace_id: "ws1",
        title: "A retried payment charges the customer twice",
        route: "/checkout/payment",
        found_by: "Chaos",
        status: "patch_ready",
        severity: "high",
        seed: "0x1",
        repro_count: 5,
        at: Date.now() / 1000,
      });
    }
    return jsonResponse(200, {
      id: "run_1",
      workspace_id: "ws1",
      number: 4471,
      trigger: "Pull request #2211",
      state: "failed",
      commit: "8f21c04",
      started_by: "Surgeon",
      held: 341,
      failed: 1,
      repaired: 0,
      duration_ms: 401000,
      started_at: Date.now() / 1000,
      finding_id: "f1",
      steps: [
        { id: "s1", run_id: "run_1", agent: "surgeon", summary: "opened the pull request and stopped", detail: "Policy requires a human.", outcome: "gated", duration_ms: 1000, at: Date.now() / 1000 },
      ],
    });
  });
  renderAt("run_1");

  expect(await screen.findByText("Blocked at a gate")).toBeInTheDocument();
  // The literal run outcome still reads "1 failing" -- gated augments the
  // status, it does not replace or hide the real result.
  expect(screen.getByText("1 failing")).toBeInTheDocument();
  expect(screen.getByText("at gate")).toBeInTheDocument();
});

test("Approve is disabled with a visible explanation for a role that cannot approve a gated patch", async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, ME); // role: approver
    if (url.includes("/findings/f1/patch")) {
      return jsonResponse(200, {
        id: "p1",
        finding_id: "f1",
        diff: "@@ -1,1 +1,1 @@\n-old\n+new\n",
        files: ["src/checkout/payment-client.ts"],
        added: 1,
        removed: 1,
        verified: true,
        pr_url: "",
        gate_state: "awaiting_approval",
      });
    }
    if (url.includes("/findings/f1")) {
      return jsonResponse(200, {
        id: "f1",
        workspace_id: "ws1",
        title: "A retried payment charges the customer twice",
        route: "/checkout/payment",
        found_by: "Chaos",
        status: "patch_ready",
        severity: "high",
        seed: "0x1",
        repro_count: 5,
        at: Date.now() / 1000,
      });
    }
    return jsonResponse(200, {
      id: "run_1",
      workspace_id: "ws1",
      number: 4471,
      trigger: "Pull request #2211",
      state: "failed",
      commit: "8f21c04",
      started_by: "Surgeon",
      held: 341,
      failed: 1,
      repaired: 0,
      duration_ms: 401000,
      started_at: Date.now() / 1000,
      finding_id: "f1",
      steps: [
        { id: "s1", run_id: "run_1", agent: "surgeon", summary: "opened the pull request and stopped", detail: "", outcome: "gated", duration_ms: 1000, at: Date.now() / 1000 },
      ],
    });
  });
  renderAt("run_1");

  const approveButton = await screen.findByRole("button", { name: "Approve and merge" });
  expect(approveButton).toBeDisabled();
  expect(screen.getByText("This patch is blocked at a gate. Only an owner can approve it.")).toBeInTheDocument();
});

test("a demo session approving the gated payments patch is told nothing was saved, not given the real success toast (CRITICAL: demo must not claim a merge that never happened)", async () => {
  const user = userEvent.setup();
  // An owner with a confirmed TOTP secret is the one client-side-eligible
  // role/2FA combination for a gated patch -- exactly the scenario the
  // review named: "Clicking Approve on the gated payments patch in the
  // demo". This isolates the SERVER's demo response being honoured from
  // the client-side role/TOTP gate (covered by the disabled-reason test
  // above).
  const DEMO_OWNER = { id: "demo", name: "Demo visitor", is_demo: true, workspace_id: "ws1", role: "owner", totp_enabled: true };
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/me")) return jsonResponse(200, DEMO_OWNER);
    if (url.includes("/findings/f1/patch/approve")) return jsonResponse(200, { demo: true, persisted: false });
    if (url.includes("/findings/f1/patch")) {
      return jsonResponse(200, {
        id: "p1",
        finding_id: "f1",
        diff: "@@ -1,1 +1,1 @@\n-old\n+new\n",
        files: ["src/checkout/payment-client.ts"],
        added: 1,
        removed: 1,
        verified: true,
        pr_url: "",
        gate_state: "awaiting_approval",
      });
    }
    if (url.includes("/findings/f1")) {
      return jsonResponse(200, {
        id: "f1",
        workspace_id: "ws1",
        title: "A retried payment charges the customer twice",
        route: "/checkout/payment",
        found_by: "Chaos",
        status: "patch_ready",
        severity: "high",
        seed: "0x1",
        repro_count: 5,
        at: Date.now() / 1000,
      });
    }
    return jsonResponse(200, {
      id: "run_2",
      workspace_id: "ws1",
      number: 10,
      trigger: "Pull request #2299",
      state: "failed",
      commit: "abc1234",
      started_by: "Surgeon",
      held: 341,
      failed: 1,
      repaired: 0,
      duration_ms: 5000,
      started_at: Date.now() / 1000,
      finding_id: "f1",
      steps: [
        { id: "s1", run_id: "run_2", agent: "surgeon", summary: "opened the pull request and stopped", detail: "", outcome: "gated", duration_ms: 1000, at: Date.now() / 1000 },
      ],
    });
  });
  renderAt("run_2");

  const approveButton = await screen.findByRole("button", { name: "Approve and merge" });
  expect(approveButton).not.toBeDisabled();
  await user.click(approveButton);

  expect(await screen.findByText(/Nothing was saved/)).toBeInTheDocument();
  expect(screen.queryByText("Patch approved. Merging the pull request.")).not.toBeInTheDocument();
});

const GATED_FIXTURE_HANDLERS = (extra: Record<string, unknown> = {}) => (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.includes("/auth/me")) return jsonResponse(200, ME);
  if (url.includes("/findings/f1/patch/reject")) return jsonResponse(200, extra.reject ?? { ok: true });
  if (url.includes("/findings/f1/patch/changes")) return jsonResponse(200, extra.changes ?? { ok: true });
  if (url.includes("/findings/f1/patch")) {
    return jsonResponse(200, {
      id: "p1", finding_id: "f1", diff: "@@ -1,1 +1,1 @@\n-old\n+new\n",
      files: ["src/catalog/search.ts"], added: 1, removed: 1, verified: true, pr_url: "", gate_state: "approved",
    });
  }
  if (url.includes("/findings/f1")) {
    return jsonResponse(200, {
      id: "f1", workspace_id: "ws1", title: "x", route: "/catalog", found_by: "Runner",
      status: "patch_ready", severity: "low", seed: "", repro_count: 1, at: Date.now() / 1000,
    });
  }
  return jsonResponse(200, {
    id: "run_3", workspace_id: "ws1", number: 20, trigger: "Manual", state: "failed", commit: "c1",
    started_by: "Runner", held: 5, failed: 1, repaired: 0, duration_ms: 1000, started_at: Date.now() / 1000,
    finding_id: "f1", steps: [],
  });
};

test("reject requires at least 10 characters and announces how many more are needed", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation(GATED_FIXTURE_HANDLERS());
  renderAt("run_3");

  await user.click(await screen.findByRole("button", { name: "Reject" }));
  const note = screen.getByLabelText(/Why is this being rejected/);
  const confirmButton = screen.getByRole("button", { name: "Confirm reject" });
  expect(confirmButton).toBeDisabled();

  await user.type(note, "too short");
  expect(confirmButton).toBeDisabled();
  expect(screen.getByText("1 more character needed.")).toBeInTheDocument();

  await user.type(note, "!!");
  expect(confirmButton).not.toBeDisabled();
  expect(screen.getByText("Ready to submit.")).toBeInTheDocument();

  await user.click(confirmButton);
  expect(await screen.findByText("Patch rejected. The finding stays open.")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("/findings/f1/patch/reject"),
    expect.objectContaining({ method: "POST" }),
  );
});

test("request changes sends an optional note to Surgeon and confirms", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation(GATED_FIXTURE_HANDLERS());
  renderAt("run_3");

  await user.click(await screen.findByRole("button", { name: "Request changes" }));
  await user.type(screen.getByLabelText(/What should change/), "Please also cover the retry path.");
  await user.click(screen.getByRole("button", { name: "Send to Surgeon" }));

  expect(await screen.findByText("Requested changes. Surgeon will try again.")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("/findings/f1/patch/changes"),
    expect.objectContaining({ method: "POST" }),
  );
});
