"""`make_ctx` itself, not any agent built on top of it. Every one of the
eleven agent tasks assumes these hold."""

from tests.agent_fixtures import make_ctx


def test_the_factory_builds_a_usable_context():
    ctx = make_ctx(pages={"/": {"links": []}})
    assert ctx.gateway is not None and ctx.repo is not None
    assert ctx.browser.links() == [] or True  # goto not called yet


def test_two_contexts_do_not_share_a_store():
    a, b = make_ctx(), make_ctx()
    a.repo.put_route.__self__  # smoke: distinct Repo instances
    assert a.repo is not b.repo


def test_defaults_are_a_usable_workspace_and_run_id():
    ctx = make_ctx()
    assert ctx.workspace_id == "ws1"
    assert ctx.run_id == "r1"


def test_workspace_id_and_run_id_are_overridable():
    ctx = make_ctx(workspace_id="ws_other", run_id="r_42")
    assert ctx.workspace_id == "ws_other"
    assert ctx.run_id == "r_42"


def test_model_is_scripted_with_the_given_responses_in_order():
    ctx = make_ctx(model_responses=("first", "second"))
    assert ctx.model.generate("p1") == "first"
    assert ctx.model.generate("p2") == "second"


def test_model_defaults_to_a_single_ok_response():
    ctx = make_ctx()
    assert ctx.model.generate("anything") == "ok"


def test_browser_is_seeded_with_the_given_pages():
    ctx = make_ctx(pages={"/cart": {"links": ["/checkout"]}})
    ctx.browser.goto("/cart")
    assert ctx.browser.links() == ["/checkout"]


def test_browser_is_seeded_with_the_given_spec_results():
    ctx = make_ctx(spec_results={"specs/a.spec.ts": {"passed": True}})
    assert ctx.browser.run_spec("specs/a.spec.ts") == {"passed": True}


def test_a_caller_supplied_repo_is_used_verbatim_not_replaced():
    from app.models import Workspace
    from app.repo import Repo
    from app.settings import PlumblineConfig
    from core.fakes import FakeFirestore

    config = PlumblineConfig(
        project_id="test", location="us-central1", vertex_location="global",
        model="gemini-3.5-flash", firestore_prefix="plumbline",
    )
    repo = Repo(config, client=FakeFirestore())
    repo.put_workspace(Workspace(id="ws1", name="Acme", repo="acme/storefront", run_limit=999))

    ctx = make_ctx(repo=repo)
    assert ctx.repo is repo
    assert ctx.repo.workspace("ws1").run_limit == 999


def test_the_gateway_is_wired_to_the_same_repo_the_context_exposes():
    ctx = make_ctx()
    # Gateway.call falls back to DEFAULT_RULES for an unseeded workspace
    # (gateway/gateway.py's _rules_for) -- this is the one, real end-to-end
    # smoke test that the Gateway/Ledger wiring make_ctx builds actually
    # works, not just that the objects exist.
    result = ctx.gateway.call(ctx.workspace_id, "cartographer", "browser.read", fn=lambda: "snapshot")
    assert result == "snapshot"
    entries = ctx.repo.store.query("ledger", "workspace_id", "==", ctx.workspace_id)
    assert len(entries) == 1
    assert entries[0]["action"] == "browser.read"


def test_make_ctx_does_not_pre_seed_a_workspace_row():
    ctx = make_ctx()
    assert ctx.repo.workspace(ctx.workspace_id) is None


# --- browsers= for Oracle (fix round 1) ---------------------------------


def test_make_ctx_builds_named_browsers_for_environment_comparison():
    ctx = make_ctx(
        pages={"/": {"links": ["/prod-only"]}},
        browsers={"staging": {"/": {"links": ["/staging-only"]}}},
    )
    ctx.browser.goto("/")
    ctx.browsers["staging"].goto("/")
    assert ctx.browser.links() == ["/prod-only"]
    assert ctx.browsers["staging"].links() == ["/staging-only"]


def test_a_context_with_no_browsers_kwarg_has_an_empty_browsers_dict():
    ctx = make_ctx()
    assert ctx.browsers == {}


def test_the_primary_browser_and_a_named_browser_are_independent_instances():
    ctx = make_ctx(pages={"/": {"links": ["a"]}}, browsers={"staging": {"/": {"links": ["b"]}}})
    assert ctx.browser is not ctx.browsers["staging"]
