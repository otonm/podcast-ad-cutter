from datetime import UTC, datetime

from models.episode import Episode


def test_feed_slug_basic():
    from pipeline.runner import _feed_slug

    assert _feed_slug("My Favourite Podcast") == "my-favourite-podcast"


def test_feed_slug_unicode():
    from pipeline.runner import _feed_slug

    # python-slugify transliterates accented chars (é→e) and drops symbols like & and —
    assert _feed_slug("Café & Music — Vol. 3") == "cafe-music-vol-3"


def test_feed_slug_truncates():
    from pipeline.runner import _feed_slug

    long_title = "A" * 200
    result = _feed_slug(long_title)
    assert len(result) <= 80


def test_episode_filename_format():
    from pipeline.runner import _episode_filename

    ep = Episode(
        guid="test-guid",
        feed_title="My Podcast",
        title="Hello World! #42",
        audio_url="https://example.com/ep.mp3",  # type: ignore[arg-type]
        published=datetime(2025, 3, 15, tzinfo=UTC),
    )
    assert _episode_filename(ep, "mp3") == "15.03.2025-hello-world-42.mp3"
