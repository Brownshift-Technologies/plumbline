"""Opt-in: shells out to a REAL `npx playwright test --reporter=json` and
drives `PlaywrightDriver.run_spec` against real, throwaway `.spec.ts`
files -- instead of `FakeBrowser.run_spec`, which every other test in this
codebase (including `tests/test_runner.py`'s own Runner suite) exercises
in its place. Skipped by default, same pattern `tests/test_playwright_live.py`
and `tests/test_oauth_live.py` already establish for "needs a real external
thing this default suite must not depend on".

This needs MORE than `tests/test_playwright_live.py` does: that file only
needs Python's own Chromium (`playwright install chromium`); this one also
needs Node, `npx`, and a real `@playwright/test` install -- a second,
independent runtime this repo already carries for `web/e2e/`'s own suite
(`cd web && npm install`), which is exactly what `_checkout` below reuses
via `PLUMBLINE_PLAYWRIGHT_TEST_HOME` (see `agents/browser.py`'s
`_ensure_local_playwright_test` for why a SYMLINKED `node_modules`, not a
second real install, is what makes that safe: two distinct installs of the
identical version of `@playwright/test` do not interoperate).

Run explicitly, on a machine with Playwright's Chromium installed AND
`web/node_modules` present (`cd web && npm install`):

    PLUMBLINE_LIVE_RUN_SPEC_TESTS=1 pytest tests/test_playwright_run_spec_live.py
"""

import json
import os
import pathlib
import shutil

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("PLUMBLINE_LIVE_RUN_SPEC_TESTS"),
    reason="opt-in: set PLUMBLINE_LIVE_RUN_SPEC_TESTS=1, Node, and `cd web && npm install` to run",
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_WEB_NODE_MODULES = _REPO_ROOT / "web" / "node_modules"

_PASS = """\
import { test, expect } from '@playwright/test';
test('a passing test', async ({ page }) => {
  await page.setContent('<h1>Hello</h1>');
  await expect(page.locator('h1')).toHaveText('Hello');
});
"""

# An `expect()` failure -- Runner must route this to `kind="assertion"`
# and NEVER to Healer (repairing an assertion would hide the real bug the
# customer is paying this platform to find). The `console.log` earns this
# spec a real console.log artefact too (see the artefact-completeness test
# below); `_FAIL_SELECTOR` deliberately has none, so a failing spec with
# nothing to say on stdout/stderr is exercised as well.
_FAIL_ASSERTION = """\
import { test, expect } from '@playwright/test';
test('a failing assertion', async ({ page }) => {
  console.log('about to make an assertion that will fail');
  await page.setContent('<h1>Hello</h1>');
  await expect(page.locator('h1')).toHaveText('Goodbye');
});
"""

# A raw locator action timing out with NO `expect()` involved -- Runner
# must route this to `kind="selector"`, Healer's whole reason to exist.
_FAIL_SELECTOR = """\
import { test, expect } from '@playwright/test';
test('a raw locator timeout', async ({ page }) => {
  await page.setContent('<h1>Hello</h1>');
  await page.locator('#does-not-exist').click({ timeout: 2000 });
});
"""

# Syntactically broken -- never reaches a single `test()` execution.
_WONT_PARSE = """\
import { test, expect } from '@playwright/test';
test('this file has a syntax error', async ({ page }) => {
  await page.setContent(<<<<not valid js at all
});
"""


@pytest.fixture
def checkout(tmp_path):
    """A throwaway checkout: real `.spec.ts` files on disk, plus this
    repo's own `web/node_modules` reachable the same way
    `_ensure_local_playwright_test` makes a real customer checkout with no
    Playwright deps of its own reachable in production -- see that
    function's docstring for why a symlink, not `NODE_PATH`, is what keeps
    the CLI and the spec's own `import` resolving to ONE physical install."""
    if not _WEB_NODE_MODULES.is_dir():
        pytest.skip(f"{_WEB_NODE_MODULES} not present -- run `cd web && npm install` first")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "pass.spec.ts").write_text(_PASS)
    (tmp_path / "specs" / "fail_assertion.spec.ts").write_text(_FAIL_ASSERTION)
    (tmp_path / "specs" / "fail_selector.spec.ts").write_text(_FAIL_SELECTOR)
    (tmp_path / "specs" / "wont_parse.spec.ts").write_text(_WONT_PARSE)
    os.environ["PLUMBLINE_PLAYWRIGHT_TEST_HOME"] = str(_WEB_NODE_MODULES)
    try:
        yield tmp_path
    finally:
        del os.environ["PLUMBLINE_PLAYWRIGHT_TEST_HOME"]


@pytest.fixture
def driver(checkout):
    from agents.browser import PlaywrightDriver

    return PlaywrightDriver(cwd=checkout)


def test_a_passing_spec_reports_passed_with_no_artefacts(driver):
    result = driver.run_spec("specs/pass.spec.ts")
    assert result["passed"] is True
    assert result["status"] == "passed"
    assert result["error"] == ""
    assert result["artefacts"] == {"video": "", "trace": "", "har": "", "console": ""}


def test_a_failing_expect_reports_matcher_true(driver):
    # This is the load-bearing pair (with the selector test below): the one
    # thing nothing in this codebase had ever verified against a real
    # Playwright process before this task.
    result = driver.run_spec("specs/fail_assertion.spec.ts")
    assert result["passed"] is False
    assert result["status"] == "failed"
    assert result["matcher"] is True
    assert "expect(" in result["error"].lower() or "tohavetext" in result["error"].lower()


def test_a_raw_locator_timeout_reports_matcher_false(driver):
    result = driver.run_spec("specs/fail_selector.spec.ts")
    assert result["passed"] is False
    assert result["status"] == "failed"
    assert result["matcher"] is False


def test_a_spec_that_fails_to_load_raises_instead_of_returning_failed(driver):
    # Point 5 of the brief: a load failure is a `crash`, not a `failed`
    # assertion -- `agents/runner.py`'s `_run_one` only ever produces
    # `kind="crash"` from a RAISED exception (see that module), so this
    # driver must raise here, never return `{"passed": False, ...}`.
    with pytest.raises(Exception):
        driver.run_spec("specs/wont_parse.spec.ts")


def test_a_failing_spec_writes_all_four_artefact_kinds_and_a_passing_one_writes_zero_files(driver):
    passing = driver.run_spec("specs/pass.spec.ts")
    failing = driver.run_spec("specs/fail_assertion.spec.ts")

    for kind in ("video", "trace", "har", "console"):
        path = failing["artefacts"][kind]
        assert path, f"{kind} artefact path was empty for a failing spec"
        assert pathlib.Path(path).is_file(), f"{kind} artefact does not exist on disk: {path}"

    assert passing["artefacts"] == {"video": "", "trace": "", "har": "", "console": ""}

    # All four kinds land in the SAME per-call artefact directory --
    # cleaned up here rather than left on disk for every live-suite run.
    shutil.rmtree(pathlib.Path(failing["artefacts"]["video"]).parent, ignore_errors=True)


def test_a_hung_locator_is_killed_by_the_driver_s_own_subprocess_timeout(checkout):
    """The subprocess timeout is its own watchdog, independent of
    Runner's batch-level one -- point 3 of the brief. A 90s default is far
    too slow for a *test*, so this constructs its own driver with a tiny
    `spec_timeout_s` and a spec that waits far longer than that, then
    asserts the call still returns (not hangs) with a `timedOut` result."""
    import time

    from agents.browser import PlaywrightDriver

    (checkout / "specs" / "hangs.spec.ts").write_text(
        "import { test } from '@playwright/test';\n"
        "test('hangs forever', async ({ page }) => {\n"
        "  await page.setContent('<h1>Hi</h1>');\n"
        "  await page.waitForTimeout(120000);\n"
        "});\n"
    )
    driver = PlaywrightDriver(cwd=checkout, spec_timeout_s=5.0)
    start = time.monotonic()
    result = driver.run_spec("specs/hangs.spec.ts")
    elapsed = time.monotonic() - start

    assert result["status"] == "timedOut"
    assert result["passed"] is False
    assert elapsed < 30, f"the driver's own kill did not bound the call (took {elapsed}s)"


if __name__ == "__main__":
    # `python3 tests/test_playwright_run_spec_live.py` -- prints the four
    # parsed dicts the task report asks for, exactly as `run_spec` returns
    # them, without pytest's own output swallowing the detail.
    os.environ.setdefault("PLUMBLINE_LIVE_RUN_SPEC_TESTS", "1")
    import tempfile

    from agents.browser import PlaywrightDriver

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "specs").mkdir()
        (root / "specs" / "pass.spec.ts").write_text(_PASS)
        (root / "specs" / "fail_assertion.spec.ts").write_text(_FAIL_ASSERTION)
        (root / "specs" / "fail_selector.spec.ts").write_text(_FAIL_SELECTOR)
        (root / "specs" / "wont_parse.spec.ts").write_text(_WONT_PARSE)
        os.environ["PLUMBLINE_PLAYWRIGHT_TEST_HOME"] = str(_WEB_NODE_MODULES)
        d = PlaywrightDriver(cwd=root)
        for name in ("pass", "fail_assertion", "fail_selector"):
            print(f"--- specs/{name}.spec.ts ---")
            print(json.dumps(d.run_spec(f"specs/{name}.spec.ts"), indent=2))
        print("--- specs/wont_parse.spec.ts ---")
        try:
            d.run_spec("specs/wont_parse.spec.ts")
        except Exception as exc:  # noqa: BLE001
            print(f"raised {type(exc).__name__}: {exc}")
