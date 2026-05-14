"""Event bus — typed asyncio broadcast for pipeline events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class PipelineEventType(StrEnum):
    """Enum of all pipeline event types (full set locked in Phase 1 per D-03)."""

    EPISODE_STAGE_CHANGED = "episode.stage_changed"
    DOWNLOAD_PROGRESS = "episode.download_progress"
    ENCODE_PROGRESS = "episode.encode_progress"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    EPISODE_COMPLETED = "episode.completed"
    EPISODE_FAILED = "episode.failed"


@dataclass
class PipelineEvent:
    """Typed event dataclass with a type discriminator field (D-01).

    Attributes:
        type: The event type discriminator.
        payload: Serializable event payload dict (exact shape defined per type in Phase 2).

    """

    type: PipelineEventType
    payload: dict  # type: ignore[type-arg]


class EventBus:
    """Broadcast-all asyncio event bus (D-02).

    Each subscriber receives its own asyncio.Queue. emit() puts every event
    on every subscriber's queue. subscribe() returns the queue; unsubscribe()
    removes it (SSE disconnect handler calls this in a finally block).
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[PipelineEvent]] = []

    def subscribe(self) -> asyncio.Queue[PipelineEvent]:
        """Create a new subscriber queue and return it."""
        raise NotImplementedError

    def unsubscribe(self, q: asyncio.Queue[PipelineEvent]) -> None:
        """Remove a subscriber queue."""
        raise NotImplementedError

    def emit(self, event: PipelineEvent) -> None:
        """Broadcast event to all subscribers. Silent no-op if no subscribers (D-04)."""
        raise NotImplementedError
