"""Task 11b: Healer.

Specs are seeded via `ctx.repo.put_spec` and discovered through
`Behaviour` rows carrying a `spec_path` -- the same rows Author (Task 11a)
writes, so Healer's input here is exactly what a real Author run would
have already left behind.
"""

from app.models import Behaviour
from agents.healer import Healer
from tests.agent_fixtures import make_ctx

_BROKEN_CSS = (
    "test('checkout submit', async ({ page }) => {\n"
    "  await page.goto('/checkout');\n"
    "  await page.locator('.btn-pay').click();\n"
    "  await expect(page.getByText('Total: $50')).toBeVisible();\n"
    "});\n"
)

_NEW_LOCATOR = "await page.getByRole('button', { name: 'Pay' }).click();"


def _ctx_drifted(seeded_results, spec_path="specs/checkout.submit.spec.ts",
                  route="/checkout", spec_content=_BROKEN_CSS, model_responses=(_NEW_LOCATOR,)):
    ctx = make_ctx(
        pages={route: {"a11y": [{"ref": "e1", "role": "button", "name": "Pay"}]}},
        spec_results={spec_path: seeded_results},
        model_responses=model_responses,
    )
    ctx.repo.put_spec("ws1", spec_path, spec_content)
    ctx.repo.put_behaviour(Behaviour(
        id="b1", workspace_id="ws1", text="user can submit checkout",
        route=route, spec_path=spec_path))
    return ctx


def test_it_repairs_a_selector_that_no_longer_resolves():
    ctx = _ctx_drifted([
        {"passed": False, "error": "strict mode violation: locator('.btn-pay') resolved to 2 elements"},
        {"passed": True},
    ])
    assert Healer().run(ctx).data["repaired"] == 1


def test_it_leaves_an_assertion_failure_alone():
    ctx = _ctx_drifted([{"passed": False, "error": "expect(received).toBe(expected)"}])
    out = Healer().run(ctx)
    assert out.data["repaired"] == 0, "an assertion failure is a real bug, not drift"
    assert out.data["abandoned"] == []


def test_it_prefers_a_role_based_locator():
    ctx = _ctx_drifted([
        {"passed": False, "error": "no element matches selector '.btn-pay'"},
        {"passed": True},
    ])
    Healer().run(ctx)
    assert "getByRole" in ctx.repo.spec("ws1", "specs/checkout.submit.spec.ts")


def test_a_repair_that_still_fails_is_reverted():
    ctx = _ctx_drifted([
        {"passed": False, "error": "strict mode violation: locator('.btn-pay') resolved to 2 elements"},
        {"passed": False, "error": "strict mode violation: locator resolved to 2 elements"},
    ])
    out = Healer().run(ctx)
    assert out.data["repaired"] == 0
    assert out.data["abandoned"] == ["checkout.submit"]
    # The draft was discarded, not written -- the spec on record is still the original.
    assert ctx.repo.spec("ws1", "specs/checkout.submit.spec.ts") == _BROKEN_CSS


def test_it_never_deletes_a_test():
    ctx = _ctx_drifted([
        {"passed": False, "error": "strict mode violation: locator resolved to 2 elements"},
        {"passed": True},
    ])
    before = len(ctx.repo.specs_for_workspace("ws1"))
    Healer().run(ctx)
    assert len(ctx.repo.specs_for_workspace("ws1")) == before


def test_it_changes_one_locator_per_failure_not_the_whole_file():
    ctx = _ctx_drifted([
        {"passed": False, "error": "strict mode violation: locator resolved to 2 elements"},
        {"passed": True},
    ])
    Healer().run(ctx)
    new = ctx.repo.spec("ws1", "specs/checkout.submit.spec.ts")
    old_lines = _BROKEN_CSS.splitlines()
    new_lines = new.splitlines()
    changed = [i for i in range(len(old_lines)) if old_lines[i] != new_lines[i]]
    assert changed == [2]  # only the .locator('.btn-pay') line, nothing else


# --- point 7: what a real app does to Healer ------------------------------


def test_a_selector_matching_two_elements_is_repaired():
    # Exact literal phrasing Playwright itself uses for this case.
    ctx = _ctx_drifted([
        {"passed": False, "error": "strict mode violation: locator('.btn-pay') resolved to 2 elements"},
        {"passed": True},
    ])
    assert Healer().run(ctx).data["repaired"] == 1


def test_a_control_whose_role_changed_but_name_did_not_is_still_repaired():
    # The old locator asked for a link named "Checkout"; the control is
    # now a button with the same name. Healer doesn't need special code
    # for this -- the current a11y() tree already reflects the new role,
    # and the model (scripted here) is what proposes the corrected one.
    content = (
        "test('checkout link', async ({ page }) => {\n"
        "  await page.goto('/checkout');\n"
        "  await page.getByRole('link', { name: 'Checkout' }).click();\n"
        "});\n"
    )
    ctx = _ctx_drifted(
        [
            {"passed": False, "error": "no element matches getByRole('link', { name: 'Checkout' })"},
            {"passed": True},
        ],
        spec_content=content,
        model_responses=["await page.getByRole('button', { name: 'Checkout' }).click();"],
    )
    Healer().run(ctx)
    new = ctx.repo.spec("ws1", "specs/checkout.submit.spec.ts")
    assert "getByRole('button', { name: 'Checkout' })" in new


def test_a_timeout_unrelated_to_a_locator_is_left_alone():
    # A bare test timeout -- the page never finished loading, a network
    # call hung -- is not "Timeout .* waiting for locator" and must not be
    # treated as drift.
    ctx = _ctx_drifted([{"passed": False, "error": "Test timeout of 30000ms exceeded."}])
    out = Healer().run(ctx)
    assert out.data["repaired"] == 0
    assert out.data["abandoned"] == []


def test_a_locator_timeout_is_repaired():
    ctx = _ctx_drifted([
        {"passed": False, "error": "Timeout 5000ms exceeded waiting for locator('.btn-pay') to be visible"},
        {"passed": True},
    ])
    assert Healer().run(ctx).data["repaired"] == 1


def test_it_never_rewrites_an_assertion_line():
    # The spec fails on the ACTION locator (drift), while an unrelated
    # `expect(...)` line elsewhere also constructs a locator. Only the
    # action line may change.
    ctx = _ctx_drifted([
        {"passed": False, "error": "strict mode violation: locator('.btn-pay') resolved to 2 elements"},
        {"passed": True},
    ])
    Healer().run(ctx)
    new = ctx.repo.spec("ws1", "specs/checkout.submit.spec.ts")
    assert "expect(page.getByText('Total: $50')).toBeVisible()" in new
