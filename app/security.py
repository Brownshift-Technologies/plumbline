"""Password hashing, TOTP code matching, and random token generation.

Argon2id (via argon2-cffi's default `PasswordHasher`) for passwords, pyotp
for TOTP, `secrets` for tokens.

Fix round 1 (Task 8b review): this module used to also own TOTP replay
protection (`verify_totp`, removed here), tracked in a plain in-process
dict. That was correct for a single instance and silently wrong the moment
a second Cloud Run instance came up warm -- a captured code replayed
against a sibling instance, with its own empty dict, would sail through.
The fix (`Repo.consume_totp_step`, app/repo.py) is a transactional counter
persisted on the user document, which needs the raw step number, not a
yes/no verdict -- so `totp_step_for` below is what remains: stateless
code-matching, no dict, no notion of "already used". Every 8b route calls
`totp_step_for` + `Repo.consume_totp_step` together; nothing in this
codebase calls a bare, replay-blind TOTP check any more, and nothing
should -- keeping the old function around under any name, correct or not,
is exactly the trap the review caught: a name that reads as "the TOTP
verifier" next to one that reads as "just the step lookup" invites a
future caller to grab the wrong one by pattern-matching the name instead
of reading either docstring.
"""

import datetime
import hashlib
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
    # A missing or malformed *stored* hash is not a password guess -- there
    # is no valid hash on the other end for a timing difference to leak
    # anything about, so this guard is not the short-circuit the comment
    # below forbids. It exists because argon2-cffi's `verify` calls
    # `.encode()` on `hashed` before it ever gets far enough to raise
    # `InvalidHashError`: a `None` or non-string hash (a half-migrated row,
    # a bad default, a caller passing the wrong field) raises a plain
    # `AttributeError` that neither except clause below catches, and an
    # exception escaping the auth path is worse than a clean `False` --
    # Tasks 6/7's review caught this. `User.password_hash` is typed
    # non-Optional, but a type hint is not runtime-enforced.
    if not hashed or not isinstance(hashed, str):
        return False
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


def totp_step_for(secret: str, code: str) -> int | None:
    """Which RFC 6238 step (30-second counter) `code` matches for `secret`,
    or `None` if it matches none of them. Stateless: no dict, no side
    effect, no notion of "already used" -- see the module docstring for
    why a stateful version of this used to live here and does not any
    more.

    `valid_window=1` (checked here as offsets -1, 0, +1) accepts the
    previous, current, and next 30-second step -- 90 seconds total. That
    is the standard RFC 6238 tolerance for client/server clock drift:
    narrower (0) starts rejecting legitimate codes from phones running
    even a few seconds off; wider admits more replay surface than
    realistic clock drift justifies. 1 step either side is the accepted
    minimum.

    Callers combine this with `Repo.consume_totp_step` (app/repo.py) for
    the actual replay defense: this function only says which step a code
    *would* match; consume_totp_step is what decides, transactionally and
    persistently, whether that step has already been spent.
    """
    totp = pyotp.TOTP(secret)
    now = datetime.datetime.fromtimestamp(int(time.time()))
    base_counter = totp.timecode(now)
    for offset in (0, -1, 1):
        # strings_equal (hmac.compare_digest under the hood) instead of a
        # plain `==`, matching pyotp.verify's own comparison -- a naive `==`
        # short-circuits on the first differing character and would leak a
        # per-digit timing oracle for brute-forcing the code.
        if strings_equal(str(code), totp.at(now, offset)):
            return base_counter + offset
    return None


def hash_token(token: str) -> str:
    # SHA-256, not argon2: unlike a password, a token from `new_token()`
    # already carries 256 bits of `secrets`-sourced entropy, so there is no
    # low-entropy secret here for a slow, salted KDF to protect against
    # offline brute force -- the point of hashing it at all is only so that
    # reading the stored value back out (a leaked collection, a misdirected
    # query) does not itself hand out a working token. A fast, unsalted
    # digest is the right tool for that job and costs nothing on every
    # reset-confirm request's lookup.
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    # token_urlsafe encodes with the URL-safe base64 alphabet and strips
    # padding, so the result never contains '+', '/', or '=' and needs no
    # percent-escaping when placed directly in a URL path, query string, or
    # cookie value. 32 bytes is 256 bits of entropy from `secrets`
    # (os.urandom-backed, not the non-cryptographic `random` module) --
    # guessing one is not a realistic attack.
    return secrets.token_urlsafe(32)
