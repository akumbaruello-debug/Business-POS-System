"""Environment loading helpers.

This is a thin wrapper over pydantic-settings; kept as a separate
module so that future ``dotenv``-style helpers (e.g. compose,
multi-file) can live here without disturbing ``settings.py``.
"""

from __future__ import annotations

from app.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings", "load_env"]


def load_env(*, force_reload: bool = False) -> Settings:
    """Load (or reload) the application settings.

    Args:
        force_reload: If True, drop the lru_cache and re-read env vars.
            Tests use this to inject overrides.

    Returns:
        The (possibly newly-loaded) ``Settings`` instance.
    """
    if force_reload:
        get_settings.cache_clear()
    return get_settings()
