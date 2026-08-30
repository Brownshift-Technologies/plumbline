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


def test_verify_returns_false_not_raise_on_non_string_signature():
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    entry = lg.entries("ws1")[0]
    lg._repo.store.put("ledger", entry["id"], {**entry, "signature": 12345})
    assert lg.verify("ws1") is False


def test_verify_returns_false_not_raise_on_unserialisable_detail():
    # A `detail` dict containing a value json.dumps can't encode (a set) --
    # exactly the shape a tampered or buggy write could produce.
    lg = _ledger()
    lg.append("ws1", "runner", "run.step", {"i": 0})
    entry = lg.entries("ws1")[0]
    lg._repo.store.put("ledger", entry["id"], {**entry, "detail": {"bad": {1, 2, 3}}})
    assert lg.verify("ws1") is False
