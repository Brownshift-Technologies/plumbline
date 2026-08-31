import { useRef, useState, type ChangeEvent } from "react";
import { Field } from "../../components/Field";
import { Button } from "../../components/Button";
import { Pill } from "../../components/Pill";
import { Icon } from "../../components/Icon";
import { useToast } from "../../components/Toast";
import { api, ApiError } from "../../lib/api";
import { isDemoWrite, demoWriteMessage } from "../../lib/demo";
import type { AsyncState } from "../../lib/useAsync";
import type { CurrentUser } from "../../lib/types";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  approver: "Approver",
  reader: "Read only",
};

export function ProfilePane({ user }: { user: AsyncState<CurrentUser> }) {
  const { show } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(user.data?.name ?? "");
  const [email, setEmail] = useState(user.data?.email ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initials = (user.data?.name ?? "?")
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("photo", file);
    try {
      const res = await api.post("/auth/photo", form);
      show(isDemoWrite(res) ? demoWriteMessage("this photo would be uploaded") : "Photo updated.");
      user.reload();
    } catch (err) {
      show(
        err instanceof ApiError && err.status === 404
          ? "Photo upload isn't available yet."
          : err instanceof Error
            ? err.message
            : "Couldn't upload that photo.",
      );
    }
  }

  async function onRemovePhoto() {
    try {
      const res = await api.del("/auth/photo");
      show(isDemoWrite(res) ? demoWriteMessage("this photo would be removed") : "Photo removed.");
      user.reload();
    } catch (err) {
      show(
        err instanceof ApiError && err.status === 404
          ? "Photo removal isn't available yet."
          : err instanceof Error
            ? err.message
            : "Couldn't remove that photo.",
      );
    }
  }

  async function onSave() {
    setError(null);
    setSaving(true);
    try {
      const res = await api.patch("/auth/me", { name, email });
      show(isDemoWrite(res) ? demoWriteMessage("your profile would be saved") : "Profile saved");
      user.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save your profile.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="setrow">
        <div>
          <h4>Photo</h4>
          <p>Shown beside your approvals in the audit ledger.</p>
        </div>
        <div style={{ display: "flex", gap: 11, alignItems: "center" }}>
          <span className="avatar" style={{ width: 54, height: 54, fontSize: 17 }}>
            {initials}
          </span>
          <input ref={fileInput} type="file" accept="image/*" hidden onChange={onUpload} aria-label="Upload photo" />
          <Button onClick={() => fileInput.current?.click()}>Upload</Button>
          <Button onClick={onRemovePhoto}>Remove</Button>
        </div>
      </div>
      <div className="setrow">
        <div>
          <h4>Name</h4>
          <p>How you appear to your team.</p>
        </div>
        <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} wrapperStyle={{ margin: 0 }} />
      </div>
      <div className="setrow">
        <div>
          <h4>Work email</h4>
          <p>Used to sign in and to send approval requests.</p>
        </div>
        <div>
          <Field label="Work email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          {user.data?.email_verified === true && (
            <Pill kind="pass">
              <Icon name="i-check" size="xs" /> Verified
            </Pill>
          )}
          {user.data?.email_verified === false && <Pill kind="warn">Not verified</Pill>}
        </div>
      </div>
      <div className="setrow">
        <div>
          <h4>Role</h4>
          <p>
            Only owners can change gate rules or approve a patch under <span className="mono">payments/*</span>.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Pill kind="info">{ROLE_LABEL[user.data?.role ?? "reader"]}</Pill>
        </div>
      </div>
      {error && (
        <p role="alert" style={{ color: "var(--fail)", fontSize: 13.5, paddingTop: 8 }}>
          {error}
        </p>
      )}
      <div style={{ display: "flex", gap: 9, padding: "20px 0" }}>
        <Button variant="pri" onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
        <Button
          onClick={() => {
            setName(user.data?.name ?? "");
            setEmail(user.data?.email ?? "");
          }}
        >
          Discard
        </Button>
      </div>
    </div>
  );
}
