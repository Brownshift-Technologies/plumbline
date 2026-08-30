from gateway.policy import decide, SCOPES


# --- from the brief: base scope + human-gate behaviour ---------------------


def test_an_agent_may_use_a_tool_in_its_scope():
    assert decide("cartographer", "browser.read").allowed is True


def test_deny_by_default_for_a_tool_outside_scope():
    d = decide("cartographer", "pr.open")
    assert d.allowed is False and "not in scope" in d.reason


def test_an_unknown_agent_is_denied():
    assert decide("intruder", "browser.read").allowed is False


def test_payments_merge_needs_a_human():
    d = decide("surgeon", "pr.merge", "src/checkout/payment-client.ts")
    assert d.allowed is False and d.needs_human is True


def test_a_non_payments_merge_does_not_need_a_human():
    d = decide("surgeon", "pr.merge", "src/catalog/list.ts")
    assert d.allowed is True and d.needs_human is False


def test_chaos_may_not_write_to_production():
    assert decide("chaos", "env.write", "prod-eu-west-1").allowed is False


def test_chaos_may_write_to_staging():
    assert decide("chaos", "env.write", "staging").allowed is True


def test_every_agent_in_the_fleet_has_a_scope():
    assert set(SCOPES) == {"cartographer", "author", "healer", "chaos",
                           "runner", "triager", "surgeon"}


# --- from the brief: the rules mechanism ------------------------------------


def test_custom_rules_can_gate_a_path_the_defaults_allow():
    rules = [{"tool": "pr.merge", "pattern": "src/catalog/*", "effect": "human"}]
    d = decide("surgeon", "pr.merge", "src/catalog/list.ts", rules=rules)
    assert d.allowed is False and d.needs_human is True


def test_custom_rules_cannot_grant_a_tool_outside_scope():
    rules = [{"tool": "pr.merge", "pattern": "*", "effect": "allow"}]
    assert decide("cartographer", "pr.merge", "x", rules=rules).allowed is False


def test_an_empty_rule_list_is_not_the_same_as_none():
    assert decide("surgeon", "pr.merge", "src/checkout/payment-client.ts", rules=[]).allowed is True
    assert decide("surgeon", "pr.merge", "src/checkout/payment-client.ts").needs_human is True


def test_a_deny_rule_blocks_without_a_human_gate():
    rules = [{"tool": "env.write", "pattern": "*", "effect": "deny"}]
    d = decide("chaos", "env.write", "staging", rules=rules)
    assert d.allowed is False and d.needs_human is False


def test_a_malformed_rule_is_ignored_not_fatal():
    assert decide("cartographer", "browser.read", rules=[{"nonsense": True}]).allowed is True


# --- beyond the brief: adversarial / edge-case rules ------------------------


def test_malformed_entries_of_various_shapes_are_all_ignored():
    """Rules are tenant data that will eventually arrive over an API. None
    of these should raise, and none should accidentally grant or block
    anything -- they're all noise `decide()` must skip.
    """
    rules = [
        None,
        42,
        "oops",
        {"tool": "pr.merge"},                                   # missing pattern/effect
        {"tool": "pr.merge", "pattern": "*", "effect": "nope"},  # unknown effect
        {"tool": "pr.merge", "pattern": 7, "effect": "deny"},    # pattern not a str
    ]
    d = decide("surgeon", "pr.merge", "src/catalog/list.ts", rules=rules)
    assert d.allowed is True


def test_a_malformed_rule_does_not_mask_a_real_matching_rule():
    """A bad row must not break every agent -- but it also must not hide a
    perfectly good rule sitting right next to it in the same list.
    """
    rules = [
        {"nonsense": True},
        {"tool": "pr.merge", "pattern": "src/catalog/*", "effect": "deny"},
    ]
    d = decide("surgeon", "pr.merge", "src/catalog/list.ts", rules=rules)
    assert d.allowed is False and d.needs_human is False


def test_a_rule_naming_a_tool_the_call_never_uses_is_inert():
    """A rule for a tool that doesn't exist in any SCOPES entry can never
    match a real call (the tool string it's compared against always comes
    from a `decide()` caller, which already passed the scope check for a
    real tool). It should just sit there unused, not error.
    """
    rules = [{"tool": "sudo.rm", "pattern": "*", "effect": "deny"}]
    assert decide("cartographer", "browser.read", "anything", rules=rules).allowed is True


def test_wildcard_pattern_denies_every_target_for_that_tool():
    rules = [{"tool": "repo.write:src", "pattern": "*", "effect": "deny"}]
    assert decide("surgeon", "repo.write:src", "anything.ts", rules=rules).allowed is False
    assert decide("surgeon", "repo.write:src", "", rules=rules).allowed is False


def test_conflicting_rules_the_stricter_effect_wins():
    rules = [
        {"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "human"},
        {"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "deny"},
    ]
    d = decide("surgeon", "pr.merge", "src/checkout/payment-client.ts", rules=rules)
    assert d.allowed is False and d.needs_human is False


def test_conflicting_rules_list_order_does_not_change_the_outcome():
    rules = [
        {"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "deny"},
        {"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "human"},
    ]
    d = decide("surgeon", "pr.merge", "src/checkout/payment-client.ts", rules=rules)
    assert d.allowed is False and d.needs_human is False


def test_path_traversal_in_target_is_normalized_before_matching():
    """A target that resolves into a gated directory via `..` must still
    trip the gate -- matching the raw, unnormalised string would let an
    attacker (or a buggy caller) describe the payments file in a way that
    looks like an unrelated catalog change.
    """
    d = decide("surgeon", "pr.merge", "src/catalog/../checkout/payment-client.ts")
    assert d.allowed is False and d.needs_human is True


def test_leading_slash_in_a_rule_pattern_is_literal_not_special():
    """No path-resolution semantics leak into rule patterns themselves --
    a pattern is just a glob string, so a leading "/" matches literally and
    has no power to match everything or reach outside the comparison."""
    rules = [{"tool": "browser.read", "pattern": "/etc/*", "effect": "deny"}]
    assert decide("cartographer", "browser.read", "docs/readme.md", rules=rules).allowed is True


def test_thousands_of_rules_still_finds_the_one_that_matches():
    """`decide()` is called on every tool call and must stay a plain
    in-memory scan -- no early-exit shortcut should make it skip a real
    match buried at the end of a very long tenant rule list."""
    noise = [
        {"tool": "pr.merge", "pattern": f"src/noise-{i}/*", "effect": "human"}
        for i in range(5000)
    ]
    rules = noise + [{"tool": "pr.merge", "pattern": "src/checkout/*", "effect": "deny"}]
    d = decide("surgeon", "pr.merge", "src/checkout/payment-client.ts", rules=rules)
    assert d.allowed is False and d.needs_human is False


# --- fix round: Rulings 24-26 from the Task 4 review ------------------------


def test_allow_only_expresses_an_allow_list_as_structured_data():
    """Ruling 24: `allow_only` replaces the old `"!a,b,c"` string DSL."""
    rules = [{"tool": "env.write", "allow_only": ["staging", "staging-*"], "effect": "deny"}]
    assert decide("chaos", "env.write", "staging", rules=rules).allowed is True
    assert decide("chaos", "env.write", "prod-eu-west-1", rules=rules).allowed is False


def test_a_pattern_literally_starting_with_bang_is_now_just_a_literal_glob():
    """The bug Ruling 24 exists to close: with `allow_only` as its own field,
    `pattern` never needs a sigil, so a pattern that happens to start with
    "!" is matched literally instead of silently mismatching everything."""
    rules = [{"tool": "env.write", "pattern": "!staging", "effect": "deny"}]
    assert decide("chaos", "env.write", "!staging", rules=rules).allowed is False
    assert decide("chaos", "env.write", "staging", rules=rules).allowed is True


def test_a_rule_with_both_pattern_and_allow_only_is_malformed():
    rules = [{"tool": "env.write", "pattern": "*", "allow_only": ["staging"], "effect": "deny"}]
    assert decide("chaos", "env.write", "prod-eu-west-1", rules=rules).allowed is True


def test_a_rule_with_neither_pattern_nor_allow_only_is_malformed():
    rules = [{"tool": "env.write", "effect": "deny"}]
    assert decide("chaos", "env.write", "prod-eu-west-1", rules=rules).allowed is True


def test_an_empty_allow_only_list_is_malformed_not_deny_everything():
    rules = [{"tool": "env.write", "allow_only": [], "effect": "deny"}]
    assert decide("chaos", "env.write", "prod-eu-west-1", rules=rules).allowed is True


def test_a_gated_tool_with_no_target_fails_closed_to_human():
    """Ruling 25: the review's Important-1 finding. `pr.merge` is gated by
    DEFAULT_RULES; an empty target matches none of its path globs, and the
    first cut of this module let that fall through to a bare allow -- a
    payments merge whose target extraction failed upstream would have sailed
    through completely ungated."""
    d = decide("surgeon", "pr.merge")
    assert d.allowed is False and d.needs_human is True


def test_a_gated_tool_with_a_blank_target_also_fails_closed():
    d = decide("surgeon", "pr.merge", "   ")
    assert d.allowed is False and d.needs_human is True


def test_an_ungated_tool_with_no_target_is_unaffected():
    assert decide("cartographer", "browser.read").allowed is True


def test_empty_rules_means_no_target_gate_either():
    """`rules=[]` is "explicitly configured no gates" -- that has to cover
    the fail-closed-on-no-target behaviour too, or an empty rule list would
    not actually mean what Task 4's own test says it means."""
    assert decide("surgeon", "pr.merge", rules=[]).allowed is True


def test_backslash_separated_target_is_normalized_like_a_forward_slash_one():
    """Ruling 26. A target spelled with backslashes must trip the same gate
    as its forward-slash form, not evade it by looking like an opaque,
    unrecognised string to a "src/checkout/*" glob."""
    d = decide("surgeon", "pr.merge", "src\\checkout\\payment-client.ts")
    assert d.allowed is False and d.needs_human is True
