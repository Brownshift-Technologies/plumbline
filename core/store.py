from core.config import Config
from core.guards import redact_deep

# The audit trail is the reasoning chain all three demos render, so it is kept
# as one document per run holding an ordered `entries` list, each entry stamped
# with a client-assigned `seq`. One read returns the whole trail already
# ordered: no server timestamps to tie-break, no composite index to keep, and
# no way for a display to show entries out of order.
#
# What that shape costs, and what it does not:
#
# - Every append rewrites the whole trail, so N entries cost O(N^2) bytes
#   written. Fine for a demo run of tens of entries; not a log sink.
# - Firestore caps a document at 1 MiB (1,048,576 bytes), so a run's trail has
#   a hard ceiling. A run that could exceed it needs per-entry documents.
# - A single document is the smallest unit Firestore can split across servers,
#   so sustained concurrent writes to one trail show up as contention and
#   latency. (Firestore's current guidance states this as hotspotting, not as a
#   fixed per-second quota -- there is no published per-document write-rate
#   number to design against.)
# - Concurrent appends do not lose entries silently: append_audit reads and
#   writes inside a transaction, so a losing writer aborts and re-runs instead
#   of overwriting. That holds up to the retry ceiling. Past it the entry is
#   not written at all, loudly: google.cloud.firestore_v1 retries an aborted
#   transaction transaction._max_attempts times (defaulting to
#   base_transaction.MAX_ATTEMPTS = 5 in google-cloud-firestore 2.28.1, the
#   version in uv.lock), then rolls back and raises
#   ValueError("Failed to commit transaction in 5 attempts.") chained from the
#   last Aborted. append_audit lets that propagate and no caller in this repo
#   catches it -- deliberate for a demo, where a dropped audit entry should
#   stop the run rather than leave a trail with an invisible hole.

# `_redact` used to be its own recursive implementation here -- dict/list/
# tuple/set/bytes/Firestore-reference walking, PII on every string leaf,
# never raising, warning on anything it didn't recognise. The Gateway (fix
# round 1 on that task) needed the exact same walk for a `.read` tool's
# structured result, and keeping a second, independent copy of "walk a
# nested structure looking for strings" in gateway.py -- or anywhere else
# that needed it next -- is how the two quietly drift apart. So the whole
# implementation moved to core.guards.redact_deep (full behaviour and
# docstring there), and this is now a plain alias: every call site below,
# and gateway/ledger.py's `from core.store import _redact`, keeps working
# unchanged, but there is exactly one implementation behind it.
_redact = redact_deep


class Store:
    def __init__(self, config: Config, client=None):
        self._prefix = config.firestore_prefix
        self._project_id = config.project_id
        # A caller-supplied client (every test in this codebase, via
        # `core.fakes.FakeFirestore`) is stored as-is. `None` means "build
        # the real Firestore client", deferred to `_client` below rather
        # than done here -- see that property for why eager construction
        # is a defect, not just a style choice.
        self._client_override = client
        self._real_client = None

    @property
    def _client(self):
        """The Firestore client this `Store` reads and writes through.

        Building the real client is deferred to first use rather than done
        in `__init__`. `google.cloud.firestore.Client.__init__` resolves
        Application Default Credentials immediately and raises
        `DefaultCredentialsError` the instant none are configured -- so an
        eager build means merely *constructing* a `Store(config)` with no
        `client=` override fails in any environment without live GCP
        credentials: a CI runner, a fresh dev sandbox, this repo's own test
        collection. That is precisely what `app.main`'s module-level
        `app = build_app()` does at *import* time (so `uvicorn
        app.main:app` has something to serve) -- discovered here because an
        eager `Store` made `from app.main import build_app`, which
        `tests/conftest.py` needs, crash before pytest could collect a
        single test. Deferring the credential lookup to first actual read
        or write leaves every real call site's behaviour unchanged (it
        still needs working credentials the moment it touches Firestore for
        real) while letting the module import -- and a `Store`/`Repo`
        construct -- cleanly in an environment that never ends up needing
        the real client at all, which describes every test in this suite.
        """
        if self._client_override is not None:
            return self._client_override
        if self._real_client is None:
            from google.cloud import firestore

            self._real_client = firestore.Client(project=self._project_id)
        return self._real_client

    def _name(self, collection: str) -> str:
        return f"{self._prefix}_{collection}"

    def put(self, collection: str, doc_id: str, data: dict) -> None:
        self._client.collection(self._name(collection)).document(doc_id).set(data)

    def get(self, collection: str, doc_id: str) -> dict | None:
        snapshot = self._client.collection(self._name(collection)).document(doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def query(self, collection: str, field: str, op: str, value) -> list[dict]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        collection_ref = self._client.collection(self._name(collection))
        results = collection_ref.where(filter=FieldFilter(field, op, value)).stream()
        return [doc.to_dict() for doc in results]

    def all(self, collection: str) -> list[dict]:
        """Every document in `collection`, unfiltered -- `query()` with no
        `where()` clause at all rather than a degenerate filter, because
        there is no field guaranteed present on every row to filter on
        trivially-true. Added for `Repo.artefact_count()` (Task 12a), whose
        whole-run "how many artefacts did this run write in total" answer
        has no single field value to scope a `query()` call to. Same cost
        profile as any other unindexed collection scan in this module --
        fine at this product's scale, not a pattern to reach for on a
        collection meant to grow unbounded.
        """
        return [doc.to_dict() for doc in self._client.collection(self._name(collection)).stream()]

    def doc(self, collection: str, doc_id: str):
        """A raw (prefixed) document reference for a caller that needs
        transactional get/set across more than one document -- `put`/`get`
        each cover exactly one document with no atomicity between them.

        Currently used only by the Ledger's head-pointer transaction
        (gateway/ledger.py), which reads and writes a `ledger_head` document
        alongside a `ledger` entry document in the same transaction so two
        concurrent appends cannot both win the same `seq`.
        """
        return self._client.collection(self._name(collection)).document(doc_id)

    def transaction(self):
        """A new Firestore transaction, for use with `doc()` and
        `@firestore.transactional` (see `append_audit` below for the pattern,
        and gateway/ledger.py for a second, generic use of it)."""
        return self._client.transaction()

    def append_audit(self, run_id: str, entry: dict) -> None:
        # Deferred like the imports above, so importing core.store needs
        # no GCP credentials.
        from google.cloud import firestore

        doc_ref = self._client.collection(self._name("audit")).document(run_id)
        redacted = _redact(entry)

        @firestore.transactional
        def append(transaction) -> None:
            # The read and the write must be one transaction. A plain
            # get-then-set loses entries: two writers that read the same
            # snapshot both compute seq = len(entries), and the second set() --
            # a whole-document replace, not a merge -- drops the first writer's
            # entry. Worse, seq stays contiguous afterwards, so the trail looks
            # well-formed and nothing downstream can tell an entry vanished.
            # Inside a transaction the second commit aborts instead, and the
            # decorator re-runs this function against the fresh trail (up to
            # transaction._max_attempts, 5 by default).
            snapshot = doc_ref.get(transaction=transaction)
            record = snapshot.to_dict() if snapshot.exists else {}
            entries = list(record.get("entries") or [])
            # seq is stamped here -- after redaction, inside the transaction --
            # so a caller-supplied "seq" cannot forge the counter, and a retry
            # renumbers against the trail it just re-read.
            entries.append(redacted | {"seq": len(entries)})
            record["entries"] = entries
            transaction.set(doc_ref, record)

        append(self._client.transaction())

    def audit_trail(self, run_id: str) -> list[dict]:
        record = self.get("audit", run_id)
        # .get("entries", []): put("audit", ...) is public, so an audit document
        # without an entries list is reachable, and this is the read path every
        # demo renders -- it must not raise.
        return record.get("entries", []) if record else []
