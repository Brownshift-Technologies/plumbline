"""OAuth providers: the `OAuthProvider` protocol plus real implementations
for Google, GitHub and Okta, and `FakeProvider` for tests.

Every real provider talks to the network with the standard library
(`urllib.request`), not `requests`/`httpx` -- both of those are dev/test-only
dependencies in `pyproject.toml` (`httpx2`, `requests` are listed under
`[project.optional-dependencies].dev`), so a production import of this
module must not need either. The default test suite never actually executes
a real provider's network call at all: `tests/test_oauth.py` exercises
`FakeProvider` exclusively, and the opt-in suite that hits real endpoints
(`tests/test_oauth_live.py`) is a separate, explicitly-run file per the
brief, so that the offline default suite never depends on network access.

Account linking (see `app/oauth_routes.py`'s `callback` for where this is
enforced): `profile()` returns `(email, name, email_verified)`, not just
`(email, name)` as the brief's own interface line sketches it. That third
value is not decorative -- it is the entire defence against the attack the
brief calls out by name: "an attacker who can create an account at a
provider with a victim's email address takes over the victim's account if
you get this wrong". Every real provider below source `email_verified` from
the provider's own signal (Google/Okta's OIDC `email_verified` claim,
GitHub's `verified` flag on `/user/emails`), and `callback` links an OAuth
profile to an *existing* password account only when this is true. See that
module's docstring for the full decision and its reasoning.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol


class OAuthError(Exception):
    """A provider-side failure: a rejected/expired/already-used code, a
    network error, or a response shape the provider was not supposed to
    send. `app/oauth_routes.py`'s `callback` catches exactly this and turns
    it into a 400 -- nothing about a provider hiccup should look like (or
    respond with the detail of) an unhandled 500."""


class OAuthProvider(Protocol):
    name: str

    def authorize_url(self, state: str) -> str:
        """Where `GET /api/auth/oauth/{name}/start` 302s the browser to,
        with `state` embedded so the provider round-trips it back to
        `callback` verbatim."""
        ...

    def exchange(self, code: str) -> dict:
        """Trade an authorization `code` for a token response. Raises
        `OAuthError` if the provider rejects it -- including a code already
        redeemed once, which is how a replayed callback URL is refused even
        if Plumbline's own state-cookie defence (see `oauth_routes.py`)
        were somehow bypassed."""
        ...

    def profile(self, token: dict) -> tuple[str, str, bool]:
        """`(email, name, email_verified)` for the account the token
        belongs to. `email_verified` must reflect the PROVIDER's own
        verification of that address, never be hardcoded True -- see the
        module docstring."""
        ...


def _post_form(url: str, fields: dict, *, headers: dict | None = None) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 -- fixed provider hosts
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise OAuthError(f"token exchange failed: {exc}") from exc


def _get_json(url: str, *, bearer: str) -> dict | list:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 -- fixed provider hosts
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise OAuthError(f"profile fetch failed: {exc}") from exc


class GoogleProvider:
    name = "google"
    _AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    _TOKEN_URL = "https://oauth2.googleapis.com/token"
    _USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

    def __init__(self, config):
        self._client_id = config.google_client_id
        self._client_secret = config.google_client_secret
        self._redirect_uri = f"{config.oauth_redirect_base}/api/auth/oauth/google/callback"

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
        return f"{self._AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange(self, code: str) -> dict:
        return _post_form(
            self._TOKEN_URL,
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    def profile(self, token: dict) -> tuple[str, str, bool]:
        access_token = token.get("access_token", "")
        info = _get_json(self._USERINFO_URL, bearer=access_token)
        # Google's userinfo endpoint returns email_verified as a real JSON
        # boolean; some OIDC providers send the string "true" instead, so
        # this tolerates either rather than trusting the type.
        verified = info.get("email_verified") in (True, "true")
        return info.get("email", ""), info.get("name", ""), verified


class GitHubProvider:
    name = "github"
    _AUTH_URL = "https://github.com/login/oauth/authorize"
    _TOKEN_URL = "https://github.com/login/oauth/access_token"
    _USER_URL = "https://api.github.com/user"
    _EMAILS_URL = "https://api.github.com/user/emails"

    def __init__(self, config):
        self._client_id = config.github_client_id
        self._client_secret = config.github_client_secret
        self._redirect_uri = f"{config.oauth_redirect_base}/api/auth/oauth/github/callback"

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
        return f"{self._AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange(self, code: str) -> dict:
        return _post_form(
            self._TOKEN_URL,
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
            },
        )

    def profile(self, token: dict) -> tuple[str, str, bool]:
        access_token = token.get("access_token", "")
        user = _get_json(self._USER_URL, bearer=access_token)
        name = user.get("name") or user.get("login", "") if isinstance(user, dict) else ""
        # GitHub's /user.email is null unless the profile email is public,
        # and carries no verification signal even when present -- the
        # verified flag lives only on /user/emails, keyed per address. Pick
        # the entry GitHub itself marks primary; an account can have several
        # emails and only the primary one is what a user would expect to
        # sign in with here.
        emails = _get_json(self._EMAILS_URL, bearer=access_token)
        primary = next((e for e in emails if isinstance(e, dict) and e.get("primary")), None)
        if primary is None:
            # No accessible primary email (token missing user:email scope,
            # or an org policy hiding it) -- fail closed: no address to
            # link or create an account with, and never guess one as
            # "verified" to make a downstream check pass.
            return "", name, False
        return primary.get("email", ""), name, bool(primary.get("verified"))


class OktaProvider:
    name = "okta"

    def __init__(self, config):
        self._client_id = config.okta_client_id
        self._client_secret = config.okta_client_secret
        self._domain = config.okta_domain
        self._redirect_uri = f"{config.oauth_redirect_base}/api/auth/oauth/okta/callback"

    def _url(self, path: str) -> str:
        return f"https://{self._domain}{path}"

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
        return f"{self._url('/oauth2/default/v1/authorize')}?{urllib.parse.urlencode(params)}"

    def exchange(self, code: str) -> dict:
        return _post_form(
            self._url("/oauth2/default/v1/token"),
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    def profile(self, token: dict) -> tuple[str, str, bool]:
        access_token = token.get("access_token", "")
        info = _get_json(self._url("/oauth2/default/v1/userinfo"), bearer=access_token)
        verified = info.get("email_verified") in (True, "true")
        return info.get("email", ""), info.get("name", ""), verified


class FakeProvider:
    """Test double implementing the same protocol, with none of it real.

    `authorize_url` never leaves the process; `exchange` and `profile` are
    driven entirely by what the test configured, plus one piece of real
    provider behaviour tests actually rely on: a `code` can be exchanged
    exactly once. Real OAuth servers enforce single-use authorization codes
    themselves (RFC 6749 4.1.2); mirroring that here is what lets
    `tests/test_oauth.py` prove a replayed callback is refused without ever
    touching a network.
    """

    def __init__(self, *, email: str, name: str = "Fake User", email_verified: bool = True):
        self.name = "fake"
        self.email = email
        self.display_name = name
        self.email_verified = email_verified
        self._redeemed_codes: set[str] = set()

    def authorize_url(self, state: str) -> str:
        return f"https://fake-provider.test/authorize?state={urllib.parse.quote(state)}"

    def exchange(self, code: str) -> dict:
        if code in self._redeemed_codes:
            raise OAuthError("authorization code already redeemed")
        self._redeemed_codes.add(code)
        return {"access_token": f"fake-token-for-{code}"}

    def profile(self, token: dict) -> tuple[str, str, bool]:
        return self.email, self.display_name, self.email_verified
