from models.episode import Episode
from models.transcript import Segment, Transcript


async def _insert_episode(db_conn) -> None:
    """Helper to satisfy foreign key."""
    await db_conn.execute(
        "INSERT INTO episodes (guid, feed_name, title, audio_url, published_at)"
        " VALUES ('ep-001', 'Test', 'Ep 1', 'https://example.com/ep1.mp3', '2025-01-01')"
    )
    await db_conn.commit()


async def test_save_and_get_transcript(db_conn):
    from db.repositories.transcript_repo import TranscriptRepository

    await _insert_episode(db_conn)
    repo = TranscriptRepository(db_conn)
    transcript = Transcript(
        episode_guid="ep-001",
        segments=(
            Segment(start_ms=0, end_ms=5000, text="Hello world"),
            Segment(start_ms=5000, end_ms=10000, text="Welcome back"),
        ),
        full_text="Hello world Welcome back",
        language="en",
        provider_model="whisper-1",
    )
    await repo.save(transcript)
    result = await repo.get_by_episode_guid("ep-001")
    assert result is not None
    assert result.episode_guid == "ep-001"
    assert len(result.segments) == 2
    assert result.segments[0].text == "Hello world"
    assert result.full_text == "Hello world Welcome back"


async def test_get_returns_none_for_missing(db_conn):
    from db.repositories.transcript_repo import TranscriptRepository

    repo = TranscriptRepository(db_conn)
    result = await repo.get_by_episode_guid("nonexistent")
    assert result is None


async def test_delete_transcript(db_conn):
    from db.repositories.transcript_repo import TranscriptRepository

    await _insert_episode(db_conn)
    repo = TranscriptRepository(db_conn)
    transcript = Transcript(
        episode_guid="ep-001",
        segments=(Segment(start_ms=0, end_ms=5000, text="Hello"),),
        full_text="Hello",
        language="en",
        provider_model="whisper-1",
    )
    await repo.save(transcript)
    await repo.delete("ep-001")
    result = await repo.get_by_episode_guid("ep-001")
    assert result is None
