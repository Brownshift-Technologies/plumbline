import { useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Field } from "../components/Field";
import { Button } from "../components/Button";
import { routes } from "../lib/routes";

export function SignIn() {
  const navigate = useNavigate();

  return (
    <div className="auth">
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
          <p className="sub">Welcome back. Pick up where run 4471 left off.</p>
          <div className="oauth">
            <button type="button" onClick={() => navigate(routes.home)}>
              <Icon name="i-git" size="s" /> Continue with GitHub
            </button>
            <button type="button" onClick={() => navigate(routes.home)}>
              Continue with Google
            </button>
            <button type="button" onClick={() => navigate(routes.home)}>
              <Icon name="i-lock" size="s" /> Continue with Okta SSO
            </button>
          </div>
          <div className="divider">or with email</div>
          <Field label="Work email" type="email" placeholder="you@company.com" />
          <Field label="Password" type="password" placeholder="••••••••••••" />
          <Button
            variant="pri"
            style={{ width: "100%", justifyContent: "center", padding: 10 }}
            onClick={() => navigate(routes.home)}
          >
            Sign in
          </Button>
          <div className="demo">
            <b>
              <Icon name="i-spark" size="s" /> Try it without an account
            </b>
            <p>
              A seeded workspace with real runs, a real failure and a real proposed
              patch. Nothing to install, no card.
            </p>
            <Button variant="pri" onClick={() => navigate(routes.home)}>
              Open the live demo
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
