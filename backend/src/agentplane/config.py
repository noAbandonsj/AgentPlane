from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    web_origin: str = "http://127.0.0.1:5173"
    log_level: str = "INFO"

    auth_mode: str = "dev"
    dev_tenant_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")

    database_url: str = (
        "postgresql+psycopg://agentplane:agentplane_dev_2026@127.0.0.1:55432/agentplane"
    )
    redis_url: str = "redis://127.0.0.1:56379/0"
    redis_run_stream: str = "agentplane:runs"
    redis_run_group: str = "agentplane-workers"

    model_base_url: str = ""
    model_api_key: str = ""
    model_name: str = ""
    model_timeout_seconds: int = Field(default=120, ge=1, le=600)
    model_max_concurrency: int = Field(default=8, ge=1, le=1000)

    worker_consumer_name: str = "worker-local-1"
    worker_block_ms: int = Field(default=5000, ge=100, le=60000)
    worker_reclaim_idle_ms: int = Field(default=60000, ge=1000)
    outbox_poll_seconds: float = Field(default=0.5, ge=0.05, le=30)
    sse_poll_seconds: float = Field(default=1, ge=0.1, le=30)
    sse_keepalive_seconds: float = Field(default=15, ge=1, le=120)

    @property
    def model_configured(self) -> bool:
        return bool(self.model_api_key.strip() and self.model_name.strip())

    @property
    def is_test(self) -> bool:
        return self.app_env.lower() == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
