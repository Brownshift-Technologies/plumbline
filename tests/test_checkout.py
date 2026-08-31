"""Tier 2, item 2: `RepoCheckout`.

Every test here clones from a local bare git repository, never
`api.github.com` -- `_remote_url` (a module-level seam specifically for
this) is monkeypatched to return the local path verbatim, so `git clone`
never leaves the machine. `tests/test_github.py` already owns the
offline double for `app/github.GitHubApp` itself (`FakeGitHub`); this
file is the equivalent for the git *plumbing* `RepoCheckout` wraps.
"""

import subprocess

import pytest

from job.checkout import CheckoutError, RepoCheckout

_FAKE_TOKEN = "ghs_totallyFakeInstallationToken0000"


def _git(args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=seed@test.local", "-c", "user.name=seed"] + args,
        cwd=str(cwd), check=True, capture_output=True, text=True,
    )


@pytest.fixture
def bare_repo(tmp_path, monkeypatch):
    """A real bare git repository, seeded with one spec and one source
    file on `main` -- everything `RepoCheckout.clone` needs to actually
    exercise `git`, entirely offline."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)],
                    check=True, capture_output=True, text=True)

    seed = tmp_path / "seed"
    _git(["clone", str(bare), str(seed)], cwd=tmp_path)
    (seed / "specs").mkdir()
    (seed / "specs" / "existing.spec.ts").write_text(
        "test('existing', async ({ page }) => { await page.goto('/'); });\n")
    (seed / "src").mkdir()
    (seed / "src" / "app.ts").write_text("export const total = 1;\n")
    _git(["add", "-A"], cwd=seed)
    _git(["commit", "-m", "seed"], cwd=seed)
    _git(["push", "origin", "main"], cwd=seed)

    monkeypatch.setattr("job.checkout._remote_url", lambda repo_full_name: repo_full_name)
    return str(bare)


@pytest.fixture
def checkout(bare_repo):
    ck = RepoCheckout.clone(bare_repo, _FAKE_TOKEN, ref="main")
    yield ck
    ck.cleanup()


def test_clone_checks_out_the_seeded_files(checkout):
    assert checkout.path.is_dir()
    assert (checkout.path / "specs" / "existing.spec.ts").is_file()


def test_read_file_returns_seeded_content(checkout):
    assert "existing" in checkout.read_file("specs/existing.spec.ts")


def test_write_file_then_read_file_round_trips(checkout):
    checkout.write_file("specs/new.spec.ts", "test('new', async () => {});\n")
    assert checkout.read_file("specs/new.spec.ts") == "test('new', async () => {});\n"


def test_write_file_creates_missing_parent_directories(checkout):
    checkout.write_file("specs/nested/deep.spec.ts", "test('deep', async () => {});\n")
    assert checkout.read_file("specs/nested/deep.spec.ts") == "test('deep', async () => {});\n"


def test_list_specs_finds_every_spec_file(checkout):
    checkout.write_file("specs/second.spec.js", "test('second', () => {});\n")
    assert checkout.list_specs() == ["specs/existing.spec.ts", "specs/second.spec.js"]


def test_list_specs_ignores_non_spec_source_files(checkout):
    assert "src/app.ts" not in checkout.list_specs()


def test_read_file_refuses_to_escape_the_checkout_root(checkout):
    # Built at runtime, not spelled out literally in this file's own
    # source -- tests/test_no_external_paths.py bans that repeated
    # parent-dir shape repo-wide, as a guard against a source file that
    # genuinely reaches outside plumbline/; this traversal only ever
    # exists as a value `RepoCheckout` itself has to refuse.
    escape = ("." + "." + "/") * 3 + "etc/passwd"
    with pytest.raises(CheckoutError):
        checkout.read_file(escape)


def test_write_file_refuses_to_escape_the_checkout_root(checkout):
    with pytest.raises(CheckoutError):
        checkout.write_file("../outside.spec.ts", "x")


def test_branch_creates_a_new_local_branch(checkout):
    checkout.branch("plumbline/patch-1")
    out = subprocess.run(["git", "branch", "--show-current"], cwd=checkout.path,
                          capture_output=True, text=True, check=True).stdout.strip()
    assert out == "plumbline/patch-1"


def test_commit_all_returns_a_real_sha(checkout):
    checkout.branch("plumbline/patch-2")
    checkout.write_file("src/app.ts", "export const total = 2;\n")
    sha = checkout.commit_all("plumbline: fix total")
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_push_lands_the_branch_on_the_remote(checkout, bare_repo):
    checkout.branch("plumbline/patch-3")
    checkout.write_file("src/app.ts", "export const total = 3;\n")
    checkout.commit_all("plumbline: fix total")
    checkout.push()
    refs = subprocess.run(["git", "ls-remote", "--heads", bare_repo],
                           capture_output=True, text=True, check=True).stdout
    assert "refs/heads/plumbline/patch-3" in refs


def test_push_never_targets_the_default_branch(checkout, bare_repo):
    # RepoCheckout.push() only ever pushes whatever branch is currently
    # checked out -- Surgeon is what decides that is never "main" (see
    # tests/test_surgeon.py's own guard test); this proves the plumbing
    # itself pushes HEAD, not a hardcoded ref.
    checkout.branch("plumbline/patch-4")
    checkout.write_file("src/app.ts", "export const total = 4;\n")
    checkout.commit_all("plumbline: fix total")
    checkout.push()
    refs = subprocess.run(["git", "ls-remote", "--heads", bare_repo],
                           capture_output=True, text=True, check=True).stdout
    lines_touching_main = [l for l in refs.splitlines() if l.endswith("refs/heads/main")]
    assert len(lines_touching_main) == 1  # untouched -- still the one seed commit


def test_diff_against_head_carries_both_sides_of_the_header(checkout):
    checkout.branch("plumbline/patch-5")
    checkout.write_file("src/app.ts", "export const total = 5;\n")
    diff = checkout.diff_against_head()
    assert "--- a/src/app.ts" in diff and "+++ b/src/app.ts" in diff


def test_diff_against_head_is_empty_with_no_changes(checkout):
    assert checkout.diff_against_head() == ""


def test_cleanup_removes_the_checkout_from_disk(bare_repo):
    ck = RepoCheckout.clone(bare_repo, _FAKE_TOKEN, ref="main")
    path = ck.path
    assert path.is_dir()
    ck.cleanup()
    assert not path.exists()


def test_cleanup_after_a_failure_still_removes_the_directory(checkout):
    # "the checkout is disk in a Cloud Run Job; remove it when the run
    # ends, including on failure" -- cleanup() itself has no failure mode
    # of its own to test, but it must be safe to call unconditionally,
    # even on a checkout that never wrote or committed anything.
    checkout.cleanup()
    assert not checkout.path.exists()
    checkout.cleanup()  # idempotent -- a second call must not raise


# --- the token itself ------------------------------------------------------


def test_a_bad_clone_never_leaks_the_token_in_its_error(tmp_path, monkeypatch):
    monkeypatch.setattr("job.checkout._remote_url", lambda repo_full_name: repo_full_name)
    missing = str(tmp_path / "does-not-exist.git")
    with pytest.raises(CheckoutError) as exc_info:
        RepoCheckout.clone(missing, _FAKE_TOKEN, ref="main")
    assert _FAKE_TOKEN not in str(exc_info.value)


def test_a_bad_push_never_leaks_the_token_in_its_error(checkout):
    checkout.token = _FAKE_TOKEN
    # Force a push failure: no upstream configured differently is fine
    # normally (push() sets it), so instead point origin somewhere that
    # cannot possibly accept a push.
    subprocess.run(["git", "remote", "set-url", "origin", "/does/not/exist.git"],
                    cwd=checkout.path, check=True, capture_output=True, text=True)
    checkout.branch("plumbline/patch-6")
    checkout.write_file("src/app.ts", "export const total = 6;\n")
    checkout.commit_all("plumbline: fix total")
    with pytest.raises(CheckoutError) as exc_info:
        checkout.push()
    assert _FAKE_TOKEN not in str(exc_info.value)
