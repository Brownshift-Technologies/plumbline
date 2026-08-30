import pytest
from core.fakes import FakeModel


def test_fake_model_returns_scripted_responses_in_order():
    model = FakeModel(["first", "second"])
    assert model.generate("a") == "first"
    assert model.generate("b") == "second"


def test_fake_model_records_calls():
    model = FakeModel(["ok"])
    model.generate("prompt text", images=[b"png-bytes"])
    assert model.calls[0]["prompt"] == "prompt text"
    assert model.calls[0]["images"] == [b"png-bytes"]


def test_fake_model_raises_when_exhausted():
    model = FakeModel(["only"])
    model.generate("a")
    with pytest.raises(AssertionError, match="exhausted"):
        model.generate("b")


def test_fake_firestore_round_trips_a_document():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    client.collection("runs").document("r1").set({"status": "queued"})
    assert client.collection("runs").document("r1").get().to_dict() == {"status": "queued"}


def test_fake_firestore_reports_missing_documents():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    assert client.collection("runs").document("nope").get().exists is False


def test_fake_firestore_exposes_raw_paths_for_assertions():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    client.collection("a11y_runs").document("r1").set({"x": 1})
    assert "a11y_runs/r1" in client.data


# --- FakeCollection.where(): real filtering ----------------------------
# A no-op where() would let a later worker test pass with the production
# filter clause wrong or deleted, so these pin that the filter is applied.


def _seeded_client():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    client.collection("runs").document("r1").set({"status": "queued", "priority": 1})
    client.collection("runs").document("r2").set({"status": "finished", "priority": 5})
    client.collection("runs").document("r3").set({"status": "running", "priority": 3})
    client.collection("other").document("x1").set({"status": "queued", "priority": 9})
    return client


def _ids(snapshots):
    return sorted(snap.id for snap in snapshots)


def test_where_filters_on_equality():
    client = _seeded_client()
    results = client.collection("runs").where(filter=("status", "==", "queued")).stream()
    assert _ids(results) == ["r1"]
    assert results[0].to_dict() == {"status": "queued", "priority": 1}


def test_where_accepts_the_legacy_positional_signature():
    # Still accepted by the real google-cloud-firestore client.
    client = _seeded_client()
    results = client.collection("runs").where("status", "==", "queued").stream()
    assert _ids(results) == ["r1"]


def test_where_accepts_a_real_field_filter_object():
    # This is the shape production code uses, so it is the shape that matters.
    from google.cloud.firestore_v1.base_query import FieldFilter

    client = _seeded_client()
    results = (
        client.collection("runs")
        .where(filter=FieldFilter("status", "==", "queued"))
        .stream()
    )
    assert _ids(results) == ["r1"]


def test_where_supports_in_gte_and_lte():
    client = _seeded_client()
    runs = client.collection("runs")
    assert _ids(runs.where("status", "in", ["queued", "running"]).stream()) == ["r1", "r3"]
    assert _ids(runs.where("priority", ">=", 3).stream()) == ["r2", "r3"]
    assert _ids(runs.where("priority", "<=", 3).stream()) == ["r1", "r3"]


def test_where_returns_a_view_and_leaves_the_collection_unfiltered():
    client = _seeded_client()
    runs = client.collection("runs")
    filtered = runs.where("status", "==", "queued")
    assert filtered is not runs
    assert _ids(filtered.stream()) == ["r1"]
    assert _ids(runs.stream()) == ["r1", "r2", "r3"]


def test_chained_where_calls_compose():
    client = _seeded_client()
    runs = client.collection("runs")
    assert _ids(runs.where("status", "in", ["queued", "running"]).where("priority", ">=", 3).stream()) == ["r3"]
    assert runs.where("status", "==", "queued").where("priority", ">=", 3).stream() == []


def test_where_excludes_documents_missing_the_filtered_field():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    client.collection("runs").document("r1").set({"status": "queued"})
    client.collection("runs").document("r2").set({"note": "no status field"})
    assert _ids(client.collection("runs").where("status", "==", "queued").stream()) == ["r1"]
    # r2 has no "status" at all: it must drop out of an inequality filter
    # rather than raising when the sentinel is compared.
    assert _ids(client.collection("runs").where("status", ">=", "a").stream()) == ["r1"]


def test_where_rejects_unsupported_operators_loudly():
    client = _seeded_client()
    with pytest.raises(ValueError, match="does not support"):
        client.collection("runs").where("status", "!=", "queued")


def test_where_rejects_an_unrecognised_filter_object():
    client = _seeded_client()
    with pytest.raises(TypeError, match="unsupported filter"):
        client.collection("runs").where(filter=object())


# --- snapshot identity --------------------------------------------------


def test_snapshots_expose_their_document_id():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    client.collection("runs").document("r1").set({"status": "queued"})
    assert client.collection("runs").document("r1").get().id == "r1"
    assert [snap.id for snap in client.collection("runs").stream()] == ["r1"]


# --- the store must not alias caller dicts ------------------------------


def test_set_copies_the_payload_so_later_mutation_does_not_rewrite_the_store():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    payload = {"status": "queued", "meta": {"attempts": 0}}
    client.collection("runs").document("r1").set(payload)
    payload["status"] = "finished"
    payload["meta"]["attempts"] = 7
    assert client.collection("runs").document("r1").get().to_dict() == {
        "status": "queued",
        "meta": {"attempts": 0},
    }


def test_to_dict_copies_so_mutating_the_result_does_not_reach_the_store():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    client.collection("runs").document("r1").set({"status": "queued", "meta": {"attempts": 0}})
    snapshot = client.collection("runs").document("r1").get().to_dict()
    snapshot["status"] = "finished"
    snapshot["meta"]["attempts"] = 7
    assert client.collection("runs").document("r1").get().to_dict() == {
        "status": "queued",
        "meta": {"attempts": 0},
    }


# --- transaction shim ---------------------------------------------------
#
# Only what @firestore.transactional drives. These pin the two behaviours
# core.store.append_audit relies on: buffered writes land on commit, and
# a commit whose read set changed underneath raises the real Aborted, which
# is the exception the real decorator retries on.


def test_transaction_commit_applies_buffered_writes():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    doc = client.collection("runs").document("r1")
    transaction = client.transaction()
    transaction._begin()
    transaction.set(doc, {"status": "queued"})
    assert doc.get().to_dict() is None  # buffered, not yet written
    transaction._commit()
    assert doc.get().to_dict() == {"status": "queued"}


def test_transaction_aborts_when_a_read_document_changed_before_commit():
    from google.api_core import exceptions
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    doc = client.collection("runs").document("r1")
    doc.set({"status": "queued"})

    transaction = client.transaction()
    transaction._begin()
    doc.get(transaction=transaction)
    doc.set({"status": "finished"})  # a second writer lands first
    transaction.set(doc, {"status": "clobbered"})

    with pytest.raises(exceptions.Aborted):
        transaction._commit()
    assert doc.get().to_dict() == {"status": "finished"}


def test_transaction_commit_succeeds_when_the_read_document_is_untouched():
    from core.fakes import FakeFirestore

    client = FakeFirestore()
    doc = client.collection("runs").document("r1")
    doc.set({"status": "queued"})

    transaction = client.transaction()
    transaction._begin()
    doc.get(transaction=transaction)
    transaction.set(doc, {"status": "finished"})
    transaction._commit()
    assert doc.get().to_dict() == {"status": "finished"}
