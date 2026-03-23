"""Tests for EpisodeCopier."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from components.episode_copier import EpisodeCopier

if TYPE_CHECKING:
    from pathlib import Path


async def test_copy_copies_file_to_correct_destination(tmp_path: Path) -> None:
    """File is copied to output_dir/{feed_slug}/{DD.MM.YYYY}-{slugified-title}.{ext}."""
    src = tmp_path / "ep-001.mp3"
    src.write_bytes(b"audio data")
    copier = EpisodeCopier(output_dir=tmp_path / "output", base_url="https://example.com")
    pub_date = datetime(2026, 3, 22, tzinfo=UTC)

    guid, dest, _url = await copier.copy("ep-001", src, "my-feed", pub_date, "My Episode")

    assert guid == "ep-001"
    assert dest.exists()
    assert dest.read_bytes() == b"audio data"
    assert dest.name == "22.03.2026-my-episode.mp3"
    assert dest.parent.name == "my-feed"


async def test_copy_returns_correct_url(tmp_path: Path) -> None:
    """Returned URL matches base_url/feed_slug/filename."""
    src = tmp_path / "ep-001.mp3"
    src.write_bytes(b"x")
    copier = EpisodeCopier(output_dir=tmp_path / "output", base_url="https://example.com")
    pub_date = datetime(2026, 3, 22, tzinfo=UTC)

    _, _, url = await copier.copy("ep-001", src, "my-feed", pub_date, "My Episode")

    assert url == "https://example.com/my-feed/22.03.2026-my-episode.mp3"


async def test_copy_skips_existing_file(tmp_path: Path) -> None:
    """If the destination already exists, skip the copy but still return the triple."""
    src = tmp_path / "ep-001.mp3"
    src.write_bytes(b"new data")
    feed_dir = tmp_path / "output" / "my-feed"
    feed_dir.mkdir(parents=True)
    existing = feed_dir / "22.03.2026-my-episode.mp3"
    existing.write_bytes(b"old data")

    copier = EpisodeCopier(output_dir=tmp_path / "output", base_url="https://example.com")
    pub_date = datetime(2026, 3, 22, tzinfo=UTC)

    guid, dest, _url = await copier.copy("ep-001", src, "my-feed", pub_date, "My Episode")

    assert guid == "ep-001"
    assert existing.read_bytes() == b"old data"  # not overwritten
    assert dest == existing


async def test_copy_creates_feed_subdirectory(tmp_path: Path) -> None:
    """The feed subdirectory is created automatically if it does not exist."""
    src = tmp_path / "ep-001.m4a"
    src.write_bytes(b"aac")
    output_dir = tmp_path / "output"
    copier = EpisodeCopier(output_dir=output_dir, base_url="https://example.com")
    pub_date = datetime(2026, 1, 5, tzinfo=UTC)

    await copier.copy("ep-001", src, "tech-talks", pub_date, "Intro")

    assert (output_dir / "tech-talks").is_dir()
