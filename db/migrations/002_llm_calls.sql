CREATE TABLE IF NOT EXISTS llm_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid TEXT    NOT NULL REFERENCES episodes(guid),
    call_type    TEXT    NOT NULL,
    model        TEXT    NOT NULL,
    cost_usd     REAL    NOT NULL DEFAULT 0.0,
    called_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_episode ON llm_calls(episode_guid);
