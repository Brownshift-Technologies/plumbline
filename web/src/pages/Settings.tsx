import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { Button } from "../components/Button";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { useCurrentUser } from "../lib/useCurrentUser";
import { routes } from "../lib/routes";
import { ProfilePane } from "./settings/ProfilePane";
import { SecurityPane } from "./settings/SecurityPane";
import { MembersPane } from "./settings/MembersPane";
import { BillingPane } from "./settings/BillingPane";

const TABS = ["profile", "security", "members", "billing"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABEL: Record<Tab, string> = {
  profile: "Profile",
  security: "Security",
  members: "Members",
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

      {user.status === "loading" && <EmptyState variant="loading" title="Loading your account…" />}
      {user.status === "error" && (
        <EmptyState variant="error" title="Couldn't load your account" description={user.error} actions={<Button onClick={user.reload}>Retry</Button>} />
      )}
      {user.status === "success" && (
        <div role="tabpanel">
          {tab === "profile" && <ProfilePane user={user} />}
          {tab === "security" && <SecurityPane user={user} onSignOut={onSignOut} />}
          {tab === "members" && <MembersPane user={user} />}
          {tab === "billing" && <BillingPane user={user} />}
        </div>
      )}
    </div>
  );
}
