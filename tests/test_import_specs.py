"""Task 14g: `agents.repo_source.import_specs`/`parse_spec_source` --
read-only import, and the three spec shapes Runner's own loader was
fixed to parse: `test.only`, `test.skip`, and a synchronous spec."""

from agents.repo_source import FakeGitHub, import_specs, parse_spec_source

_ASYNC_SPEC = """
import { test, expect } from '@playwright/test';

test.describe('/checkout', () => {
  test('shows an error banner on a declined card', async ({ page }) => {
    await page.goto('/checkout');
  });

  test.only('focuses attention on this one test', async ({ page }) => {
    await page.goto('/checkout');
  });

  test.skip('is not run yet', async ({ page }) => {
    await page.goto('/checkout');
  });
});
"""

_SYNC_SPEC = """
import { test, expect } from '@playwright/test';

test('renders the pricing table with no network calls at all', () => {
  expect(1 + 1).toBe(2);
});
"""


def test_import_parses_a_spec_that_uses_test_only():
    rows = parse_spec_source(_ASYNC_SPEC, "specs/checkout.spec.ts")
    only_row = next(r for r in rows if "only" in r["tags"])
    assert only_row["text"] == "focuses attention on this one test"
    assert only_row["route"] == "/checkout"

    skip_row = next(r for r in rows if "skip" in r["tags"])
    assert skip_row["text"] == "is not run yet"

    plain_row = next(r for r in rows if r["tags"] == ())
    assert plain_row["text"] == "shows an error banner on a declined card"

    assert len(rows) == 3


def test_import_parses_a_synchronous_spec_with_no_await():
    rows = parse_spec_source(_SYNC_SPEC, "specs/pricing.spec.ts")
    assert len(rows) == 1
    assert rows[0]["text"] == "renders the pricing table with no network calls at all"
    # No test.describe() in this file -- route falls back to the filename.
    assert rows[0]["route"] == "/pricing"


def test_import_writes_behaviours_and_never_writes_to_the_repo():
    fake = FakeGitHub(files={
        "specs/checkout.spec.ts": _ASYNC_SPEC,
        "specs/pricing.spec.ts": _SYNC_SPEC,
        "README.md": "# not a spec",
    })
    behaviours = import_specs(fake, "acme/storefront", "main", "ws1")

    assert len(behaviours) == 4  # 3 from checkout + 1 from pricing
    assert all(b.workspace_id == "ws1" for b in behaviours)
    assert all(b.source == "import" for b in behaviours)

    # Nothing in this call sequence ever opened a pull request or wrote
    # a file back to the (fake) repo.
    assert fake.pull_requests == []
    assert fake.files["specs/checkout.spec.ts"] == _ASYNC_SPEC  # untouched


def test_import_ignores_non_spec_files():
    fake = FakeGitHub(files={"README.md": "# not a spec", "src/app.ts": "export {}"})
    assert import_specs(fake, "acme/storefront", "main", "ws1") == []
