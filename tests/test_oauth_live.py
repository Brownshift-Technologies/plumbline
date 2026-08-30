"""Opt-in: exercises the real Google/GitHub/Okta endpoints in
`app/providers.py`, instead of `FakeProvider`. Skipped by default so the
default `pytest`/`pytest tests/` run (this task's 418-test suite included)
never makes a network call or needs real OAuth app credentials -- see
`app/providers.py`'s module docstring for why that split exists at all.

Run explicitly, with real credentials and a real authorization code
obtained by hand (this is a manual/CI-secret-gated check, not something a
generated `code` can drive -- an authorization code is only ever issued by
the provider to a real, interactive, human login):

    PLUMBLINE_LIVE_OAUTH_TESTS=1 \\
    GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \\
    OAUTH_REDIRECT_BASE=https://your-deployed-host \\
    pytest tests/test_oauth_live.py -m live_oauth
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("PLUMBLINE_LIVE_OAUTH_TESTS"),
    reason="opt-in: set PLUMBLINE_LIVE_OAUTH_TESTS=1 and real provider credentials to run",
)


def test_google_authorize_url_is_well_formed():
    from app.providers import GoogleProvider
    from app.settings import load_settings

    provider = GoogleProvider(load_settings())
    url = provider.authorize_url("some-state")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "state=some-state" in url


def test_github_authorize_url_is_well_formed():
    from app.providers import GitHubProvider
    from app.settings import load_settings

    provider = GitHubProvider(load_settings())
    url = provider.authorize_url("some-state")
    assert url.startswith("https://github.com/login/oauth/authorize?")


def test_okta_authorize_url_is_well_formed():
    from app.providers import OktaProvider
    from app.settings import load_settings

    provider = OktaProvider(load_settings())
    url = provider.authorize_url("some-state")
    assert "/oauth2/default/v1/authorize" in url


def test_google_exchange_with_a_real_authorization_code():
    # Requires a real `code` from a completed interactive Google login --
    # supply one out of band (e.g. GOOGLE_LIVE_TEST_CODE) to actually run
    # the exchange; otherwise this documents the expectation and skips.
    code = os.getenv("GOOGLE_LIVE_TEST_CODE")
    if not code:
        pytest.skip("set GOOGLE_LIVE_TEST_CODE to a freshly-obtained real authorization code")
    from app.providers import GoogleProvider
    from app.settings import load_settings

    provider = GoogleProvider(load_settings())
    token = provider.exchange(code)
    assert "access_token" in token
    email, name, verified = provider.profile(token)
    assert "@" in email
