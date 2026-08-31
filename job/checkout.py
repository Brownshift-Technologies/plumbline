"""Tier 2, item 2 (Agent C's own contract item): `RepoCheckout` -- a real,
disk-backed shallow clone of a workspace's connected GitHub repository,
living for exactly one Cloud Run Job run.

**Why this exists.** Before this module, Author "wrote" a spec by putting
its text in Firestore (`Repo.put_spec`) and Surgeon "opened a PR" by
formatting a UUID into a fake URL -- neither ever touched a real
repository. `RepoCheckout` is the seam that makes both real: Author and
Healer write actual `.spec.ts` files onto this checkout's disk so a real
`PlaywrightDriver` (Agent B's `cwd=` wiring) can execute them, and Surgeon
commits a branch here and pushes it before ever asking `app/github.py` to
open a pull request against it.

**Nothing outside `job/` constructs one.** `job/worker.py` is the only
caller of `clone()` in this codebase (see its own `_checkout_factory`) --
every agent (`agents/author.py`, `agents/healer.py`, `agents/surgeon.py`)
only ever receives an already-built `RepoCheckout | None` on
`ctx.checkout` and never mints one itself. That keeps "does this workspace
even have a connected repo" a single decision made once, in one place,
rather than something eleven agents would each have to reason about
independently.

**The token never sits in a URL, and every error message is scrubbed of
it.** The GitHub App installation token this module is handed (already
minted by `app/github.py`'s `GitHubApp.installation_token` before
`clone()` is ever called) is passed to `git` as an `http.extraHeader`
config value, not embedded in the remote URL -- an embedded-in-URL token
is exactly the shape that leaks into `git`'s own "fatal: repository
'https://x-access-token:ghs_.../...' not found" error text on a bad
clone. Belt-and-braces on top of that: `_run` scrubs the literal token
value out of both the command it echoes back and the subprocess's stderr
before either ever reaches a `CheckoutError` message -- the one thing
this module refuses to do is rely on `core.guards.redact_deep` catching a
token that should never have been in a string in the first place (see
that module's own docstring, and this task's own non-negotiable rule).

**`_remote_url` is a module-level seam, not a method, specifically so the
offline test suite can monkeypatch it.** Real production clones a
`github.com` HTTPS URL; the offline suite (`tests/test_checkout.py`)
points it at a local bare repository instead and never touches a network
-- see that file's own fixtures. Nothing about `RepoCheckout`'s own logic
branches on which one it got.

**Extra state beyond the fixed contract.** The Tier 2 contract fixes
`path` and nine methods; it does not forbid a real implementation from
carrying more. `github`/`repo_full_name`/`default_branch` are plain
attributes `job/worker.py`'s factory sets right after `clone()` returns
(never through `clone()`'s own fixed signature, which three agents are
coding against and must not have to change) -- they are what lets
`agents/surgeon.py` call `ctx.checkout.github.open_pull_request(...)`
without this codebase growing a second GitHub client (see
`app/github.py`'s own module docstring on why there must only ever be
one) or a new field on `AgentContext` beyond the `checkout` this task
already owns.
"""

import pathlib
import shutil
import subprocess
import tempfile


class CheckoutError(Exception):
    pass


def _remote_url(repo_full_name: str) -> str:
    """The clone/push URL, token-free -- the token itself travels as an
    `http.extraHeader`, never interpolated into this string. Monkeypatched
    wholesale by the offline test suite to return a local bare repository
    path instead of ever building a `github.com` URL."""
    return f"https://github.com/{repo_full_name}.git"


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _scrub_cmd(cmd: list[str], token: str) -> list[str]:
    return [_scrub(part, token) for part in cmd]


def _run(cmd: list[str], cwd, token: str = "") -> str:
    """The one place this module ever shells out to `git`. `token`, when
    given, is scrubbed out of both the echoed command and `stderr` before
    either can reach a `CheckoutError` -- see the module docstring's own
    rule on why this is defence in depth, not the primary control (the
    primary control is never putting the token in argv/URL in a form
    `git` itself would echo back on failure)."""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd) if cwd is not None else None,
            capture_output=True, text=True, timeout=120, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        safe_cmd = " ".join(_scrub_cmd(cmd, token))
        safe_stderr = _scrub((exc.stderr or "").strip(), token)
        raise CheckoutError(f"git command failed ({safe_cmd}): {safe_stderr}") from None
    except subprocess.TimeoutExpired:
        raise CheckoutError(f"git command timed out: {' '.join(_scrub_cmd(cmd, token))}") from None
    except FileNotFoundError as exc:
        raise CheckoutError(f"git is not available: {exc}") from None


_SPEC_SUFFIXES = (".spec.ts", ".spec.js")


class RepoCheckout:
    """A shallow clone of the workspace's connected repo, on the Job's
    disk. Lives for one run. Agents write into it; Surgeon commits and
    pushes a branch from it. Nothing outside job/ constructs one.

    Signature fixed by the Tier 2 contract: `path`, `clone`, `read_file`,
    `write_file`, `list_specs`, `branch`, `commit_all`, `push`,
    `diff_against_head`, `cleanup`. Everything else on this class
    (`github`, `repo_full_name`, `default_branch`, `token`) is additive
    state `job/worker.py`'s factory sets after construction -- see the
    module docstring's own note on why.
    """

    def __init__(self, path, *, token: str = "", github=None,
                 repo_full_name: str = "", default_branch: str = "main"):
        self.path = pathlib.Path(path)
        self.token = token
        self.github = github
        self.repo_full_name = repo_full_name
        self.default_branch = default_branch
        # The commit this checkout started at -- `diff_against_head`'s own
        # baseline, captured once, right after `clone()`'s own checkout
        # completes, so it stays valid whether that method is called
        # before or after `branch()`/`commit_all()`.
        self._base_sha = ""

    @classmethod
    def clone(cls, repo_full_name: str, token: str, ref: str = "") -> "RepoCheckout":
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="plumbline-checkout-"))
        cmd = ["git", "-c", f"http.extraHeader=Authorization: Bearer {token}",
               "clone", "--depth", "1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [_remote_url(repo_full_name), str(tmp)]
        try:
            _run(cmd, cwd=None, token=token)
        except CheckoutError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

        checkout = cls(tmp, token=token, repo_full_name=repo_full_name,
                        default_branch=ref or "main")
        checkout._base_sha = _run(["git", "rev-parse", "HEAD"], cwd=tmp).strip()
        return checkout

    # -- file access -----------------------------------------------------

    def _resolve(self, rel: str) -> pathlib.Path:
        root = self.path.resolve()
        target = (self.path / rel).resolve()
        if target != root and root not in target.parents:
            raise CheckoutError(f"{rel!r} escapes the checkout root")
        return target

    def read_file(self, rel: str) -> str:
        return self._resolve(rel).read_text()

    def write_file(self, rel: str, content: str) -> None:
        target = self._resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def list_specs(self) -> list[str]:
        return sorted(
            p.relative_to(self.path).as_posix()
            for p in self.path.rglob("*")
            if p.is_file()
            and ".git" not in p.relative_to(self.path).parts
            and p.name.endswith(_SPEC_SUFFIXES)
        )

    # -- git plumbing ------------------------------------------------------

    def branch(self, name: str) -> None:
        _run(["git", "checkout", "-b", name], cwd=self.path)

    def commit_all(self, message: str) -> str:
        _run(["git", "add", "-A"], cwd=self.path)
        _run([
            "git", "-c", "user.email=plumbline[bot]@users.noreply.github.com",
            "-c", "user.name=plumbline[bot]", "commit", "-m", message,
        ], cwd=self.path)
        return _run(["git", "rev-parse", "HEAD"], cwd=self.path).strip()

    def push(self) -> None:
        _run([
            "git", "-c", f"http.extraHeader=Authorization: Bearer {self.token}",
            "push", "-u", "origin", "HEAD",
        ], cwd=self.path, token=self.token)

    def diff_against_head(self) -> str:
        if not self._base_sha:
            return ""
        return _run(["git", "diff", self._base_sha], cwd=self.path)

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
