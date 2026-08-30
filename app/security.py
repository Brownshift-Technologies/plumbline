"""Password hashing, TOTP verification, and random token generation.

Argon2id (via argon2-cffi's default `PasswordHasher`) for passwords, pyotp
for TOTP, `secrets` for tokens. Each function wraps one well-audited
library; the only cryptographic logic added here is TOTP replay tracking
(see `verify_totp`), because pyotp itself has none.
"""

import datetime
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pyotp.utils import strings_equal

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    # No length/prefix short-circuit runs before the argon2 call: every
    # input, right or wrong, goes through the full hash-and-compare, so
    # failure timing cannot leak how close a guess was. argon2-cffi's
    # internal comparison is already constant-time (it hashes the candidate
    # with the params embedded in `hashed`, then compares digests byte for
    # byte); the only way to defeat that from here would be adding a cheap
    # early-exit in front of it, which this deliberately does not do.
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, VerificationError):
        return False
    except InvalidHashError:
        # InvalidHashError subclasses ValueError, not Argon2Error, so it is
        # not caught by VerificationError above. It is raised for a hash
        # string argon2 cannot even parse the header of (corrupted row,
        # empty string, foreign format) -- that must fail closed like any
        # other mismatch, not propagate and crash the auth path.
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


# TOTP window and replay protection -----------------------------------------
#
# `valid_window=1` (checked here as offsets -1, 0, +1) accepts the previous,
# current, and next 30-second step -- 90 seconds total. That is the standard
# RFC 6238 tolerance for client/server clock drift: narrower (0) starts
# rejecting legitimate codes from phones running even a few seconds off;
# wider admits more replay surface than realistic clock drift justifies. 1
# step either side is the accepted minimum.
#
# pyotp.TOTP.verify has no replay protection of its own -- the same six
# digits validate every time they're checked inside the window, so a code
# captured over a shoulder, in a proxy log, or via a compromised network tap
# is reusable for up to 90 seconds. `_used_totp_counters` tracks accepted
# (secret, counter) pairs and refuses a counter a second time, closing that
# window down to "first use only", same as HOTP-with-server-state.
#
# This state is process-local (a plain in-memory dict), not persisted via
# Repo. A single-process deployment is fully protected; a horizontally
# scaled deployment with no shared state between instances is not -- see the
# task report for that caveat and why the interface as specified does not
# thread a store through here.
_used_totp_counters: dict[str, set[int]] = {}


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    now = datetime.datetime.fromtimestamp(int(time.time()))
    base_counter = totp.timecode(now)
    for offset in (0, -1, 1):
        # strings_equal (hmac.compare_digest under the hood) instead of a
        # plain `==`, matching pyotp.verify's own comparison -- a naive `==`
        # short-circuits on the first differing character and would leak a
        # per-digit timing oracle for brute-forcing the code.
        if strings_equal(str(code), totp.at(now, offset)):
            counter = base_counter + offset
            used = _used_totp_counters.setdefault(secret, set())
            if counter in used:
                return False  # replay of an already-consumed code
            used.add(counter)
            # Bound memory: a counter more than one window stale can never
            # be presented again by verify_totp's own offset range, so it is
            # safe to forget.
            used.intersection_update(range(base_counter - 1, base_counter + 2))
            return True
    return False


def new_token() -> str:
    # token_urlsafe encodes with the URL-safe base64 alphabet and strips
    # padding, so the result never contains '+', '/', or '=' and needs no
    # percent-escaping when placed directly in a URL path, query string, or
    # cookie value. 32 bytes is 256 bits of entropy from `secrets`
    # (os.urandom-backed, not the non-cryptographic `random` module) --
    # guessing one is not a realistic attack.
    return secrets.token_urlsafe(32)
