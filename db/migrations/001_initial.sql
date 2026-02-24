PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS episodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guid         TEXT    NOT NULL UNIQUE,
    feed_name    TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    audio_url    TEXT    NOT NULL,
    published_at TEXT,
    duration_sec INTEGER,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transcripts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid   TEXT NOT NULL UNIQUE REFERENCES episodes(guid),
    language       TEXT NOT NULL DEFAULT 'en',
    full_text      TEXT NOT NULL,
    provider_model TEXT NOT NULL,
    transcribed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    start_ms      INTEGER NOT NULL,
    end_ms        INTEGER NOT NULL,
    text          TEXT    NOT NULL,
    CHECK (end_ms > start_ms)
);
CREATE INDEX IF NOT EXISTS idx_segments_transcript ON transcript_segments(transcript_id);

CREATE TABLE IF NOT EXISTS topic_contexts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid TEXT NOT NULL UNIQUE REFERENCES episodes(guid),
    domain       TEXT NOT NULL,
    topic        TEXT NOT NULL,
    hosts        TEXT,
    notes        TEXT,
    extracted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ad_segments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid TEXT    NOT NULL REFERENCES episodes(guid),
    start_ms     INTEGER NOT NULL,
    end_ms       INTEGER NOT NULL,
    confidence   REAL    NOT NULL,
    reason       TEXT    NOT NULL,
    sponsor_name TEXT,
    was_cut      INTEGER NOT NULL DEFAULT 0,
    detected_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (end_ms > start_ms),
    CHECK (confidence BETWEEN 0.0 AND 1.0)
);
CREATE INDEX IF NOT EXISTS idx_ad_segments_episode ON ad_segments(episode_guid);
