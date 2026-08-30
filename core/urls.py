"""Shared URL normalisation for every agent that decides whether a
site-derived URL refers to a route this workspace already knows about.

Fleet-wide rule established across Tasks 12c-12g's review rounds: a URL
must be normalised the way a BROWSER would before it is ever compared or
treated as "internal" -- strip ASCII tab/CR/LF, fold a backslash to a
forward slash, lowercase the scheme, THEN compare. Skipping this lets a
string like "/\\evil.com" or "/<TAB>/evil.com" -- both of which a real
browser resolves off-origin -- read as an ordinary same-origin path to a
naive `str` comparison or a bare `.startswith("/")` check. This is the
same class of bug `agents/cartographer.py`'s `_internal_href` already
guards against for its own narrower case (a protocol-relative `//host`
href during a crawl); this module is the general form, reused by every
later agent that maps an attacker-influenced URL (a production incident's
`url`, a page's own `links()`) onto a `Route` this workspace already
trusts -- Sentinel mapping an `Incident` onto a route to reproduce and
Economist cross-referencing an `Incident` against a `Behaviour`'s route
before deciding a test is safe to flag, chiefly.
"""

from urllib.parse import urlsplit

_STRIP = str.maketrans("", "", "\t\r\n")


def normalise_url(raw: str) -> str:
    """`raw`, read the way a browser would before acting on it: every
    ASCII tab/CR/LF stripped -- not just trimmed off the ends, since an
    EMBEDDED one is exactly what lets a string look like an ordinary
    same-origin path while a browser reads it as something else -- with
    backslashes folded to forward slashes (Windows-style and
    browser-tolerated slash confusion collapse to the one character a
    POSIX-style comparison expects), and the scheme, if any, lowercased.
    Never the whole string: a path or query is legitimately case-sensitive,
    and lower-casing it would corrupt a comparison against a real route.
    """
    if not raw:
        return raw
    cleaned = raw.translate(_STRIP).replace("\\", "/")
    if "://" in cleaned:
        scheme, rest = cleaned.split("://", 1)
        cleaned = f"{scheme.lower()}://{rest}"
    return cleaned


def route_of(raw: str) -> str:
    """The path component `raw` names, `normalise_url`'d first. An empty
    string, a bare fragment, or a scheme-and-host-only URL all collapse to
    `"/"` -- the same "no path means home" reading a browser gives an
    empty or root URL.

    Deliberately returns only the PATH, dropping any host entirely (a
    caller comparing this against `Route.path` never wants a host in the
    comparison -- routes in this codebase are always host-less). This is
    safe specifically because a caller is expected to use the returned
    path ONLY as a same-origin argument to something like
    `BrowserDriver.goto`, never to re-attach the original (possibly
    attacker-chosen) host and follow it -- the exact discipline that keeps
    a protocol-relative `//evil.com/x` from ever causing an off-origin
    navigation: its extracted path, `/x`, is harmless on its own, and is
    the only part of it this function ever hands back.
    """
    normalised = normalise_url(raw)
    path = urlsplit(normalised).path or "/"
    return path.split("#", 1)[0] or "/"
