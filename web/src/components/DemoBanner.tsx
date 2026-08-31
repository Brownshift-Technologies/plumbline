import { Icon } from "./Icon";
import { useCurrentUser } from "../lib/useCurrentUser";

/**
 * Persistent reminder that a demo session is active. Reads `/api/auth/me`
 * itself (shared `useCurrentUser` hook, cached per navigation) rather than
 * depending on a specific screen having already fetched the user, so it
 * renders correctly no matter which screen the demo session lands on
 * first.
 *
 * The wording changed with this task: every demo session now gets its own
 * real, writable sandbox workspace, so "nothing you do here is saved" is
 * no longer true and would read as a bug the moment a visitor approves the
 * gated patch and sees it actually take effect. What is still true, and
 * still worth saying persistently, is that this sandbox is temporary --
 * see `app/sessions.py`'s `DEMO_TTL_SECONDS`.
 */
export function DemoBanner() {
  const { status, data } = useCurrentUser();

  if (status !== "success" || !data?.is_demo) return null;

  return (
    <div className="demo-banner" role="status">
      <Icon name="i-spark" size="s" />
      This is your own live sandbox -- everything you do here really works. It disappears in 2 hours.
      <a href="https://plumbline.dev" rel="noreferrer">
        Create an account
      </a>
    </div>
  );
}
