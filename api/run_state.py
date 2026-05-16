"""Shared mutable state for the active pipeline run, stored on the aiohttp app dict."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

VALID_STAGES: tuple[str, ...] = ("download", "transcribe", "topic", "ad-detect", "edit")


@dataclass(slots=True)
class FeedRunCounts:
    """Per-feed episode counters updated by the pipeline as it progresses."""

    episodes_total: int = 0
    episodes_done: int = 0
    episodes_failed: int = 0


@dataclass(slots=True)
class RunState:
    """Tracks the active pipeline run — task reference, timestamps, and per-feed counts."""

    state: str = "idle"
    started_at: datetime | None = None
    active_feed_slug: str | None = None
    current_episode_guid: str | None = None
    task: asyncio.Task | None = None  # type: ignore[type-arg]
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    feeds: dict[str, FeedRunCounts] = field(default_factory=dict)

    def reset_to_idle(self) -> None:
        """Reset all fields to their idle defaults."""
        self.state = "idle"
        self.started_at = None
        self.active_feed_slug = None
        self.current_episode_guid = None
        self.task = None
        self.stop_event.clear()
        self.feeds.clear()
