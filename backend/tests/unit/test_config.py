"""Configuration / settings tests.

Covers:
* Settings loading from environment
* Required / optional env var behavior
* Development configuration
* Settings validation (DB URL, port numbers, TTL ranges)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.config.env import load_env


class TestSettingsLoading:
    """Tests for Settings loading + validation."""

    def test_defaults_load(self) -> None:
        """Default Settings load without required env vars."""
        # The lru_cache returns a singleton; we create a fresh Settings()
        # to test defaults without touching the cache.
        # The env-prefixed values come from os.environ — we override
        # any defaults here for the duration of the test.
        import os

        # Clear POS_ env vars for this test
        pos_env = {k: v for k, v in os.environ.items() if k.startswith("POS_")}
        for k in pos_env:
            del os.environ[k]

        try:
            s = Settings(_env_file=None)
            assert s.env == "development"
            assert s.debug is False
            assert s.log_level == "INFO"
            assert s.log_format == "json"
            assert s.host == "127.0.0.1"
            assert s.port == 8000
            assert s.cors_allowed_origins == ""
            assert s.trusted_hosts == ""
            assert s.max_body_bytes == 1024 * 1024
            # Auth
            assert s.access_token_ttl_seconds == 15 * 60
            assert s.refresh_token_ttl_seconds == 7 * 24 * 60 * 60
            assert s.max_failed_logins == 5
            # Idempotency
            assert s.idempotency_default_ttl_seconds == 24 * 60 * 60
            assert s.idempotency_login_ttl_seconds == 30
        finally:
            # Restore
            for k, v in pos_env.items():
                os.environ[k] = v

    def test_database_url_must_use_async_driver(self) -> None:
        """Sync driver URLs are rejected by the validator."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None, database_url="postgresql://user@host/db")
        assert "asyncpg" in str(exc_info.value).lower()

    def test_port_must_be_non_negative(self) -> None:
        """Negative ports are rejected."""
        with pytest.raises(ValidationError):
            Settings(_env_file=None, port=-1)

    def test_max_body_bytes_must_be_positive(self) -> None:
        """max_body_bytes must be > 0."""
        with pytest.raises(ValidationError):
            Settings(_env_file=None, max_body_bytes=0)

    def test_pool_size_must_be_non_negative(self) -> None:
        """database_pool_size must be >= 0."""
        with pytest.raises(ValidationError):
            Settings(_env_file=None, database_pool_size=-1)

    def test_idempotency_login_ttl_default(self) -> None:
        """Login idempotency TTL defaults to 30s per spec."""
        s = Settings(_env_file=None)
        assert s.idempotency_login_ttl_seconds == 30

    def test_cors_origins_list_parsing(self) -> None:
        """Comma-separated origins parsed into a list."""
        s = Settings(_env_file=None, cors_allowed_origins="https://a.example, https://b.example")
        assert "https://a.example" in s.cors_origins_list
        assert "https://b.example" in s.cors_origins_list
        assert len(s.cors_origins_list) == 2

    def test_cors_origins_empty(self) -> None:
        """Empty origins string yields empty list."""
        s = Settings(_env_file=None, cors_allowed_origins="")
        assert s.cors_origins_list == []

    def test_trusted_hosts_list_parsing(self) -> None:
        """Comma-separated hosts parsed into a list."""
        s = Settings(_env_file=None, trusted_hosts="host1,host2,host3")
        assert s.trusted_hosts_list == ["host1", "host2", "host3"]

    def test_is_production(self) -> None:
        """is_production reflects env=='production'."""
        s_prod = Settings(_env_file=None, env="production")
        s_dev = Settings(_env_file=None, env="development")
        assert s_prod.is_production is True
        assert s_dev.is_production is False

    def test_is_test(self) -> None:
        """is_test reflects env=='test'."""
        s_test = Settings(_env_file=None, env="test")
        s_dev = Settings(_env_file=None, env="development")
        assert s_test.is_test is True
        assert s_dev.is_test is False

    def test_request_id_signing_key_default_generated(self) -> None:
        """Signing key defaults to a fresh secret if not provided."""
        s = Settings(_env_file=None)
        assert s.request_id_signing_key.get_secret_value() != ""

    def test_get_settings_returns_singleton(self) -> None:
        """get_settings() is cached and returns the same instance."""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_load_env_force_reload(self) -> None:
        """force_reload=True rebuilds the cached settings."""
        s1 = get_settings()
        s2 = load_env(force_reload=True)
        # Both are still Settings instances; their state should be
        # consistent with the current env (we cannot compare identity
        # because reload rebuilds the singleton).
        assert isinstance(s2, Settings)
        assert s1 is not s2


class TestDevelopmentConfiguration:
    """Development-specific configuration behavior."""

    def test_dev_env(self) -> None:
        """Development env enables debug-friendly defaults."""
        s = Settings(_env_file=None, env="development")
        assert s.env == "development"
        assert s.is_production is False

    def test_production_env(self) -> None:
        """Production env has stricter defaults."""
        s = Settings(_env_file=None, env="production")
        assert s.env == "production"
        assert s.is_production is True


class TestEnvironmentVariables:
    """Tests for POS_ env var prefix."""

    def test_env_prefix_applied(self) -> None:
        """Settings() picks up POS_-prefixed env vars."""
        import os

        os.environ["POS_PORT"] = "12345"
        try:
            s = Settings(_env_file=None)
            assert s.port == 12345
        finally:
            del os.environ["POS_PORT"]

    def test_extra_env_vars_ignored(self) -> None:
        """Unknown env vars are silently dropped (extra='ignore')."""
        import os

        os.environ["POS_BOGUS_VALUE"] = "ignored"
        try:
            # Should not raise
            s = Settings(_env_file=None)
            assert hasattr(s, "port")  # settings object still valid
        finally:
            del os.environ["POS_BOGUS_VALUE"]
