"""Tests for Pipeline — feed orchestration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.pipeline import Pipeline
from config.config_loader import FeedConfig
from models.feed import AudioMetadata, Episode, FeedParseInput, ParsedFeed, PublisherInput
from models.transcription import Transcription, TranscriptionCost, TranscriptionSegment

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
    cfg.credentials.groq_api_key = "sk-test"
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
    config.credentials.groq_api_key = "sk-test"
    config.app.base_url = "http://localhost"
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
    m_copier: MagicMock,
    *,
    episodes: list[Episode],
    parsed: ParsedFeed,
    transcribed_guids: set[str],
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
    mock_store.update_episode_url = AsyncMock()
    m_store.return_value = mock_store

    m_pub.return_value.publish = AsyncMock(return_value=Path("/out/my-podcast.rss"))
    m_pub.return_value.update_episode_url = AsyncMock()

    m_ts.return_value.get_transcribed_guids = AsyncMock(return_value=transcribed_guids)
    m_ts.return_value.save_transcription = AsyncMock()
    m_ts.return_value.save_segments = AsyncMock()

    m_ams.return_value.save_all = AsyncMock()
    m_cs.return_value.save_cost = AsyncMock()

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
    m_copier.return_value.copy = AsyncMock(return_value=(
        "ep-1",
        Path("/out/my-podcast/22.03.2026-my-episode.mp3"),
        "http://localhost/my-podcast/22.03.2026-my-episode.mp3",
    ))


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
    config.credentials.groq_api_key = "sk-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
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
    config.credentials.groq_api_key = "sk-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
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
    config.credentials.groq_api_key = "sk-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
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
    config.credentials.groq_api_key = "sk-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
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
    config.credentials.groq_api_key = "sk-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
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
    config.credentials.groq_api_key = "sk-test"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
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
    config.credentials.groq_api_key = "sk-groq-test"
    config.app.base_url = "http://localhost"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor") as mock_trans_cls,
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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_not_called()
    m_prober.return_value.probe.assert_not_called()
    m_prep.return_value.preprocess.assert_not_called()
    m_trans.return_value.transcribe.assert_not_called()
    m_copier.return_value.copy.assert_not_called()
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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
            episodes=[ep], parsed=parsed, transcribed_guids={"ep-1"},
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_awaited_once_with(
        "ep-1", "https://example.com/ep.mp3", on_progress=pipeline._on_download_progress
    )
    m_prober.return_value.probe.assert_awaited_once()
    m_ams.return_value.save_all.assert_awaited_once()
    m_prep.return_value.preprocess.assert_awaited_once()
    m_trans.return_value.transcribe.assert_not_called()
    m_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()


async def test_branch_c_audio_exists_no_transcription_transcribes_from_output(
    tmp_path: Path,
) -> None:
    """Branch C: audio exists, no transcription — probe+preprocess+transcribe; no download/copy."""
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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
        )
        pipeline = Pipeline(config)
        await pipeline.run()

    m_ep_dl.return_value.download.assert_not_called()
    m_prober.return_value.probe.assert_awaited_once_with("ep-1", audio_file)
    m_prep.return_value.preprocess.assert_awaited_once_with(
        "ep-1", audio_file, 60.0, on_progress=pipeline._on_preprocess_progress
    )
    m_trans.return_value.transcribe.assert_awaited_once()
    m_ts.return_value.save_transcription.assert_awaited_once()
    m_ts.return_value.save_segments.assert_awaited_once()
    m_cs.return_value.save_cost.assert_awaited_once()
    m_copier.return_value.copy.assert_not_called()
    m_store.return_value.update_episode_url.assert_awaited_once()


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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
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
    m_cs.return_value.save_cost.assert_awaited_once()
    m_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()


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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
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
    # ep1 failed before copy; ep2 copy should succeed
    assert m_copier.return_value.copy.await_count == 1


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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
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
        patch("components.pipeline.EpisodeCopier") as m_copier,
    ):
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_copier,
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
    assert m_copier.return_value.copy.await_count == 1
