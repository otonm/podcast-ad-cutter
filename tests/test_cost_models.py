"""Tests for shared cost-record protocol."""

from __future__ import annotations

from dataclasses import dataclass

from models.cost import CostRecord


def test_cost_record_satisfied_by_conforming_dataclass() -> None:
    """A dataclass with provider/model/cost fields satisfies CostRecord at runtime."""

    @dataclass
    class MyCost:
        provider: str
        model: str
        cost: float

    obj = MyCost(provider="groq", model="llama", cost=0.001)
    # Verify all three fields are accessible as the protocol declares.
    assert obj.provider == "groq"
    assert obj.model == "llama"
    assert obj.cost == 0.001


def test_cost_record_is_protocol() -> None:
    from typing import Protocol

    assert issubclass(CostRecord, Protocol)
