import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { Button } from "../components/Button";
import { SkeletonBlock, SkeletonLines } from "../components/Skeleton";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useCurrentUser } from "../lib/useCurrentUser";
import { routes } from "../lib/routes";
import { ProfilePane } from "./settings/ProfilePane";
import { SecurityPane } from "./settings/SecurityPane";
import { MembersPane } from "./settings/MembersPane";
import { BillingPane } from "./settings/BillingPane";
import { McpPane } from "./settings/McpPane";
import { WorkspacePane } from "./settings/WorkspacePane";

const TABS = ["profile", "security", "members", "workspace", "mcp", "billing"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABEL: Record<Tab, string> = {
  profile: "Profile",
  security: "Security",
  members: "Members",
  workspace: "Workspace",
  mcp: "MCP",
  billing: "Billing",
};

export function Settings() {
  const [tab, setTab] = useState<Tab>("profile");
  const navigate = useNavigate();
  const { show } = useToast();
  const user = useCurrentUser();

  async function onSignOut() {
    try {
      await api.post("/auth/signout");
    } catch {
      // Even if the server call fails, drop the client back to sign-in --
      // staying "signed in" to a session the server has already forgotten
      // is worse than a stale cookie the next request will reject anyway.
    }
    show("Signed out.");
    navigate(routes.signin);
  }

  return (
    <div className="body" style={{ maxWidth: 940 }}>
      <div className="pagehead">
        <h1>Settings</h1>
        <p>Your account and this workspace.</p>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            className={tab === t ? "on" : undefined}
            onClick={() => setTab(t)}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      {user.status === "loading" && (
        <div style={{ padding: "20px 0" }} aria-busy="true">
          <span className="visually-hidden" role="status">Loading your account…</span>
          <SkeletonBlock width="20%" height={14} style={{ marginBottom: 14 }} />
          <SkeletonLines count={4} />
        </div>
      )}
      {user.status === "error" && (
        <EmptyState variant="error" title="Couldn't load your account" description={user.error} actions={<Button onClick={user.reload}>Retry</Button>} />
      )}
      {user.status === "success" && (
        <div role="tabpanel" aria-label={TAB_LABEL[tab]}>
          {/* Names the active pane at h2, so the h4s inside each .setrow
              (now h3) no longer jump straight from the page h1 -- axe's
              heading-order. Visually hidden: the selected tab already
              shows the same word, so rendering it twice would be noise. */}
          <h2 className="visually-hidden">{TAB_LABEL[tab]}</h2>
          {tab === "profile" && <ProfilePane user={user} />}
          {tab === "security" && <SecurityPane user={user} onSignOut={onSignOut} />}
          {tab === "members" && <MembersPane user={user} />}
          {tab === "workspace" && <WorkspacePane user={user} />}
          {tab === "mcp" && <McpPane />}
          {tab === "billing" && <BillingPane user={user} />}
        </div>
      )}
    </div>
  );
}
