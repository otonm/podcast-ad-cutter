"""Tests for Pipeline — feed orchestration."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from api.event_bus import EventBus, PipelineEvent, PipelineEventType
from components.pipeline import Pipeline, _Stores
from config.config_loader import FeedConfig
from models.ad_detection import AdDetectionCost, AdSegment, AdSegmentDetection, CutRange
from models.feed import AudioMetadata, Episode, FeedParseInput, ParsedFeed, PublisherInput
from models.topic import TopicExtraction, TopicExtractionCost
from models.transcription import Transcription, TranscriptionCost, TranscriptionSegment
from utils.exceptions import FfmpegError, TranscriptionError

# A non-empty AdSegment used as default in tests that require Guard 2 to proceed.
_DEFAULT_AD_SEGMENT = AdSegment(
    guid="ep-1", start_ms=0, end_ms=5000, confidence=0.9,
    sponsor=None, ad_topic=None, indices=[0],
)

# A CutRange returned by AdParser when ad segments meet the threshold.
_DEFAULT_CUT_RANGE = CutRange(start_ms=0, end_ms=5000)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Branch-test helpers
# ---------------------------------------------------------------------------


def _branch_config(
    output_dir: Path | MagicMock, *, episode: Episode | None = None
) -> tuple[MagicMock, Episode, ParsedFeed]:
    """Build config/episode/parsed-feed for decision-tree branch tests."""
    ep = episode or Episode(
        guid="ep-1",
        url="https://example.com/ep.mp3",
        title="My Episode",
        pub_date=datetime(2026, 3, 22, tzinfo=UTC),
    )
    feed_cfg = FeedConfig(
        title="My Podcast", url="http://x.com/feed", enabled=True, episodes_to_keep=5
    )
    config = MagicMock()
    config.app.feeds = [feed_cfg]
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = output_dir
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "http://localhost"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.app.log.per_episode = False
    config.app.log.file_level = "DEBUG"
    parsed = ParsedFeed(
        config_title="My Podcast",
        feed_url="http://x.com/feed",
        title="My Podcast",
        episodes=[ep],
    )
    return config, ep, parsed


def _wire_branch_mocks(
    m_dl: MagicMock,
    m_fp: MagicMock,
    m_pub: MagicMock,
    m_db: MagicMock,
    m_store: MagicMock,
    m_ts: MagicMock,
    m_ams: MagicMock,
    m_cs: MagicMock,
    m_ep_dl: MagicMock,
    m_prober: MagicMock,
    m_prep: MagicMock,
    m_trans: MagicMock,
    m_ad_store: MagicMock,
    m_topic_ext: MagicMock,
    m_topic_store: MagicMock,
    m_ad_detector: MagicMock,
    m_ad_parser: MagicMock,
    m_audio_editor: MagicMock,
    m_episode_copier: MagicMock,
    *,
    episodes: list[Episode],
    parsed: ParsedFeed,
    transcribed_guids: set[str],
    extracted_guids: set[str] | None = None,
    ad_segments: list[AdSegment] | None = None,
) -> None:
    """Wire standard mocks for all branch and error tests."""
    m_dl.return_value.download_all = AsyncMock(return_value=[("My Podcast", "<rss/>")])
    m_fp.return_value.parse_all.return_value = [parsed]

    mock_db_obj = MagicMock()
    m_db.return_value.__aenter__ = AsyncMock(return_value=mock_db_obj)
    m_db.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_store = AsyncMock()
    mock_store.save_episodes = AsyncMock()
    mock_store.get_episodes_for_feed = AsyncMock(return_value=episodes)
    mock_store.get_guids_for_feed = AsyncMock(return_value=set())
    mock_store.update_episode_url = AsyncMock()
    m_store.return_value = mock_store

    m_pub.return_value.publish = AsyncMock(return_value=Path("/out/my-podcast.rss"))
    m_pub.return_value.update_episode_url = AsyncMock()

    m_ts.return_value.get_transcribed_guids = AsyncMock(return_value=transcribed_guids)
    m_ts.return_value.save_transcription = AsyncMock()
    m_ts.return_value.save_segments = AsyncMock()
    m_ts.return_value.get_segments_for_guid = AsyncMock(return_value=[])
    m_ts.return_value.get_transcription_text = AsyncMock(return_value="Hello world")

    m_ams.return_value.save_all = AsyncMock()
    m_cs.return_value.save_cost = AsyncMock()

    m_topic_store.return_value.get_extracted_guids = AsyncMock(
        return_value=extracted_guids if extracted_guids is not None else set()
    )
    m_topic_store.return_value.save_topic = AsyncMock()
    m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
    m_topic_ext.return_value.extract = AsyncMock(return_value=(
        "ep-1",
        TopicExtraction(
            guid="ep-1",
            podcast="My Podcast",
            title="My Episode",
            topic="The topic.",
            hosts="Host A",
            show="My Show",
        ),
        TopicExtractionCost(provider="openai", model="gpt-4o-mini", cost=0.0001),
    ))

    meta = AudioMetadata(guid="ep-1", duration=60.0, codec="aac", channels=1, bitrate=32000)
    m_ep_dl.return_value.download = AsyncMock(return_value=Path("/cache/ep.mp3"))
    m_prober.return_value.probe = AsyncMock(return_value=meta)
    m_prep.return_value.preprocess = AsyncMock(return_value=Path("/cache/ep.mono.m4a"))
    m_trans.return_value.transcribe = AsyncMock(return_value=(
        "ep-1",
        Transcription(guid="ep-1", text="Hello world"),
        [TranscriptionSegment(guid="ep-1", start_ms=0, end_ms=1000, text="Hello")],
        TranscriptionCost(provider="groq", model="whisper-large-v3-turbo", cost=0.001),
    ))
    m_ad_store.return_value.get_detected_guids = AsyncMock(return_value=set())
    m_ad_store.return_value.get_segments_for_guid = AsyncMock(
        return_value=ad_segments if ad_segments is not None else []
    )
    m_ad_store.return_value.save_segments = AsyncMock()
    m_ad_store.return_value.mark_detected = AsyncMock()
    m_ad_detector.return_value.detect = AsyncMock(return_value=(
        "ep-1",
        [],
        AdDetectionCost(provider="openai", model="gpt-4o-mini", cost=0.0001),
    ))
    m_ad_parser.return_value.parse = MagicMock(return_value=[])
    m_audio_editor.return_value.edit = AsyncMock(return_value=None)

    mock_copy_dest = MagicMock()
    mock_copy_dest.stat.return_value.st_size = 1024
    m_episode_copier.return_value.copy = AsyncMock(
        return_value=("ep-1", mock_copy_dest, "http://localhost/my-podcast/22.03.2026-my-episode.mp3")
    )


# ---------------------------------------------------------------------------
# Feed-selection tests
# ---------------------------------------------------------------------------


async def test_run_passes_only_enabled_feeds() -> None:
    """Disabled feeds must not be forwarded to the downloader."""
    enabled = make_feed("enabled")
    disabled = make_feed("disabled", enabled=False)
    config = make_config([enabled, disabled])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("enabled", "<xml/>")])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([("enabled", enabled.url)])


async def test_run_preserves_config_order() -> None:
    """Enabled feeds must be forwarded in the order they appear in config."""
    feed_a, feed_b, feed_c = make_feed("a"), make_feed("b"), make_feed("c")
    config = make_config([feed_a, feed_b, feed_c])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with(
        [("a", feed_a.url), ("b", feed_b.url), ("c", feed_c.url)]
    )


async def test_run_returns_parser_result() -> None:
    """Pipeline.run() returns what FeedParser.parse_all() returns."""
    feed = make_feed("test")
    parsed = [ParsedFeed(config_title="test", feed_url=feed.url, title="test")]
    config = make_config([feed])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.FeedPublisher") as mock_publisher_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("test", "<rss/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.get_episodes_for_feed = AsyncMock(return_value=[])
        mock_store.get_guids_for_feed = AsyncMock(return_value=set())
        mock_store_cls.return_value = mock_store
        mock_publisher_cls.return_value = AsyncMock()
        pipeline = Pipeline(config)
        result = await pipeline.run()

    assert result == parsed


async def test_run_calls_feed_parser() -> None:
    """Pipeline.run() passes FeedParseInput objects to FeedParser.parse_all."""
    feed = make_feed("test")
    config = make_config([feed])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("test", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=[])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        await pipeline.run()

    expected_input = FeedParseInput(
        config_title="test",
        feed_url=feed.url,
        episodes_to_keep=feed.episodes_to_keep,
        xml_text="<xml/>",
    )
    mock_fp.parse_all.assert_called_once_with([expected_input])


async def test_run_with_no_enabled_feeds() -> None:
    """When all feeds are disabled the downloader is called with an empty list."""
    disabled = make_feed("disabled", enabled=False)
    config = make_config([disabled])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        result = await pipeline.run()

    mock_dl.download_all.assert_called_once_with([])
    assert result == []


async def test_run_with_feed_name_forces_disabled_feed() -> None:
    """--feed must process a disabled feed, ignoring enabled=False."""
    disabled = make_feed("target", enabled=False)
    other = make_feed("other", enabled=True)
    config = make_config([disabled, other])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("target", "<xml/>")])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([("target", disabled.url)])


async def test_run_with_feed_name_excludes_other_feeds() -> None:
    """--feed must pass only the named feed, even when others are enabled."""
    target = make_feed("target", enabled=True)
    other = make_feed("other", enabled=True)
    config = make_config([target, other])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("target", "<xml/>")])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([("target", target.url)])


async def test_run_with_unknown_feed_name_raises() -> None:
    """--feed with a title that matches no feed must raise ValueError."""
    feed = make_feed("existing")
    config = make_config([feed])

    with patch("components.pipeline.FeedDownloader"):
        pipeline = Pipeline(config, feed_name="nonexistent")
        with pytest.raises(ValueError, match="nonexistent"):
            await pipeline.run()


# ---------------------------------------------------------------------------
# FeedPublisher integration
# ---------------------------------------------------------------------------


async def test_run_calls_feed_publisher() -> None:
    """Pipeline.run() must call FeedPublisher.publish() once per parsed feed."""
    feed = make_feed("My Podcast")
    config = make_config([feed])
    config.app.base_url = "https://podcasts.example.com"
    config.app.paths.output_dir = Path("/output")
    ep = Episode(guid="g1", url="http://x.com/ep.mp3", title="Ep 1")
    parsed = [ParsedFeed(config_title="My Podcast", feed_url="http://x.com/feed", title="My Podcast", episodes=[ep])]

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.FeedPublisher") as mock_publisher_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("My Podcast", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.get_episodes_for_feed = AsyncMock(return_value=[])
        mock_store.get_guids_for_feed = AsyncMock(return_value=set())
        mock_store_cls.return_value = mock_store
        mock_publisher = AsyncMock()
        mock_publisher_cls.return_value = mock_publisher
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_publisher.publish.assert_awaited_once()
    publisher_input: PublisherInput = mock_publisher.publish.call_args[0][0]
    assert publisher_input.base_url == "https://podcasts.example.com"
    assert publisher_input.title == "My Podcast"


async def test_run_passes_new_channel_fields_to_publisher() -> None:
    """The 10 new channel-level fields on ParsedFeed must reach PublisherInput unchanged."""
    feed = make_feed("My Podcast")
    config = make_config([feed])
    config.app.base_url = "https://podcasts.example.com"
    config.app.paths.output_dir = Path("/output")

    # Build a ParsedFeed with all 10 new channel fields populated.
    parsed = [
        ParsedFeed(
            config_title="My Podcast",
            feed_url="http://x.com/feed",
            title="My Podcast",
            itunes_type="episodic",
            itunes_subtitle="A short subtitle",
            itunes_summary="A long summary",
            owner_name="Test Owner",
            owner_email="owner@test.com",
            image_title="My Podcast Image",
            image_link="https://example.com",
            content_encoded="<p>Encoded content</p>",
            itunes_new_feed_url="https://new.example.com/feed",
            itunes_complete=True,
        )
    ]

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.FeedPublisher") as mock_publisher_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("My Podcast", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.get_episodes_for_feed = AsyncMock(return_value=[])
        mock_store.get_guids_for_feed = AsyncMock(return_value=set())
        mock_store_cls.return_value = mock_store
        mock_publisher = AsyncMock()
        mock_publisher_cls.return_value = mock_publisher
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_publisher.publish.assert_awaited_once()
    pi: PublisherInput = mock_publisher.publish.call_args[0][0]
    assert pi.itunes_type == "episodic"
    assert pi.itunes_subtitle == "A short subtitle"
    assert pi.itunes_summary == "A long summary"
    assert pi.owner_name == "Test Owner"
    assert pi.owner_email is None  # email is scrubbed to prevent podcast directory matching
    assert pi.image_title == "My Podcast Image"
    assert pi.image_link == "https://example.com"
    assert pi.content_encoded == "<p>Encoded content</p>"
    assert pi.itunes_new_feed_url == "https://new.example.com/feed"
    assert pi.itunes_complete is True


async def test_run_saves_parsed_episodes() -> None:
    """Pipeline must call save_episodes once per successfully parsed feed."""
    feed = make_feed("Feed A")
    config = make_config([feed])
    ep = Episode(guid="g1", url="http://x.com/ep.mp3", title="Ep 1")
    parsed = [ParsedFeed(config_title="Feed A", feed_url="http://x.com/feed", title="Feed A", episodes=[ep])]

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.FeedPublisher") as mock_pub_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("Feed A", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.get_guids_for_feed = AsyncMock(return_value=set())
        mock_store_cls.return_value = mock_store
        mock_pub_cls.return_value.publish = AsyncMock(return_value=MagicMock())
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_store.save_episodes.assert_awaited_once_with("Feed A", [ep])


# ---------------------------------------------------------------------------
# _on_download_progress branch coverage
# ---------------------------------------------------------------------------


async def test_on_download_progress_starting(caplog: pytest.LogCaptureFixture) -> None:
    """Progress callback at 0.0 logs a 'Downloading' message."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with caplog.at_level(logging.DEBUG, logger="components.pipeline"):
        await pipeline._on_download_progress("ep-001", 0.0)

    assert "Downloading episode 'ep-001'" in caplog.text


async def test_on_download_progress_complete(caplog: pytest.LogCaptureFixture) -> None:
    """Progress callback at 1.0 logs a 'downloaded' message."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with caplog.at_level(logging.DEBUG, logger="components.pipeline"):
        await pipeline._on_download_progress("ep-001", 1.0)

    assert "Episode 'ep-001' downloaded." in caplog.text


async def test_on_download_progress_complete_tty() -> None:
    """Progress callback at 1.0 writes a newline to stderr when stdout is a TTY."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with patch("sys.stderr") as mock_stderr:
        mock_stderr.isatty.return_value = True
        await pipeline._on_download_progress("ep-001", 1.0)

    mock_stderr.write.assert_called_once_with("\n")
    mock_stderr.flush.assert_called_once()


async def test_on_download_progress_intermediate_tty() -> None:
    """Progress callback at an intermediate value writes percentage in-place when TTY."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with patch("sys.stderr") as mock_stderr:
        mock_stderr.isatty.return_value = True
        await pipeline._on_download_progress("ep-001", 0.5)

    mock_stderr.write.assert_called_once_with("\r  Episode 'ep-001': 50%")
    mock_stderr.flush.assert_called_once()


async def test_on_download_progress_intermediate_non_tty() -> None:
    """Progress callback at an intermediate value writes nothing when not a TTY."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with patch("sys.stderr") as mock_stderr:
        mock_stderr.isatty.return_value = False
        await pipeline._on_download_progress("ep-001", 0.5)

    mock_stderr.write.assert_not_called()
    mock_stderr.flush.assert_not_called()


# ---------------------------------------------------------------------------
# _on_preprocess_progress branch coverage
# ---------------------------------------------------------------------------


async def test_on_preprocess_progress_starting(caplog: pytest.LogCaptureFixture) -> None:
    """Progress callback at 0.0 logs a 'Preprocessing' message."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with caplog.at_level(logging.DEBUG, logger="components.pipeline"):
        await pipeline._on_preprocess_progress("ep-001", 0.0)

    assert "Preprocessing episode 'ep-001'" in caplog.text


async def test_on_preprocess_progress_complete(caplog: pytest.LogCaptureFixture) -> None:
    """Progress callback at 1.0 logs a 'preprocessed' message."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with caplog.at_level(logging.DEBUG, logger="components.pipeline"):
        await pipeline._on_preprocess_progress("ep-001", 1.0)

    assert "Episode 'ep-001' preprocessed." in caplog.text


async def test_on_preprocess_progress_complete_tty() -> None:
    """Progress callback at 1.0 writes a newline to stderr when stdout is a TTY."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with patch("sys.stderr") as mock_stderr:
        mock_stderr.isatty.return_value = True
        await pipeline._on_preprocess_progress("ep-001", 1.0)

    mock_stderr.write.assert_called_once_with("\n")
    mock_stderr.flush.assert_called_once()


async def test_on_preprocess_progress_intermediate_tty() -> None:
    """Progress callback at an intermediate value writes percentage in-place when TTY."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with patch("sys.stderr") as mock_stderr:
        mock_stderr.isatty.return_value = True
        await pipeline._on_preprocess_progress("ep-001", 0.5)

    mock_stderr.write.assert_called_once_with("\r  Episode 'ep-001': 50%")
    mock_stderr.flush.assert_called_once()


async def test_on_preprocess_progress_intermediate_non_tty() -> None:
    """Progress callback at an intermediate value writes nothing when not a TTY."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
    ):
        pipeline = Pipeline(config)

    with patch("sys.stderr") as mock_stderr:
        mock_stderr.isatty.return_value = False
        await pipeline._on_preprocess_progress("ep-001", 0.5)

    mock_stderr.write.assert_not_called()
    mock_stderr.flush.assert_not_called()


# ---------------------------------------------------------------------------
# Transcriptor constructor
# ---------------------------------------------------------------------------


async def test_pipeline_constructs_transcriptor_with_model_id_and_key() -> None:
    """Pipeline resolves provider/model/api_key and passes them as primitives to EpisodeTranscriptor."""
    feed_cfg = FeedConfig(title="Trans Podcast", url="http://x.com/feed", enabled=True, episodes_to_keep=5)
    config = MagicMock()
    config.app.feeds = [feed_cfg]
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-groq-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "http://localhost"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor") as mock_trans_cls,
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        Pipeline(config)

    mock_trans_cls.assert_called_once_with(
        provider="groq",
        model="whisper-large-v3-turbo",
        api_key="sk-groq-test",
    )


# ---------------------------------------------------------------------------
# Per-episode decision-tree branch tests
# ---------------------------------------------------------------------------


async def test_branch_a_transcription_and_audio_exist(tmp_path: Path) -> None:
    """Branch A: both transcription and audio exist — only URL update, no processing."""
    audio_file = tmp_path / "my-podcast" / "22.03.2026-my-episode.mp3"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(tmp_path)

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_not_called()
    m_prober.return_value.probe.assert_not_called()
    m_prep.return_value.preprocess.assert_not_called()
    m_trans.return_value.transcribe.assert_not_called()
    m_topic_ext.return_value.extract.assert_not_called()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()


async def test_branch_b_transcription_exists_no_audio_redownloads_and_copies(
    tmp_path: Path,
) -> None:
    """Branch B: transcription and ad segments exist, no output — re-download and copy (no qualifying cuts)."""
    config, ep, parsed = _branch_config(MagicMock())  # MagicMock → glob returns empty → no audio

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once_with(
        "ep-1", "https://example.com/ep.mp3", on_progress=ANY
    )
    m_prober.return_value.probe.assert_awaited_once()
    m_ams.return_value.save_all.assert_awaited_once()
    m_prep.return_value.preprocess.assert_not_called()  # no preprocess when transcript cached
    m_trans.return_value.transcribe.assert_not_called()
    m_episode_copier.return_value.copy.assert_awaited_once()


async def test_branch_c_audio_exists_no_transcription_transcribes_from_output(
    tmp_path: Path,
) -> None:
    """Branch C: cached audio exists, no transcription — probe+preprocess+transcribe+ad detect; no download."""
    # Branch C is triggered by cached audio (in cache_dir), NOT output file.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached_file = cache_dir / "ep-1.mp3"  # named {guid}.{ext}
    cached_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(MagicMock())  # output_dir is MagicMock -> no output file
    config.app.paths.cache_dir = cache_dir

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_not_called()
    m_prober.return_value.probe.assert_awaited_once_with("ep-1", cached_file)
    m_prep.return_value.preprocess.assert_awaited_once_with(
        "ep-1", cached_file, 60.0, on_progress=ANY
    )
    m_trans.return_value.transcribe.assert_awaited_once()
    m_ts.return_value.save_transcription.assert_awaited_once()
    m_ts.return_value.save_segments.assert_awaited_once()
    m_topic_ext.return_value.extract.assert_awaited_once()
    m_topic_store.return_value.save_topic.assert_awaited_once()
    assert m_cs.return_value.save_cost.await_count == 3  # transcription cost + topic cost + ad cost
    m_ad_detector.return_value.detect.assert_awaited_once()


async def test_branch_d_no_transcription_no_audio_full_pipeline() -> None:
    """Branch D: nothing exists — full pipeline runs (download+probe+preprocess+transcribe+copy)."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once()
    m_prober.return_value.probe.assert_awaited_once()
    m_ams.return_value.save_all.assert_awaited_once()
    m_prep.return_value.preprocess.assert_awaited_once()
    m_trans.return_value.transcribe.assert_awaited_once()
    m_ts.return_value.save_transcription.assert_awaited_once()
    m_ts.return_value.save_segments.assert_awaited_once()
    m_topic_ext.return_value.extract.assert_awaited_once()
    m_topic_store.return_value.save_topic.assert_awaited_once()
    assert m_cs.return_value.save_cost.await_count == 3  # transcription cost + topic cost + ad cost


async def test_download_uses_fresh_feed_url_over_stale_db_url() -> None:
    """Download uses the URL from the current-run RSS parse, not the potentially stale DB URL.

    The DB may hold a signed URL from a previous run that has since expired.
    The fresh URL parsed from the RSS feed on this run should be preferred.
    """
    stale_url = "https://cdn.example.com/ep.mp3?Expires=1000&Signature=OLD"
    fresh_url = "https://cdn.example.com/ep.mp3?Expires=9999999999&Signature=NEW"

    # DB episode carries the stale URL.
    db_ep = Episode(
        guid="ep-1",
        url=stale_url,
        title="My Episode",
        pub_date=datetime(2026, 3, 22, tzinfo=UTC),
    )
    # The parsed feed for this run carries the fresh URL for the same GUID.
    fresh_ep = Episode(
        guid="ep-1",
        url=fresh_url,
        title="My Episode",
        pub_date=datetime(2026, 3, 22, tzinfo=UTC),
    )
    config, _, _ = _branch_config(MagicMock())
    parsed = ParsedFeed(
        config_title="My Podcast",
        feed_url="http://x.com/feed",
        title="My Podcast",
        episodes=[fresh_ep],
    )

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            # DB returns the stale-URL episode; parsed feed has the fresh URL.
            episodes=[db_ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once_with(
        "ep-1", fresh_url, on_progress=ANY
    )


# ---------------------------------------------------------------------------
# Error handling — loop independence
# ---------------------------------------------------------------------------


async def test_download_error_skips_episode_continues_loop() -> None:
    """A download error on ep1 does not prevent ep2 from being processed."""
    ep1 = Episode(guid="ep-1", url="https://example.com/ep1.mp3", title="Ep 1",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    ep2 = Episode(guid="ep-2", url="https://example.com/ep2.mp3", title="Ep 2",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    config, _, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep1, ep2], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = AsyncMock(side_effect=[
            RuntimeError("network failure"),
            Path("/cache/ep-2.mp3"),
        ])
        pipeline = Pipeline(config)
        await pipeline.run()

    assert m_ep_dl.return_value.download.await_count == 2
    assert m_trans.return_value.transcribe.await_count == 1


async def test_transcribe_error_skips_episode_continues_loop() -> None:
    """A transcription error on ep1 does not prevent ep2 from being processed."""
    ep1 = Episode(guid="ep-1", url="https://example.com/ep1.mp3", title="Ep 1",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    ep2 = Episode(guid="ep-2", url="https://example.com/ep2.mp3", title="Ep 2",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    config, _, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep1, ep2], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_trans.return_value.transcribe = AsyncMock(side_effect=[
            RuntimeError("transcription failed"),
            ("ep-2", Transcription(guid="ep-2", text="ok"),
             [], TranscriptionCost(provider="groq", model="w", cost=0.0)),
        ])
        pipeline = Pipeline(config)
        await pipeline.run()

    assert m_trans.return_value.transcribe.await_count == 2
    # ep1 failed before reaching Guard 2; ep2 reaches Guard 2 with no qualifying cuts → copy
    m_episode_copier.return_value.copy.assert_awaited()


async def test_preprocess_error_skips_episode_continues_loop() -> None:
    """A preprocess error on ep1 leaves ep2 fully processed."""
    ep1 = Episode(guid="ep-1", url="https://example.com/ep1.mp3", title="Ep 1",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    ep2 = Episode(guid="ep-2", url="https://example.com/ep2.mp3", title="Ep 2",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    config, _, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep1, ep2], parsed=parsed, transcribed_guids=set(),
        )
        m_prep.return_value.preprocess = AsyncMock(side_effect=[
            RuntimeError("ffmpeg error"),
            Path("/cache/ep-2.mono.m4a"),
        ])
        pipeline = Pipeline(config)
        await pipeline.run()

    assert m_prep.return_value.preprocess.await_count == 2
    # transcribe only called once (ep1 never reached it)
    assert m_trans.return_value.transcribe.await_count == 1


async def test_multiple_episodes_independent_failures() -> None:
    """Two consecutive failures do not prevent the third episode from being processed."""
    ep1 = Episode(guid="ep-1", url="https://example.com/ep1.mp3", title="Ep 1",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    ep2 = Episode(guid="ep-2", url="https://example.com/ep2.mp3", title="Ep 2",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    ep3 = Episode(guid="ep-3", url="https://example.com/ep3.mp3", title="Ep 3",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    config, _, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep1, ep2, ep3], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        # ep1 download fails, ep2 preprocess fails, ep3 succeeds
        m_ep_dl.return_value.download = AsyncMock(side_effect=[
            RuntimeError("ep1 download"),
            Path("/cache/ep-2.mp3"),
            Path("/cache/ep-3.mp3"),
        ])
        m_prep.return_value.preprocess = AsyncMock(side_effect=[
            RuntimeError("ep2 preprocess"),
            Path("/cache/ep-3.mono.m4a"),
        ])
        pipeline = Pipeline(config)
        await pipeline.run()

    assert m_ep_dl.return_value.download.await_count == 3
    assert m_prep.return_value.preprocess.await_count == 2
    assert m_trans.return_value.transcribe.await_count == 1
    # ep3 succeeds with no qualifying cuts → copy (not edit)
    m_episode_copier.return_value.copy.assert_awaited()


# ---------------------------------------------------------------------------
# Constructor wiring — AdDetector, AdParser, AudioEditor, AdStore
# ---------------------------------------------------------------------------


def _make_wiring_config() -> MagicMock:
    """Minimal config for constructor wiring tests."""
    feed_cfg = FeedConfig(title="Pod", url="http://x.com/feed", enabled=True, episodes_to_keep=5)
    config = MagicMock()
    config.app.feeds = [feed_cfg]
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    return config


async def test_pipeline_constructs_ad_detector() -> None:
    """Pipeline resolves provider/model/api_key and passes them as primitives to AdDetector."""
    config = _make_wiring_config()

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector") as mock_ad_detector_cls,
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        Pipeline(config)

    mock_ad_detector_cls.assert_called_once_with(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-openai-test",
        context_window=None,
    )


async def test_pipeline_constructs_ad_parser() -> None:
    """Pipeline instantiates AdParser() with no arguments."""
    config = _make_wiring_config()

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser") as mock_ad_parser_cls,
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        Pipeline(config)

    mock_ad_parser_cls.assert_called_once_with()


async def test_pipeline_constructs_audio_editor() -> None:
    """Pipeline passes output_dir/file_type/bitrate as primitives to AudioEditor."""
    config = _make_wiring_config()

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor") as mock_audio_editor_cls,
        patch("components.pipeline.EpisodeCopier"),
    ):
        Pipeline(config)

    mock_audio_editor_cls.assert_called_once_with(
        output_dir=config.app.paths.output_dir,
        file_type="mp3",
        bitrate="128k",
    )


async def test_pipeline_constructs_episode_copier() -> None:
    """Pipeline.__init__ must instantiate EpisodeCopier with output_dir and base_url."""
    config = _make_wiring_config()
    config.app.base_url = "https://example.com"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier") as mock_copier_cls,
    ):
        Pipeline(config)

    mock_copier_cls.assert_called_once_with(
        output_dir=config.app.paths.output_dir,
        base_url="https://example.com",
    )


async def test_run_loads_ad_detected_guids_before_episode_loop() -> None:
    """run() creates AdStore(db.conn) and awaits get_detected_guids() before processing episodes."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_db_obj = m_db.return_value.__aenter__.return_value
    assert m_ad_store.call_count == 1
    assert m_ad_store.call_args == call(mock_db_obj.conn)
    m_ad_store.return_value.get_detected_guids.assert_awaited_once()


async def test_branch_b_audio_editor_returns_path_uses_computed_url() -> None:
    """Branch B: when AudioEditor.edit() returns a path, the URL is derived from it."""
    config, ep, parsed = _branch_config(MagicMock())
    output_file = MagicMock()
    output_file.stat.return_value.st_size = 0

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ad_parser.return_value.parse = MagicMock(return_value=[_DEFAULT_CUT_RANGE])
        m_audio_editor.return_value.edit = AsyncMock(return_value=output_file)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_store.return_value.update_episode_url.assert_awaited_once()
    called_url = m_store.return_value.update_episode_url.call_args[0][1]
    assert called_url.endswith(".mp3")


async def test_branch_d_audio_editor_returns_path_uses_computed_url() -> None:
    """Branch D: when AudioEditor.edit() returns a path, the URL is derived from it."""
    config, ep, parsed = _branch_config(MagicMock())
    output_file = MagicMock()
    output_file.stat.return_value.st_size = 0

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ad_parser.return_value.parse = MagicMock(return_value=[_DEFAULT_CUT_RANGE])
        m_audio_editor.return_value.edit = AsyncMock(return_value=output_file)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_store.return_value.update_episode_url.assert_awaited_once()
    called_url = m_store.return_value.update_episode_url.call_args[0][1]
    assert called_url.endswith(".mp3")


# ---------------------------------------------------------------------------
# Plan 02-03 — New decision-tree branch tests (ad detection tail)
# ---------------------------------------------------------------------------


async def test_branch_a_output_exists_short_circuits(tmp_path: Path) -> None:
    """Branch A: output file exists — reconstruct URL only, no download/probe/process/detect/edit."""
    audio_file = tmp_path / "my-podcast" / "22.03.2026-my-episode.mp3"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(tmp_path)

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        # transcribed_guids is empty — Branch A must trigger from audio_exists alone
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_not_called()
    m_prober.return_value.probe.assert_not_called()
    m_prep.return_value.preprocess.assert_not_called()
    m_trans.return_value.transcribe.assert_not_called()
    m_ad_detector.return_value.detect.assert_not_called()
    m_audio_editor.return_value.edit.assert_not_called()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()


async def test_branch_b_transcription_exists_no_output_with_ads() -> None:
    """Branch B: transcription exists, no output — download, probe (no preprocess), ad tail, URL updated."""
    output_file = MagicMock()
    output_file.stat.return_value.st_size = 0
    config, ep, parsed = _branch_config(MagicMock())  # MagicMock -> glob empty -> no audio

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ts.return_value.get_segments_for_guid = AsyncMock(
            return_value=[TranscriptionSegment(guid="ep-1", start_ms=0, end_ms=1000, text="Hello")]
        )
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(
            return_value=TopicExtraction(
                guid="ep-1", podcast="My Podcast", title="My Episode",
                topic="A topic.", hosts="Host A", show="My Show",
            )
        )
        m_ad_parser.return_value.parse = MagicMock(return_value=[_DEFAULT_CUT_RANGE])
        m_audio_editor.return_value.edit = AsyncMock(return_value=output_file)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once()
    m_prober.return_value.probe.assert_awaited_once()
    m_ams.return_value.save_all.assert_awaited_once()
    m_prep.return_value.preprocess.assert_not_called()  # no preprocess in Branch B
    m_ts.return_value.get_segments_for_guid.assert_awaited_once_with("ep-1")
    m_topic_store.return_value.get_topic_for_guid.assert_awaited_once_with("ep-1")
    m_ad_detector.return_value.detect.assert_awaited_once()
    m_ad_parser.return_value.parse.assert_called_once()
    m_ad_store.return_value.save_segments.assert_awaited_once()
    m_ad_store.return_value.mark_detected.assert_awaited_once()
    m_cs.return_value.save_cost.assert_awaited()
    m_audio_editor.return_value.edit.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()


async def test_branch_b_audio_editor_returns_none_copies_original_to_output() -> None:
    """Branch B: AudioEditor returns None (all audio classified as ads) — original file is copied."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        mock_copy_dest = MagicMock()
        mock_copy_dest.stat.return_value.st_size = 2048
        m_episode_copier.return_value.copy = AsyncMock(
            return_value=("ep-1", mock_copy_dest, "http://localhost/my-podcast/22.03.2026-my-episode.mp3")
        )
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ts.return_value.get_segments_for_guid = AsyncMock(return_value=[])
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
        m_audio_editor.return_value.edit = AsyncMock(return_value=None)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_episode_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()


async def test_branch_d_full_pipeline_with_ad_detection() -> None:
    """Branch D: full pipeline — download, probe, preprocess, transcribe, ad detect, edit, URL update."""
    output_file = MagicMock()
    output_file.stat.return_value.st_size = 0
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
        m_ad_parser.return_value.parse = MagicMock(return_value=[_DEFAULT_CUT_RANGE])
        m_audio_editor.return_value.edit = AsyncMock(return_value=output_file)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once()
    m_prober.return_value.probe.assert_awaited_once()
    m_prep.return_value.preprocess.assert_awaited_once()
    m_trans.return_value.transcribe.assert_awaited_once()
    m_topic_ext.return_value.extract.assert_awaited_once()
    m_ad_detector.return_value.detect.assert_awaited_once()
    m_ad_store.return_value.save_segments.assert_awaited_once()
    m_ad_store.return_value.mark_detected.assert_awaited_once()
    m_audio_editor.return_value.edit.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()


async def test_branch_d_ad_already_detected_loads_from_store() -> None:
    """Branch D: ad already detected — skips AdDetector, loads segments from AdStore."""
    output_file = MagicMock()
    output_file.stat.return_value.st_size = 0
    config, ep, parsed = _branch_config(MagicMock())
    existing_segments = [
        AdSegment(
            guid="ep-1", start_ms=0, end_ms=5000, confidence=0.9,
            sponsor="Acme", ad_topic="Promo", indices=[0, 1],
        )
    ]

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        m_ad_store.return_value.get_segments_for_guid = AsyncMock(return_value=existing_segments)
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
        m_ad_parser.return_value.parse = MagicMock(return_value=[_DEFAULT_CUT_RANGE])
        m_audio_editor.return_value.edit = AsyncMock(return_value=output_file)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ad_detector.return_value.detect.assert_not_called()
    m_ad_store.return_value.get_segments_for_guid.assert_awaited_once_with("ep-1")
    m_audio_editor.return_value.edit.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()


async def test_branch_c_audio_exists_no_transcription_runs_ad_detection(
    tmp_path: Path,
) -> None:
    """Branch C: cached audio exists, no transcription — probe, preprocess, transcribe, ad detect."""
    # Branch C is triggered by cached audio (in cache_dir), NOT output file.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached_file = cache_dir / "ep-1.mp3"  # named {guid}.{ext}
    cached_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(MagicMock())  # output_dir is MagicMock -> no output file
    config.app.paths.cache_dir = cache_dir

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_audio_editor.return_value.edit = AsyncMock(return_value=None)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_prober.return_value.probe.assert_awaited_once()
    m_prep.return_value.preprocess.assert_awaited_once()
    m_trans.return_value.transcribe.assert_awaited_once()
    m_ad_detector.return_value.detect.assert_awaited_once()
    # No qualifying ad cuts — original audio is copied to output
    m_episode_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()


async def test_feed_rss_missing_publishes_new_feed(tmp_path: Path) -> None:
    """RSS file does not exist yet — WriteNew path: save episodes and publish."""
    # No RSS file created → output_dir / "my-podcast.rss" does not exist.
    config, ep, parsed = _branch_config(tmp_path)

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        # DB is empty — all episodes are new.
        m_store.return_value.get_guids_for_feed = AsyncMock(return_value=set())
        pipeline = Pipeline(config)
        await pipeline.run()

    m_store.return_value.save_episodes.assert_awaited_once()
    m_pub.return_value.publish.assert_awaited_once()


async def test_feed_rss_exists_with_new_items_publishes(tmp_path: Path) -> None:
    """RSS file exists but feed contains a new episode not yet in the DB — publish update."""
    rss_file = tmp_path / "my-podcast.rss"
    rss_file.write_text("<rss/>")

    config, ep, parsed = _branch_config(tmp_path)

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        # DB has no episodes — ep-1 is new even though the RSS file already exists.
        m_store.return_value.get_guids_for_feed = AsyncMock(return_value=set())
        pipeline = Pipeline(config)
        await pipeline.run()

    m_store.return_value.save_episodes.assert_awaited_once()
    m_pub.return_value.publish.assert_awaited_once()


async def test_branch_c_topic_already_extracted_loads_from_store(tmp_path: Path) -> None:
    """Branch C: topic already extracted — skips TopicExtractor, fetches from TopicStore."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached_file = cache_dir / "ep-1.mp3"
    cached_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(MagicMock())
    config.app.paths.cache_dir = cache_dir

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            extracted_guids={"ep-1"},
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_topic_ext.return_value.extract.assert_not_called()
    m_topic_store.return_value.get_topic_for_guid.assert_awaited_once_with("ep-1")


async def test_branch_d_topic_already_extracted_loads_from_store() -> None:
    """Branch D: topic already extracted — skips TopicExtractor, fetches from TopicStore."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            extracted_guids={"ep-1"},
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_topic_ext.return_value.extract.assert_not_called()
    m_topic_store.return_value.get_topic_for_guid.assert_awaited_once_with("ep-1")

# ---------------------------------------------------------------------------
# Cache cleanup tests
# ---------------------------------------------------------------------------

_PATCHES = (
    "components.pipeline.FeedDownloader",       # [0]
    "components.pipeline.FeedParser",            # [1]
    "components.pipeline.FeedPublisher",         # [2]
    "components.pipeline.Database",              # [3]
    "components.pipeline.EpisodeStore",          # [4]
    "components.pipeline.TranscriptionStore",    # [5]
    "components.pipeline.AudioMetadataStore",    # [6]
    "components.pipeline.CostTrackingStore",     # [7]
    "components.pipeline.EpisodeDownloader",     # [8]
    "components.pipeline.AudioProber",           # [9]
    "components.pipeline.AudioPreprocessor",     # [10]
    "components.pipeline.EpisodeTranscriptor",   # [11]
    "components.pipeline.AdStore",               # [12]
    "components.pipeline.TopicExtractor",        # [13]
    "components.pipeline.TopicStore",            # [14]
    "components.pipeline.AdDetector",            # [15]
    "components.pipeline.AdParser",              # [16]
    "components.pipeline.AudioEditor",           # [17]
    "components.pipeline.EpisodeCopier",         # [18]  ← NEW
)


async def test_branch_c_deletes_mono_after_transcription(tmp_path: Path) -> None:
    """Branch C: mono file is deleted after successful transcription."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "ep-1.mp3").write_bytes(b"audio")
    mono_file = tmp_path / "ep-1.mono.m4a"
    mono_file.write_bytes(b"mono")

    config, ep, parsed = _branch_config(MagicMock())
    config.app.paths.cache_dir = cache_dir

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_prep.return_value.preprocess = AsyncMock(return_value=mono_file)
        await Pipeline(config).run()

    assert not mono_file.exists()


async def test_branch_d_deletes_mono_after_transcription(tmp_path: Path) -> None:
    """Branch D: mono file is deleted after successful transcription."""
    raw_file = tmp_path / "ep-1.mp3"
    raw_file.write_bytes(b"audio")
    mono_file = tmp_path / "ep-1.mono.m4a"
    mono_file.write_bytes(b"mono")

    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = AsyncMock(return_value=raw_file)
        m_prep.return_value.preprocess = AsyncMock(return_value=mono_file)
        await Pipeline(config).run()

    assert not mono_file.exists()


async def test_branch_c_deletes_mono_on_transcription_error(tmp_path: Path) -> None:
    """Branch C: mono file is deleted even when transcription raises."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "ep-1.mp3").write_bytes(b"audio")
    mono_file = tmp_path / "ep-1.mono.m4a"
    mono_file.write_bytes(b"mono")

    config, ep, parsed = _branch_config(MagicMock())
    config.app.paths.cache_dir = cache_dir

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_prep.return_value.preprocess = AsyncMock(return_value=mono_file)
        m_trans.return_value.transcribe = AsyncMock(side_effect=TranscriptionError("STT failure"))
        await Pipeline(config).run()  # exception swallowed at episode loop level

    assert not mono_file.exists()


async def test_branch_d_deletes_mono_on_transcription_error(tmp_path: Path) -> None:
    """Branch D: mono file is deleted even when transcription raises."""
    raw_file = tmp_path / "ep-1.mp3"
    raw_file.write_bytes(b"audio")
    mono_file = tmp_path / "ep-1.mono.m4a"
    mono_file.write_bytes(b"mono")

    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = AsyncMock(return_value=raw_file)
        m_prep.return_value.preprocess = AsyncMock(return_value=mono_file)
        m_trans.return_value.transcribe = AsyncMock(side_effect=TranscriptionError("STT failure"))
        await Pipeline(config).run()

    assert not mono_file.exists()


async def test_branch_b_deletes_raw_after_pipeline(tmp_path: Path) -> None:
    """Branch B: re-downloaded audio is deleted after the pipeline completes."""
    raw_file = tmp_path / "ep-1.mp3"
    raw_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ep_dl.return_value.download = AsyncMock(return_value=raw_file)
        await Pipeline(config).run()

    assert not raw_file.exists()


async def test_branch_c_deletes_raw_after_pipeline(tmp_path: Path) -> None:
    """Branch C: cached audio is deleted after the pipeline completes."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    raw_file = cache_dir / "ep-1.mp3"
    raw_file.write_bytes(b"audio")
    mono_file = tmp_path / "ep-1.mono.m4a"
    mono_file.write_bytes(b"mono")

    config, ep, parsed = _branch_config(MagicMock())
    config.app.paths.cache_dir = cache_dir

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_prep.return_value.preprocess = AsyncMock(return_value=mono_file)
        await Pipeline(config).run()

    assert not raw_file.exists()


async def test_branch_d_deletes_raw_after_pipeline(tmp_path: Path) -> None:
    """Branch D: downloaded audio is deleted after the pipeline completes."""
    raw_file = tmp_path / "ep-1.mp3"
    raw_file.write_bytes(b"audio")
    mono_file = tmp_path / "ep-1.mono.m4a"
    mono_file.write_bytes(b"mono")

    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = AsyncMock(return_value=raw_file)
        m_prep.return_value.preprocess = AsyncMock(return_value=mono_file)
        await Pipeline(config).run()

    assert not raw_file.exists()


async def test_branch_d_deletes_raw_on_preprocessing_error(tmp_path: Path) -> None:
    """Branch D: raw file is deleted even when preprocessing raises (download succeeded)."""
    raw_file = tmp_path / "ep-1.mp3"
    raw_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = AsyncMock(return_value=raw_file)
        m_prep.return_value.preprocess = AsyncMock(side_effect=FfmpegError("ffmpeg failed"))
        await Pipeline(config).run()

    assert not raw_file.exists()


async def test_branch_d_deletes_raw_on_audio_editor_error(tmp_path: Path) -> None:
    """Branch D: raw file is deleted even when the audio editor raises."""
    raw_file = tmp_path / "ep-1.mp3"
    raw_file.write_bytes(b"audio")
    mono_file = tmp_path / "ep-1.mono.m4a"
    mono_file.write_bytes(b"mono")

    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = AsyncMock(return_value=raw_file)
        m_prep.return_value.preprocess = AsyncMock(return_value=mono_file)
        m_audio_editor.return_value.edit = AsyncMock(side_effect=RuntimeError("edit failed"))
        await Pipeline(config).run()

    assert not raw_file.exists()


async def test_ad_detection_builds_ad_segment_from_valid_indices() -> None:
    """Valid indices in segment_map produce an AdSegment with correct time bounds."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        # detect returns a block covering index 0 (the only segment in the stub transcription)
        m_ad_detector.return_value.detect = AsyncMock(return_value=(
            "ep-1",
            [AdSegmentDetection(indices=[0], confidence=0.95, sponsor="Acme", ad_topic="promo")],
            AdDetectionCost(provider="openai", model="gpt-4o-mini", cost=0.0001),
        ))
        # Guard 3 fetches transcription segments from DB — match what transcribe produced
        m_ts.return_value.get_segments_for_guid = AsyncMock(
            return_value=[TranscriptionSegment(guid="ep-1", start_ms=0, end_ms=1000, text="Hello")]
        )
        # Guard 3 saves ad_segments; Guard 2 re-fetches them — connect the two mocks
        _saved_ad_segs: list[AdSegment] = []
        m_ad_store.return_value.save_segments = AsyncMock(
            side_effect=lambda _guid, segs: _saved_ad_segs.extend(segs)
        )
        m_ad_store.return_value.get_segments_for_guid = AsyncMock(
            side_effect=lambda _guid: _saved_ad_segs
        )
        captured: list[list[AdSegment]] = []
        original_parse = lambda segs, **_: captured.append(segs) or []  # noqa: E731
        m_ad_parser.return_value.parse = MagicMock(side_effect=original_parse)
        await Pipeline(config).run()

    assert len(captured) == 1
    assert len(captured[0]) == 1
    assert captured[0][0].start_ms == 0
    assert captured[0][0].end_ms == 1000
    assert captured[0][0].sponsor == "Acme"


async def test_ad_detection_skips_segment_with_all_out_of_range_indices() -> None:
    """A detection whose indices are all outside segment_map is silently skipped."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        # Return a detection whose indices are all outside the segment_map (only index 0 exists)
        m_ad_detector.return_value.detect = AsyncMock(return_value=(
            "ep-1",
            [AdSegmentDetection(indices=[99, 100], confidence=0.9, sponsor="X", ad_topic="promo")],
            AdDetectionCost(provider="openai", model="gpt-4o-mini", cost=0.0001),
        ))
        m_ad_parser.return_value.parse = MagicMock(return_value=[])
        await Pipeline(config).run()

    # No valid ad segments → audio editor not called; original audio is copied to output.
    m_audio_editor.return_value.edit.assert_not_called()
    m_episode_copier.return_value.copy.assert_awaited_once()


async def test_guard5_cached_audio_transcribes_without_redownload(tmp_path: Path) -> None:
    """Guard 5: if audio is on disk from a previous run, transcription runs without re-downloading."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "ep-1.mp3").write_bytes(b"audio")

    config, ep, parsed = _branch_config(MagicMock())
    config.app.paths.cache_dir = cache_dir

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        await Pipeline(config).run()

    m_ep_dl.return_value.download.assert_not_called()
    m_prober.return_value.probe.assert_awaited_once()
    m_trans.return_value.transcribe.assert_awaited_once()


async def test_guard2_no_ad_segments_copies_to_output() -> None:
    """Guard 2: when no ad segments were detected, episode is copied to output folder."""
    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[],  # detection ran, found nothing
        )
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        await Pipeline(config).run()

    m_audio_editor.return_value.edit.assert_not_called()
    m_episode_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()


async def test_guard2_no_qualifying_cuts_copies_to_output() -> None:
    """Guard 2: when ad segments exist but all fall below thresholds, episode is copied."""
    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        m_ad_parser.return_value.parse = MagicMock(return_value=[])  # all below threshold
        await Pipeline(config).run()

    m_audio_editor.return_value.edit.assert_not_called()
    m_episode_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()


# ---------------------------------------------------------------------------
# Per-episode log lifecycle tests
# ---------------------------------------------------------------------------


def _make_per_episode_config(tmp_path: Path, *, per_episode: bool) -> tuple[MagicMock, Episode, ParsedFeed]:
    """Build config/episode/parsed-feed with per_episode log settings."""
    config, ep, parsed = _branch_config(MagicMock())
    config.app.log.per_episode = per_episode
    config.app.log.file_level = "DEBUG"
    config.app.log.rotate = False
    config.app.log.keep_last = 10
    config.app.paths.log_dir = tmp_path
    return config, ep, parsed


async def test_per_episode_log_open_called_once_per_episode(tmp_path: Path) -> None:
    """When per_episode=True, open_episode_log is called once for each episode."""
    config, ep, parsed = _make_per_episode_config(tmp_path, per_episode=True)

    _patches = [
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.FeedPublisher"),
        patch("components.pipeline.Database"),
        patch("components.pipeline.EpisodeStore"),
        patch("components.pipeline.TranscriptionStore"),
        patch("components.pipeline.AudioMetadataStore"),
        patch("components.pipeline.CostTrackingStore"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioProber"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.AdStore"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.TopicStore"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
        patch("components.pipeline.open_episode_log"),
        patch("components.pipeline.close_episode_log"),
        patch("components.pipeline.rotate_episode_logs"),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _patches]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier, m_open, _m_close, _m_rotate) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
            m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_open.return_value = (MagicMock(), MagicMock(), MagicMock())
        await Pipeline(config).run()

    m_open.assert_called_once_with(
        guid=ep.guid,
        podcast_title="My Podcast",
        episode_title=ep.title,
        log_dir=tmp_path,
        file_level="DEBUG",
    )


async def test_per_episode_log_not_opened_when_disabled(tmp_path: Path) -> None:
    """When per_episode=False, open_episode_log is never called."""
    config, ep, parsed = _make_per_episode_config(tmp_path, per_episode=False)

    _patches = [
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.FeedPublisher"),
        patch("components.pipeline.Database"),
        patch("components.pipeline.EpisodeStore"),
        patch("components.pipeline.TranscriptionStore"),
        patch("components.pipeline.AudioMetadataStore"),
        patch("components.pipeline.CostTrackingStore"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioProber"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.AdStore"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.TopicStore"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
        patch("components.pipeline.open_episode_log"),
        patch("components.pipeline.close_episode_log"),
        patch("components.pipeline.rotate_episode_logs"),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _patches]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier, m_open, _m_close, _m_rotate) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
            m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        await Pipeline(config).run()

    m_open.assert_not_called()


async def test_per_episode_log_closed_even_on_exception(tmp_path: Path) -> None:
    """close_episode_log is called even when episode processing raises."""
    config, ep, parsed = _make_per_episode_config(tmp_path, per_episode=True)

    _patches = [
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.FeedPublisher"),
        patch("components.pipeline.Database"),
        patch("components.pipeline.EpisodeStore"),
        patch("components.pipeline.TranscriptionStore"),
        patch("components.pipeline.AudioMetadataStore"),
        patch("components.pipeline.CostTrackingStore"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioProber"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.AdStore"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.TopicStore"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
        patch("components.pipeline.open_episode_log"),
        patch("components.pipeline.close_episode_log"),
        patch("components.pipeline.rotate_episode_logs"),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _patches]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier, m_open, m_close, _m_rotate) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
            m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        fake_handler = MagicMock()
        m_open.return_value = (MagicMock(), fake_handler, MagicMock())
        # Force episode processing to raise
        m_ep_dl.return_value.download = AsyncMock(side_effect=RuntimeError("boom"))
        await Pipeline(config).run()

    m_close.assert_called_once_with(fake_handler)


@pytest.mark.asyncio
async def test_per_episode_rotate_called_when_rotate_enabled(tmp_path: Path) -> None:
    """rotate_episode_logs is called after close when rotate=True."""
    config, ep, parsed = _make_per_episode_config(tmp_path, per_episode=True)
    config.app.log.rotate = True
    config.app.log.keep_last = 3

    _patches = [
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.FeedPublisher"),
        patch("components.pipeline.Database"),
        patch("components.pipeline.EpisodeStore"),
        patch("components.pipeline.TranscriptionStore"),
        patch("components.pipeline.AudioMetadataStore"),
        patch("components.pipeline.CostTrackingStore"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioProber"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.AdStore"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.TopicStore"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
        patch("components.pipeline.open_episode_log"),
        patch("components.pipeline.close_episode_log"),
        patch("components.pipeline.rotate_episode_logs"),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _patches]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier, m_open, _m_close, m_rotate) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
            m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        fake_log_path = MagicMock()
        fake_log_path.parent = tmp_path / "episodes" / "my-podcast"
        m_open.return_value = (MagicMock(), MagicMock(), fake_log_path)
        await Pipeline(config).run()

    m_rotate.assert_called_once_with(fake_log_path.parent, 3)


@pytest.mark.asyncio
async def test_per_episode_rotate_not_called_when_rotate_disabled(tmp_path: Path) -> None:
    """rotate_episode_logs is not called when rotate=False."""
    config, ep, parsed = _make_per_episode_config(tmp_path, per_episode=True)
    config.app.log.rotate = False

    _patches = [
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.FeedPublisher"),
        patch("components.pipeline.Database"),
        patch("components.pipeline.EpisodeStore"),
        patch("components.pipeline.TranscriptionStore"),
        patch("components.pipeline.AudioMetadataStore"),
        patch("components.pipeline.CostTrackingStore"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioProber"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.AdStore"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.TopicStore"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
        patch("components.pipeline.open_episode_log"),
        patch("components.pipeline.close_episode_log"),
        patch("components.pipeline.rotate_episode_logs"),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _patches]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier, m_open, _m_close, m_rotate) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
            m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_open.return_value = (MagicMock(), MagicMock(), MagicMock())
        await Pipeline(config).run()

    m_rotate.assert_not_called()


# ---------------------------------------------------------------------------
# Output folder trimming
# ---------------------------------------------------------------------------


async def test_trim_output_dir_removes_orphaned_files(tmp_path: Path) -> None:
    """Files whose stem does not match any current episode are deleted."""
    from components.pipeline import Pipeline

    feed_dir = tmp_path / "my-feed"
    feed_dir.mkdir()
    # File for an episode still in the active window
    kept = feed_dir / "22.03.2026-my-episode.mp3"
    kept.write_bytes(b"keep me")
    # File for an old episode that has rolled out of the window
    orphan = feed_dir / "01.01.2020-old-episode.mp3"
    orphan.write_bytes(b"delete me")

    config = MagicMock()
    config.app.feeds = []
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "https://example.com"
    config.app.paths.output_dir = tmp_path
    config.app.paths.cache_dir = tmp_path / "cache"
    config.app.paths.data_dir = tmp_path / "data"
    config.app.paths.log_dir = tmp_path / "logs"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        pipeline = Pipeline(config)
        await pipeline._trim_output_dir(feed_dir, 1)

    assert kept.exists()
    assert not orphan.exists()


async def test_trim_output_dir_keeps_all_current_episodes(tmp_path: Path) -> None:
    """No files are deleted when all files match current episodes."""
    from components.pipeline import Pipeline

    feed_dir = tmp_path / "my-feed"
    feed_dir.mkdir()
    f1 = feed_dir / "22.03.2026-episode-one.mp3"
    f2 = feed_dir / "21.03.2026-episode-two.mp3"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")

    config = MagicMock()
    config.app.feeds = []
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "https://example.com"
    config.app.paths.output_dir = tmp_path
    config.app.paths.cache_dir = tmp_path / "cache"
    config.app.paths.data_dir = tmp_path / "data"
    config.app.paths.log_dir = tmp_path / "logs"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        pipeline = Pipeline(config)
        await pipeline._trim_output_dir(feed_dir, 2)

    assert f1.exists()
    assert f2.exists()


async def test_trim_output_dir_noop_when_dir_missing(tmp_path: Path) -> None:
    """No error when the output feed directory does not exist yet."""
    from components.pipeline import Pipeline

    config = MagicMock()
    config.app.feeds = []
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "https://example.com"
    config.app.paths.output_dir = tmp_path
    config.app.paths.cache_dir = tmp_path / "cache"
    config.app.paths.data_dir = tmp_path / "data"
    config.app.paths.log_dir = tmp_path / "logs"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        pipeline = Pipeline(config)
        # Must not raise
        await pipeline._trim_output_dir(tmp_path / "nonexistent-feed", 0)


async def test_run_calls_trim_output_dir_after_episode_loop() -> None:
    """Pipeline.run() must call _trim_output_dir once per feed after processing all episodes."""
    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        pipeline._trim_output_dir = AsyncMock()
        await pipeline.run()

    pipeline._trim_output_dir.assert_awaited_once()
    call_args = pipeline._trim_output_dir.call_args
    assert call_args is not None
    output_feed_dir, episodes_to_keep = call_args.args
    assert output_feed_dir == config.app.paths.output_dir / "my-podcast"
    assert episodes_to_keep == config.app.feeds[0].episodes_to_keep


def _make_trim_pipeline(tmp_path: Path) -> Pipeline:
    from components.pipeline import Pipeline

    config = MagicMock()
    config.app.feeds = []
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "https://example.com"
    config.app.paths.output_dir = tmp_path
    config.app.paths.cache_dir = tmp_path / "cache"
    config.app.paths.data_dir = tmp_path / "data"
    config.app.paths.log_dir = tmp_path / "logs"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        return Pipeline(config)


@pytest.mark.asyncio
async def test_trim_output_dir_preserves_old_file_when_new_episode_download_fails(
    tmp_path: Path,
) -> None:
    """A failed-download episode must not displace an older episode's output file.

    Scenario: episodes_to_keep=3, output dir has files for B/C/D.
    New episode A was inserted into the DB (newest) but its download failed, so no file exists.
    The old trim logic received [A, B, C] and deleted D's file.
    The new logic receives episodes_to_keep=3 and must keep all three existing files.
    """
    feed_dir = tmp_path / "my-feed"
    feed_dir.mkdir()
    b = feed_dir / "22.03.2026-episode-b.mp3"
    c = feed_dir / "21.03.2026-episode-c.mp3"
    d = feed_dir / "20.03.2026-episode-d.mp3"
    b.write_bytes(b"b")
    c.write_bytes(b"c")
    d.write_bytes(b"d")

    pipeline = _make_trim_pipeline(tmp_path)
    await pipeline._trim_output_dir(feed_dir, 3)

    assert b.exists()
    assert c.exists()
    assert d.exists(), "oldest file must survive when a newer episode has no output file"


@pytest.mark.asyncio
async def test_trim_output_dir_keeps_n_most_recent_by_date(tmp_path: Path) -> None:
    """When the directory has more files than episodes_to_keep, the oldest are deleted."""
    feed_dir = tmp_path / "my-feed"
    feed_dir.mkdir()
    a = feed_dir / "23.03.2026-episode-a.mp3"
    b = feed_dir / "22.03.2026-episode-b.mp3"
    c = feed_dir / "21.03.2026-episode-c.mp3"
    d = feed_dir / "20.03.2026-episode-d.mp3"
    for f in (a, b, c, d):
        f.write_bytes(b"x")

    pipeline = _make_trim_pipeline(tmp_path)
    await pipeline._trim_output_dir(feed_dir, 3)

    assert a.exists()
    assert b.exists()
    assert c.exists()
    assert not d.exists(), "oldest file must be trimmed when directory exceeds episodes_to_keep"


@pytest.mark.asyncio
async def test_trim_output_dir_deletes_unrecognized_filename_before_named_episodes(
    tmp_path: Path,
) -> None:
    """Files whose names don't match DD.MM.YYYY-… are treated as oldest and trimmed first."""
    feed_dir = tmp_path / "my-feed"
    feed_dir.mkdir()
    episode_file = feed_dir / "22.03.2026-episode.mp3"
    unknown = feed_dir / "feed.xml"
    episode_file.write_bytes(b"audio")
    unknown.write_bytes(b"xml")

    pipeline = _make_trim_pipeline(tmp_path)
    await pipeline._trim_output_dir(feed_dir, 1)

    assert episode_file.exists()
    assert not unknown.exists(), "unrecognized filenames sort as oldest and must be trimmed"


# ---------------------------------------------------------------------------
# Concern fixes: null transcription text (High) & assert meta is not None (High)
# ---------------------------------------------------------------------------


async def test_guard4_does_not_call_topic_extractor_when_transcription_text_is_none(
    tmp_path: Path,
) -> None:
    """Guard 4: when get_transcription_text returns None, topic extraction must not be attempted."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub,
        patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts,
        patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl,
        patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans,
        patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext,
        patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector,
        patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor,
        patch(_PATCHES[18]) as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
        )
        # DB inconsistency: row exists but text is missing
        m_ts.return_value.get_transcription_text = AsyncMock(return_value=None)
        pipeline = Pipeline(config)
        await pipeline.run()

    # Topic extraction must not have been attempted when transcription text is None
    m_topic_ext.return_value.extract.assert_not_called()


async def test_guard2_logs_descriptive_error_when_probe_returns_none_and_cuts_exist(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Guard 2: RuntimeError with a descriptive message must be logged when meta is None but cuts exist."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub,
        patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts,
        patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl,
        patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans,
        patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext,
        patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector,
        patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor,
        patch(_PATCHES[18]) as m_episode_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        # Guard 2: ad detection cached, cuts are non-empty, probe returns None
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        m_ad_parser.return_value.parse = MagicMock(return_value=[_DEFAULT_CUT_RANGE])
        m_prober.return_value.probe = AsyncMock(return_value=None)

        pipeline = Pipeline(config)
        with caplog.at_level(logging.ERROR, logger="components.pipeline"):
            await pipeline.run()

    # Must log RuntimeError with a descriptive message, not a bare AssertionError
    assert any(
        r.exc_info and isinstance(r.exc_info[1], RuntimeError)
        and "audio metadata" in str(r.exc_info[1])
        for r in caplog.records
    ), "Expected a RuntimeError mentioning 'audio metadata' in the pipeline error log"


# ---------------------------------------------------------------------------
# _Stores counter field tests (02-01-01)
# ---------------------------------------------------------------------------


def _make_stores_kwargs(**overrides: object) -> dict:
    """Return minimal keyword args for _Stores construction."""
    base = {
        "episode": MagicMock(),
        "transcription": MagicMock(),
        "audio_metadata": MagicMock(),
        "cost": MagicMock(),
        "topic": MagicMock(),
        "ad": MagicMock(),
        "transcribed_guids": set(),
        "extracted_guids": set(),
        "ad_detected_guids": set(),
    }
    base.update(overrides)
    return base


def test_stores_episodes_total_stores_value() -> None:
    stores = _Stores(**_make_stores_kwargs(episodes_total=5))
    assert stores.episodes_total == 5


def test_stores_episodes_done_defaults_to_zero() -> None:
    stores = _Stores(**_make_stores_kwargs(episodes_total=3))
    assert stores.episodes_done == 0


def test_stores_episodes_failed_defaults_to_zero() -> None:
    stores = _Stores(**_make_stores_kwargs(episodes_total=3))
    assert stores.episodes_failed == 0


def test_stores_episodes_total_is_required() -> None:
    with pytest.raises(TypeError):
        _Stores(**_make_stores_kwargs())  # episodes_total missing


# ---------------------------------------------------------------------------
# RUN_STARTED / RUN_COMPLETED event tests (02-01-03)
# ---------------------------------------------------------------------------

_ALL_BRANCH_PATCHES = [
    "components.pipeline.FeedDownloader",
    "components.pipeline.FeedParser",
    "components.pipeline.FeedPublisher",
    "components.pipeline.Database",
    "components.pipeline.EpisodeStore",
    "components.pipeline.TranscriptionStore",
    "components.pipeline.AudioMetadataStore",
    "components.pipeline.CostTrackingStore",
    "components.pipeline.EpisodeDownloader",
    "components.pipeline.AudioProber",
    "components.pipeline.AudioPreprocessor",
    "components.pipeline.EpisodeTranscriptor",
    "components.pipeline.AdStore",
    "components.pipeline.TopicExtractor",
    "components.pipeline.TopicStore",
    "components.pipeline.AdDetector",
    "components.pipeline.AdParser",
    "components.pipeline.AudioEditor",
    "components.pipeline.EpisodeCopier",
]


def _run_all_patches() -> contextlib.AbstractContextManager:
    return contextlib.ExitStack()


async def _run_with_event_bus(
    config: MagicMock,
    ep: Episode,
    parsed: ParsedFeed,
    mock_bus: MagicMock,
    *,
    transcribed_guids: set[str] | None = None,
) -> None:
    """Run the pipeline with all branch mocks wired and an injected event bus."""
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(patch(p)) for p in _ALL_BRANCH_PATCHES]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=transcribed_guids or set(),
        )
        pipeline = Pipeline(config, event_bus=mock_bus)
        await pipeline.run()


async def test_run_started_emitted_with_feeds_and_total_episodes() -> None:
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)
    await _run_with_event_bus(config, ep, parsed, mock_bus)

    emitted_types = [call_args[0][0].type for call_args in mock_bus.emit.call_args_list]
    assert PipelineEventType.RUN_STARTED in emitted_types

    run_started_event = next(
        call_args[0][0]
        for call_args in mock_bus.emit.call_args_list
        if call_args[0][0].type == PipelineEventType.RUN_STARTED
    )
    assert set(run_started_event.payload.keys()) == {"feeds", "total_episodes"}
    assert isinstance(run_started_event.payload["feeds"], list)
    assert isinstance(run_started_event.payload["total_episodes"], int)


async def test_run_completed_emitted_last_with_feeds_key() -> None:
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)
    await _run_with_event_bus(config, ep, parsed, mock_bus)

    assert mock_bus.emit.called
    last_event: PipelineEvent = mock_bus.emit.call_args_list[-1][0][0]
    assert last_event.type == PipelineEventType.RUN_COMPLETED
    assert "feeds" in last_event.payload


async def test_run_started_before_episode_events_run_completed_last() -> None:
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)
    await _run_with_event_bus(config, ep, parsed, mock_bus)

    types = [c[0][0].type for c in mock_bus.emit.call_args_list]
    episode_event_types = {
        PipelineEventType.EPISODE_COMPLETED,
        PipelineEventType.EPISODE_FAILED,
        PipelineEventType.EPISODE_STAGE_CHANGED,
        PipelineEventType.DOWNLOAD_PROGRESS,
        PipelineEventType.ENCODE_PROGRESS,
    }
    run_started_idx = types.index(PipelineEventType.RUN_STARTED)
    run_completed_idx = len(types) - 1
    assert types[run_completed_idx] == PipelineEventType.RUN_COMPLETED
    for i, t in enumerate(types):
        if t in episode_event_types:
            assert i > run_started_idx
            assert i < run_completed_idx


# ---------------------------------------------------------------------------
# EPISODE_STAGE_CHANGED tests (02-01-05)
# ---------------------------------------------------------------------------


async def _run_full_pipeline_with_bus(mock_bus: MagicMock) -> list[MagicMock]:
    """Run a full Branch D pipeline (all stages fire) with an event bus.

    Returns mock_bus.emit.call_args_list for inspection.
    """
    config, ep, parsed = _branch_config(MagicMock())
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(patch(p)) for p in _ALL_BRANCH_PATCHES]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        # Make ad-detect produce qualifying cuts so the "edit" stage fires.
        m_ad_parser.return_value.parse = MagicMock(return_value=[_DEFAULT_CUT_RANGE])
        out_path = MagicMock()
        out_path.stat.return_value.st_size = 2048
        m_audio_editor.return_value.edit = AsyncMock(return_value=out_path)
        pipeline = Pipeline(config, event_bus=mock_bus)
        await pipeline.run()
    return mock_bus.emit.call_args_list


def _stage_events(call_args_list: list, stage: str) -> list[PipelineEvent]:
    return [
        c[0][0] for c in call_args_list
        if c[0][0].type == PipelineEventType.EPISODE_STAGE_CHANGED
        and c[0][0].payload.get("stage") == stage
    ]


@pytest.mark.parametrize("stage", ["download", "preprocess", "transcribe", "topic", "ad-detect", "edit"])
async def test_stage_changed_emits_started_then_completed(stage: str) -> None:
    mock_bus = MagicMock(spec=EventBus)
    calls = await _run_full_pipeline_with_bus(mock_bus)
    events = _stage_events(calls, stage)
    statuses = [e.payload["status"] for e in events]
    assert statuses == ["started", "completed"], f"stage={stage}: expected ['started','completed'], got {statuses}"


@pytest.mark.parametrize("stage", ["download", "preprocess", "transcribe", "topic", "ad-detect", "edit"])
async def test_stage_changed_payload_has_required_keys(stage: str) -> None:
    mock_bus = MagicMock(spec=EventBus)
    calls = await _run_full_pipeline_with_bus(mock_bus)
    events = _stage_events(calls, stage)
    for event in events:
        assert set(event.payload.keys()) == {"guid", "stage", "status", "feed_slug"}, (
            f"stage={stage}: missing keys in payload {event.payload}"
        )


async def test_stage_changed_guard1_output_exists_no_stage_emits(tmp_path: Path) -> None:
    """Guard 1 (output file exists) must not emit any EPISODE_STAGE_CHANGED events."""
    audio_file = tmp_path / "my-podcast" / "22.03.2026-my-episode.mp3"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"audio")

    config, ep, parsed = _branch_config(tmp_path)
    mock_bus = MagicMock(spec=EventBus)
    await _run_with_event_bus(config, ep, parsed, mock_bus, transcribed_guids={"ep-1"})

    stage_events = [
        c[0][0] for c in mock_bus.emit.call_args_list
        if c[0][0].type == PipelineEventType.EPISODE_STAGE_CHANGED
    ]
    assert stage_events == [], f"Expected no stage events for Guard 1 path, got {stage_events}"


# ---------------------------------------------------------------------------
# DOWNLOAD_PROGRESS / ENCODE_PROGRESS tests (02-01-07)
# ---------------------------------------------------------------------------


async def test_download_progress_event_emitted_with_correct_payload() -> None:
    """DOWNLOAD_PROGRESS events must be emitted for each on_progress tick during download."""
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)

    progress_ticks = [0.0, 0.5, 1.0]

    async def fake_download(guid: str, url: str, *, on_progress=None) -> Path:
        if on_progress is not None:
            for pct in progress_ticks:
                await on_progress(guid, pct)
        return Path("/cache/ep.mp3")

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(patch(p)) for p in _ALL_BRANCH_PATCHES]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = fake_download
        pipeline = Pipeline(config, event_bus=mock_bus)
        await pipeline.run()

    dl_events = [
        c[0][0] for c in mock_bus.emit.call_args_list
        if c[0][0].type == PipelineEventType.DOWNLOAD_PROGRESS
    ]
    assert len(dl_events) == len(progress_ticks), f"Expected {len(progress_ticks)} DOWNLOAD_PROGRESS events"
    for event, expected_pct in zip(dl_events, progress_ticks, strict=True):
        assert set(event.payload.keys()) == {"guid", "feed_slug", "percent"}
        assert event.payload["percent"] == expected_pct
        assert event.payload["guid"] == ep.guid


async def test_download_progress_preserves_existing_log_behavior() -> None:
    """The existing _on_download_progress log side-effects must still fire when event bus is set."""
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)

    async def fake_download(guid: str, url: str, *, on_progress=None) -> Path:
        if on_progress is not None:
            await on_progress(guid, 0.0)
        return Path("/cache/ep.mp3")

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(patch(p)) for p in _ALL_BRANCH_PATCHES]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = fake_download
        with patch.object(Pipeline, "_on_download_progress", new_callable=AsyncMock) as mock_dl_progress:
            pipeline = Pipeline(config, event_bus=mock_bus)
            await pipeline.run()

    mock_dl_progress.assert_awaited_once_with(ep.guid, 0.0)


async def test_encode_progress_event_emitted_with_correct_payload() -> None:
    """ENCODE_PROGRESS events must be emitted for each on_progress tick during preprocessing."""
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)

    progress_ticks = [0.0, 0.25, 1.0]

    async def fake_preprocess(guid: str, raw: Path, duration: float, *, on_progress=None) -> Path:
        if on_progress is not None:
            for pct in progress_ticks:
                await on_progress(guid, pct)
        return Path("/cache/ep.mono.m4a")

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(patch(p)) for p in _ALL_BRANCH_PATCHES]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_prep.return_value.preprocess = fake_preprocess
        pipeline = Pipeline(config, event_bus=mock_bus)
        await pipeline.run()

    enc_events = [
        c[0][0] for c in mock_bus.emit.call_args_list
        if c[0][0].type == PipelineEventType.ENCODE_PROGRESS
    ]
    assert len(enc_events) == len(progress_ticks), f"Expected {len(progress_ticks)} ENCODE_PROGRESS events"
    for event, expected_pct in zip(enc_events, progress_ticks, strict=True):
        assert set(event.payload.keys()) == {"guid", "feed_slug", "percent"}
        assert event.payload["percent"] == expected_pct
        assert event.payload["guid"] == ep.guid


# ---------------------------------------------------------------------------
# EPISODE_COMPLETED / EPISODE_FAILED tests (02-01-09)
# ---------------------------------------------------------------------------


async def test_episode_completed_event_emitted_with_correct_payload() -> None:
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)
    await _run_with_event_bus(config, ep, parsed, mock_bus)

    completed = [
        c[0][0] for c in mock_bus.emit.call_args_list
        if c[0][0].type == PipelineEventType.EPISODE_COMPLETED
    ]
    assert len(completed) == 1
    payload = completed[0].payload
    assert set(payload.keys()) == {"guid", "feed_slug", "outcome", "feed_done", "feed_failed", "feed_total"}
    assert payload["guid"] == ep.guid
    assert payload["feed_done"] == 1
    assert payload["feed_failed"] == 0
    assert payload["feed_total"] == 1


async def test_episode_failed_event_emitted_on_exception() -> None:
    config, ep, parsed = _branch_config(MagicMock())
    mock_bus = MagicMock(spec=EventBus)

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(patch(p)) for p in _ALL_BRANCH_PATCHES]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = AsyncMock(side_effect=RuntimeError("download failed"))
        pipeline = Pipeline(config, event_bus=mock_bus)
        await pipeline.run()

    failed = [
        c[0][0] for c in mock_bus.emit.call_args_list
        if c[0][0].type == PipelineEventType.EPISODE_FAILED
    ]
    assert len(failed) == 1
    payload = failed[0].payload
    assert set(payload.keys()) == {"guid", "feed_slug", "error", "feed_done", "feed_failed", "feed_total"}
    assert payload["guid"] == ep.guid
    assert payload["feed_failed"] == 1
    assert payload["feed_done"] == 0
    assert payload["feed_total"] == 1
    assert "download failed" in payload["error"]


async def test_episode_done_failed_counters_increment_across_episodes() -> None:
    ep1 = Episode(guid="ep-1", url="https://x.com/ep1.mp3", title="Ep 1",
                  pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    ep2 = Episode(guid="ep-2", url="https://x.com/ep2.mp3", title="Ep 2",
                  pub_date=datetime(2026, 3, 23, tzinfo=UTC))
    config, _, _ = _branch_config(MagicMock(), episode=ep1)
    parsed = ParsedFeed(
        config_title="My Podcast", feed_url="http://x.com/feed", title="My Podcast",
        episodes=[ep1, ep2],
    )
    mock_bus = MagicMock(spec=EventBus)

    call_count = 0

    async def flaky_download(guid: str, url: str, *, on_progress=None) -> Path:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("ep2 failed")
        return Path(f"/cache/{guid}.mp3")

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(patch(p)) for p in _ALL_BRANCH_PATCHES]
        (m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
         m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext,
         m_topic_store, m_ad_detector, m_ad_parser, m_audio_editor,
         m_episode_copier) = mocks
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep1, ep2], parsed=parsed, transcribed_guids=set(),
        )
        m_ep_dl.return_value.download = flaky_download
        pipeline = Pipeline(config, event_bus=mock_bus)
        await pipeline.run()

    completed = [c[0][0] for c in mock_bus.emit.call_args_list
                 if c[0][0].type == PipelineEventType.EPISODE_COMPLETED]
    failed = [c[0][0] for c in mock_bus.emit.call_args_list
              if c[0][0].type == PipelineEventType.EPISODE_FAILED]
    assert len(completed) == 1
    assert len(failed) == 1
    assert completed[0].payload["feed_done"] == 1
    assert failed[0].payload["feed_failed"] == 1
    assert failed[0].payload["feed_total"] == 2
