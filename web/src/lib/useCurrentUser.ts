import { api } from "./api";
import { useAsync } from "./useAsync";
import type { CurrentUser } from "./types";

/** GET /api/auth/me, shared by the shell (demo banner, avatar) and Settings. */
export function useCurrentUser() {
  return useAsync<CurrentUser>(() => api.get<CurrentUser>("/auth/me"), []);
}
