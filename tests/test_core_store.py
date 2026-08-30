from core import fakes
from core.config import load_config
from core.fakes import FakeFirestore
from core import store as store_module
from core.store import Store


def test_put_prefixes_collection_name():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.put("runs", "r1", {"status": "queued"})
    assert "a11y_runs/r1" in client.data


def test_get_returns_none_for_missing_doc():
    store = Store(load_config(prefix="a11y"), client=FakeFirestore())
    assert store.get("runs", "missing") is None


def test_get_round_trips():
    store = Store(load_config(prefix="a11y"), client=FakeFirestore())
    store.put("runs", "r1", {"status": "done"})
    assert store.get("runs", "r1") == {"status": "done"}


def test_append_audit_redacts_pii_and_orders_entries():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit("run-1", {"step": "intake", "note": "email sam@example.com"})
    store.append_audit("run-1", {"step": "verify", "note": "clean"})
    trail = store.audit_trail("run-1")
    assert [e["step"] for e in trail] == ["intake", "verify"]
    assert trail[0]["note"] == "email [EMAIL]"
    assert trail[0]["seq"] == 0
    assert trail[1]["seq"] == 1


def test_audit_trail_is_empty_for_unknown_run():
    store = Store(load_config(prefix="a11y"), client=FakeFirestore())
    assert store.audit_trail("nope") == []


# --- query(): coverage ruled in by the controller -----------------------
#
# The brief's five tests never call query(), which is the only Store method
# that goes through the fake's filtering. These pin that the filter actually
# discriminates, that prefixing isolates collections, that a non-equality
# operator works, and that no match is an empty list.


def test_query_returns_only_matching_documents():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.put("runs", "r1", {"status": "queued"})
    store.put("runs", "r2", {"status": "finished"})
    store.put("runs", "r3", {"status": "queued"})
    results = store.query("runs", "status", "==", "queued")
    assert sorted(r["status"] for r in results) == ["queued", "queued"]
    assert len(results) == 2
    # A non-filtering store would return all three documents here, including
    # the "finished" one -- this assertion is what a broken filter breaks.
    assert all(r["status"] == "queued" for r in results)


def test_query_excludes_documents_in_a_different_prefixed_collection():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    other_store = Store(load_config(prefix="other"), client=client)
    store.put("runs", "r1", {"status": "queued"})
    other_store.put("runs", "x1", {"status": "queued"})
    results = store.query("runs", "status", "==", "queued")
    assert len(results) == 1
    assert results == [{"status": "queued"}]


def test_query_supports_the_gte_operator():
    # The only query() test that exercises a non-equality operator.
    # (That the fake honours a real FieldFilter is already pinned by
    # tests/test_fakes.py::test_where_accepts_a_real_field_filter_object.)
    store = Store(load_config(prefix="a11y"), client=FakeFirestore())
    store.put("runs", "r1", {"priority": 1})
    store.put("runs", "r2", {"priority": 5})
    assert store.query("runs", "priority", ">=", 3) == [{"priority": 5}]


def test_query_returns_an_empty_list_when_nothing_matches():
    # Callers branch on the empty result, so pin it: no match is [], not None
    # and not an error.
    store = Store(load_config(prefix="a11y"), client=FakeFirestore())
    store.put("runs", "r1", {"status": "finished"})
    assert store.query("runs", "status", "==", "queued") == []


# --- review fixes: concurrency, deep redaction, malformed audit docs ----


def test_two_interleaved_appends_both_survive(monkeypatch):
    # Writer A reads the trail; writer B completes a whole append before A
    # writes. Before the fix A's set() replaced the document and B's entry
    # vanished, leaving a contiguous seq that hid the loss. Now A's commit
    # aborts, the transaction re-runs against the fresh trail, and both land.
    client = FakeFirestore()
    writer_a = Store(load_config(prefix="a11y"), client=client)
    writer_b = Store(load_config(prefix="a11y"), client=client)

    original_get = fakes.FakeDoc.get
    interleaved = []

    def get_then_let_the_other_writer_in(self, transaction=None):
        snapshot = original_get(self, transaction=transaction)
        if self._path == "a11y_audit/run-1" and not interleaved:
            interleaved.append(True)
            writer_b.append_audit("run-1", {"step": "second"})
        return snapshot

    monkeypatch.setattr(fakes.FakeDoc, "get", get_then_let_the_other_writer_in)
    writer_a.append_audit("run-1", {"step": "first"})

    assert interleaved == [True], "the interleaving never happened"
    trail = writer_a.audit_trail("run-1")
    assert [e["step"] for e in trail] == ["second", "first"]
    assert [e["seq"] for e in trail] == [0, 1]


def test_append_audit_redacts_pii_nested_in_lists_dicts_tuples_and_bytes():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit(
        "run-1",
        {
            "step": "intake",
            "note": "email sam@example.com",
            "findings": ["contact bob@example.com", "ssn 123-45-6789"],
            "patient": {"email": "carol@example.com", "phone": "415-555-0132"},
            "raw": b"email dave@example.com",
            "tuple_field": ("eve@example.com",),
        },
    )
    stored = client.data["a11y_audit/run-1"]["entries"][0]
    assert stored == {
        "step": "intake",
        "note": "email [EMAIL]",
        "findings": ["contact [EMAIL]", "ssn [SSN]"],
        "patient": {"email": "[EMAIL]", "phone": "[PHONE]"},
        "raw": b"email [EMAIL]",
        "tuple_field": ("[EMAIL]",),
        "seq": 0,
    }


def test_append_audit_redacts_pii_deep_inside_nested_containers():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit(
        "run-1",
        {"results": [{"contacts": [{"value": "reach sam@example.com"}]}]},
    )
    stored = client.data["a11y_audit/run-1"]["entries"][0]
    assert stored["results"] == [{"contacts": [{"value": "reach [EMAIL]"}]}]


def test_append_audit_redacts_dict_keys_and_keeps_colliding_values():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit("run-1", {"sam@example.com": 1, "bob@example.com": 2})
    stored = client.data["a11y_audit/run-1"]["entries"][0]
    assert stored == {"[EMAIL]": 1, "[EMAIL]#2": 2, "seq": 0}


def test_append_audit_drops_bytes_it_cannot_decode():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit("run-1", {"blob": b"\xff\xfe\x00binary"})
    stored = client.data["a11y_audit/run-1"]["entries"][0]
    assert stored["blob"] == "[BINARY:9B]"


def test_caller_supplied_seq_cannot_forge_the_counter():
    store = Store(load_config(prefix="a11y"), client=FakeFirestore())
    store.append_audit("run-1", {"step": "intake", "seq": 99})
    store.append_audit("run-1", {"step": "verify", "seq": 99})
    assert [e["seq"] for e in store.audit_trail("run-1")] == [0, 1]


def test_append_audit_keeps_other_fields_on_the_audit_document():
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.put("audit", "run-1", {"owner": "a11y-worker"})
    store.append_audit("run-1", {"step": "intake"})
    assert client.data["a11y_audit/run-1"]["owner"] == "a11y-worker"
    assert [e["step"] for e in store.audit_trail("run-1")] == ["intake"]


def test_audit_trail_is_empty_for_a_document_without_entries():
    # put("audit", ...) is public, so this document shape is reachable; the
    # read path every demo renders must not raise on it.
    store = Store(load_config(prefix="a11y"), client=FakeFirestore())
    store.put("audit", "run-1", {"owner": "a11y-worker"})
    assert store.audit_trail("run-1") == []


def test_append_audit_redacts_pii_inside_sets_and_stores_them_as_lists():
    # A set is not a leaf, and Firestore does not treat it as one either:
    # firestore_v1/_helpers.encode_value encodes list/tuple/set/frozenset alike
    # as an ArrayValue, so an unredacted set persists. Deduplicating findings
    # into a set is a shape agent code produces naturally.
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit(
        "run-1",
        {
            "emails": {"sam@example.com", "bob@example.com"},
            "frozen": frozenset({"ssn 123-45-6789"}),
            "nested": {"inner": [{"tags": {"carol@example.com"}}]},
        },
    )
    stored = client.data["a11y_audit/run-1"]["entries"][0]
    # Stored as lists, sorted here only because set iteration order is not
    # stable across runs. Two members that redact to the same string must both
    # survive -- a set would have deduplicated one away.
    assert sorted(stored["emails"]) == ["[EMAIL]", "[EMAIL]"]
    assert stored["frozen"] == ["ssn [SSN]"]
    assert stored["nested"] == {"inner": [{"tags": ["[EMAIL]"]}]}
    assert stored["seq"] == 0


def test_append_audit_redacts_bytes_dict_keys():
    # protobuf accepts a bytes key where MapValue wants a string field name, so
    # a bytes key carrying PII really would persist as that field's name.
    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit("run-1", {b"sam@example.com": 1, b"bob@example.com": 2})
    stored = client.data["a11y_audit/run-1"]["entries"][0]
    assert stored == {"[EMAIL]": 1, "[EMAIL]#2": 2, "seq": 0}


class _PathOnlyClient:
    """Just enough client for DocumentReference._document_path to resolve.

    ``_get_document_path`` reads only ``client._database_string``; nothing
    about this test needs a real Firestore client.
    """

    _database_string = "projects/proj/databases/(default)"


def _reference(*segments):
    from google.cloud.firestore_v1 import DocumentReference

    return DocumentReference(*segments, client=_PathOnlyClient())


def test_append_audit_redacts_pii_in_a_document_reference_path():
    # encode_value checks getattr(value, "_document_path", None) *before* its
    # list and dict branches and stores the result as a reference_value, so an
    # unredacted reference persists its whole path -- including a document ID
    # that is an email address, which Firestore permits. Carrying a reference
    # to the record just read is a shape agent code produces naturally.
    from google.cloud.firestore_v1 import _helpers

    ref = _reference("patients", "sam@example.com")
    # The exposure this closes: unredacted, the address persists in full.
    assert _helpers.encode_value(ref).reference_value.endswith("sam@example.com")

    client = FakeFirestore()
    store = Store(load_config(prefix="a11y"), client=client)
    store.append_audit("run-1", {"subject": ref, "seen": [_reference("cases", "ssn 123-45-6789")]})
    stored = client.data["a11y_audit/run-1"]["entries"][0]
    assert stored["subject"] == "projects/proj/databases/(default)/documents/patients/[EMAIL]"
    assert stored["seen"] == ["projects/proj/databases/(default)/documents/cases/ssn [SSN]"]
    # And what is stored is now a plain string, not a live address a reader
    # could dereference or write back through.
    assert "@" not in _helpers.encode_value(stored["subject"]).string_value


def test_redact_leaves_a_client_less_reference_alone_instead_of_raising():
    # DocumentReference._document_path raises ValueError without a client, and
    # getattr's default does not swallow that. encode_value reads the same
    # property and raises the same error, so such a reference never reaches
    # storage either way -- _redact just must not be what breaks the run.
    from google.cloud.firestore_v1 import DocumentReference

    ref = DocumentReference("patients", "sam@example.com")
    assert store_module._redact(ref) is ref


def test_redact_warns_when_an_unhandled_but_encodable_type_passes_through(caplog):
    # GeoPoint is not a type _redact knows, and Firestore encodes it happily
    # (geo_point_value), so it persists unscanned. The warning is the only
    # thing that makes such a leak visible without a reviewer finding it.
    from google.cloud.firestore_v1._helpers import GeoPoint

    point = GeoPoint(37.7749, -122.4194)
    with caplog.at_level("WARNING", logger="core.store"):
        assert store_module._redact(point) is point
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "google.cloud.firestore_v1._helpers.GeoPoint" in message
    assert "unhandled type" in message


def test_redact_does_not_warn_for_the_scalars_it_deliberately_passes_through(caplog):
    # The warning is only worth anything if it is not noise: every audit entry
    # carries integers and timestamps, and warning on those would bury the one
    # line that matters.
    import datetime

    with caplog.at_level("WARNING", logger="core.store"):
        assert store_module._redact(
            {"n": 1, "ok": True, "score": 0.5, "none": None, "at": datetime.datetime(2026, 1, 1)}
        ) == {"n": 1, "ok": True, "score": 0.5, "none": None, "at": datetime.datetime(2026, 1, 1)}
    assert caplog.records == []
