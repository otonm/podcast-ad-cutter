"""Tests for Pipeline graceful-stop and run_state update hooks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from api.run_state import RunState
from components.pipeline import Pipeline
from config.config_loader import FeedConfig
from models.feed import Episode, ParsedFeed


def make_feed(title: str, *, enabled: bool = True) -> FeedConfig:
    return FeedConfig(
        title=title,
        url=f"https://example.com/{title}.rss",
        enabled=enabled,
        episodes_to_keep=10,
    )


def make_config(feeds: list[FeedConfig]) -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = feeds
    cfg.app.models.transcription.provider = "groq"
    cfg.app.models.transcription.model = "whisper-large-v3-turbo"
    cfg.app.models.context_extraction.provider = "openai"
    cfg.app.models.context_extraction.model = "gpt-4o-mini"
    cfg.app.models.ad_detection.provider = "openai"
    cfg.app.models.ad_detection.model = "gpt-4o-mini"
    cfg.app.output.file_type = "mp3"
    cfg.app.output.bitrate = "128k"
    cfg.credentials.groq_api_key = "sk-test"
    cfg.credentials.openai_api_key = "sk-openai-test"
    cfg.app.log.per_episode = False
    cfg.app.log.file_level = "DEBUG"
    return cfg


def _make_episode(guid: str, n: int = 1) -> Episode:
    return Episode(
        guid=guid,
        url=f"https://example.com/ep{n}.mp3",
        title=f"Episode {n}",
        pub_date=datetime(2026, 3, n, tzinfo=UTC),
    )


def _make_parsed_feed(title: str, episodes: list[Episode]) -> ParsedFeed:
    return ParsedFeed(
        config_title=title,
        feed_url=f"https://example.com/{title}.rss",
        title=title,
        episodes=episodes,
    )


def _patch_pipeline_internals(episodes: list[Episode], parsed: ParsedFeed):
    """Return a context manager that patches all I/O inside Pipeline.run()."""
    mock_db_obj = MagicMock()
    mock_db_cm = MagicMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db_obj)
    mock_db_cm.__aexit__ = AsyncMock(return_value=False)

    mock_store = AsyncMock()
    mock_store.save_episodes = AsyncMock()
    mock_store.get_episodes_for_feed = AsyncMock(return_value=episodes)
    mock_store.update_episode_url = AsyncMock()
    mock_store.is_skipped = AsyncMock(return_value=False)

    import contextlib

    @contextlib.contextmanager
    def _patches():
        with (
            patch("components.pipeline.FeedDownloader") as m_dl,
            patch("components.pipeline.FeedParser") as m_fp,
            patch("components.pipeline.FeedPublisher") as m_pub,
            patch("components.pipeline.Database", return_value=mock_db_cm),
            patch("components.pipeline.EpisodeStore", return_value=mock_store),
            patch("components.pipeline.TranscriptionStore") as m_ts,
            patch("components.pipeline.AudioMetadataStore"),
            patch("components.pipeline.CostTrackingStore"),
            patch("components.pipeline.TopicStore") as m_topic,
            patch("components.pipeline.AdStore") as m_ad,
            patch("components.pipeline.AdDetector"),
            patch("components.pipeline.TopicExtractor"),
            patch("components.pipeline.EpisodeTranscriptor"),
            patch("components.pipeline.EpisodeDownloader"),
            patch("components.pipeline.AudioProber"),
            patch("components.pipeline.AudioPreprocessor"),
            patch("components.pipeline.AdParser"),
            patch("components.pipeline.AudioEditor"),
            patch("components.pipeline.EpisodeCopier"),
        ):
            m_dl.return_value.download_all = AsyncMock(
                return_value=[(parsed.config_title, "<rss/>")]
            )
            m_fp.return_value.parse_all.return_value = [parsed]
            m_pub.return_value.publish = AsyncMock(return_value=Path("/out/feed.rss"))
            m_pub.return_value.update_episode_url = AsyncMock()

            m_ts.return_value.get_transcribed_guids = AsyncMock(return_value=set())
            m_ts.return_value.save_transcription = AsyncMock()
            m_ts.return_value.save_segments = AsyncMock()
            m_ts.return_value.get_segments_for_guid = AsyncMock(return_value=[])
            m_ts.return_value.get_transcription_text = AsyncMock(return_value="text")

            m_topic.return_value.get_extracted_guids = AsyncMock(return_value=set())
            m_topic.return_value.save_topic = AsyncMock()
            m_topic.return_value.get_topic_for_guid = AsyncMock(return_value=None)

            m_ad.return_value.get_detected_guids = AsyncMock(return_value=set())
            m_ad.return_value.get_segments_for_guid = AsyncMock(return_value=[])
            m_ad.return_value.save_segments = AsyncMock()
            m_ad.return_value.mark_detected = AsyncMock()

            yield

    return _patches()


class TestGracefulStop:
    async def test_stop_event_set_before_feed_loop_skips_all_episodes(self) -> None:
        ep1 = _make_episode("ep-1", 1)
        ep2 = _make_episode("ep-2", 2)
        episodes = [ep1, ep2]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        stop_event = asyncio.Event()
        stop_event.set()

        call_count = 0

        async def fake_process(**_kwargs) -> str:  # pragma: no cover
            nonlocal call_count
            call_count += 1
            return "done"

        with _patch_pipeline_internals(episodes, parsed):
            pipeline = Pipeline(config, stop_event=stop_event)
            with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                await pipeline.run()

        assert call_count == 0

    async def test_stop_event_set_after_first_episode_halts_before_second(self) -> None:
        ep1 = _make_episode("ep-1", 1)
        ep2 = _make_episode("ep-2", 2)
        episodes = [ep1, ep2]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        stop_event = asyncio.Event()
        call_count = 0

        async def fake_process(*, episode: Episode, **_kwargs) -> str:
            nonlocal call_count
            call_count += 1
            if episode.guid == "ep-1":
                stop_event.set()
            return "done"

        with _patch_pipeline_internals(episodes, parsed):
            pipeline = Pipeline(config, stop_event=stop_event)
            with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                await pipeline.run()

        assert call_count == 1

    async def test_no_stop_event_processes_all_episodes(self) -> None:
        ep1 = _make_episode("ep-1", 1)
        ep2 = _make_episode("ep-2", 2)
        episodes = [ep1, ep2]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        call_count = 0

        async def fake_process(**_kwargs) -> str:
            nonlocal call_count
            call_count += 1
            return "done"

        with _patch_pipeline_internals(episodes, parsed):
            pipeline = Pipeline(config)
            with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                await pipeline.run()

        assert call_count == 2

    async def test_stop_event_set_during_skipped_episode_halts_before_next(self) -> None:
        ep_skipped = _make_episode("ep-skipped", 1)
        ep_next = _make_episode("ep-next", 2)
        episodes = [ep_skipped, ep_next]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        stop_event = asyncio.Event()
        call_count = 0

        async def fake_process(**_kwargs) -> str:  # pragma: no cover
            nonlocal call_count
            call_count += 1
            return "done"

        async def is_skipped_with_stop(guid: str) -> bool:
            if guid == "ep-skipped":
                stop_event.set()
                return True
            return False  # pragma: no cover

        with _patch_pipeline_internals(episodes, parsed):
            with patch("components.pipeline.EpisodeStore") as mock_store_cls:
                mock_store_cls.return_value.save_episodes = AsyncMock()
                mock_store_cls.return_value.get_episodes_for_feed = AsyncMock(return_value=episodes)
                mock_store_cls.return_value.update_episode_url = AsyncMock()
                mock_store_cls.return_value.is_skipped = is_skipped_with_stop
                pipeline = Pipeline(config, stop_event=stop_event)
                with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                    await pipeline.run()

        assert call_count == 0

    async def test_stop_event_prevents_second_feed_from_running(self) -> None:
        ep1 = _make_episode("ep-1", 1)
        feed_cfg_a = make_feed("Feed A")
        feed_cfg_b = make_feed("Feed B")
        config = make_config([feed_cfg_a, feed_cfg_b])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"

        parsed_a = _make_parsed_feed("Feed A", [ep1])
        parsed_b = _make_parsed_feed("Feed B", [ep1])

        stop_event = asyncio.Event()
        call_count = 0

        async def fake_process(**_kwargs) -> str:
            nonlocal call_count
            call_count += 1
            stop_event.set()
            return "done"

        mock_db_obj = MagicMock()
        mock_db_cm = MagicMock()
        mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db_obj)
        mock_db_cm.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.save_episodes = AsyncMock()
        mock_store.get_episodes_for_feed = AsyncMock(return_value=[ep1])
        mock_store.update_episode_url = AsyncMock()
        mock_store.is_skipped = AsyncMock(return_value=False)

        with (
            patch("components.pipeline.FeedDownloader") as m_dl,
            patch("components.pipeline.FeedParser") as m_fp,
            patch("components.pipeline.FeedPublisher") as m_pub,
            patch("components.pipeline.Database", return_value=mock_db_cm),
            patch("components.pipeline.EpisodeStore", return_value=mock_store),
            patch("components.pipeline.TranscriptionStore") as m_ts,
            patch("components.pipeline.AudioMetadataStore"),
            patch("components.pipeline.CostTrackingStore"),
            patch("components.pipeline.TopicStore") as m_topic,
            patch("components.pipeline.AdStore") as m_ad,
            patch("components.pipeline.AdDetector"),
            patch("components.pipeline.TopicExtractor"),
            patch("components.pipeline.EpisodeTranscriptor"),
            patch("components.pipeline.EpisodeDownloader"),
            patch("components.pipeline.AudioProber"),
            patch("components.pipeline.AudioPreprocessor"),
            patch("components.pipeline.AdParser"),
            patch("components.pipeline.AudioEditor"),
            patch("components.pipeline.EpisodeCopier"),
        ):
            m_dl.return_value.download_all = AsyncMock(
                return_value=[("Feed A", "<rss/>"), ("Feed B", "<rss/>")]
            )
            m_fp.return_value.parse_all.return_value = [parsed_a, parsed_b]
            m_pub.return_value.publish = AsyncMock(return_value=Path("/out/feed.rss"))
            m_pub.return_value.update_episode_url = AsyncMock()
            m_ts.return_value.get_transcribed_guids = AsyncMock(return_value=set())
            m_ts.return_value.save_transcription = AsyncMock()
            m_ts.return_value.save_segments = AsyncMock()
            m_ts.return_value.get_segments_for_guid = AsyncMock(return_value=[])
            m_ts.return_value.get_transcription_text = AsyncMock(return_value="text")
            m_topic.return_value.get_extracted_guids = AsyncMock(return_value=set())
            m_topic.return_value.save_topic = AsyncMock()
            m_topic.return_value.get_topic_for_guid = AsyncMock(return_value=None)
            m_ad.return_value.get_detected_guids = AsyncMock(return_value=set())
            m_ad.return_value.get_segments_for_guid = AsyncMock(return_value=[])
            m_ad.return_value.save_segments = AsyncMock()
            m_ad.return_value.mark_detected = AsyncMock()

            pipeline = Pipeline(config, stop_event=stop_event)
            with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                await pipeline.run()

        assert call_count == 1


class TestRunStateUpdates:
    async def test_run_state_current_episode_set_during_processing(self) -> None:
        ep1 = _make_episode("ep-guid-1", 1)
        episodes = [ep1]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        run_state = RunState()
        observed_guid: str | None = None

        async def fake_process(**_kwargs) -> str:
            nonlocal observed_guid
            observed_guid = run_state.current_episode_guid
            return "done"

        with _patch_pipeline_internals(episodes, parsed):
            pipeline = Pipeline(config, run_state=run_state)
            with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                await pipeline.run()

        assert observed_guid == "ep-guid-1"
        assert run_state.current_episode_guid is None

    async def test_run_state_feed_counts_update_after_episode(self) -> None:
        ep1 = _make_episode("ep-guid-1", 1)
        episodes = [ep1]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        run_state = RunState()

        async def fake_process(**_kwargs) -> str:
            return "done"

        with _patch_pipeline_internals(episodes, parsed):
            pipeline = Pipeline(config, run_state=run_state)
            with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                await pipeline.run()

        assert "my-show" in run_state.feeds
        counts = run_state.feeds["my-show"]
        assert counts.episodes_done == 1
        assert counts.episodes_total == 1

    async def test_run_state_feed_counts_update_after_episode_failure(self) -> None:
        ep1 = _make_episode("ep-guid-1", 1)
        episodes = [ep1]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        run_state = RunState()

        async def fake_process_fail(**_kwargs) -> str:
            raise RuntimeError("episode failed")

        with _patch_pipeline_internals(episodes, parsed):
            pipeline = Pipeline(config, run_state=run_state)
            with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process_fail):
                await pipeline.run()

        assert "my-show" in run_state.feeds
        counts = run_state.feeds["my-show"]
        assert counts.episodes_failed == 1
        assert counts.episodes_total == 1


class TestSkippedEpisodeGuard:
    async def test_skipped_episode_is_not_processed(self) -> None:
        ep_skipped = _make_episode("ep-skipped", 1)
        ep_normal = _make_episode("ep-normal", 2)
        episodes = [ep_skipped, ep_normal]
        feed_cfg = make_feed("My Show")
        config = make_config([feed_cfg])
        config.app.paths.data_dir = MagicMock()
        config.app.paths.output_dir = MagicMock()
        config.app.paths.cache_dir = MagicMock()
        config.app.base_url = "http://localhost"
        parsed = _make_parsed_feed("My Show", episodes)

        processed_guids: list[str] = []
        skipped_guids = {"ep-skipped"}

        async def fake_process(*, episode: Episode, **_kwargs) -> str:
            processed_guids.append(episode.guid)
            return "done"

        async def guid_aware_is_skipped(guid: str) -> bool:
            return guid in skipped_guids

        with _patch_pipeline_internals(episodes, parsed):
            with patch("components.pipeline.EpisodeStore") as mock_store_cls:
                mock_store_cls.return_value.save_episodes = AsyncMock()
                mock_store_cls.return_value.get_episodes_for_feed = AsyncMock(return_value=episodes)
                mock_store_cls.return_value.update_episode_url = AsyncMock()
                mock_store_cls.return_value.is_skipped = guid_aware_is_skipped
                pipeline = Pipeline(config)
                with patch.object(pipeline, "_process_episode_until_final", side_effect=fake_process):
                    await pipeline.run()

        assert "ep-normal" in processed_guids
        assert "ep-skipped" not in processed_guids
        assert len(processed_guids) == 1
