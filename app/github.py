"""Task 14g: the GitHub App client -- its own identity, per-repository
installation, short-lived tokens, never an OAuth user token.

**Why a GitHub App, not OAuth.** `app/providers.py`'s existing
`GitHubProvider` is Task 8b's SIGN-IN integration -- it authenticates a
*person*. This module is a completely different thing: a GitHub App has
its own bot identity, so every pull request Plumbline opens is authored
by "plumbline[bot]", not by whoever happened to click "connect" -- and
access is per-installation, so it survives that person leaving the
company. An OAuth token acting as a user is exactly the failure mode
this module exists to avoid.

**Minimum permissions, and `contents: read` is not negotiable.**
`PERMISSIONS` below is requested on every installation-token mint and is
never widened at runtime -- `contents: read`, `pull_requests: write`,
`checks: write`. Surgeon opens a branch and a pull request (write
operations that need `pull_requests: write` and a NEW ref, never a push
to the default branch's own ref -- see `open_pull_request`'s docstring),
and Triager reads a PR's diff (`contents: read` covers a `GET .../pulls`
diff view; no write scope is needed to READ). If `contents` ever needed
to be `write` for this to work, the human approval gate this whole
product is built around would be decorative: the app could push straight
to `main` and nothing downstream would ever see a pull request to
approve.

**Tokens are cached per installation and refreshed on expiry, not
minted per call.** `installation_token` is the ONLY method that talks to
GitHub's `/app/installations/{id}/access_tokens` endpoint; every other
method here calls it first and reuses whatever comes back. Real
installation tokens live one hour; re-minting one on every single API
call would multiply GitHub's own rate limits by however many calls a
run happens to make, for no benefit -- the whole point of caching is
"mint once, reuse until it is genuinely about to expire".

**A token is a secret that must never be observable after the fact.**
Nothing in this module logs a raw token, and neither does any caller in
`agents/repo_source.py`/`app/github_routes.py` -- `core.guards.redact_deep`
already recognises the `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_`
shapes (an installation token is `ghs_`-prefixed, squarely inside that
set) and scrubs one wherever it reaches `gateway/ledger.py`'s `append` or
`core.store.append_audit`. `tests/test_github.py`'s
`test_a_token_never_reaches_a_ledger_entry` is what proves that holds
for a REAL installation token this module actually mints, not merely
for the regex in the abstract.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

_API_BASE = "https://api.github.com"
# Requested on every token mint, and nothing here ever asks for more.
# `contents: write` on the default branch is the one permission this
# module must NEVER hold -- see the module docstring.
PERMISSIONS = {"contents": "read", "pull_requests": "write", "checks": "write"}


class GitHubError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_app_jwt(app_id: str, private_key_pem: bytes, *, now: float | None = None) -> str:
    """A GitHub App JWT (RS256), signed by hand against `cryptography`
    (already a transitive dependency of this project's Google Cloud
    libraries -- see `pyproject.toml`) rather than pulling in PyJWT for
    one call site. `exp` is capped at 9 minutes, under GitHub's own
    10-minute ceiling for app JWTs; `iat` is backdated 60 seconds to
    tolerate ordinary clock skew between this process and GitHub's."""
    now = time.time() if now is None else now
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(
        {"iat": int(now) - 60, "exp": int(now) + 540, "iss": app_id}, separators=(",", ":"),
    ).encode())
    signing_input = f"{header}.{payload}"
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def verify_webhook_signature(secret: str, raw_body: bytes, header_value: str | None) -> bool:
    """Constant-time verify `X-Hub-Signature-256` -- GitHub's own HMAC-SHA256
    over the raw request body, hex-encoded and prefixed `sha256=`. An
    absent header or one with the wrong prefix is `False`, never a crash
    (a webhook body is untrusted input; nothing about a malformed header
    should raise before this function gets to say so). `hmac.compare_digest`
    is what makes the actual byte comparison constant-time -- a `==` here
    would let a timing attack narrow down the correct signature one byte
    at a time; see `tests/test_github.py`'s `test_the_signature_compare_is_
    constant_time` for why this specific function, not `==`, is asserted on.
    """
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value[len("sha256="):])


def _default_request(method: str, url: str, *, token: str | None = None, body: dict | None = None, accept: str | None = None):
    """The real transport -- `urllib.request`, not `requests` (a dev-only
    dependency; see `app/providers.py`/`app/webhooks.py` for the same
    choice made the same way). Returns parsed JSON for a normal GitHub
    response, or raw decoded text when `accept` asks for a non-JSON media
    type (a PR diff, fetched with `Accept: application/vnd.github.v3.diff`).
    """
    headers = {"Accept": accept or "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 -- fixed GitHub host
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise GitHubError(f"GitHub API {method} {url} -> {exc.code}: {exc.read().decode(errors='replace')}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"GitHub API unreachable: {exc}") from exc
    if accept and "json" not in accept:
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError) as exc:
        raise GitHubError(f"malformed GitHub API response: {exc}") from exc


class GitHubApp:
    """One GitHub App identity, bound to a private key, minting per-
    installation tokens on demand. `bind(repo_full_name, installation_id)`
    is how a caller (`app/github_routes.py`, on a successful install
    callback) teaches this instance which installation owns which repo --
    every other method here takes `repo` alone (matching this task's own
    interface list, verbatim) and resolves the installation id itself,
    exactly the way `installation_token`'s own cache means a caller never
    threads a token through by hand either."""

    def __init__(self, app_id: str, private_key_pem: bytes, *, request=None, now=None):
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._request = request or _default_request
        self._now = now or time.time
        self._tokens: dict[str, tuple[str, float]] = {}
        self._repo_installations: dict[str, str] = {}

    def bind(self, repo_full_name: str, installation_id: str) -> None:
        self._repo_installations[repo_full_name] = installation_id

    def _installation_for(self, repo: str) -> str:
        installation_id = self._repo_installations.get(repo)
        if installation_id is None:
            raise GitHubError(f"no installation bound for repo {repo!r} -- call bind() first")
        return installation_id

    def installation_token(self, installation_id: str) -> str:
        cached = self._tokens.get(installation_id)
        now = self._now()
        if cached is not None and cached[1] > now:
            return cached[0]
        jwt_token = sign_app_jwt(self._app_id, self._private_key_pem, now=now)
        resp = self._request(
            "POST", f"{_API_BASE}/app/installations/{installation_id}/access_tokens",
            token=jwt_token, body={"permissions": PERMISSIONS},
        )
        token = resp["token"]
        expires_at = _parse_expiry(resp.get("expires_at"), now)
        self._tokens[installation_id] = (token, expires_at)
        return token

    def revoke(self, installation_id: str) -> None:
        """Drop the cached token for `installation_id` -- called on
        disconnect (`app/github_routes.py`'s `DELETE /api/workspaces/{id}
        /repo`), so a workspace that disconnects its repo does not leave
        a still-valid, still-cached token sitting in this process for the
        rest of its natural one-hour life."""
        self._tokens.pop(installation_id, None)

    def list_installation_repos(self, installation_id: str) -> list[dict]:
        """Every repo this installation can see -- `GET /api/github/repos`
        (`app/github_routes.py`) is what a customer picks ONE of, via
        `POST /api/workspaces/{id}/repo`, to actually connect."""
        token = self.installation_token(installation_id)
        listing = self._request("GET", f"{_API_BASE}/installation/repositories", token=token)
        repos = listing.get("repositories", []) if isinstance(listing, dict) else []
        return [
            {"full_name": r.get("full_name", ""), "default_branch": r.get("default_branch", "main")}
            for r in repos if isinstance(r, dict)
        ]

    def list_specs(self, repo: str, ref: str) -> list[str]:
        token = self.installation_token(self._installation_for(repo))
        tree = self._request("GET", f"{_API_BASE}/repos/{repo}/git/trees/{ref}?recursive=1", token=token)
        entries = tree.get("tree", []) if isinstance(tree, dict) else []
        return [
            e["path"] for e in entries
            if isinstance(e, dict) and e.get("type") == "blob"
            and isinstance(e.get("path"), str) and e["path"].endswith((".spec.ts", ".spec.js"))
        ]

    def read_file(self, repo: str, path: str, ref: str) -> str:
        token = self.installation_token(self._installation_for(repo))
        resp = self._request("GET", f"{_API_BASE}/repos/{repo}/contents/{path}?ref={ref}", token=token)
        content = resp.get("content", "") if isinstance(resp, dict) else ""
        return base64.b64decode(content).decode("utf-8", errors="replace")

    def pull_request_diff(self, repo: str, number: int) -> str:
        token = self.installation_token(self._installation_for(repo))
        return self._request(
            "GET", f"{_API_BASE}/repos/{repo}/pulls/{number}", token=token,
            accept="application/vnd.github.v3.diff",
        )

    def open_pull_request(self, repo: str, branch: str, title: str, body: str, changes: dict, default_branch: str) -> str:
        """Opens a pull request FROM a brand-new branch -- never pushes
        to `default_branch`'s own ref. `contents: read` is all this
        module ever holds (see the module docstring), so `default_branch`
        is read once, to find the commit a new branch starts from, and
        every write below (`POST .../git/refs`, `PUT .../contents/{path}`)
        targets `branch`, the NEW ref this call itself just created --
        `tests/test_github.py`'s `test_opening_a_pull_request_targets_a_
        new_branch_not_the_default` asserts on exactly this by inspecting
        every request this method actually makes."""
        token = self.installation_token(self._installation_for(repo))
        base_ref = self._request("GET", f"{_API_BASE}/repos/{repo}/git/ref/heads/{default_branch}", token=token)
        base_sha = base_ref["object"]["sha"]

        self._request(
            "POST", f"{_API_BASE}/repos/{repo}/git/refs", token=token,
            body={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        for path, new_content in changes.items():
            self._request(
                "PUT", f"{_API_BASE}/repos/{repo}/contents/{path}", token=token,
                body={
                    "message": f"plumbline: update {path}",
                    "content": base64.b64encode(new_content.encode()).decode(),
                    "branch": branch,
                },
            )
        pr = self._request(
            "POST", f"{_API_BASE}/repos/{repo}/pulls", token=token,
            body={"title": title, "body": body, "head": branch, "base": default_branch},
        )
        return pr.get("html_url", "")

    def create_check_run(self, repo: str, sha: str, conclusion: str, summary: str) -> dict:
        token = self.installation_token(self._installation_for(repo))
        return self._request(
            "POST", f"{_API_BASE}/repos/{repo}/check-runs", token=token,
            body={
                "name": "plumbline", "head_sha": sha, "status": "completed",
                "conclusion": conclusion, "output": {"title": "Plumbline run", "summary": summary},
            },
        )


def _parse_expiry(expires_at, now: float) -> float:
    """GitHub's `expires_at` is an ISO-8601 string
    (`"2026-08-30T18:30:00Z"`). Parsed by hand (no `dateutil` dependency)
    rather than trusted blindly: a malformed or missing value falls back
    to `now` (already-expired), which is the fail-CLOSED direction -- a
    token whose real expiry could not be determined is refreshed on its
    very next use rather than assumed to be good for an hour it never
    actually promised."""
    if not isinstance(expires_at, str):
        return now
    try:
        import datetime

        dt = datetime.datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except ValueError:
        return now
