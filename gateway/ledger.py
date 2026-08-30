"""Hash-chained, append-only audit ledger.

Each workspace has its own chain of entries stored in the `ledger`
collection. Every entry signs its own payload together with the previous
entry's signature (`prev`), so tampering with any entry -- editing it,
deleting it, or reordering the chain -- breaks the signature link for
every entry that follows. The first entry of a workspace chains from
`Ledger.GENESIS` rather than a real signature.

`core.store.Store` has no delete and no guaranteed ordering (see
`core/store.py`), so `entries()` re-sorts by the client-assigned `seq` on
every read, and nothing here relies on write order.

`verify()` is the integrity check a real attacker or a real bug would hit
first -- a tampered or malformed document -- so it is written to return
`False` on anything it cannot make sense of rather than raise. Raising
out of an integrity check would turn a tampered ledger into a crash
instead of a detected tamper.
"""

import hashlib
import json
import time
import uuid

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
        existing = self.entries(workspace_id)
        prev = existing[-1]["signature"] if existing else self.GENESIS
        payload = {
            "workspace_id": workspace_id,
            "actor": actor,
            "action": action,
            "detail": detail,
            "seq": len(existing),
            "at": time.time(),
        }
        signature = self._sign(prev, payload)
        entry = {**payload, "id": uuid.uuid4().hex, "prev": prev, "signature": signature}
        self._repo.store.put("ledger", entry["id"], entry)
        return signature

    def verify(self, workspace_id: str) -> bool:
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
