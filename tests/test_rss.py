from pathlib import Path


def test_parse_feed_with_enclosure():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    enclosure_ep = [e for e in episodes if e.guid == "ep-042"]
    assert len(enclosure_ep) == 1
    assert str(enclosure_ep[0].audio_url) == "https://example.com/ep42.mp3"


def test_parse_feed_with_media_content():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    media_ep = [e for e in episodes if e.guid == "ep-041"]
    assert len(media_ep) == 1
    assert str(media_ep[0].audio_url) == "https://example.com/ep41.mp3"


def test_parse_feed_skips_items_without_audio():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    guids = [e.guid for e in episodes]
    assert "ep-040" not in guids


def test_parse_feed_returns_sorted_by_date():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    assert episodes[0].guid == "ep-042"  # newest first


async def test_fetch_episodes():
    import httpx
    import respx

    from config.config_loader import FeedConfig
    from pipeline.rss import fetch_episodes

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    feed_cfg = FeedConfig(name="Test Podcast", url="https://feeds.example.com/test.rss")

    with respx.mock:
        respx.get("https://feeds.example.com/test.rss").respond(200, text=xml)
        async with httpx.AsyncClient() as client:
            episodes = await fetch_episodes(feed_cfg, client=client)
    assert len(episodes) > 0
    assert episodes[0].guid == "ep-042"
