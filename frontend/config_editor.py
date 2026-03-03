"""Read, mutate, and write config.yaml using PyYAML.

Known limitation: PyYAML does not preserve comments when round-tripping YAML.
Any comments in config.yaml will be lost after the first write.
"""

from pathlib import Path
from typing import cast

import yaml

# Default config path — overridden by web.py at startup.
_config_path: Path = Path("config.yaml")


def set_config_path(path: Path) -> None:
    """Override the default config path. Called by webui.py at startup."""
    global _config_path
    _config_path = path


def get_config_path() -> Path:
    """Return the active config file path."""
    return _config_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load() -> dict[str, object]:
    raw = yaml.safe_load(_config_path.read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"config.yaml must be a mapping, got {type(raw).__name__}")
    return cast("dict[str, object]", raw)


def _save(data: dict[str, object]) -> None:
    _config_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))


# ---------------------------------------------------------------------------
# Feed mutations
# ---------------------------------------------------------------------------


def add_feed(name: str, url: str, *, enabled: bool = True) -> None:
    """Append a new feed entry to config.yaml."""
    data = _load()
    existing = data.get("feeds")
    feeds: list[dict[str, object]] = cast("list[dict[str, object]]", existing) if existing else []
    feeds.append({"name": name, "url": url, "enabled": enabled})
    data["feeds"] = feeds
    _save(data)


def delete_feed(name: str) -> None:
    """Remove the feed with the given name from config.yaml."""
    data = _load()
    existing = data.get("feeds")
    feeds: list[dict[str, object]] = cast("list[dict[str, object]]", existing) if existing else []
    data["feeds"] = [f for f in feeds if f.get("name") != name]
    _save(data)


def toggle_feed(name: str) -> None:
    """Flip the enabled boolean for the feed with the given name."""
    data = _load()
    existing = data.get("feeds")
    feeds: list[dict[str, object]] = cast("list[dict[str, object]]", existing) if existing else []
    for feed in feeds:
        if feed.get("name") == name:
            feed["enabled"] = not bool(feed.get("enabled", True))
            break
    data["feeds"] = feeds
    _save(data)


def reorder_feeds(names: list[str]) -> None:
    """Reorder feeds in config.yaml to match the given name order.

    Any feed name not present in `names` is appended at the end unchanged.
    """
    data = _load()
    existing = data.get("feeds")
    feeds: list[dict[str, object]] = cast("list[dict[str, object]]", existing) if existing else []
    feed_map = {str(f.get("name", "")): f for f in feeds}
    reordered = [feed_map[n] for n in names if n in feed_map]
    leftover = [f for f in feeds if str(f.get("name", "")) not in names]
    data["feeds"] = reordered + leftover
    _save(data)


# ---------------------------------------------------------------------------
# Settings mutations
# ---------------------------------------------------------------------------


def update_settings(
    *,
    transcription_provider: str,
    transcription_model: str,
    interpretation_provider: str,
    interpretation_model: str,
    min_confidence: float,
    episodes_to_keep: int,
    verbose_log: bool,
) -> None:
    """Update model and confidence settings in config.yaml."""
    data = _load()

    raw_t = data.get("transcription")
    transcription: dict[str, object] = cast("dict[str, object]", raw_t) if raw_t else {}
    transcription["provider"] = transcription_provider
    transcription["model"] = transcription_model
    data["transcription"] = transcription

    raw_i = data.get("interpretation")
    interpretation: dict[str, object] = cast("dict[str, object]", raw_i) if raw_i else {}
    interpretation["provider"] = interpretation_provider
    interpretation["model"] = interpretation_model
    data["interpretation"] = interpretation

    raw_a = data.get("ad_detection")
    ad_detection: dict[str, object] = cast("dict[str, object]", raw_a) if raw_a else {}
    ad_detection["min_confidence"] = min_confidence
    data["ad_detection"] = ad_detection

    data["episodes_to_keep"] = episodes_to_keep

    raw_l = data.get("logging")
    logging_cfg: dict[str, object] = cast("dict[str, object]", raw_l) if raw_l else {}
    logging_cfg["level"] = "DEBUG" if verbose_log else "INFO"
    data["logging"] = logging_cfg

    _save(data)


def update_scheduler(*, enabled: bool, interval_minutes: int) -> None:
    """Update scheduler settings in config.yaml."""
    data = _load()
    scheduler: dict[str, object] = cast("dict[str, object]", data.get("scheduler") or {})
    scheduler["enabled"] = enabled
    scheduler["interval_minutes"] = interval_minutes
    data["scheduler"] = scheduler
    _save(data)
