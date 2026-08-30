import time as time_module

import core.fakes as fakes
from gateway.ledger import Ledger
from app.repo import Repo
from app.settings import PlumblineConfig
from core.fakes import FakeFirestore


def _config():
    return PlumblineConfig(
        project_id="t",
        location="us-central1",
        vertex_location="global",
        model="gemini-3.5-flash",
        firestore_prefix="plumbline",
    )


def _ledger():
    return Ledger(Repo(_config(), client=FakeFirestore()))


def _ledger_with_fake():
    fake = FakeFirestore()
    return Ledger(Repo(_config(), client=fake)), fake


# --- from the brief -------------------------------------------------------


def test_first_entry_chains_from_genesis():
    lg = _ledger()
    sig = lg.append("ws1", "surgeon", "pr.open", {"pr": 2211})
    assert len(sig) == 64


def test_each_entry_chains_to_the_previous():
    lg = _ledger()
    lg.append("ws1", "chaos", "net.fault", {"ms": 240})
    lg.append("ws1", "surgeon", "pr.open", {"pr": 2211})
    entries = lg.entries("ws1")
    assert entries[1]["prev"] == entries[0]["signature"]


def test_verify_passes_on_an_untouched_chain():
    lg = _ledger()
    for i in range(4):
        lg.append("ws1", "runner", "run.step", {"i": i})
    assert lg.verify("ws1") is True


def test_verify_fails_when_an_entry_is_edited():
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    lg.append("ws1", "runner", "run.step", {"i": 1})
    first = lg.entries("ws1")[0]
    lg._repo.store.put("ledger", first["id"], {**first, "action": "run.tamper"})
    assert lg.verify("ws1") is False


def test_chains_are_per_workspace():
    lg = _ledger()
    lg.append("ws1", "a", "x", {})
    sig2 = lg.append("ws2", "a", "x", {})
    assert lg.entries("ws2")[0]["prev"] == Ledger.GENESIS
    assert sig2 != lg.entries("ws1")[0]["signature"]


# --- extra coverage: what an attacker or a real bug would try ------------


def test_genesis_constant_is_used_for_the_very_first_entry():
    # Not just "some 64-char string" -- the actual GENESIS constant, so a
    # future change to its value can't silently drift the two apart.
    lg = _ledger()
    lg.append("ws1", "a", "x", {})
    assert lg.entries("ws1")[0]["prev"] == Ledger.GENESIS


def test_verify_fails_when_a_middle_entry_is_deleted():
    # Store has no delete op, so we reach into the fake's backing dict
    # directly to simulate what a real deletion (or a lost write) would
    # look like: the chain has a hole in it.
    lg, fake = _ledger_with_fake()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    lg.append("ws1", "runner", "run.step", {"i": 1})
    lg.append("ws1", "runner", "run.step", {"i": 2})
    middle = lg.entries("ws1")[1]
    del fake.data[f"plumbline_ledger/{middle['id']}"]
    assert lg.verify("ws1") is False


def test_verify_fails_when_entries_are_reordered():
    # A cheap reorder attack: swap the `seq` field of two otherwise-untouched
    # entries without recomputing their signatures. Because `seq` is part of
    # the signed payload, this must be caught -- reordering isn't free just
    # because the individual entries weren't edited.
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    lg.append("ws1", "runner", "run.step", {"i": 1})
    e0, e1 = lg.entries("ws1")
    lg._repo.store.put("ledger", e0["id"], {**e0, "seq": e1["seq"]})
    lg._repo.store.put("ledger", e1["id"], {**e1, "seq": e0["seq"]})
    assert lg.verify("ws1") is False


def test_verify_fails_when_two_entries_collide_on_seq():
    # Ledger.append has no transactional guard around read-then-write, so a
    # race (two concurrent appends both computing seq = len(existing)) can
    # land two entries on the same seq. Simulate that by forging a sibling
    # entry that also claims to chain from the real first entry. Both
    # entries individually have valid signatures; verify() must still
    # reject the chain because only one of them can validly occupy the
    # "next" link after the real first entry.
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    lg.append("ws1", "runner", "run.step", {"i": 1})
    e0, e1 = lg.entries("ws1")

    forged_payload = {
        "workspace_id": "ws1",
        "actor": "attacker",
        "action": "evil",
        "detail": {},
        "seq": e1["seq"],
        "at": 0.0,
    }
    forged_signature = Ledger._sign(e0["signature"], forged_payload)
    forged = {
        **forged_payload,
        "id": "forged",
        "prev": e0["signature"],
        "signature": forged_signature,
    }
    lg._repo.store.put("ledger", "forged", forged)
    assert lg.verify("ws1") is False


def test_verify_is_insensitive_to_detail_key_insertion_order():
    # json.dumps(sort_keys=True) is there for exactly this: a `detail` dict
    # whose keys were re-inserted in a different order (e.g. by a store
    # round-trip) is the SAME payload, and must still verify -- otherwise
    # verify() would flag untouched entries as tampered.
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"z": 1, "a": 2, "m": 3})
    entry = lg.entries("ws1")[0]
    reordered_detail = {"m": 3, "a": 2, "z": 1}
    assert reordered_detail == entry["detail"]  # same content
    assert list(reordered_detail) != list(entry["detail"])  # different key order
    lg._repo.store.put("ledger", entry["id"], {**entry, "detail": reordered_detail})
    assert lg.verify("ws1") is True


def test_verify_returns_false_not_raise_on_missing_field():
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    entry = lg.entries("ws1")[0]
    broken = {k: v for k, v in entry.items() if k != "action"}
    lg._repo.store.put("ledger", entry["id"], broken)
    assert lg.verify("ws1") is False


def test_verify_returns_false_on_a_plain_non_string_signature():
    # `!=` never raises comparing a str to an int, so this one would pass
    # even with the isinstance() guard deleted -- it's here to pin that a
    # mundane type-confused signature (an int landing where a hex string
    # belongs) is rejected, not to exercise the guard itself. See
    # test_verify_rejects_a_signature_object_that_lies_about_equality below
    # for a case where the guard is the only thing standing between a
    # forged entry and a false "verified".
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    entry = lg.entries("ws1")[0]
    lg._repo.store.put("ledger", entry["id"], {**entry, "signature": 12345})
    assert lg.verify("ws1") is False


def test_verify_rejects_a_signature_object_that_lies_about_equality():
    # Storage is just a dict -- nothing stops a "signature" from being an
    # object instead of a string. An object whose __eq__ always returns
    # True defeats a bare `!=` comparison: `computed_sig != signature`
    # falls back to `signature.__ne__(computed_sig)` (str doesn't know how
    # to compare itself to this type), which Python derives from __eq__ as
    # `not True` = False -- i.e. "equal", no matter what the real signature
    # is. That is exactly what verify()'s isinstance(signature, str) check
    # exists to block *before* any such comparison runs. Delete that check
    # and this test fails, unlike the plain-int case above.
    class _AlwaysEqual:
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    entry = lg.entries("ws1")[0]
    lg._repo.store.put("ledger", entry["id"], {**entry, "signature": _AlwaysEqual()})
    assert lg.verify("ws1") is False


def test_verify_returns_false_not_raise_on_unserialisable_detail():
    # A `detail` dict containing a value json.dumps can't encode (a set) --
    # exactly the shape a tampered or buggy write could produce.
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    entry = lg.entries("ws1")[0]
    lg._repo.store.put("ledger", entry["id"], {**entry, "detail": {"bad": {1, 2, 3}}})
    assert lg.verify("ws1") is False


# --- fix round 1: transactional append + redaction -----------------------


def test_append_redacts_actor_action_and_detail_before_signing():
    # actor/action/detail are free-form data an agent can put anything into.
    # They must be redacted before they land in an append-only, queryable
    # collection -- and the signature must cover what's actually stored, or
    # verify() would fail on every entry the moment redaction changed
    # anything.
    lg = _ledger()
    lg.append(
        "ws1",
        "reach sam@example.com",
        "note sam@example.com",
        {"note": "email sam@example.com", "nested": {"contact": "sam@example.com"}},
    )
    entry = lg.entries("ws1")[0]
    assert entry["actor"] == "reach [EMAIL]"
    assert entry["action"] == "note [EMAIL]"
    assert entry["detail"] == {"note": "email [EMAIL]", "nested": {"contact": "[EMAIL]"}}
    assert lg.verify("ws1") is True


def test_concurrent_appends_to_one_workspace_do_not_fork_the_chain():
    # Mirrors tests/test_core_store.py::test_two_interleaved_appends_both_survive,
    # for the failure this task's review sharpened: append_audit's race
    # *drops* a write and leaves a consistent trail; Ledger.append's race
    # (before this fix) could FORK the chain -- two entries, same seq, each
    # individually validly signed, and nothing but verify() would ever
    # notice.
    #
    # Writer A reads the ws1 head pointer inside its transaction; before A
    # writes anything, writer B runs a whole append to completion against
    # the same (still-empty) head. A's commit must then abort -- the head
    # version it read is stale -- and the decorator re-runs A's transaction
    # against the head B just wrote, landing A at seq=1 chained from B's
    # signature rather than forking a second seq=0.
    fake = FakeFirestore()
    lg_a = Ledger(Repo(_config(), client=fake))
    lg_b = Ledger(Repo(_config(), client=fake))

    original_get = fakes.FakeDoc.get
    interleaved = []

    def get_then_let_the_other_writer_in(self, transaction=None):
        snapshot = original_get(self, transaction=transaction)
        if self._path == "plumbline_ledger_head/ws1" and not interleaved:
            interleaved.append(True)
            lg_b.append("ws1", "b", "race.b", {})
        return snapshot

    fakes.FakeDoc.get = get_then_let_the_other_writer_in
    try:
        lg_a.append("ws1", "a", "race.a", {})
    finally:
        fakes.FakeDoc.get = original_get

    assert interleaved == [True], "the interleaving never happened"
    entries = lg_a.entries("ws1")
    assert [e["seq"] for e in entries] == [0, 1]
    assert [e["actor"] for e in entries] == ["b", "a"]  # b's commit landed first
    assert entries[1]["prev"] == entries[0]["signature"]
    assert lg_a.verify("ws1") is True


# --- Step 0a: timestamp must not drift on retry ---------------------------


def test_the_recorded_time_does_not_drift_across_a_retry(monkeypatch):
    """A contended append must record when the caller acted, not which
    attempt won. Interleave a competing writer, then assert the retried
    entry's `at` matches the clock reading taken before the first attempt.

    Same interleaving trick as
    test_concurrent_appends_to_one_workspace_do_not_fork_the_chain above:
    writer B's whole append runs to completion from inside writer A's first
    head read, forcing A's commit to abort (the head version A read is now
    stale) and the decorator to re-run A's `_append` closure. A ticking fake
    clock means attempt 1 and the retry would disagree on `at` if the
    timestamp were captured inside that closure instead of once, up front,
    in `append` itself -- which is exactly the bug Step 0a fixes.
    """
    fake = FakeFirestore()
    lg_a = Ledger(Repo(_config(), client=fake))
    lg_b = Ledger(Repo(_config(), client=fake))

    ticks = iter([100.0, 200.0, 300.0, 400.0, 500.0])
    monkeypatch.setattr(time_module, "time", lambda: next(ticks))

    original_get = fakes.FakeDoc.get
    interleaved = []

    def get_then_let_the_other_writer_in(self, transaction=None):
        snapshot = original_get(self, transaction=transaction)
        if self._path == "plumbline_ledger_head/ws1" and not interleaved:
            interleaved.append(True)
            lg_b.append("ws1", "b", "race.b", {})
        return snapshot

    monkeypatch.setattr(fakes.FakeDoc, "get", get_then_let_the_other_writer_in)

    lg_a.append("ws1", "a", "race.a", {})

    assert interleaved == [True], "the interleaving never happened"
    entries = {e["actor"]: e for e in lg_a.entries("ws1")}
    # b's append (the only clock read besides a's own first one) consumed
    # the second tick; a's retried entry must still carry the FIRST tick,
    # read before a's transaction ever began -- not a third or later tick
    # from whichever attempt happened to be the one that committed.
    assert entries["b"]["at"] == 200.0
    assert entries["a"]["at"] == 100.0
