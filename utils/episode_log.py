"""Per-episode file logging helpers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from slugify import slugify

if TYPE_CHECKING:
    from pathlib import Path


def open_episode_log(
    *,
    guid: str,
    podcast_title: str,
    episode_title: str,
    log_dir: Path,
    file_level: str = "DEBUG",
) -> tuple[logging.Logger, logging.FileHandler]:
    """Attach a per-episode FileHandler to the root logger.

    All log messages emitted by any logger while this handler is attached will
    be written to the episode log file.  The caller is responsible for calling
    :func:`close_episode_log` when episode processing is done, even on exception.

    Log path: ``<log_dir>/episodes/<YYYY-MM-DDTHH-MM-SS>.<podcast-slug>.<episode-slug>.log``

    Args:
        guid: Episode GUID, used to name the returned ``episode_logger``.
        podcast_title: Podcast title, slugified for the filename.
        episode_title: Episode title, slugified for the filename.
        log_dir: Parent log directory; a ``episodes/`` subdirectory is created inside it.
        file_level: Log level for the file handler. Defaults to ``DEBUG``.

    Returns:
        A ``(episode_logger, handler)`` tuple.  ``episode_logger`` is a named
        :class:`logging.Logger` (``episode.<guid>``) that propagates to the root
        logger; ``handler`` must be passed to :func:`close_episode_log`.

    """
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    episodes_dir = log_dir / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S")
    podcast_slug = slugify(podcast_title)
    episode_slug = slugify(episode_title)
    log_path = episodes_dir / f"{timestamp}.{podcast_slug}.{episode_slug}.log"

    handler_level_int = getattr(logging, file_level)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(handler_level_int)
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    handler._pac_prior_root_level = root.level  # noqa: SLF001
    if root.level > handler_level_int:
        root.setLevel(handler_level_int)
    root.addHandler(handler)
    episode_logger = logging.getLogger(f"episode.{guid}")
    return episode_logger, handler


def close_episode_log(handler: logging.FileHandler) -> None:
    """Remove *handler* from the root logger and close the underlying file.

    Args:
        handler: The :class:`logging.FileHandler` previously returned by
            :func:`open_episode_log`.

    """
    root = logging.getLogger()
    root.removeHandler(handler)
    prior = getattr(handler, "_pac_prior_root_level", None)
    if prior is not None:
        root.setLevel(prior)
    handler.close()
