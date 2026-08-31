import { Icon } from "./Icon";
import { useCurrentUser } from "../lib/useCurrentUser";

/**
 * Persistent reminder that a demo session is active. Reads `/api/auth/me`
 * itself (shared `useCurrentUser` hook, cached per navigation) rather than
 * depending on a specific screen having already fetched the user, so it
 * renders correctly no matter which screen the demo session lands on
 * first.
 *
 * The wording has been wrong twice. It first said "nothing you do here is
 * saved", which stopped being true when every demo session got its own
 * real, writable sandbox. It then said the sandbox disappears in 2 hours,
 * which stopped being true when a sandbox started behaving like an
 * account: `POST /api/auth/demo` resolves a returning visitor's cookie
 * back to the workspace they already built, and the sweep reaps on
 * `Workspace.last_seen_at`, so one you keep opening is never collected.
 *
 * So the banner no longer makes a promise about time at all. It says what
 * is durably true -- this is yours, it really works, and it is not a real
 * account -- because a banner that overstates permanence is exactly as
 * bad as one that understates it.
 */
export function DemoBanner() {
  const { status, data } = useCurrentUser();

  if (status !== "success" || !data?.is_demo) return null;

  return (
    <div className="demo-banner" role="status">
      <Icon name="i-spark" size="s" />
      This is your own live sandbox -- everything you do here really works, and it's still here when you come back.
      <a href="https://plumbline.dev" rel="noreferrer">
        Create an account
      </a>
    </div>
  );
}
