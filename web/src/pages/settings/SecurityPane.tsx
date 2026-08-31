import { useState, type FormEvent } from "react";
import QRCode from "qrcode";
import { Field } from "../../components/Field";
import { Button } from "../../components/Button";
import { EmptyState } from "../../components/EmptyState";
import { SkeletonBlock } from "../../components/Skeleton";
import { useToast } from "../../components/Toast";
import { api } from "../../lib/api";
import { useAsync } from "../../lib/useAsync";
import type { AsyncState } from "../../lib/useAsync";
import { isDemoWrite, demoWriteMessage } from "../../lib/demo";
import type { CurrentUser, Session, TotpEnrollResponse } from "../../lib/types";

function PasswordForm() {
  const { show } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (next.length < 12) {
      setError("Use at least 12 characters.");
      return;
    }
    if (next !== confirm) {
      setError("The new password and its confirmation don't match.");
      return;
    }
    setSaving(true);
    try {
      const res = await api.post("/auth/password", { current, new: next });
      show(isDemoWrite(res) ? demoWriteMessage("your password would be updated") : "Password updated. Other sessions signed out.");
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update your password.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit}>
      {error && (
        <p role="alert" style={{ color: "var(--fail)", fontSize: 13.5, marginBottom: 8 }}>
          {error}
        </p>
      )}
      <Field label="Current password" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
      <Field
        label="New password"
        type="password"
        value={next}
        onChange={(e) => setNext(e.target.value)}
        hint="Use a passphrase. Length beats symbols."
        autoComplete="new-password"
      />
      <Field label="Confirm new password" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
      <Button type="submit" variant="pri" disabled={saving}>
        {saving ? "Updating…" : "Update password"}
      </Button>
    </form>
  );
}

function TotpSection({ user }: { user: AsyncState<CurrentUser> }) {
  const { show } = useToast();
  const [enrolling, setEnrolling] = useState(false);
  const [enroll, setEnroll] = useState<TotpEnrollResponse | null>(null);
  const [qrSvg, setQrSvg] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [removeCode, setRemoveCode] = useState("");
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const enabled = user.data?.totp_enabled === true;

  async function startEnroll() {
    setError(null);
    try {
      const res = await api.post<TotpEnrollResponse>("/auth/totp/enrol");
      setEnroll(res);
      const svg = await QRCode.toString(res.otpauth_url, { type: "svg", margin: 1, width: 176 });
      setQrSvg(svg);
      setEnrolling(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start enrolment.");
    }
  }

  async function confirmEnroll() {
    setError(null);
    try {
      const res = await api.post("/auth/totp/verify", { code });
      if (isDemoWrite(res)) {
        show(demoWriteMessage("two-factor authentication would be enabled"));
      } else {
        show("Two-factor authentication enabled.");
      }
      setEnrolling(false);
      setEnroll(null);
      setCode("");
      user.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That code didn't verify. Try the newest one from your app.");
    }
  }

  async function onRemove() {
    setError(null);
    setRemoving(true);
    try {
      const res = await api.del("/auth/totp", { code: removeCode });
      show(isDemoWrite(res) ? demoWriteMessage("two-factor authentication would be removed") : "Two-factor authentication removed.");
      setRemoveCode("");
      user.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't remove two-factor authentication.");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className="setrow">
      <div>
        <h4>Two-factor authentication</h4>
        <p>Required for anyone who can approve a patch.</p>
      </div>
      <div>
        {error && (
          <p role="alert" style={{ color: "var(--fail)", fontSize: 13.5, marginBottom: 8 }}>
            {error}
          </p>
        )}
        {!enrolling && (
          <div style={{ display: "flex", gap: 13, alignItems: "center" }}>
            <button
              type="button"
              className={enabled ? "toggle" : "toggle off"}
              aria-pressed={enabled}
              aria-label="Two-factor authentication"
              onClick={() => (enabled ? undefined : startEnroll())}
            >
              <i />
            </button>
            <span style={{ fontSize: 13.5, color: "var(--muted)" }}>
              {enabled ? "Authenticator app enabled" : "Not enabled"}
            </span>
          </div>
        )}
        {enrolling && enroll && (
          <div style={{ marginTop: 10 }}>
            {qrSvg && (
              <div
                aria-label="Scan this QR code with your authenticator app"
                style={{ width: 176, height: 176, marginBottom: 10 }}
                dangerouslySetInnerHTML={{ __html: qrSvg }}
              />
            )}
            <p style={{ fontSize: 12.5, color: "var(--muted)" }}>
              Or enter this key manually: <span className="mono">{enroll.secret}</span>
            </p>
            <Field
              label="6-digit code from your app"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode="numeric"
              wrapperStyle={{ marginTop: 10 }}
            />
            <Button variant="pri" onClick={confirmEnroll} disabled={code.length < 6}>
              Confirm
            </Button>
          </div>
        )}
        {enabled && !enrolling && (
          <div style={{ marginTop: 14 }}>
            <Field label="Current code, to remove two-factor" value={removeCode} onChange={(e) => setRemoveCode(e.target.value)} wrapperStyle={{ marginBottom: 8 }} />
            <Button variant="dang" onClick={onRemove} disabled={removeCode.length < 6 || removing}>
              {removing ? "Removing…" : "Remove two-factor authentication"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function SessionsSection() {
  const { show } = useToast();
  const sessions = useAsync<Session[]>(() => api.get<Session[]>("/auth/sessions"), []);
  const rows = sessions.status === "success" ? sessions.data ?? [] : [];

  async function signOut(id: string, label: string) {
    try {
      const res = await api.del(`/auth/sessions/${id}`);
      show(isDemoWrite(res) ? demoWriteMessage(`${label} would be signed out`) : `Signed out of ${label}`);
      sessions.reload();
    } catch (err) {
      show(err instanceof Error ? err.message : "Couldn't sign that session out.");
    }
  }

  async function signOutEverywhere() {
    const others = rows.filter((s) => !s.current);
    const results = await Promise.allSettled(others.map((s) => api.del(`/auth/sessions/${s.id}`)));
    const anyDemo = results.some((r) => r.status === "fulfilled" && isDemoWrite(r.value));
    show(anyDemo ? demoWriteMessage("every other session would be signed out") : "Signed out everywhere else.");
    sessions.reload();
  }

  return (
    <div className="setrow">
      <div>
        <h4>Active sessions</h4>
        <p>Sign out anywhere you don't recognise.</p>
      </div>
      <div>
        {sessions.status === "loading" && (
          <div className="panel" aria-busy="true">
            <span className="visually-hidden" role="status">Loading sessions…</span>
            <table>
              <tbody>
                {[0, 1].map((i) => (
                  <tr key={i} data-skeleton-row="">
                    <td>
                      <SkeletonBlock width="60%" />
                    </td>
                    <td style={{ width: 90 }}>
                      <SkeletonBlock width={60} height={26} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {sessions.status === "error" && (
          <EmptyState variant="error" title="Couldn't load sessions" description={sessions.error} actions={<Button onClick={sessions.reload}>Retry</Button>} />
        )}
        {sessions.status === "success" && (
          <>
            <div className="panel">
              <table>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id}>
                      <td>
                        {s.user_agent || "Unknown device"}
                        {s.current && <span className="pill pass" style={{ marginLeft: 7 }}>This device</span>}
                        <div style={{ fontSize: 12.5, color: "var(--faint)", marginTop: 3 }}>{s.ip_city}</div>
                      </td>
                      <td style={{ width: 90 }}>
                        {!s.current && (
                          <Button size="sm" onClick={() => signOut(s.id, s.user_agent || "that device")}>
                            Sign out
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.some((s) => !s.current) && (
              <div style={{ marginTop: 10 }}>
                <Button onClick={signOutEverywhere}>Sign out everywhere else</Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export function SecurityPane({ user, onSignOut }: { user: AsyncState<CurrentUser>; onSignOut: () => void }) {
  return (
    <div>
      <div className="setrow">
        <div>
          <h4>Change password</h4>
          <p>You stay signed in here and are signed out everywhere else.</p>
        </div>
        <PasswordForm />
      </div>
      <TotpSection user={user} />
      <SessionsSection />
      <div className="setrow">
        <div>
          <h4>Sign out</h4>
          <p>End this session on this device.</p>
        </div>
        <div>
          <Button variant="dang" onClick={onSignOut}>
            Sign out of Plumbline
          </Button>
        </div>
      </div>
    </div>
  );
}
