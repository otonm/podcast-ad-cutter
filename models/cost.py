"""Shared cost-record protocol for API billing dataclasses."""

from __future__ import annotations

from typing import Protocol


class CostRecord(Protocol):
    """Structural type satisfied by any cost dataclass.

    Any dataclass with ``provider``, ``model``, and ``cost`` fields satisfies
    this protocol (e.g. ``TranscriptionCost``, ``TopicExtractionCost``,
    ``AdDetectionCost``).
    """

    provider: str
    model: str
    cost: float
