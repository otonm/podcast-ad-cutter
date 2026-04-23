"""Per-episode file logging helpers."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from slugify import slugify


def open_episode_log(
    *,
    guid: str,
    podcast_title: str,
    episode_title: str,
    log_dir: Path,
    file_level: str = "DEBUG",
) -> tuple[logging.Logger, logging.FileHandler, Path]:
    """Attach a per-episode FileHandler to the root logger.

    All log messages emitted by any logger while this handler is attached will
    be written to the episode log file.  The caller is responsible for calling
    :func:`close_episode_log` when episode processing is done, even on exception.

    Log path: ``<log_dir>/episodes/<podcast-slug>/<episode-slug>.<YYYY-MM-DDTHH-MM-SS>.log``

    Args:
        guid: Episode GUID, used to name the returned ``episode_logger``.
        podcast_title: Podcast title, slugified for the feed subdirectory name.
        episode_title: Episode title, slugified for the filename prefix.
        log_dir: Parent log directory; subdirectories are created inside it.
        file_level: Log level for the file handler. Defaults to ``DEBUG``.

    Returns:
        A ``(episode_logger, handler, log_path)`` tuple.  ``episode_logger`` is a
        named :class:`logging.Logger` (``episode.<guid>``) that propagates to the
        root logger; ``handler`` must be passed to :func:`close_episode_log`;
        ``log_path`` is the :class:`~pathlib.Path` of the written log file
        (its parent is the feed subdirectory, useful for rotation).

    """
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    podcast_slug = slugify(podcast_title)
    episode_slug = slugify(episode_title)
    feed_dir = log_dir / "episodes" / podcast_slug
    feed_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S")
    log_path = feed_dir / f"{episode_slug}.{timestamp}.log"

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
    return episode_logger, handler, log_path


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


def rotate_episode_logs(feed_dir: Path, keep_last: int) -> None:
    """Prune per-episode logs in feed_dir, keeping keep_last per episode slug.

    Groups ``*.log`` files by episode slug (the filename prefix before the
    timestamp), then for each group deletes the oldest files beyond keep_last.
    Sorted by modification time so the decision is independent of filename order.

    Args:
        feed_dir: Directory containing per-episode log files for one feed.
            If it does not exist, this function is a no-op.
        keep_last: Number of most-recent log files to retain per episode slug.
            Pass 0 to delete all files in every group.

    """
    if not feed_dir.exists():
        return
    if keep_last < 0:
        raise ValueError(f"keep_last must be >= 0, got {keep_last}")
    groups: dict[str, list[Path]] = {}
    for f in feed_dir.glob("*.log"):
        episode_slug = f.stem.rsplit(".", 1)[0]
        groups.setdefault(episode_slug, []).append(f)
    for files in groups.values():
        files.sort(key=lambda p: p.stat().st_mtime)
        to_delete = files[:-keep_last] if keep_last > 0 else files
        for old_file in to_delete:
            old_file.unlink()
