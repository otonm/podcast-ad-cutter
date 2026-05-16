"""Tests for RunState dataclass defaults and reset semantics."""

from __future__ import annotations

from datetime import UTC, datetime

from api.run_state import VALID_STAGES, FeedRunCounts, RunState


class TestRunState:
    async def test_defaults(self) -> None:
        rs = RunState()
        assert rs.state == "idle"
        assert rs.started_at is None
        assert rs.active_feed_slug is None
        assert rs.current_episode_guid is None
        assert rs.task is None
        assert rs.feeds == {}

    async def test_valid_stages_tuple(self) -> None:
        assert VALID_STAGES == ("download", "transcribe", "topic", "ad-detect", "edit")

    async def test_stop_event_is_asyncio_event(self) -> None:
        import asyncio
        rs = RunState()
        assert isinstance(rs.stop_event, asyncio.Event)
        assert not rs.stop_event.is_set()

    async def test_reset_to_idle_restores_defaults(self) -> None:
        rs = RunState()
        rs.state = "running"
        rs.started_at = datetime.now(UTC)
        rs.active_feed_slug = "my-show"
        rs.current_episode_guid = "guid-123"
        rs.feeds["my-show"] = FeedRunCounts(episodes_total=3, episodes_done=1)
        rs.stop_event.set()

        rs.reset_to_idle()

        assert rs.state == "idle"
        assert rs.started_at is None
        assert rs.active_feed_slug is None
        assert rs.current_episode_guid is None
        assert rs.task is None
        assert rs.feeds == {}
        assert not rs.stop_event.is_set()

    async def test_reset_to_idle_clears_feeds(self) -> None:
        rs = RunState()
        rs.feeds["feed-a"] = FeedRunCounts(episodes_total=5, episodes_done=5)
        rs.feeds["feed-b"] = FeedRunCounts(episodes_total=2, episodes_done=1, episodes_failed=1)
        rs.reset_to_idle()
        assert rs.feeds == {}


class TestFeedRunCounts:
    async def test_defaults(self) -> None:
        fc = FeedRunCounts()
        assert fc.episodes_total == 0
        assert fc.episodes_done == 0
        assert fc.episodes_failed == 0

    async def test_custom_values(self) -> None:
        fc = FeedRunCounts(episodes_total=10, episodes_done=7, episodes_failed=2)
        assert fc.episodes_total == 10
        assert fc.episodes_done == 7
        assert fc.episodes_failed == 2
