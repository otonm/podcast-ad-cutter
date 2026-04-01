"""Tests for Pipeline — feed orchestration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from components.pipeline import Pipeline
from config.config_loader import FeedConfig
from models.ad_detection import AdDetectionCost, AdSegment, AdSegmentDetection  # noqa: TC002
from models.feed import AudioMetadata, Episode, FeedParseInput, ParsedFeed, PublisherInput
from models.topic import TopicExtraction, TopicExtractionCost
from models.transcription import Transcription, TranscriptionCost, TranscriptionSegment
from utils.exceptions import FfmpegError, TranscriptionError

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
    return cfg


# ---------------------------------------------------------------------------
# Branch-test helpers
# ---------------------------------------------------------------------------


def _branch_config(output_dir: Path | MagicMock) -> tuple[MagicMock, Episode, ParsedFeed]:
    """Build config/episode/parsed-feed for decision-tree branch tests."""
    ep = Episode(
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
    *,
    episodes: list[Episode],
    parsed: ParsedFeed,
    transcribed_guids: set[str],
    extracted_guids: set[str] | None = None,
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
    m_ad_store.return_value.get_segments_for_guid = AsyncMock(return_value=[])
    m_ad_store.return_value.save_segments = AsyncMock()
    m_ad_store.return_value.mark_detected = AsyncMock()
    m_ad_detector.return_value.detect = AsyncMock(return_value=(
        "ep-1",
        [],
        AdDetectionCost(provider="openai", model="gpt-4o-mini", cost=0.0001),
    ))
    m_ad_parser.return_value.parse = MagicMock(return_value=[])
    m_audio_editor.return_value.edit = AsyncMock(return_value=None)


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
    assert pi.owner_email == "owner@test.com"
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


async def test_on_download_progress_intermediate() -> None:
    """Progress callback at an intermediate value writes percentage in-place to stderr."""
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
        await pipeline._on_download_progress("ep-001", 0.5)

    mock_stderr.write.assert_called_once_with("\r  Episode 'ep-001': 50%")
    mock_stderr.flush.assert_called_once()


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


async def test_on_preprocess_progress_intermediate() -> None:
    """Progress callback at an intermediate value writes percentage in-place to stderr."""
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
        await pipeline._on_preprocess_progress("ep-001", 0.5)

    mock_stderr.write.assert_called_once_with("\r  Episode 'ep-001': 50%")
    mock_stderr.flush.assert_called_once()


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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    """Branch B: transcription OK, no audio — download, probe, preprocess, copy; no transcribe."""
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once_with(
        "ep-1", "https://example.com/ep.mp3", on_progress=pipeline._on_download_progress
    )
    m_prober.return_value.probe.assert_awaited_once()
    m_ams.return_value.save_all.assert_awaited_once()
    m_prep.return_value.preprocess.assert_not_called()  # no preprocess when transcript cached
    m_trans.return_value.transcribe.assert_not_called()


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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_not_called()
    m_prober.return_value.probe.assert_awaited_once_with("ep-1", cached_file)
    m_prep.return_value.preprocess.assert_awaited_once_with(
        "ep-1", cached_file, 60.0, on_progress=pipeline._on_preprocess_progress
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep1, ep2], parsed=parsed, transcribed_guids=set(),
        )
        m_trans.return_value.transcribe = AsyncMock(side_effect=[
            RuntimeError("transcription failed"),
            ("ep-2", Transcription(guid="ep-2", text="ok"),
             [], TranscriptionCost(provider="groq", model="w", cost=0.0)),
        ])
        pipeline = Pipeline(config)
        await pipeline.run()

    assert m_trans.return_value.transcribe.await_count == 2
    # ep1 failed before audio edit; ep2 audio edit should succeed (but editor returns None)
    m_audio_editor.return_value.edit.assert_awaited()


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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep1, ep2, ep3], parsed=parsed, transcribed_guids=set(),
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
    m_audio_editor.return_value.edit.assert_awaited()


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
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
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
    ):
        Pipeline(config)

    mock_ad_detector_cls.assert_called_once_with(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-openai-test",
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
    ):
        Pipeline(config)

    mock_audio_editor_cls.assert_called_once_with(
        output_dir=config.app.paths.output_dir,
        file_type="mp3",
        bitrate="128k",
    )


async def test_pipeline_does_not_instantiate_episode_copier() -> None:
    """Pipeline.__init__ must not call EpisodeCopier after removal."""
    config = _make_wiring_config()

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
        Pipeline(config)

    # If EpisodeCopier is still imported and instantiated, this import will succeed
    # and we can verify it's no longer referenced in the module.
    import components.pipeline as pipeline_module  # noqa: PLC0415
    assert not hasattr(pipeline_module, "EpisodeCopier"), (
        "EpisodeCopier should have been removed from components.pipeline"
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    # AdStore must be instantiated with the db connection object.
    # Called twice per feed: once for the skip-check, once for the processing block.
    mock_db_obj = m_db.return_value.__aenter__.return_value
    assert m_ad_store.call_count == 2
    assert all(c == call(mock_db_obj.conn) for c in m_ad_store.call_args_list)
    # get_detected_guids must be awaited at least once
    m_ad_store.return_value.get_detected_guids.assert_awaited()


async def test_branch_b_audio_editor_returns_path_uses_computed_url() -> None:
    """Branch B: when AudioEditor.edit() returns a path, the URL is derived from it."""
    config, ep, parsed = _branch_config(MagicMock())
    output_file = Path("/out/my-podcast/22.03.2026-my-episode.mp3")

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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
        )
        m_audio_editor.return_value.edit = AsyncMock(return_value=output_file)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_store.return_value.update_episode_url.assert_awaited_once()
    called_url = m_store.return_value.update_episode_url.call_args[0][1]
    assert called_url.endswith(".mp3")


async def test_branch_d_audio_editor_returns_path_uses_computed_url() -> None:
    """Branch D: when AudioEditor.edit() returns a path, the URL is derived from it."""
    config, ep, parsed = _branch_config(MagicMock())
    output_file = Path("/out/my-podcast/22.03.2026-my-episode.mp3")

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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
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
    ):
        # transcribed_guids is empty — Branch A must trigger from audio_exists alone
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    output_file = Path("/out/my-podcast/22.03.2026-my-episode.mp3")
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
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
        m_audio_editor.return_value.edit = AsyncMock(return_value=output_file)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once()
    m_prober.return_value.probe.assert_awaited_once()
    m_ams.return_value.save_all.assert_awaited_once()
    m_prep.return_value.preprocess.assert_not_called()  # D-05: no preprocess in Branch B
    m_ts.return_value.get_segments_for_guid.assert_awaited_once_with("ep-1")
    m_topic_store.return_value.get_topic_for_guid.assert_awaited_once_with("ep-1")
    m_ad_detector.return_value.detect.assert_awaited_once()
    m_ad_parser.return_value.parse.assert_called_once()
    m_ad_store.return_value.save_segments.assert_awaited_once()
    m_ad_store.return_value.mark_detected.assert_awaited_once()
    m_cs.return_value.save_cost.assert_awaited()
    m_audio_editor.return_value.edit.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()


async def test_branch_b_transcription_exists_no_output_no_ads_keeps_original_url() -> None:
    """Branch B: AudioEditor returns None — no URL update (original URL preserved)."""
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
        )
        m_ts.return_value.get_segments_for_guid = AsyncMock(return_value=[])
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
        m_audio_editor.return_value.edit = AsyncMock(return_value=None)
        pipeline = Pipeline(config)
        await pipeline.run()

    # AudioEditor returned None -> no URL update
    m_store.return_value.update_episode_url.assert_not_called()
    m_pub.return_value.update_episode_url.assert_not_called()


async def test_branch_d_full_pipeline_with_ad_detection() -> None:
    """Branch D: full pipeline — download, probe, preprocess, transcribe, ad detect, edit, URL update."""
    output_file = Path("/out/my-podcast/22.03.2026-my-episode.mp3")
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
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
    output_file = Path("/out/my-podcast/22.03.2026-my-episode.mp3")
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        m_ad_store.return_value.get_segments_for_guid = AsyncMock(return_value=existing_segments)
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        m_audio_editor.return_value.edit = AsyncMock(return_value=None)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_prober.return_value.probe.assert_awaited_once()
    m_prep.return_value.preprocess.assert_awaited_once()
    m_trans.return_value.transcribe.assert_awaited_once()
    m_ad_detector.return_value.detect.assert_awaited_once()
    # AudioEditor returned None -> no URL update
    m_store.return_value.update_episode_url.assert_not_called()
    m_pub.return_value.update_episode_url.assert_not_called()


# ---------------------------------------------------------------------------
# Feed-level existence and new-items checks
# ---------------------------------------------------------------------------


async def test_feed_rss_exists_no_new_items_skips_feed(tmp_path: Path) -> None:
    """RSS file exists and all feed episodes are already in the DB — skip entire feed."""
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        # All feed episodes already known to the DB and all have completed ad detection.
        m_store.return_value.get_guids_for_feed = AsyncMock(return_value={"ep-1"})
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        pipeline = Pipeline(config)
        await pipeline.run()

    m_store.return_value.save_episodes.assert_not_called()
    m_pub.return_value.publish.assert_not_called()
    m_ep_dl.return_value.download.assert_not_called()


async def test_feed_rss_exists_undetected_episodes_does_not_skip(tmp_path: Path) -> None:
    """RSS file exists, no new GUIDs from RSS, but episode has not been through ad detection.

    The undetected episode is not skipped and enters the state machine.
    """
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        # Episode already in DB (not new) but never through ad detection.
        m_store.return_value.get_guids_for_feed = AsyncMock(return_value={"ep-1"})
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value=set())
        pipeline = Pipeline(config)
        await pipeline.run()

    # Feed was not skipped — save_episodes and publish were called for the undetected episode.
    m_store.return_value.save_episodes.assert_awaited_once()
    m_pub.return_value.publish.assert_awaited_once()


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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor,
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

    # AudioEditor.edit must still be called — the out-of-range detection is skipped, not an error
    m_audio_editor.return_value.edit.assert_awaited_once()
