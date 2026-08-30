import os
from dataclasses import dataclass

from core.config import Config, load_config

FIRESTORE_PREFIX = "plumbline"


@dataclass(frozen=True)
class PlumblineConfig(Config):
    demo_workspace_id: str = "ws_demo"
    session_ttl_days: int = 14
    # Task 8b: OAuth. Every field below defaults to "" so that
    # `tests/conftest.py`'s `config` fixture -- which builds a
    # `PlumblineConfig` with none of these -- keeps working unchanged; a
    # provider whose id/secret is empty simply cannot complete a real
    # exchange, which is exactly right for a test process that only ever
    # talks to `FakeProvider`. `oauth_state_secret` signs the CSRF `state`
    # itsdangerous carries between `start` and `callback` (see
    # `app/oauth_routes.py`); `app/main.py`'s `build_app` falls back to a
    # fixed, clearly-insecure dev value when it is unset rather than
    # generating a random one per process -- a random per-process secret is
    # exactly Task 6's per-instance-dict mistake again: Cloud Run's `start`
    # and `callback` for one login can land on two different warm
    # instances, and a real deployment MUST set OAUTH_STATE_SECRET (Secret
    # Manager, not baked into the image) so every instance signs with the
    # same key.
    oauth_state_secret: str = ""
    oauth_redirect_base: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    okta_domain: str = ""
    okta_client_id: str = ""
    okta_client_secret: str = ""


def load_settings() -> PlumblineConfig:
    base = load_config(FIRESTORE_PREFIX)
    return PlumblineConfig(
        project_id=base.project_id,
        location=base.location,
        vertex_location=base.vertex_location,
        model=base.model,
        firestore_prefix=base.firestore_prefix,
        oauth_state_secret=os.getenv("OAUTH_STATE_SECRET", ""),
        oauth_redirect_base=os.getenv("OAUTH_REDIRECT_BASE", ""),
        google_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
        google_client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        github_client_id=os.getenv("GITHUB_OAUTH_CLIENT_ID", ""),
        github_client_secret=os.getenv("GITHUB_OAUTH_CLIENT_SECRET", ""),
        okta_domain=os.getenv("OKTA_DOMAIN", ""),
        okta_client_id=os.getenv("OKTA_OAUTH_CLIENT_ID", ""),
        okta_client_secret=os.getenv("OKTA_OAUTH_CLIENT_SECRET", ""),
    )
