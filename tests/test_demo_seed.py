"""Task 15: the seeded demo workspace."""


def test_seeding_twice_does_not_duplicate(repo, config):
    from seed.demo import seed_demo

    seed_demo(repo, config)
    seed_demo(repo, config)
    assert len(repo.runs_for_workspace(config.demo_workspace_id)) == 18


def test_the_seeded_workspace_has_the_gated_patch(repo, config):
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    finding = next(f for f in repo.findings_for_workspace(ws.id) if "twice" in f.title)
    assert repo.patch_for_finding(finding.id).gate_state == "awaiting_approval"


def test_the_demo_workspace_is_flagged(repo, config):
    from seed.demo import seed_demo

    assert seed_demo(repo, config).is_demo is True


# --- beyond the required three: matching the approved design exactly ------


def test_seeding_produces_seventeen_routes_with_the_designed_coverage(repo, config):
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    routes = repo.routes_for_workspace(ws.id)
    assert len(routes) == 17
    by_path = {r.path: r.coverage_pct for r in routes}
    assert by_path["/"] == 100
    assert by_path["/checkout/payment"] == 58
    assert by_path["/checkout/3ds"] == 0
    assert by_path["/admin/pricing/bulk"] == 8


def test_seeding_produces_exactly_342_behaviours(repo, config):
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    assert len(repo.behaviours_for_workspace(ws.id)) == 342


def test_seeding_produces_seven_findings(repo, config):
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    assert len(repo.findings_for_workspace(ws.id)) == 7


def test_runs_are_numbered_4454_through_4471(repo, config):
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    numbers = sorted(r.number for r in repo.runs_for_workspace(ws.id))
    assert numbers == list(range(4454, 4472))


def test_run_4471_carries_its_seven_step_reasoning_chain(repo, config):
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    run = next(r for r in repo.runs_for_workspace(ws.id) if r.number == 4471)
    steps = repo.steps_for_run(run.id)
    assert [s.agent for s in steps] == [
        "cartographer", "author", "healer", "chaos", "runner", "triager", "surgeon",
    ]
    assert steps[-1].outcome == "gated"


def test_the_seeded_run_4471_links_to_the_gated_double_charge_finding(repo, config):
    """The single most important path in the demo: a judge opens run 4471
    and must see the gated double-charge patch behind "Approve and
    merge". That only happens if `GET /api/runs/{run_4471_id}` can name
    the finding it produced -- which means the seed itself has to set
    `Finding.run_id`, not just write both rows unlinked next to each
    other. See `app/repo.py`'s `finding_for_run` and
    `app/run_routes.py`'s `finding_id`."""
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    run = next(r for r in repo.runs_for_workspace(ws.id) if r.number == 4471)

    finding = repo.finding_for_run(run.id)
    assert finding is not None
    assert "twice" in finding.title

    patch = repo.patch_for_finding(finding.id)
    assert patch is not None
    assert patch.gate_state == "awaiting_approval"


def test_reseeding_restores_the_pristine_fixture(repo, config):
    """`seed_demo` is called again on every FRESH `POST /api/auth/demo`
    entry (`app/main.py`'s `seed_demo_if_missing`) -- never mid-session
    for a visitor who is already in one (that route is only hit once, to
    issue the session in the first place). So the one shared demo
    workspace resetting to this exact pristine fixture on every new
    visitor's entry -- undoing whatever a previous visitor approved,
    rejected, or edited -- is the intended behaviour, not a bug:
    `seed_demo` is idempotent in the "always produces the same result"
    sense, not the "never overwrites a later mutation" sense. This is
    what keeps every demo visitor's very first look at the product
    looking exactly like `design/preview.html`, regardless of what the
    last visitor left behind."""
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    finding = next(f for f in repo.findings_for_workspace(ws.id) if "twice" in f.title)
    patch = repo.patch_for_finding(finding.id)
    repo.put_patch(type(patch)(**{**patch.__dict__, "gate_state": "merged"}))

    seed_demo(repo, config)

    assert repo.patch_for_finding(finding.id).gate_state == "awaiting_approval"


def test_finding_ages_match_the_design_exactly(repo, config):
    """Fix round 1: the original seed used `now - offset * 3600` (0-6
    hours across all seven findings) -- off by two orders of magnitude
    from `design/preview.html`'s own Findings table (22 min / 2 days / 3
    days / 4 days / 9 days). A judge opening Findings must see materially
    the same data the approved design shows."""
    import time

    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    now = time.time()
    by_title = {f.title: f for f in repo.findings_for_workspace(ws.id)}

    def age_days(title: str) -> float:
        return (now - by_title[title].at) / 86400

    assert age_days("A retried payment charges the customer twice") < (1 / 24)  # well under an hour
    assert 1.9 < age_days("Changing a password doesn't end other sessions") < 2.1
    assert 2.9 < age_days("Cart total drifts a cent when currency changes") < 3.1
    assert 3.9 < age_days("Order history paginates past the last page") < 4.1
    assert 8.9 < age_days("Admin pricing table sorts unstably") < 9.1


# --- Task 17e: the seeded ledger is no longer empty -----------------------


def test_the_seeded_ledger_has_entries(repo, config):
    from gateway.ledger import Ledger
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    entries = Ledger(repo).entries(ws.id)
    # "30-60, enough that checked: N is convincing, not so much the page
    # is noise" -- the brief's own range.
    assert 30 <= len(entries) <= 60


def test_the_seeded_chain_verifies_intact(repo, config):
    from gateway.ledger import Ledger
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    ledger = Ledger(repo)
    assert ledger.verify(ws.id) is True
    assert len(ledger.entries(ws.id)) > 0


def test_seeding_twice_does_not_fork_or_double_the_chain(repo, config):
    from gateway.ledger import Ledger
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    ledger = Ledger(repo)
    first = ledger.entries(ws.id)

    seed_demo(repo, config)

    second = ledger.entries(ws.id)
    assert len(second) == len(first)
    assert [e["id"] for e in second] == [e["id"] for e in first]
    assert ledger.verify(ws.id) is True


def test_the_gate_refusal_is_in_the_ledger(repo, config):
    """The entry that matters most: Surgeon's `pr.merge` on the payments
    file, held for a human -- the ledger proving the gate actually held,
    not just the UI asserting it."""
    from gateway.ledger import Ledger
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    entries = Ledger(repo).entries(ws.id)
    gate = next(
        e for e in entries
        if e["actor"] == "surgeon" and e["action"] == "pr.merge"
        and e["detail"].get("target") == "src/checkout/payment-client.ts"
    )
    assert gate["detail"]["decision"] == "gated"
    assert "human" in gate["detail"]["reason"]


def test_a_blocked_call_is_in_the_ledger_not_only_allows(repo, config):
    """A ledger of nothing but allows is not evidence of governance."""
    from gateway.ledger import Ledger
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    entries = Ledger(repo).entries(ws.id)
    denied = next(
        e for e in entries
        if e["actor"] == "chaos" and e["action"] == "env.write"
        and e["detail"].get("target") == "production"
    )
    assert denied["detail"]["decision"] == "blocked"

    decisions = {e["detail"].get("decision") for e in entries if "decision" in e["detail"]}
    assert "allowed" in decisions
    assert "blocked" in decisions
    assert "gated" in decisions


def test_a_redacted_entry_shows_redaction_happened(repo, config):
    """One seeded entry's detail would have carried a live card number --
    Ledger.append redacts before signing, so neither the raw card number
    nor the chain's own integrity survives the seed intact by accident."""
    from gateway.ledger import Ledger
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    ledger = Ledger(repo)
    entries = ledger.entries(ws.id)
    redacted = next(e for e in entries if "excerpt" in e["detail"])

    assert "4111111111111111" not in redacted["detail"]["excerpt"]
    assert "[CARD]" in redacted["detail"]["excerpt"]
    assert ledger.verify(ws.id) is True


def test_entry_timestamps_match_the_runs_they_belong_to(repo, config):
    """Ledger entries dated 1970, or all identical, would undercut the
    same credibility the finding-age fix round above already protects."""
    import time

    from gateway.ledger import Ledger
    from seed.demo import seed_demo

    ws = seed_demo(repo, config)
    now = time.time()
    entries = Ledger(repo).entries(ws.id)

    ats = {e["at"] for e in entries}
    assert len(ats) > 1  # not all-identical
    assert all(now - 11 * 86400 < a <= now for a in ats)  # nothing at epoch 0

    gate = next(
        e for e in entries
        if e["actor"] == "surgeon" and e["action"] == "pr.merge"
    )
    # Run 4471 started ~22 minutes ago; the gate is near the end of it.
    age_minutes = (now - gate["at"]) / 60
    assert 0 <= age_minutes < 22

    # seq order must match chronological (`at`) order -- the chain follows
    # insertion order, so those two orderings have to agree.
    assert [e["seq"] for e in entries] == sorted(e["seq"] for e in entries)
    assert [e["at"] for e in entries] == sorted(e["at"] for e in entries)
