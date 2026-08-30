"""Task 14c: `GET /api/ledger`, `GET /api/ledger/verify`, `GET /api/ledger.csv`.

`GET /api/ledger/verify` is what makes "append-only" a claim anyone can
test rather than one this product merely asserts -- it runs
`Ledger.verify` (already written; Task 8's own docstring calls this route
out by name as a forward dependency) and reports `{"intact": bool,
"checked": n}`.

`GET /api/ledger.csv` streams. `csv.writer` cannot write to an async
generator directly (it wants a file-like `.write()`), so `_Sink` below is
the smallest thing that satisfies that interface while actually handing
each written row straight back to the generator -- one row is ever held
in memory at a time, never the whole export. See `core.store.Store`'s
own module docstring for why a workspace's ledger has no row-count
ceiling in the first place (`gateway/ledger.py`: one document per entry,
not one document holding a growing list) -- an export that materialised
the whole thing first would reintroduce exactly the ceiling the storage
shape was chosen to avoid.
"""

import csv
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.deps import current_session

# No `prefix=` -- `GET /api/ledger.csv` is a sibling of `/api/ledger`, not
# a child path under it (`/api/ledger/ledger.csv` would be the wrong
# shape), so every route below spells its own full path instead.
router = APIRouter()

_MAX_PAGE_SIZE = 200
_DEFAULT_PAGE_SIZE = 50
_CSV_FIELDS = ("seq", "at", "actor", "action", "target", "decision", "detail")


class _Sink:
    """A `.write()` target `csv.writer` can use that hands each finished
    line straight back out, instead of buffering into a `StringIO` (which
    would defeat the whole point of streaming)."""

    def __init__(self):
        self.value = ""

    def write(self, s: str) -> None:
        self.value = s


def _entry_row(entry: dict) -> dict:
    detail = entry.get("detail") or {}
    return {
        "seq": entry.get("seq"), "at": entry.get("at"), "actor": entry.get("actor"),
        "action": entry.get("action"), "target": detail.get("target", ""),
        "decision": detail.get("decision", ""),
        "detail": json.dumps(detail, sort_keys=True),
    }


@router.get("/api/ledger")
def list_ledger(
    request: Request, limit: int = _DEFAULT_PAGE_SIZE, cursor: str | None = None,
    sess=Depends(current_session),
):
    ledger = request.app.state.ledger
    page_size = max(1, min(limit, _MAX_PAGE_SIZE))
    entries = ledger.entries(sess.workspace_id)  # already seq-ascending

    start = 0
    if cursor:
        try:
            start = next(i for i, e in enumerate(entries) if e["id"] == cursor) + 1
        except StopIteration:
            start = 0  # unknown/foreign cursor -- fail safe to the first page

    page = entries[start:start + page_size]
    next_cursor = page[-1]["id"] if page and (start + page_size) < len(entries) else None
    return {"entries": page, "next_cursor": next_cursor, "total": len(entries)}


@router.get("/api/ledger/verify")
def verify_ledger(request: Request, sess=Depends(current_session)):
    ledger = request.app.state.ledger
    intact = ledger.verify(sess.workspace_id)
    checked = len(ledger.entries(sess.workspace_id))
    return {"intact": intact, "checked": checked}


@router.get("/api/ledger.csv")
def ledger_csv(request: Request, sess=Depends(current_session)):
    ledger = request.app.state.ledger

    def rows():
        sink = _Sink()
        writer = csv.DictWriter(sink, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        yield sink.value
        # `Ledger.entries` already returns the whole workspace's chain as
        # a Python list (it has to, to sort by `seq` -- see that module's
        # own docstring), so the "does not materialise a year of entries"
        # guarantee here is about the HTTP RESPONSE, not the Firestore
        # read: this endpoint never builds a second, CSV-formatted copy of
        # the whole export in memory, and the client never waits for the
        # whole file before the first byte arrives.
        for entry in ledger.entries(sess.workspace_id):
            writer.writerow(_entry_row(entry))
            yield sink.value

    return StreamingResponse(rows(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=ledger.csv",
    })
