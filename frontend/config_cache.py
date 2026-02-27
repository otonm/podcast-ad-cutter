"""Cached AppConfig — loaded once at startup, refreshed by a file-watcher task.

All routes call get_config() instead of load_config() directly.
set_config() is called from the lifespan and after every config mutation.
"""

from config.config_loader import AppConfig

_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Return the cached config. Raises RuntimeError if not yet loaded."""
    if _config is None:
        raise RuntimeError("Config cache not initialised — lifespan must run first")
    return _config


def set_config(cfg: AppConfig) -> None:
    """Replace the cached config. Called at startup and after every write."""
    global _config
    _config = cfg
