import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { RequireSession } from "./RequireSession";

function renderAt(meStatus: number, body: unknown = {}) {
  global.fetch = (() =>
    Promise.resolve(new Response(JSON.stringify(body), {
      status: meStatus, headers: { "content-type": "application/json" },
    }))) as typeof fetch;
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<RequireSession />}>
          <Route path="/" element={<p>dashboard</p>} />
        </Route>
        <Route path="/signin" element={<p>sign in page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

test("an unauthenticated visitor is sent to sign in, not shown the dashboard", async () => {
  renderAt(401);
  expect(await screen.findByText("sign in page")).toBeInTheDocument();
  expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
});

test("a signed-in user reaches the dashboard", async () => {
  renderAt(200, { id: "u1", name: "Roger", is_demo: false });
  expect(await screen.findByText("dashboard")).toBeInTheDocument();
});

test("a demo session is a real session and reaches the dashboard", async () => {
  renderAt(200, { id: "demo", name: "Demo visitor", is_demo: true });
  expect(await screen.findByText("dashboard")).toBeInTheDocument();
});

test("nothing renders while the session is still being checked", async () => {
  global.fetch = (() => new Promise(() => {})) as unknown as typeof fetch;
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<RequireSession />}>
          <Route path="/" element={<p>dashboard</p>} />
        </Route>
        <Route path="/signin" element={<p>sign in page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  // Neither outcome yet: no premature bounce to /signin on every reload.
  await waitFor(() => {
    expect(screen.queryByText("sign in page")).not.toBeInTheDocument();
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
  });
});
