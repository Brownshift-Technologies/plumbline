import { Navigate, Outlet } from "react-router-dom";
import { useCurrentUser } from "../lib/useCurrentUser";

/**
 * Sends anyone without a live session to sign in, before a screen behind
 * the shell renders.
 *
 * Without this, `/` matched `Home` for everyone: an unauthenticated
 * visitor got the dashboard chrome, three `401`s from Home's own fetches,
 * and empty panels — with no sign-in form and, crucially, no "Open the
 * live demo" button anywhere on screen. That is the first thing anyone
 * pasting the bare URL sees, so it is the one route that has to be right.
 *
 * A demo session is a real session (`is_demo: true`), so it passes here
 * exactly like a signed-in user — the demo door leads somewhere.
 *
 * While `/api/auth/me` is in flight the shell renders nothing rather than
 * a redirect: bouncing to `/signin` on every reload before the cookie has
 * been checked would log people out visually on each refresh. `replace`
 * keeps the dead route out of history, so Back from `/signin` does not
 * land on the page that just rejected them.
 */
export function RequireSession() {
  const { status } = useCurrentUser();

  if (status === "loading") return null;
  if (status === "error") return <Navigate to="/signin" replace />;
  return <Outlet />;
}
