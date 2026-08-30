import { Icon } from "./Icon";
import { useCurrentUser } from "../lib/useCurrentUser";

/**
 * Persistent reminder that a demo session is active and nothing it does is
 * saved. Reads `/api/auth/me` itself (shared `useCurrentUser` hook, cached
 * per navigation) rather than depending on a specific screen having already
 * fetched the user, so it renders correctly no matter which screen the
 * demo session lands on first.
 */
export function DemoBanner() {
  const { status, data } = useCurrentUser();

  if (status !== "success" || !data?.is_demo) return null;

  return (
    <div className="demo-banner" role="status">
      <Icon name="i-spark" size="s" />
      You're in a live demo. Nothing you do here is saved.
      <a href="https://plumbline.dev" rel="noreferrer">
        Create an account
      </a>
    </div>
  );
}
