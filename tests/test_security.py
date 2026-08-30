import string
import time

import pyotp

from app.security import (
    hash_password,
    new_token,
    new_totp_secret,
    verify_password,
    verify_totp,
)

# --- from the brief ---------------------------------------------------------


def test_a_hash_is_not_the_password():
    assert hash_password("correct horse battery") != "correct horse battery"


def test_the_right_password_verifies():
    assert verify_password("correct horse battery", hash_password("correct horse battery"))


def test_the_wrong_password_does_not():
    assert not verify_password("wrong", hash_password("correct horse battery"))


def test_two_hashes_of_one_password_differ():
    assert hash_password("same") != hash_password("same")


def test_a_current_totp_code_verifies():
    s = new_totp_secret()
    assert verify_totp(s, pyotp.TOTP(s).now())


def test_a_wrong_totp_code_does_not():
    assert not verify_totp(new_totp_secret(), "000000")


def test_tokens_are_unique_and_long():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 32


# --- attacker-shaped tests beyond the brief ---------------------------------


def test_wrong_password_does_not_short_circuit_on_length():
    # A length- or prefix-based early exit would return False before the
    # argon2 call for some of these and after it for others -- the risk the
    # brief calls out. Every one of these must go through the same
    # hash-and-compare path and simply come back False; none should raise.
    hashed = hash_password("correct horse battery")
    wrong_guesses = [
        "",
        "c",
        "correct",
        "correct horse",
        "correct horse battery!",  # right password + 1 char
        "x" * 500,  # far longer than the real password
    ]
    for guess in wrong_guesses:
        assert verify_password(guess, hashed) is False


def test_wrong_and_right_password_cost_roughly_the_same():
    # argon2-cffi always runs the full memory-hard hash before comparing, so
    # a correct and an incorrect guess should take about the same wall time.
    # A short-circuit (e.g. bailing out on a quick length/prefix check
    # before hashing) would make the wrong-password path much faster than
    # the right one. Threshold is deliberately generous (3x) -- this is a
    # coarse smoke check against a gross short-circuit, not a precision
    # timing-attack measurement, which would be too flaky for a test suite.
    pw = "correct horse battery"
    hashed = hash_password(pw)
    iterations = 8

    start = time.perf_counter()
    for _ in range(iterations):
        assert verify_password(pw, hashed) is True
    right_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(iterations):
        assert verify_password("wrong password entirely", hashed) is False
    wrong_elapsed = time.perf_counter() - start

    ratio = max(right_elapsed, wrong_elapsed) / max(min(right_elapsed, wrong_elapsed), 1e-9)
    assert ratio < 3.0, f"right/wrong verify timing diverged too much: ratio={ratio:.2f}"


def test_verify_password_never_raises_on_a_malformed_hash():
    # A corrupted or foreign-format hash (truncated, wrong scheme, empty)
    # must fail closed, not blow up the auth path with an exception.
    for garbage in ["", "not-a-hash", "$argon2id$truncated"]:
        assert verify_password("anything", garbage) is False


def test_verify_password_fails_closed_on_a_none_hash():
    # Tasks 6/7's review: a None stored hash (a half-migrated row, a bad
    # ORM default reaching a field typed non-Optional) must not raise
    # AttributeError out of the auth path -- it must simply not verify.
    assert verify_password("anything", None) is False


def test_verify_password_fails_closed_on_a_non_string_hash():
    # Any other non-string type reaching this far (an int id swapped in by
    # a caller bug, a bytes value from a driver that didn't decode) must
    # fail closed the same way, not raise.
    for garbage in (12345, b"argon2-bytes-not-str", [], {}, object()):
        assert verify_password("anything", garbage) is False


def test_a_totp_code_does_not_verify_twice():
    # Replay: a code captured once (over a shoulder, in a log, on a
    # compromised link) must not be usable a second time inside its
    # validity window -- this is the standard TOTP replay attack.
    s = new_totp_secret()
    code = pyotp.TOTP(s).now()
    assert verify_totp(s, code) is True
    assert verify_totp(s, code) is False


def test_a_totp_replay_is_rejected_even_from_the_adjacent_window():
    # The same captured code must not become valid again just because time
    # moved into the following window and the code is still inside
    # valid_window's tolerance -- it was already consumed.
    s = new_totp_secret()
    totp = pyotp.TOTP(s)
    code = totp.now()
    assert verify_totp(s, code) is True
    # Still within the +/-1 step tolerance, but the same counter -> reused.
    assert verify_totp(s, code) is False


def test_totp_accepts_the_adjacent_window_for_clock_skew():
    s = new_totp_secret()
    totp = pyotp.TOTP(s)
    now = int(time.time())
    previous_step_code = totp.at(now - totp.interval)
    assert verify_totp(s, previous_step_code) is True


def test_totp_rejects_a_code_two_windows_away():
    # Window is +/-1 step (30s); a code from two steps away (60s) is outside
    # that tolerance and must not verify -- otherwise the window is wider
    # than the clock-skew justification for it.
    s = new_totp_secret()
    totp = pyotp.TOTP(s)
    now = int(time.time())
    far_code = totp.at(now - 2 * totp.interval)
    # Guard against the rare case where an old step happens to produce the
    # same 6 digits as something inside the accepted window.
    if far_code in (totp.at(now), totp.at(now - totp.interval), totp.at(now + totp.interval)):
        return
    assert verify_totp(s, far_code) is False


def test_totp_replay_tracking_is_per_secret():
    # Consuming a code on one user's secret must not affect another user's
    # independently-generated code, even if by coincidence they collide.
    s1, s2 = new_totp_secret(), new_totp_secret()
    code1 = pyotp.TOTP(s1).now()
    assert verify_totp(s1, code1) is True
    assert verify_totp(s1, code1) is False
    code2 = pyotp.TOTP(s2).now()
    assert verify_totp(s2, code2) is True


def test_tokens_contain_no_characters_that_need_url_escaping():
    # secrets.token_urlsafe must never need percent-escaping to sit in a
    # URL path, query string, or Set-Cookie value.
    unsafe = set("+/=")
    for _ in range(50):
        token = new_token()
        assert not unsafe & set(token)
        assert set(token) <= set(string.ascii_letters + string.digits + "-_")
