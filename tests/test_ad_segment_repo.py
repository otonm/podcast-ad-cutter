from models.ad_segment import AdSegment


async def _insert_episode(db_conn) -> None:
    await db_conn.execute(
        "INSERT INTO episodes (guid, feed_name, title, audio_url, published_at)"
        " VALUES ('ep-001', 'Test', 'Ep 1', 'https://example.com/ep1.mp3', '2025-01-01')"
    )
    await db_conn.commit()


async def test_save_all_and_get_by_episode(db_conn):
    from db.repositories.ad_segment_repo import AdSegmentRepository

    await _insert_episode(db_conn)
    repo = AdSegmentRepository(db_conn)
    segments = [
        AdSegment(
            episode_guid="ep-001",
            start_ms=60000,
            end_ms=120000,
            confidence=0.9,
            reason="Promo code",
            sponsor_name="Acme",
        ),
        AdSegment(
            episode_guid="ep-001",
            start_ms=300000,
            end_ms=360000,
            confidence=0.8,
            reason="Sponsor mention",
        ),
    ]
    await repo.save_all(segments)
    results = await repo.get_by_episode("ep-001")
    assert len(results) == 2
    assert results[0].start_ms == 60000
    assert results[1].sponsor_name is None


async def test_mark_cut(db_conn):
    from db.repositories.ad_segment_repo import AdSegmentRepository

    await _insert_episode(db_conn)
    repo = AdSegmentRepository(db_conn)
    segments = [
        AdSegment(
            episode_guid="ep-001",
            start_ms=60000,
            end_ms=120000,
            confidence=0.9,
            reason="Promo code",
        ),
    ]
    await repo.save_all(segments)
    results = await repo.get_by_episode("ep-001")
    assert results[0].was_cut is False
    await repo.mark_cut("ep-001")
    results = await repo.get_by_episode("ep-001")
    assert results[0].was_cut is True
