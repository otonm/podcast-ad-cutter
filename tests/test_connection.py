import aiosqlite


async def test_db_connection_applies_pragmas(db_conn: aiosqlite.Connection) -> None:
    cursor = await db_conn.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_db_tables_exist(db_conn: aiosqlite.Connection) -> None:
    cursor = await db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    assert "episodes" in tables
    assert "transcripts" in tables
    assert "transcript_segments" in tables
    assert "topic_contexts" in tables
    assert "ad_segments" in tables
