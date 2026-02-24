import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_db(db_path: Path) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Open a connection, set pragmas, apply pending migrations, and yield."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await _apply_migrations(conn)
        yield conn
    finally:
        await conn.close()


async def _apply_migrations(conn: aiosqlite.Connection) -> None:
    """Apply any unapplied SQL migration files in order."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  filename TEXT NOT NULL UNIQUE,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    cursor = await conn.execute("SELECT filename FROM _migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    migrations_dir = Path("db/migrations")
    if not migrations_dir.exists():
        return

    for sql_file in sorted(migrations_dir.glob("*.sql")):
        if sql_file.name not in applied:
            logger.info(f"Applying migration: {sql_file.name}")
            sql = sql_file.read_text()
            await conn.executescript(sql)
            await conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (sql_file.name,))
            await conn.commit()
