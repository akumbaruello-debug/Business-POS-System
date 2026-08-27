"""Application settings.

All settings are loaded from environment variables (with the ``POS_``
prefix) and validated by pydantic-settings.

**Source of truth:** ``Backend-Architecture-V1.0.md`` §2 Technology
Stack and ``Backend-Implementation-Plan-V1.0.md`` §3.2.

Why a typed ``Settings`` class (and not raw ``os.environ``):
* Centralizes the environment contract; one place to look for tunables.
* Validates types / ranges at startup — fail fast.
* Plays nicely with IDE autocomplete and mypy.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


Environment = Literal["development", "test", "production"]
LogFormat = Literal["json", "console"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Validated application configuration."""

    model_config = SettingsConfigDict(
        env_prefix="POS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Reading env vars happens once per Settings() instance.
    )

    # ---- App ----
    env: Environment = "development"
    debug: bool = False
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    # ---- HTTP server ----
    host: str = "127.0.0.1"
    port: int = 8000
    cors_allowed_origins: str = ""
    trusted_hosts: str = ""
    max_body_bytes: int = 1 * 1024 * 1024  # 1 MiB

    # ---- Database ----
    database_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5433/pos_dev"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 10
    database_statement_timeout_ms: int = 30_000

    # ---- Auth ----
    access_token_ttl_seconds: int = 15 * 60  # 15 min
    refresh_token_ttl_seconds: int = 7 * 24 * 60 * 60  # 7 days
    max_failed_logins: int = 5
    failed_login_window_seconds: int = 15 * 60  # 15 min
    lockout_duration_seconds: int = 15 * 60  # 15 min

    # ---- Idempotency ----
    idempotency_default_ttl_seconds: int = 24 * 60 * 60  # 24 h
    idempotency_login_ttl_seconds: int = 30  # 30 s

    # ---- Server-generated secret used to sign request IDs (HMAC) ----
    # In production this MUST be set via env. We never persist it.
    request_id_signing_key: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(32)),
    )

    # ---- Validators -----------------------------------------------------

    @field_validator("port", "database_pool_size", "database_max_overflow")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        return v

    @field_validator(
        "max_body_bytes",
        "access_token_ttl_seconds",
        "refresh_token_ttl_seconds",
        "idempotency_default_ttl_seconds",
    )
    @classmethod
    def _strictly_positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        if not v.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
            raise ValueError(
                "database_url must use the asyncpg (or psycopg) async driver",
            )
        return v

    # ---- Helpers --------------------------------------------------------

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse ``cors_allowed_origins`` into a list."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def trusted_hosts_list(self) -> list[str]:
        """Parse ``trusted_hosts`` into a list."""
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_test(self) -> bool:
        return self.env == "test"


# ---------------------------------------------------------------------------
# Cached factory
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide singleton ``Settings`` instance.

    lru_cache ensures we only parse env vars once per process, while
    still allowing tests to clear the cache and inject overrides via
    ``Settings(...)`` directly.
    """
    return Settings()
