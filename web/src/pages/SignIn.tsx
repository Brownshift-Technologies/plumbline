import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Field } from "../components/Field";
import { Button } from "../components/Button";
import { api, API_BASE, ApiError } from "../lib/api";
import { routes } from "../lib/routes";

const OAUTH_PROVIDERS = [
  { id: "github", label: "Continue with GitHub", icon: "i-git" as const },
  { id: "google", label: "Continue with Google", icon: undefined },
  { id: "okta", label: "Continue with Okta SSO", icon: "i-lock" as const },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function messageOf(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Couldn't reach the server. Try again.";
}

export function SignIn() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);
  const [forgotOpen, setForgotOpen] = useState(false);
  const [resetEmail, setResetEmail] = useState("");
  const [resetSent, setResetSent] = useState(false);

  const emailError = touched && !EMAIL_RE.test(email) ? "Enter a valid work email." : null;
  const passwordError = touched && password.length === 0 ? "Enter your password." : null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    setServerError(null);
    if (!EMAIL_RE.test(email) || password.length === 0) return;
    setSubmitting(true);
    try {
      await api.post("/auth/signin", { email, password });
      navigate(routes.home);
    } catch (err) {
      setServerError(messageOf(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function onDemo() {
    setDemoError(null);
    setDemoLoading(true);
    try {
      await api.post("/auth/demo");
      navigate(routes.home);
    } catch (err) {
      setDemoError(messageOf(err));
    } finally {
      setDemoLoading(false);
    }
  }

  async function onForgotSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/auth/reset/request", { email: resetEmail });
    } catch {
      // The endpoint always answers 200 regardless of whether the email
      // exists (an account-enumeration guard) -- a network failure here
      // still shows the same confirmation, never a different message.
    }
    setResetSent(true);
  }

  function oauthHref(provider: string) {
    return `${API_BASE}/auth/oauth/${provider}/start`;
  }

  return (
    <div className="auth" data-screen="signin">
      <div className="auth-l">
        <div className="logo" style={{ paddingLeft: 0 }}>
          <svg width="20" height="24" viewBox="0 0 20 24" aria-hidden="true">
            <line x1="10" y1="1" x2="10" y2="13" stroke="#1F4FD8" strokeWidth="1.8" strokeLinecap="round" />
            <path d="M10 13 L15.5 18 L10 23 L4.5 18 Z" fill="#1F4FD8" />
          </svg>
          <b>Plumbline</b>
        </div>
        <h2>The test suite you never had time to write.</h2>
        <p>
          Connect a repository and Plumbline maps it, writes the behaviours, breaks
          them on purpose, and opens a pull request when it finds the fix. You approve
          everything.
        </p>
        <div className="facts">
          <div>
            <Icon name="i-check" size="s" /> SOC 2 Type II and GDPR aligned
          </div>
          <div>
            <Icon name="i-check" size="s" /> No agent can merge without an approval
          </div>
          <div>
            <Icon name="i-check" size="s" /> Every action in an exportable audit ledger
          </div>
        </div>
      </div>
      <div className="auth-r">
        <div className="authbox">
          <h3>Sign in to Plumbline</h3>
          <p className="sub">Welcome back.</p>

          <div className="oauth">
            {OAUTH_PROVIDERS.map((p) => (
              <a key={p.id} href={oauthHref(p.id)} role="button">
                {p.icon && <Icon name={p.icon} size="s" />} {p.label}
              </a>
            ))}
          </div>
          <div className="divider">or with email</div>

          <form onSubmit={onSubmit} noValidate>
            {serverError && (
              <p role="alert" style={{ color: "var(--fail)", fontSize: 13.5, marginBottom: 10 }}>
                {serverError}
              </p>
            )}
            <Field
              label="Work email"
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              invalid={Boolean(emailError)}
              hint={emailError ?? undefined}
              autoComplete="email"
            />
            <Field
              label="Password"
              type="password"
              placeholder="••••••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              invalid={Boolean(passwordError)}
              hint={passwordError ?? undefined}
              autoComplete="current-password"
            />
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                margin: "2px 0 14px",
              }}
            >
              <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 13.5, color: "var(--muted)" }}>
                <input type="checkbox" style={{ width: "auto", margin: 0 }} /> Keep me signed in
              </label>
              <button
                type="button"
                className="lnk"
                style={{ fontWeight: 500 }}
                onClick={() => setForgotOpen((o) => !o)}
                aria-expanded={forgotOpen}
              >
                Forgot password?
              </button>
            </div>

            {forgotOpen && (
              <div style={{ marginBottom: 14 }}>
                {resetSent ? (
                  <p style={{ fontSize: 13.5, color: "var(--muted)" }} role="status">
                    If an account exists for that email, a reset link is on its way.
                  </p>
                ) : (
                  <form onSubmit={onForgotSubmit} style={{ display: "grid", gap: 8 }}>
                    <Field
                      label="Email to send the reset link to"
                      type="email"
                      value={resetEmail}
                      onChange={(e) => setResetEmail(e.target.value)}
                      wrapperStyle={{ margin: 0 }}
                    />
                    <Button type="submit" size="sm">
                      Send reset link
                    </Button>
                  </form>
                )}
              </div>
            )}

            <Button
              type="submit"
              variant="pri"
              style={{ width: "100%", justifyContent: "center", padding: 10 }}
              disabled={submitting}
            >
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <div className="demo">
            <b>
              <Icon name="i-spark" size="s" /> Try it without an account
            </b>
            <p>
              A seeded workspace with real runs, a real failure and a real proposed
              patch. Nothing to install, no card.
            </p>
            {demoError && (
              <p role="alert" style={{ color: "var(--fail)", fontSize: 13, marginTop: 8 }}>
                {demoError}
              </p>
            )}
            <Button variant="pri" onClick={onDemo} disabled={demoLoading}>
              {demoLoading ? "Opening demo…" : "Open the live demo"}
            </Button>
          </div>

          <p style={{ marginTop: 18, fontSize: 13.5, color: "var(--muted)", textAlign: "center" }}>
            New here? <a href="#" className="lnk">Create an account</a>
          </p>
        </div>
      </div>
    </div>
  );
}
