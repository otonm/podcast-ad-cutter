from pathlib import Path

import pytest

from config.config_loader import AppConfig, load_config


@pytest.fixture
def app_config() -> AppConfig:
    return load_config(Path("tests/fixtures/test_config.yaml"))


@pytest.fixture
async def db_conn():
    """In-memory SQLite connection with schema applied."""
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")
    schema = Path("db/schema.sql").read_text()
    await conn.executescript(schema)
    yield conn
    await conn.close()
