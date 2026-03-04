from datetime import UTC, datetime
from pathlib import Path


def test_parse_pub_date_rfc2822():
    from pipeline.feed_publisher import _parse_pub_date
    assert _parse_pub_date("Wed, 01 Jan 2025 00:00:00 GMT") == "01.01.2025"


def test_parse_pub_date_with_timezone_offset():
    from pipeline.feed_publisher import _parse_pub_date
    result = _parse_pub_date("Mon, 15 Mar 2025 12:00:00 +0000")
    assert result == "15.03.2025"


def test_parse_pub_date_none_returns_today():
    from pipeline.feed_publisher import _parse_pub_date
    today = datetime.now(tz=UTC).strftime("%d.%m.%Y")
    assert _parse_pub_date(None) == today


def test_parse_pub_date_garbage_returns_today():
    from pipeline.feed_publisher import _parse_pub_date
    today = datetime.now(tz=UTC).strftime("%d.%m.%Y")
    assert _parse_pub_date("not-a-date") == today


def test_prune_old_episodes_deletes_oldest(tmp_path):
    from pipeline.feed_publisher import prune_old_episodes

    files = [
        tmp_path / "01.01.2024-episode-old.mp3",
        tmp_path / "15.06.2024-episode-mid.mp3",
        tmp_path / "01.01.2025-episode-newer.mp3",
        tmp_path / "15.03.2025-episode-newest.mp3",
    ]
    for f in files:
        f.write_bytes(b"audio")

    prune_old_episodes(tmp_path, "mp3", max_episodes=2)

    remaining = sorted(tmp_path.glob("*.mp3"))
    assert len(remaining) == 2
    assert remaining[0].name == "01.01.2025-episode-newer.mp3"
    assert remaining[1].name == "15.03.2025-episode-newest.mp3"


def test_prune_old_episodes_ignores_non_date_files(tmp_path):
    from pipeline.feed_publisher import prune_old_episodes

    keep_file = tmp_path / "not-a-date-file.mp3"
    keep_file.write_bytes(b"audio")
    dated_file = tmp_path / "01.01.2025-episode.mp3"
    dated_file.write_bytes(b"audio")

    prune_old_episodes(tmp_path, "mp3", max_episodes=0)

    assert keep_file.exists()
    assert not dated_file.exists()


def test_prune_old_episodes_no_delete_when_under_limit(tmp_path):
    from pipeline.feed_publisher import prune_old_episodes

    for name in ["01.01.2025-ep1.mp3", "15.03.2025-ep2.mp3"]:
        (tmp_path / name).write_bytes(b"audio")

    prune_old_episodes(tmp_path, "mp3", max_episodes=10)

    assert len(list(tmp_path.glob("*.mp3"))) == 2


def test_patch_feed_xml_replaces_enclosure(tmp_path):
    from pipeline.feed_publisher import _patch_feed_xml

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Test Podcast</title>
  <item>
    <title>Episode 42</title>
    <guid>ep-042</guid>
    <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
    <enclosure url="https://example.com/ep42.mp3" length="50000000" type="audio/mpeg"/>
  </item>
</channel>
</rss>"""

    podcast_dir = tmp_path / "test-podcast"
    podcast_dir.mkdir()
    local_file = podcast_dir / "01.01.2025-episode-42.mp3"
    local_file.write_bytes(b"x" * 1234)

    patched = _patch_feed_xml(xml, podcast_dir, "test-podcast", "https://example.com", "mp3")

    assert "https://example.com/test-podcast/01.01.2025-episode-42.mp3" in patched
    assert "1234" in patched  # file size updated


def test_patch_feed_xml_keeps_original_when_no_local_file(tmp_path):
    from pipeline.feed_publisher import _patch_feed_xml

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <item>
    <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
    <enclosure url="https://example.com/ep42.mp3" length="50000000" type="audio/mpeg"/>
  </item>
</channel>
</rss>"""

    podcast_dir = tmp_path / "test-podcast"
    podcast_dir.mkdir()
    # No local file

    patched = _patch_feed_xml(xml, podcast_dir, "test-podcast", "https://example.com", "mp3")

    assert "https://example.com/ep42.mp3" in patched


async def test_generate_feed_rss_writes_file(tmp_path):
    import httpx
    import respx

    from config.config_loader import (
        AdDetectionConfig,
        AppConfig,
        AudioConfig,
        FeedConfig,
        InterpretationConfig,
        LoggingConfig,
        PathsConfig,
        PublishingConfig,
        RetryConfig,
        TranscriptionConfig,
    )
    from pipeline.feed_publisher import generate_feed_rss

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>My Podcast</title>
  <item>
    <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
    <enclosure url="https://example.com/ep.mp3" length="1000" type="audio/mpeg"/>
  </item>
</channel>
</rss>"""

    feed_cfg = FeedConfig(name="My Podcast", url="https://feeds.example.com/test.rss")

    podcast_dir = tmp_path / "my-podcast"
    podcast_dir.mkdir()
    (podcast_dir / "01.01.2025-some-episode.mp3").write_bytes(b"audio")

    cfg = AppConfig(
        feeds=[feed_cfg],
        paths=PathsConfig(output_dir=tmp_path, database=tmp_path / "test.db"),
        transcription=TranscriptionConfig(provider="openai", model="whisper-1"),
        interpretation=InterpretationConfig(provider="openai", model="gpt-4o"),
        ad_detection=AdDetectionConfig(),
        audio=AudioConfig(),
        logging=LoggingConfig(),
        retry=RetryConfig(),
        publishing=PublishingConfig(base_url="https://example.com"),
    )

    with respx.mock:
        respx.get("https://feeds.example.com/test.rss").respond(200, text=xml)
        async with httpx.AsyncClient() as client:
            await generate_feed_rss(
                feed_cfg,
                cfg,
                feed_slug="my-podcast",
                podcast_dir=podcast_dir,
                client=client,
            )

    rss_path = tmp_path / "my-podcast.rss"
    assert rss_path.exists()
    content = rss_path.read_text()
    assert "https://example.com/my-podcast/" in content


async def test_generate_feed_rss_skips_when_no_base_url(tmp_path):
    import httpx
    import respx

    from config.config_loader import (
        AdDetectionConfig,
        AppConfig,
        AudioConfig,
        FeedConfig,
        InterpretationConfig,
        LoggingConfig,
        PathsConfig,
        PublishingConfig,
        RetryConfig,
        TranscriptionConfig,
    )
    from pipeline.feed_publisher import generate_feed_rss

    feed_cfg = FeedConfig(name="My Podcast", url="https://feeds.example.com/test.rss")
    cfg = AppConfig(
        feeds=[feed_cfg],
        paths=PathsConfig(output_dir=tmp_path, database=tmp_path / "test.db"),
        transcription=TranscriptionConfig(provider="openai", model="whisper-1"),
        interpretation=InterpretationConfig(provider="openai", model="gpt-4o"),
        ad_detection=AdDetectionConfig(),
        audio=AudioConfig(),
        logging=LoggingConfig(),
        retry=RetryConfig(),
        publishing=PublishingConfig(base_url=None),
    )

    with respx.mock:
        # No URL registered — if HTTP is called, respx will raise
        async with httpx.AsyncClient() as client:
            await generate_feed_rss(
                feed_cfg,
                cfg,
                feed_slug="my-podcast",
                podcast_dir=tmp_path / "my-podcast",
                client=client,
            )

    # No RSS file should be written
    assert not (tmp_path / "my-podcast.rss").exists()
