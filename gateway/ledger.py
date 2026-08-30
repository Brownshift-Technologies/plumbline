"""Hash-chained, append-only audit ledger.

Each workspace has its own chain of entries stored one-per-document in the
`ledger` collection. Every entry signs its own payload together with the
previous entry's signature (`prev`), so tampering with any entry -- editing
it, deleting it, or reordering the chain -- breaks the signature link for
every entry that follows. The first entry of a workspace chains from
`Ledger.GENESIS` rather than a real signature.

Concurrency: `append` reads and writes a per-workspace head pointer
(`ledger_head/{workspace_id}`, `{"seq": int, "signature": str}`) inside one
Firestore transaction, alongside the new entry document. A plain
get-then-put here would let two concurrent appends both read the same head,
both compute the same seq/prev, and both persist -- not a lost write like
`core.store.append_audit`'s single-document race, but a silent FORK: two
differently-signed entries at the same seq, each individually valid, that
nothing short of `verify()` would ever notice. The head is its own document
(not one document holding every entry, the way `append_audit` shapes its
trail) so a workspace's ledger has no 1 MiB ceiling -- entries stay
one-per-document, so a workspace can accumulate tens of thousands of them.

Redaction: `actor`, `action`, and `detail` are free-form data written by
agent code -- root causes, HAR excerpts, prompts -- headed into a collection
that is queryable and, by design, never edited afterwards. They go through
`core.store._redact` (the same barrier `append_audit` uses) before anything
is signed. The signature covers the REDACTED payload, not the raw one: since
`verify()` re-signs whatever is actually stored, signing the raw payload
would make every entry fail verification the moment redaction changed
anything.

`core.store.Store` has no delete and no guaranteed ordering (see
`core/store.py`), so `entries()` re-sorts by the client-assigned `seq` on
every read, and nothing here relies on write order.

`verify()` is the integrity check a real attacker or a real bug would hit
first -- a tampered or malformed document -- so it is written to return
`False` on anything it cannot make sense of rather than raise. Raising out
of an integrity check would turn a tampered ledger into a crash instead of
a detected tamper.
"""

import hashlib
import json
import time
import uuid

from core.store import _redact

_PAYLOAD_KEYS = ("workspace_id", "actor", "action", "detail", "seq", "at")

# Errors a malformed or tampered ledger document can plausibly raise while
# `verify` reconstructs and re-signs its payload: a missing field (KeyError),
# a `detail` value `json.dumps` cannot serialise -- e.g. a set (TypeError),
# or a `seq` that cannot be compared for sorting (TypeError).
_MALFORMED_ENTRY_ERRORS = (KeyError, TypeError, ValueError)


class Ledger:
    GENESIS = "0" * 64

    def __init__(self, repo):
        self._repo = repo

    @staticmethod
    def _sign(prev: str, payload: dict) -> str:
        # sort_keys=True makes the signature depend only on the payload's
        # content, never on the insertion order of `detail`'s keys -- two
        # dicts with the same keys/values in different orders sign (and
        # therefore verify) identically.
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((prev + body).encode()).hexdigest()

    def entries(self, workspace_id: str) -> list[dict]:
        rows = self._repo.store.query("ledger", "workspace_id", "==", workspace_id)
        return sorted(rows, key=lambda e: e["seq"])

    def append(self, workspace_id: str, actor: str, action: str, detail: dict) -> str:
        from google.cloud import firestore

        redacted_actor = _redact(actor)
        redacted_action = _redact(action)
        redacted_detail = _redact(detail)

        # Captured once, before the transactional closure, and closed over
        # below -- NOT read again inside `_append`. Under contention the
        # decorator re-runs `_append` against the head the winner just wrote
        # (see the comment there), and if `at` were read inside that closure
        # each retry would stamp whichever wall-clock moment its own attempt
        # happened to run at, rather than the moment the caller actually
        # called `append`. This ledger is the audit-of-record: *when*
        # something happened is part of what it certifies, and nothing in
        # the signed payload would distinguish attempt 1's clock from
        # attempt 3's -- the skew would be silent.
        at = time.time()

        head_ref = self._repo.store.doc("ledger_head", workspace_id)
        entry_id = uuid.uuid4().hex
        entry_ref = self._repo.store.doc("ledger", entry_id)

        @firestore.transactional
        def _append(transaction) -> str:
            # The head read and both writes (entry + head) must be one
            # transaction -- see the module docstring for what a plain
            # get-then-put would let through. A conflicting commit aborts
            # here and the decorator re-runs this function against the head
            # the winner just wrote (up to transaction._max_attempts, 5 by
            # default).
            head_snapshot = head_ref.get(transaction=transaction)
            head = head_snapshot.to_dict() if head_snapshot.exists else None
            prev = head["signature"] if head else self.GENESIS
            seq = head["seq"] + 1 if head else 0

            payload = {
                "workspace_id": workspace_id,
                "actor": redacted_actor,
                "action": redacted_action,
                "detail": redacted_detail,
                "seq": seq,
                "at": at,
            }
            signature = self._sign(prev, payload)
            entry = {**payload, "id": entry_id, "prev": prev, "signature": signature}

            transaction.set(entry_ref, entry)
            transaction.set(head_ref, {"seq": seq, "signature": signature})
            return signature

        return _append(self._repo.store.transaction())

    def verify(self, workspace_id: str) -> bool:
        # No caller yet in this codebase -- Task 14c wires this to
        # GET /api/ledger/verify and Task 17d puts a "verify chain" control
        # in the UI on top of that. Not dead code; a forward dependency.
        try:
            chain = self.entries(workspace_id)
        except _MALFORMED_ENTRY_ERRORS:
            return False

        prev = self.GENESIS
        for e in chain:
            try:
                signature = e["signature"]
                if not isinstance(signature, str):
                    return False
                payload = {k: e[k] for k in _PAYLOAD_KEYS}
                if e["prev"] != prev or self._sign(prev, payload) != signature:
                    return False
            except _MALFORMED_ENTRY_ERRORS:
                return False
            prev = signature
        return True
