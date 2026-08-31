"""Task 14g: the agent-facing repo source -- a spec-file parser for
`POST /api/github/import`, and `FakeGitHub`, the offline test double for
`app/github.GitHubApp`.

**Import is a read, and stays one.** `import_specs` below calls only
`source.list_specs`/`source.read_file` -- both read-only on
`app/github.GitHubApp`'s own contract -- and returns plain dicts for its
caller (`app/github_routes.py`'s `POST /api/github/import`) to turn into
`Behaviour` rows. Nothing in this module ever calls `open_pull_request`
or writes anything back to a customer's repository; a customer's FIRST
action with this integration must not be able to modify their repo. If a
later change to this module ever imports `open_pull_request` at all,
that is the defect worth stopping and re-reading this docstring over.

**The spec parser is regex-based, not a JS/TS AST.** Plumbline has no
JavaScript parser dependency anywhere in this codebase, and importing
one for this alone would be a heavy answer to "find `test(...)` calls in
a source file". `_TEST_CALL` matches `test(...)`, `test.only(...)`, and
`test.skip(...)` -- the exact three forms `job/orchestrator.py`'s own
Runner loader was fixed to parse (see this module's own tests,
`test_import_parses_a_spec_that_uses_test_only`) -- and captures only the
FIRST string-literal argument (the test's title), which is all a
`Behaviour.text` needs. It does not care whether the callback is `async
({ page }) => {...}` or a bare synchronous `() => {...}` (or has no
callback captured at all): the match is on the `test(` CALL, never on
its body, so a synchronous spec parses exactly like an async one --
`test_import_parses_a_synchronous_spec_with_no_await` is what proves
that.
"""

import re

from app.models import Behaviour

_TEST_CALL = re.compile(r"\btest(\.only|\.skip)?\s*\(\s*(['\"`])(.*?)\2")
_DESCRIBE_CALL = re.compile(r"\btest\.describe(?:\.\w+)?\s*\(\s*(['\"`])(.*?)\1")


def _default_route(spec_path: str) -> str:
    """`specs/checkout.spec.ts` -> `/checkout` -- a plain, honest fallback
    for a spec with no `test.describe(...)` header to read a route name
    from. Not a guess dressed up as certainty: a human (or a later Author
    pass) can always edit `Behaviour.route` after import."""
    stem = spec_path.rsplit("/", 1)[-1]
    for suffix in (".spec.ts", ".spec.js"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return f"/{stem}"


def parse_spec_source(content: str, spec_path: str) -> list[dict]:
    """Every `test(...)`/`test.only(...)`/`test.skip(...)` call in
    `content`, as `{"text", "route", "tags", "spec_path"}` dicts -- never
    a `Behaviour` itself (this module does not touch `Repo`; see the
    module docstring). `route` is the first `test.describe(...)` title
    found in the file, applied to every test in it (one route per spec
    file is the common Playwright convention this codebase already
    follows -- see `seed/demo.py`'s own fixtures), or `_default_route`
    when there is none."""
    describe = _DESCRIBE_CALL.search(content)
    route = describe.group(2) if describe else _default_route(spec_path)

    rows = []
    for match in _TEST_CALL.finditer(content):
        modifier, title = match.group(1), match.group(3)
        tags = ("only",) if modifier == ".only" else ("skip",) if modifier == ".skip" else ()
        rows.append({"text": title, "route": route, "tags": tags, "spec_path": spec_path})
    return rows


def import_specs(source, repo: str, ref: str, workspace_id: str) -> list[Behaviour]:
    """Walk every `*.spec.ts`/`*.spec.js` in `repo` at `ref` (via
    `source.list_specs`/`source.read_file` -- `GitHubApp` or `FakeGitHub`,
    both satisfy this) and return one `Behaviour` per parsed test. Pure:
    builds and returns `Behaviour` objects, never calls `repo.put_behaviour`
    itself -- the route (`app/github_routes.py`) owns the one write, so
    that write happens exactly once, in exactly one place, and is trivial
    to audit as "this is the only place import touches Plumbline's own
    store, and it never touches the customer's repo at all"."""
    behaviours = []
    for path in source.list_specs(repo, ref):
        content = source.read_file(repo, path, ref)
        for row in parse_spec_source(content, path):
            behaviours.append(Behaviour(
                id=f"beh_import_{abs(hash((path, row['text']))):x}",
                workspace_id=workspace_id, text=row["text"], route=row["route"],
                spec_path=row["spec_path"], tags=row["tags"], source="import",
            ))
    return behaviours


class FakeGitHub:
    """The offline test double for `app/github.GitHubApp` -- same method
    names and signatures, an in-memory repo instead of api.github.com.
    Every write method (`open_pull_request`) records what it was called
    with (`self.pull_requests`) rather than actually doing anything, so a
    test can assert "this was never called" (import) or inspect exactly
    what branch/files a call would have touched (Surgeon-shaped tests, a
    forward dependency for whichever task next wires an agent up to
    this)."""

    def __init__(self, files: dict[str, str] | None = None, default_branch: str = "main"):
        self.files = dict(files or {})
        self.default_branch = default_branch
        self.pull_requests: list[dict] = []
        self.check_runs: list[dict] = []
        self.tokens_minted = 0
        # Same shape as `GitHubApp._tokens` (installation_id -> (token,
        # expires_at)), so `revoke()` behaves identically for both, and a
        # test that primes/inspects this cache works unchanged against
        # either class.
        self._tokens: dict[str, tuple[str, float]] = {}

    def installation_token(self, installation_id: str) -> str:
        self.tokens_minted += 1
        token = f"ghs_fake{self.tokens_minted:04d}"
        self._tokens[installation_id] = (token, float("inf"))
        return token

    def revoke(self, installation_id: str) -> None:
        self._tokens.pop(installation_id, None)

    def list_installation_repos(self, installation_id: str) -> list[dict]:
        return [{"full_name": "acme/storefront", "default_branch": self.default_branch}]

    def bind(self, repo_full_name: str, installation_id: str) -> None:
        pass  # FakeGitHub resolves nothing by installation; kept for interface parity

    def list_specs(self, repo: str, ref: str) -> list[str]:
        return [p for p in self.files if p.endswith((".spec.ts", ".spec.js"))]

    def read_file(self, repo: str, path: str, ref: str) -> str:
        return self.files[path]

    def pull_request_diff(self, repo: str, number: int) -> str:
        return "--- a/file\n+++ b/file\n"

    def open_pull_request(self, repo: str, branch: str, title: str, body: str, changes: dict, default_branch: str) -> str:
        if branch == default_branch:
            raise ValueError("refusing to open a pull request from the default branch")
        self.pull_requests.append({
            "repo": repo, "branch": branch, "title": title, "body": body,
            "changes": dict(changes), "default_branch": default_branch,
        })
        return f"https://github.com/{repo}/pull/{len(self.pull_requests)}"

    def create_check_run(self, repo: str, sha: str, conclusion: str, summary: str) -> dict:
        run = {"repo": repo, "sha": sha, "conclusion": conclusion, "summary": summary}
        self.check_runs.append(run)
        return run
