import aiosqlite

from models.episode import Episode


async def test_upsert_and_get_by_guid(db_conn: aiosqlite.Connection) -> None:
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    ep = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",  # type: ignore[arg-type]
        published="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
        duration_seconds=3600,
    )
    await repo.upsert(ep)
    result = await repo.get_by_guid("ep-001")
    assert result is not None
    assert result.guid == "ep-001"
    assert result.title == "Episode 1"


async def test_get_by_guid_returns_none(db_conn: aiosqlite.Connection) -> None:
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    result = await repo.get_by_guid("nonexistent")
    assert result is None


async def test_upsert_updates_existing(db_conn: aiosqlite.Connection) -> None:
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    ep1 = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",  # type: ignore[arg-type]
        published="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
    )
    await repo.upsert(ep1)
    ep2 = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1 Updated",
        audio_url="https://example.com/ep1.mp3",  # type: ignore[arg-type]
        published="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
    )
    await repo.upsert(ep2)
    result = await repo.get_by_guid("ep-001")
    assert result is not None
    assert result.title == "Episode 1 Updated"


async def test_list_by_feed(db_conn: aiosqlite.Connection) -> None:
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    for i in range(3):
        ep = Episode(
            guid=f"ep-{i}",
            feed_title="Test Pod",
            title=f"Episode {i}",
            audio_url=f"https://example.com/ep{i}.mp3",  # type: ignore[arg-type]
            published="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
        )
        await repo.upsert(ep)
    results = await repo.list_by_feed("Test Pod")
    assert len(results) == 3
