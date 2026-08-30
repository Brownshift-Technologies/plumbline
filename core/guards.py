import datetime
import logging
import re
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# Both repeats around the "@" are bounded, and that is load-bearing rather than
# cosmetic: with `+` on either side this pattern is quadratic in the length of
# the text, and redact_pii is reachable from the wire (core.web logs a
# redacted exception detail for every failed event) and from every audit write
# (core.store._redact). Measured on the unbounded form, doubling the input
# quadrupled the time -- 2 KB 0.004 s, 4 KB 0.015 s, 8 KB 0.056 s, 16 KB
# 0.230 s, 32 KB 0.908 s, 64 KB 3.611 s -- which is minutes at Pub/Sub's 10 MB
# message cap, on the event loop, inside the arm that exists to keep the
# endpoint returning 204.
#
# There were two independent quadratic sources, not one, and bounding only the
# domain leaves the other live:
#
#   - the domain, `[\w.-]+\.`: `[\w.-]` contains the dot, so the engine can
#     split "a.a.a.a…" between the run and the literal in a linear number of
#     ways, and retries each one. Worst case `a@a.a.a.a…`.
#   - the local part, `[\w.%+-]+@`: this one is not catastrophic backtracking
#     but plain O(n) work at each of O(n) start positions. `\b` offers a start
#     position at every label, and from each one the run scans forward to the
#     "@" before failing. Worst case `a.a.a.a…@`.
#
# The bounds are RFC 5321's own limits -- 64 octets of local part, 255 of
# domain -- so no address that could be delivered is affected. They make the
# work at each start position a constant rather than O(n): measured after this
# change, all three adversarial shapes double rather than quadruple (see
# tests/test_guards.py::test_redaction_is_linear_in_input_length).
#
# The local-part bound is what keeps the *unbounded* TLD repeat safe as well:
# `[A-Za-z]{2,}` backtracks through a long letter run when the character after
# it is a word character that is not a letter, but only a start position within
# 64 characters of an "@" can reach that repeat at all, so the fan-in is capped
# and the total stays linear. The TLD is therefore left unbounded, which keeps
# arbitrary-length TLDs redacting as they did before.
#
# What the bounds cost: an over-limit address does not simply fail to redact
# as a whole. Measured across five over-limit shapes, three of five redact
# only in part and leave a fragment of the address in the output:
#
#   - domain > 255 chars but with a "." within reach of the cap: the domain
#     quantifier backtracks to the last "." + TLD that fits within 255 chars,
#     so the match still fires, just starting later or ending earlier than
#     the full address, and a trailing remainder survives literally. E.g.
#     "sam@" + "sub."*63 + "example.com" (263-char domain) redacts to
#     "[EMAIL].com", and "sam@" + "sub."*250 + "example.com" (1000-char
#     domain) redacts to "[EMAIL]" followed by ~750 surviving characters of
#     "sub.sub...." tail.
#   - local part > 64 chars but containing a "." within reach: the local
#     quantifier's \b start positions let an in-bounds slice starting later
#     in the local part match instead, so the leading local-part characters
#     survive literally as a prefix. E.g. "verylongname."*7 + "sam@example.com"
#     (91-char local part) redacts to
#     "verylongname.verylongname.verylongname[EMAIL]".
#
# Total escape -- nothing redacted at all -- happens only in the two shapes
# with no such "." to backtrack onto:
#
#   - local part > 64 chars with no separator anywhere in it, e.g. 65 bare
#     word characters before "@": every \b start position is still more than
#     64 characters from "@", so no start position can reach it.
#   - domain > 255 chars with no "." in the first 255 characters after "@",
#     e.g. "sam@" + "d"*300 + ".com": the domain quantifier can never reach
#     an anchoring "." within its cap, so the whole pattern fails to match --
#     and because nothing else in redact_pii claims the span, the local part
#     leaks too.
#
# Both are longer than any deliverable address. That is the deliberate trade
# -- a pathological string is no longer a denial of service -- and the
# truncation in core.web bounds the wire path a second time (see there
# for the size of the fragment truncation itself can leave unredacted).
_EMAIL = re.compile(r"\b[\w.%+-]{1,64}@[\w.-]{1,255}\.[A-Za-z]{2,}\b")

# Both number patterns accept hyphen, dot, space or no separator at all, and
# are bounded by "not a digit" on either side rather than \b: a word boundary
# would miss a run glued to a letter (``tel4155550132``), and a leak is worse
# than over-redacting an ambiguous digit run. The bounds also keep the two
# patterns disjoint by digit count -- SSN is exactly 9, phone is 10 (plus an
# optional leading US country code) -- so neither can claim the other's span.
_SSN = re.compile(r"(?<!\d)\d{3}[-. ]?\d{2}[-. ]?\d{4}(?!\d)")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[-. ]?)?(?:\(\d{3}\)[-. ]?|\d{3}[-. ]?)\d{3}[-. ]?\d{4}(?!\d)"
)

# A bare 13-19 digit-run regex is not enough: order numbers, trace ids and
# timestamps are long digit runs too, and Plumbline's checkout-flow traces
# and HARs are full of them. Redacting a non-card digit run would corrupt
# the exact artefact a person needs in order to read a failure. `_PAN`
# candidates every digit run in that length range (allowing space/hyphen
# grouping, the way a PAN is usually written or logged), and `_redact_pan`
# only claims one that also passes a Luhn check -- the checksum every real
# card number satisfies and an arbitrary digit run passes only by chance
# (roughly 1 in 10). This is the same "narrow claim before a wider one can
# misread it" ordering _EMAIL already uses against the number patterns:
# `_redact_pan` runs first in `redact_pii`, before SSN and phone, so a
# 16-digit card is claimed whole before the SSN pattern can carve a false
# positive out of its middle.
_PAN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _redact_pan(text: str) -> str:
    def swap(m):
        digits = re.sub(r"[ -]", "", m.group(0))
        return "[CARD]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)

    return _PAN.sub(swap, text)

_OVERRIDE_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+)?(prior|previous)\s+", re.I),
    re.compile(r"reveal\s+your\s+(system\s+)?prompt", re.I),
]


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str | None
    text: str


def redact_pii(text: str) -> str:
    # Email is matched first because the number patterns would otherwise
    # consume an email's local part and leave the domain behind:
    # "415-555-0132@example.com" would persist as "[PHONE]@example.com".
    # PAN runs next, before SSN and phone, for the same reason: a 16-digit
    # card number is also a run of digits an SSN or phone pattern could
    # carve a false positive out of the middle of ("4242 4242 4242 4242"
    # contains a valid-looking 3-2-4 SSN grouping at "242 42 4242"). Once
    # PAN has claimed and replaced the whole run with "[CARD]", there is
    # nothing left for SSN or phone to misread. SSN then runs before phone.
    # Neither of those two can claim the other's span (see the pattern
    # comment above), but in a run of several adjacent digit groups they
    # partition it differently depending on which matches first:
    # "546 742173 6614" redacts to "[SSN] 6614" in this order, and to
    # "546 [PHONE]" if the two .sub() calls are swapped. SSN goes first so
    # the more sensitive reading wins.
    text = _EMAIL.sub("[EMAIL]", text)
    text = _redact_pan(text)
    text = _SSN.sub("[SSN]", text)
    text = _PHONE.sub("[PHONE]", text)
    return text


def check_input(text: str) -> GuardResult:
    for pattern in _OVERRIDE_PATTERNS:
        if pattern.search(text):
            return GuardResult(allowed=False, reason="instruction_override", text=redact_pii(text))
    return GuardResult(allowed=True, reason=None, text=redact_pii(text))


# --- redact_deep: PII redaction across a whole structure, not one string --
#
# Promoted here from core.store's private `_redact` (fix round 1 on the
# Gateway task): the Gateway needs to redact a `.read` tool's result before
# handing it back, and that result is not always a bare string -- a HAR
# capture, a trace object, a findings list are all naturally shaped as
# nested dicts/lists/tuples. redact_pii's contract is str-only by design
# (see its own docstring), so something has to walk the structure down to
# its string leaves and reassemble the same shape around the redacted
# values. core.store already had exactly that walker, privately, doing
# double duty for every audit write; keeping a second, independent
# implementation of "walk a nested structure looking for strings" in
# gateway.py (or anywhere else that needed it next) is how the two
# quietly drift apart from each other over time. This is the one
# implementation now -- core.store._redact is a thin alias for it (see
# core/store.py), not a parallel copy.
#
# Behaviour, unchanged from the promoted original:
#   - str                       -> redact_pii(value)
#   - something with a Firestore-style `_document_path` (duck-typed via
#     getattr, so this needs no google.cloud import) -> its path,
#     redacted. Firestore document IDs allow "@", so the PII is usually in
#     the ID segment; returned as a plain string, not a rebuilt reference,
#     because a reference built around a redacted path would be a live,
#     writable address for a document that cannot exist.
#   - dict                      -> every key AND value redacted (a dict
#     can be keyed by an email address or a case number just as easily as
#     it can hold one as a value); two keys colliding after redaction are
#     both kept, suffixed ("#2", "#3", ...) rather than one silently
#     overwriting the other.
#   - list / tuple              -> same container type, every item redacted.
#   - set / frozenset           -> a list of every member redacted. Not a
#     set: redaction can map two distinct members onto the same string,
#     which a set would silently deduplicate away, dropping a member the
#     caller wrote.
#   - bytes                     -> UTF-8 decoded and redacted when that
#     succeeds; a "[BINARY:NB]" length marker when it doesn't (a regex
#     cannot scan a binary blob for the PII inside it, and passing
#     unscanned bytes through would put data past this barrier entirely).
#   - None / int / float / datetime.datetime -> passed through unchanged
#     (bool is an int subclass and already covered). These are scalars
#     with no string inside for redact_pii to scan; an int CAN still carry
#     PII (a phone number persists as an integer), but redact_pii is
#     str-only by signature, and warning on every timestamp and count in
#     an ordinary structure would bury the one warning that matters.
#   - anything else              -> passed through unchanged, and logged --
#     an unrecognised type reaching storage or a caller unscanned has
#     leaked before (a set, then a Firestore reference type), always found
#     by a reviewer rather than by anything the code said. This still does
#     not raise: a leak is bad, a call that dies because of a redaction
#     helper is worse.
#
# New behaviour, added when this was promoted: cycle safety. The original
# `_redact` had none -- audit-entry dicts are built fresh from JSON-shaped
# data, which cannot cycle back on itself, so it was never exercised. A
# `.read` tool's result is not guaranteed to have come from JSON first (a
# HAR-derived object graph can carry a parent/back-reference), and walking
# a cycle with no guard would not "hang" so much as blow the recursion
# stack -- Python raises RecursionError, which is exactly the kind of
# crash-instead-of-degrade this function's "never raise" contract exists
# to avoid. `_seen` tracks the ids of every container currently being
# descended into (not every container ever seen -- two unconnected
# branches that happen to alias the same list are not a cycle, and must
# not be flagged as one); re-entering one still on that stack returns the
# literal string "[CIRCULAR]" in its place -- visible in the output, the
# same way "[BINARY:9B]" already marks a value this function could not
# faithfully redact-and-preserve, rather than the branch just vanishing.
_CIRCULAR = "[CIRCULAR]"


def redact_deep(value, _seen: frozenset | None = None):
    if isinstance(value, str):
        return redact_pii(value)
    document_path = _document_path_of(value)
    if isinstance(document_path, str):
        return redact_pii(document_path)
    if isinstance(value, dict):
        return _redact_mapping(value, _seen)
    if isinstance(value, (list, tuple)):
        return _redact_sequence(value, type(value), _seen)
    if isinstance(value, (set, frozenset)):
        # Returned as a list -- see the module-level note above.
        return _redact_sequence(value, list, _seen)
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return f"[BINARY:{len(value)}B]"
        return redact_pii(text).encode("utf-8")
    if isinstance(value, (type(None), int, float, datetime.datetime)):
        return value
    _warn_unhandled(value)
    return value


def _enter(container, seen) -> frozenset | None:
    """The `_seen` set to descend into `container` with, or None if
    `container` is already on the current recursion stack (a cycle)."""
    ids = frozenset() if seen is None else seen
    if id(container) in ids:
        return None
    return ids | {id(container)}


def _redact_sequence(items, ctor, seen):
    next_seen = _enter(items, seen)
    if next_seen is None:
        return _CIRCULAR
    return ctor(redact_deep(item, next_seen) for item in items)


def _redact_mapping(mapping: dict, seen) -> dict:
    next_seen = _enter(mapping, seen)
    if next_seen is None:
        return _CIRCULAR
    redacted: dict = {}
    for key, value in mapping.items():
        if isinstance(key, bytes):
            # protobuf accepts a bytes key where a MapValue wants a string
            # field name, so a bytes key really does persist -- and it
            # persists as a string. (int and tuple keys raise TypeError
            # there, so those cannot reach storage at all.) Redact it
            # through the same bytes path as values, then carry it as the
            # string it will be stored as, so the collision suffixing
            # below applies to it too.
            key = redact_deep(key, next_seen)
            if isinstance(key, bytes):
                key = key.decode("utf-8")
        new_key = redact_pii(key) if isinstance(key, str) else key
        # Two different keys can redact to the same string ("[EMAIL]"),
        # which would silently drop one of the values. Suffix instead:
        # this is about stopping silent loss, not trading one kind for
        # another.
        if isinstance(new_key, str) and new_key in redacted:
            suffix = 2
            while f"{new_key}#{suffix}" in redacted:
                suffix += 1
            new_key = f"{new_key}#{suffix}"
        redacted[new_key] = redact_deep(value, next_seen)
    return redacted


def _document_path_of(value):
    """The value's Firestore document path, or None if it has none.

    ``_document_path`` is a property that raises ValueError when the
    reference was built without a client, and getattr's default does not
    swallow that. Declining to redact such a reference costs nothing:
    Firestore's own encoder reads the same property and raises the same
    ValueError, so it was never going to reach storage. Catching it only
    keeps this function from being what raises.
    """
    try:
        return getattr(value, "_document_path", None)
    except ValueError:
        return None


def _warn_unhandled(value) -> None:
    """Leave a trace when a type reaches storage or a caller unscanned.

    Firestore encodes more types than this function recognises (GeoPoint,
    Vector, and whatever a future client version adds), and every one of
    those persists without ever being looked at for PII. The types
    Firestore *cannot* encode raise TypeError before anything is written,
    so they are not exposures -- but they land here too, and a warning
    naming them is cheaper than a branch guessing which is which.
    """
    cls = type(value)
    _log.warning(
        "redact_deep passed through an unhandled type: %s.%s -- if it can be "
        "stored or returned as-is, it was persisted/returned without being "
        "scanned for PII",
        cls.__module__,
        cls.__qualname__,
    )
