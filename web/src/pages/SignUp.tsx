import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Field } from "../components/Field";
import { Button } from "../components/Button";
import { api, API_BASE, ApiError } from "../lib/api";
import { routes } from "../lib/routes";

/**
 * Create a real account.
 *
 * This screen did not exist. `SignIn.tsx` ended with
 * `New here? <a href="#">Create an account</a>` -- a dead link -- so the
 * only way into the product was the demo door, and a demo session's runs
 * are SIMULATED (`app/run_routes.py`: `simulate_run(...) if sess.is_demo`).
 * Nobody could reach the path where the fleet actually executes: a real
 * account enqueues a real Cloud Run Job that crawls a real site.
 *
 * `POST /api/auth/signup` has always existed and always worked. Only the
 * way in was missing.
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// app/auth_routes.py's _MIN_PASSWORD_LEN. Mirrored so the form can say so
// before a round trip; the server still enforces it either way.
const MIN_PASSWORD = 12;

const OAUTH_PROVIDERS = [
  { id: "github", label: "Continue with GitHub", icon: "i-git" as const },
  { id: "google", label: "Continue with Google", icon: undefined },
  { id: "okta", label: "Continue with Okta SSO", icon: "i-lock" as const },
];

function oauthHref(provider: string): string {
  return `${API_BASE}/auth/oauth/${provider}/start`;
}

function messageOf(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Couldn't reach the server. Try again.";
}

export function SignUp() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const nameError = touched && name.trim().length === 0 ? "Tell us your name." : null;
  const emailError = touched && !EMAIL_RE.test(email) ? "Enter a valid work email." : null;
  const passwordError =
    touched && password.length < MIN_PASSWORD
      ? `Use at least ${MIN_PASSWORD} characters.`
      : null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (
      name.trim().length === 0 ||
      !EMAIL_RE.test(email) ||
      password.length < MIN_PASSWORD
    ) {
      return;
    }
    setSubmitting(true);
    setServerError(null);
    try {
      await api.post("/auth/signup", { email, password, name: name.trim() });
      // Signup issues the session cookie itself, so there is no second
      // sign-in step -- go straight to the product.
      navigate(routes.home);
    } catch (err) {
      setServerError(messageOf(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth" data-screen="signup">
      <div className="auth-l">
        <div className="logo" style={{ paddingLeft: 0 }}>
          <svg width="20" height="24" viewBox="0 0 20 24" aria-hidden="true">
            <line x1="10" y1="1" x2="10" y2="13" stroke="#1F4FD8" strokeWidth="1.8" strokeLinecap="round" />
            <path d="M10 13 L15.5 18 L10 23 L4.5 18 Z" fill="#1F4FD8" />
          </svg>
          <b>Plumbline</b>
        </div>
        <h2>Point it at your app and watch it work.</h2>
        <p>
          A real account runs the fleet for real: Cartographer crawls the URL you
          give it, Auditor checks every route it finds, and Chaos goes looking for
          what nobody wrote a test for.
        </p>
        <div className="facts">
          <div>
            <Icon name="i-check" size="s" /> Real runs against your own staging URL
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
          <h3>Create your account</h3>
          <p className="sub">Free while you try it. No card.</p>

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
              label="Your name"
              placeholder="Ada Lovelace"
              value={name}
              onChange={(e) => setName(e.target.value)}
              invalid={Boolean(nameError)}
              hint={nameError ?? undefined}
              autoComplete="name"
            />
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
              hint={passwordError ?? `At least ${MIN_PASSWORD} characters.`}
              autoComplete="new-password"
            />

            <Button
              type="submit"
              variant="pri"
              style={{ width: "100%", justifyContent: "center", padding: 10, marginTop: 4 }}
              disabled={submitting}
            >
              {submitting ? "Creating your account…" : "Create account"}
            </Button>
          </form>

          <p style={{ marginTop: 18, fontSize: 13.5, color: "var(--muted)", textAlign: "center" }}>
            Already have an account?{" "}
            <Link to={routes.signin} className="lnk">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
