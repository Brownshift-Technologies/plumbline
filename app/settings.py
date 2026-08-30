from dataclasses import dataclass

from core.config import Config, load_config

FIRESTORE_PREFIX = "plumbline"


@dataclass(frozen=True)
class PlumblineConfig(Config):
    demo_workspace_id: str = "ws_demo"
    session_ttl_days: int = 14


def load_settings() -> PlumblineConfig:
    base = load_config(FIRESTORE_PREFIX)
    return PlumblineConfig(
        project_id=base.project_id,
        location=base.location,
        vertex_location=base.vertex_location,
        model=base.model,
        firestore_prefix=base.firestore_prefix,
    )
