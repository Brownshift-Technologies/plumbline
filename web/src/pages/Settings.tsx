import { useState } from "react";
import { Field } from "../components/Field";
import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { useToast } from "../components/Toast";

const TABS = ["profile", "security", "members", "billing"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABEL: Record<Tab, string> = {
  profile: "Profile",
  security: "Security",
  members: "Members",
  billing: "Billing",
};

function ProfilePane() {
  const { show } = useToast();
  return (
    <div>
      <div className="setrow">
        <div>
          <h4>Name</h4>
          <p>How you appear to your team.</p>
        </div>
        <Field label="Name" defaultValue="Roger Koranteng" wrapperStyle={{ margin: 0 }} />
      </div>
      <div className="setrow">
        <div>
          <h4>Work email</h4>
          <p>Used to sign in and to send approval requests.</p>
        </div>
        <div>
          <Field label="Work email" type="email" defaultValue="roger@acme.com" />
          <Pill kind="pass">Verified</Pill>
        </div>
      </div>
      <div style={{ display: "flex", gap: 9, padding: "20px 0" }}>
        <Button variant="pri" onClick={() => show("Profile saved")}>
          Save changes
        </Button>
        <Button>Discard</Button>
      </div>
    </div>
  );
}

export function Settings() {
  const [tab, setTab] = useState<Tab>("profile");

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
      {tab === "profile" && <ProfilePane />}
      {tab !== "profile" && (
        <div style={{ padding: "20px 0", color: "var(--muted)", fontSize: 14 }}>
          {TAB_LABEL[tab]} settings live here.
        </div>
      )}
    </div>
  );
}
