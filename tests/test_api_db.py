"""Tests for read-only DB viewer endpoints — GET /api/v1/db/episodes|transcriptions|ads|costs."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from aiohttp.test_utils import TestClient, TestServer
from slugify import slugify

from api.event_bus import EventBus
from api.routes.db import _is_complete
from api.run_state import RunState
from api.server import create_app
from database.connection import Database

# ---------------------------------------------------------------------------
# YAML fixture (two feeds for slug-filter tests)
# ---------------------------------------------------------------------------

_TWO_FEEDS_YAML = """\
feeds:
  - title: "Show A"
    url: "https://show-a.example/feed.rss"
    enabled: true
    episodes_to_keep: 5
  - title: "Show B"
    url: "https://show-b.example/feed.rss"
    enabled: false
    episodes_to_keep: 10
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
paths:
  output_dir: "./output"
  cache_dir: "./cache"
  data_dir: "./data"
  log_dir: "./logs"
ad_detection:
  min_duration: 10000
  min_confidence: 0.7
output:
  file_type: "mp3"
  bitrate: "128k"
log:
  level: "ERROR"
  to_file: false
base_url: "http://localhost:8080"
"""

# ---------------------------------------------------------------------------
# App factory helper
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path, yaml_content: str = _TWO_FEEDS_YAML) -> tuple[object, Path]:
    """Create a test app with a real config file on disk."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml_content)
    cfg = MagicMock()
    cfg.app.paths.data_dir = tmp_path
    cfg.app.paths.output_dir = tmp_path / "output"
    app = create_app(EventBus(), time.monotonic(), RunState(), cfg, config_path, tmp_path / "logs")
    return app, config_path


def _pub_date_str(pubdate: str) -> str:
    """Convert ISO pubdate string to the output-file date prefix."""
    return datetime.fromisoformat(pubdate).astimezone().strftime("%d.%m.%Y")


# ---------------------------------------------------------------------------
# GET /api/v1/db/episodes
# ---------------------------------------------------------------------------


class TestGetEpisodes:
    async def test_episodes_returns_rows_with_feed_slug_and_pipeline_state(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes returns rows with feed_slug and pipeline_state."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, pubdate, url) VALUES (?, ?, ?, ?, ?)",
                ("Show A", "ep-guid-1", "Episode One", "2024-01-15T10:00:00Z", "https://example.com/ep1"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes")
            assert resp.status == 200
            data = await resp.json()
            assert len(data) >= 1
            row = next(r for r in data if r["guid"] == "ep-guid-1")
            assert row["feed_slug"] == "show-a"
            assert row["pipeline_state"] in {"pending", "downloaded", "transcribed", "processed", "complete", "skipped"}

    async def test_episodes_pagination_respects_offset_and_limit(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?offset=N&limit=N paginates correctly."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            for i in range(5):
                await db.conn.execute(
                    "INSERT INTO episodes (podcast, guid, title, pubdate, url) VALUES (?, ?, ?, ?, ?)",
                    ("Show A", f"ep-guid-{i}", f"Episode {i}", f"2024-01-{i+1:02d}T10:00:00Z", "https://example.com/ep"),
                )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?limit=2&offset=0")
            assert resp.status == 200
            page1 = await resp.json()
            assert len(page1) == 2

            resp2 = await client.get("/api/v1/db/episodes?limit=2&offset=2")
            assert resp2.status == 200
            page2 = await resp2.json()
            assert len(page2) == 2

            guids_page1 = {r["guid"] for r in page1}
            guids_page2 = {r["guid"] for r in page2}
            assert guids_page1.isdisjoint(guids_page2)

    async def test_episodes_limit_too_large_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?limit=9999 returns 400 (limit must be 1-200)."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?limit=9999")
            assert resp.status == 400

    async def test_episodes_negative_limit_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?limit=-1 returns 400."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?limit=-1")
            assert resp.status == 400

    async def test_episodes_zero_limit_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?limit=0 returns 400."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?limit=0")
            assert resp.status == 400

    async def test_episodes_negative_offset_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?offset=-1 returns 400."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?offset=-1")
            assert resp.status == 400

    async def test_episodes_feed_filter_maps_slug_to_podcast_title(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?feed=show-a returns only Show A episodes."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", "ep-a", "Ep A", "https://example.com/a"),
            )
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show B", "ep-b", "Ep B", "https://example.com/b"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?feed=show-a")
            assert resp.status == 200
            data = await resp.json()
            assert all(r["feed_slug"] == "show-a" for r in data)
            guids = {r["guid"] for r in data}
            assert "ep-a" in guids
            assert "ep-b" not in guids

    async def test_episodes_pipeline_state_skipped_takes_priority(self, tmp_path: Path) -> None:
        """Skipped episodes show pipeline_state='skipped' regardless of other joins."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url, skipped) VALUES (?, ?, ?, ?, ?)",
                ("Show A", "ep-skipped", "Skipped Ep", "https://example.com/s", 1),
            )
            # Also insert transcription to confirm it doesn't override skipped
            await db.conn.execute(
                "INSERT INTO transcriptions (guid, transcription) VALUES (?, ?)",
                ("ep-skipped", "some text"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes")
            assert resp.status == 200
            data = await resp.json()
            row = next(r for r in data if r["guid"] == "ep-skipped")
            assert row["pipeline_state"] == "skipped"

    async def test_episodes_pipeline_state_complete_when_output_file_exists(self, tmp_path: Path) -> None:
        """pipeline_state='complete' when output file exists on disk (from processed state)."""
        db_path = tmp_path / "data.db"
        podcast = "Show A"
        guid = "ep-complete"
        title = "Complete Episode"
        pubdate = "2024-03-10T12:00:00Z"

        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, pubdate, url) VALUES (?, ?, ?, ?, ?)",
                (podcast, guid, title, pubdate, "https://example.com/c"),
            )
            # Insert ad_detection_runs to make DB state = processed
            await db.conn.execute(
                "INSERT INTO ad_detection_runs (guid) VALUES (?)", (guid,)
            )
            await db.conn.commit()

        # Create the output file
        feed_slug = slugify(podcast)
        pub_date_str = _pub_date_str(pubdate)
        title_slug = slugify(title)
        output_dir = tmp_path / "output"
        (output_dir / feed_slug).mkdir(parents=True, exist_ok=True)
        (output_dir / feed_slug / f"{pub_date_str}-{title_slug}.mp3").touch()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes")
            assert resp.status == 200
            data = await resp.json()
            row = next(r for r in data if r["guid"] == guid)
            assert row["pipeline_state"] == "complete"

    async def test_episodes_pipeline_state_complete_upgrades_from_downloaded_state(self, tmp_path: Path) -> None:
        """pipeline_state='complete' upgrades from downloaded state when output file exists."""
        db_path = tmp_path / "data.db"
        podcast = "Show A"
        guid = "ep-dl-complete"
        title = "Downloaded Complete"
        pubdate = "2024-04-05T08:00:00Z"

        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, pubdate, url) VALUES (?, ?, ?, ?, ?)",
                (podcast, guid, title, pubdate, "https://example.com/dl"),
            )
            # Only audio metadata — DB state = downloaded
            await db.conn.execute(
                "INSERT INTO episode_audio_metadata (guid, duration, codec, channels, bitrate) VALUES (?, ?, ?, ?, ?)",
                (guid, 120.0, "mp3", 2, 128),
            )
            await db.conn.commit()

        # Create the output file
        feed_slug = slugify(podcast)
        pub_date_str = _pub_date_str(pubdate)
        title_slug = slugify(title)
        output_dir = tmp_path / "output"
        (output_dir / feed_slug).mkdir(parents=True, exist_ok=True)
        (output_dir / feed_slug / f"{pub_date_str}-{title_slug}.mp3").touch()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes")
            assert resp.status == 200
            data = await resp.json()
            row = next(r for r in data if r["guid"] == guid)
            assert row["pipeline_state"] == "complete"

    async def test_episodes_pipeline_state_complete_upgrades_from_transcribed_state(self, tmp_path: Path) -> None:
        """pipeline_state='complete' upgrades from transcribed state when output file exists."""
        db_path = tmp_path / "data.db"
        podcast = "Show A"
        guid = "ep-tr-complete"
        title = "Transcribed Complete"
        pubdate = "2024-05-20T16:00:00Z"

        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, pubdate, url) VALUES (?, ?, ?, ?, ?)",
                (podcast, guid, title, pubdate, "https://example.com/tr"),
            )
            # Only transcription — DB state = transcribed
            await db.conn.execute(
                "INSERT INTO transcriptions (guid, transcription) VALUES (?, ?)",
                (guid, "hello world"),
            )
            await db.conn.commit()

        # Create the output file
        feed_slug = slugify(podcast)
        pub_date_str = _pub_date_str(pubdate)
        title_slug = slugify(title)
        output_dir = tmp_path / "output"
        (output_dir / feed_slug).mkdir(parents=True, exist_ok=True)
        (output_dir / feed_slug / f"{pub_date_str}-{title_slug}.mp3").touch()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes")
            assert resp.status == 200
            data = await resp.json()
            row = next(r for r in data if r["guid"] == guid)
            assert row["pipeline_state"] == "complete"

    async def test_episodes_pipeline_state_pending_when_no_joins_match(self, tmp_path: Path) -> None:
        """pipeline_state='pending' for a brand-new episode with no join rows."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", "ep-pending", "Pending Ep", "https://example.com/pend"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes")
            assert resp.status == 200
            data = await resp.json()
            row = next(r for r in data if r["guid"] == "ep-pending")
            assert row["pipeline_state"] == "pending"

    async def test_episodes_invalid_limit_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?limit=abc returns 400."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?limit=abc")
            assert resp.status == 400

    async def test_episodes_invalid_offset_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?offset=xyz returns 400."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?offset=xyz")
            assert resp.status == 400

    async def test_episodes_unknown_feed_slug_returns_empty_list(self, tmp_path: Path) -> None:
        """GET /api/v1/db/episodes?feed=does-not-exist returns empty list."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/episodes?feed=does-not-exist")
            assert resp.status == 200
            data = await resp.json()
            assert data == []


# ---------------------------------------------------------------------------
# GET /api/v1/db/transcriptions/{guid}
# ---------------------------------------------------------------------------


class TestGetTranscriptions:
    async def test_transcriptions_returns_text_and_segments(self, tmp_path: Path) -> None:
        """GET /api/v1/db/transcriptions/{guid} returns {guid, text, segments}."""
        db_path = tmp_path / "data.db"
        guid = "ep-trans-1"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", guid, "Trans Ep", "https://example.com/t1"),
            )
            await db.conn.execute(
                "INSERT INTO transcriptions (guid, transcription) VALUES (?, ?)",
                (guid, "Hello world"),
            )
            await db.conn.execute(
                "INSERT INTO transcription_segments (guid, start_ms, end_ms, text) VALUES (?, ?, ?, ?)",
                (guid, 0, 5000, "Hello"),
            )
            await db.conn.execute(
                "INSERT INTO transcription_segments (guid, start_ms, end_ms, text) VALUES (?, ?, ?, ?)",
                (guid, 5000, 10000, "world"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/v1/db/transcriptions/{guid}")
            assert resp.status == 200
            data = await resp.json()
            assert data["guid"] == guid
            assert data["text"] == "Hello world"
            segs = data["segments"]
            assert len(segs) == 2
            assert segs[0]["start"] == 0
            assert segs[0]["end"] == 5000
            assert segs[0]["text"] == "Hello"
            assert segs[1]["start"] == 5000
            assert segs[1]["end"] == 10000

    async def test_transcriptions_returns_404_when_missing(self, tmp_path: Path) -> None:
        """GET /api/v1/db/transcriptions/nonexistent returns 404."""
        async with Database(tmp_path / "data.db"):
            pass
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/transcriptions/nonexistent-guid")
            assert resp.status == 404


# ---------------------------------------------------------------------------
# GET /api/v1/db/ads/{guid}
# ---------------------------------------------------------------------------


class TestGetAds:
    async def test_ads_returns_detected_true_with_segments(self, tmp_path: Path) -> None:
        """GET /api/v1/db/ads/{guid} returns {guid, detected: true, segments[...]}."""
        db_path = tmp_path / "data.db"
        guid = "ep-ads-1"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", guid, "Ads Ep", "https://example.com/a1"),
            )
            await db.conn.execute(
                "INSERT INTO ad_detection_runs (guid) VALUES (?)", (guid,)
            )
            await db.conn.execute(
                "INSERT INTO ad_segments (guid, start_ms, end_ms, confidence, sponsor, ad_topic, indices) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guid, 1000, 30000, 0.95, "ACME Corp", "Streaming", "[]"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/v1/db/ads/{guid}")
            assert resp.status == 200
            data = await resp.json()
            assert data["guid"] == guid
            assert data["detected"] is True
            assert len(data["segments"]) == 1
            seg = data["segments"][0]
            assert seg["start_ms"] == 1000
            assert seg["end_ms"] == 30000
            assert seg["confidence"] == 0.95
            assert seg["sponsor"] == "ACME Corp"
            assert seg["ad_topic"] == "Streaming"

    async def test_ads_detected_true_with_empty_segments_when_run_row_exists(self, tmp_path: Path) -> None:
        """detected=True even when ad_segments is empty (run row exists but no ads found)."""
        db_path = tmp_path / "data.db"
        guid = "ep-ads-empty"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", guid, "No Ads Ep", "https://example.com/a2"),
            )
            await db.conn.execute(
                "INSERT INTO ad_detection_runs (guid) VALUES (?)", (guid,)
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/v1/db/ads/{guid}")
            assert resp.status == 200
            data = await resp.json()
            assert data["detected"] is True
            assert data["segments"] == []

    async def test_ads_response_does_not_contain_indices(self, tmp_path: Path) -> None:
        """Response must not contain the indices column."""
        db_path = tmp_path / "data.db"
        guid = "ep-ads-idx"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", guid, "Idx Ep", "https://example.com/a3"),
            )
            await db.conn.execute(
                "INSERT INTO ad_detection_runs (guid) VALUES (?)", (guid,)
            )
            await db.conn.execute(
                "INSERT INTO ad_segments (guid, start_ms, end_ms, confidence, sponsor, ad_topic, indices) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guid, 2000, 20000, 0.8, "SponsorX", "Finance", "[0, 1, 2]"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/v1/db/ads/{guid}")
            assert resp.status == 200
            data = await resp.json()
            for seg in data["segments"]:
                assert "indices" not in seg

    async def test_ads_returns_404_when_no_run_row(self, tmp_path: Path) -> None:
        """GET /api/v1/db/ads/nonexistent returns 404."""
        async with Database(tmp_path / "data.db"):
            pass
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/ads/no-such-guid")
            assert resp.status == 404


# ---------------------------------------------------------------------------
# GET /api/v1/db/costs
# ---------------------------------------------------------------------------


class TestGetCosts:
    async def test_costs_returns_total_by_model_by_episode(self, tmp_path: Path) -> None:
        """GET /api/v1/db/costs returns {total, by_model, by_episode}."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", "ep-cost-1", "Cost Ep 1", "https://example.com/c1"),
            )
            await db.conn.execute(
                "INSERT INTO cost_tracking (provider, model, cost, guid) VALUES (?, ?, ?, ?)",
                ("groq", "whisper-large", 0.01, "ep-cost-1"),
            )
            await db.conn.execute(
                "INSERT INTO cost_tracking (provider, model, cost, guid) VALUES (?, ?, ?, ?)",
                ("groq", "llama-3.3-70b", 0.02, "ep-cost-1"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/costs")
            assert resp.status == 200
            data = await resp.json()
            assert "total" in data
            assert "by_model" in data
            assert "by_episode" in data
            assert abs(data["total"] - 0.03) < 0.0001
            models = {(m["provider"], m["model"]) for m in data["by_model"]}
            assert ("groq", "whisper-large") in models
            assert ("groq", "llama-3.3-70b") in models
            ep_guids = {e["guid"] for e in data["by_episode"]}
            assert "ep-cost-1" in ep_guids

    async def test_costs_feed_filter_applies_to_all_sections(self, tmp_path: Path) -> None:
        """?feed=show-a filter applies total, by_model, and by_episode to Show A only."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", "ep-a-cost", "Ep A", "https://example.com/a"),
            )
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show B", "ep-b-cost", "Ep B", "https://example.com/b"),
            )
            await db.conn.execute(
                "INSERT INTO cost_tracking (provider, model, cost, guid) VALUES (?, ?, ?, ?)",
                ("groq", "whisper", 0.05, "ep-a-cost"),
            )
            await db.conn.execute(
                "INSERT INTO cost_tracking (provider, model, cost, guid) VALUES (?, ?, ?, ?)",
                ("groq", "whisper", 0.07, "ep-b-cost"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/costs?feed=show-a")
            assert resp.status == 200
            data = await resp.json()
            assert abs(data["total"] - 0.05) < 0.0001
            ep_guids = {e["guid"] for e in data["by_episode"]}
            assert "ep-a-cost" in ep_guids
            assert "ep-b-cost" not in ep_guids

    async def test_costs_by_episode_omits_null_guid_rows(self, tmp_path: Path) -> None:
        """by_episode never contains entries with NULL guid."""
        db_path = tmp_path / "data.db"
        async with Database(db_path) as db:
            await db.conn.execute(
                "INSERT INTO episodes (podcast, guid, title, url) VALUES (?, ?, ?, ?)",
                ("Show A", "ep-real-guid", "Real Ep", "https://example.com/r"),
            )
            # NULL guid row (pre-migration cost row)
            await db.conn.execute(
                "INSERT INTO cost_tracking (provider, model, cost) VALUES (?, ?, ?)",
                ("groq", "whisper", 0.01),
            )
            # Row with a real guid
            await db.conn.execute(
                "INSERT INTO cost_tracking (provider, model, cost, guid) VALUES (?, ?, ?, ?)",
                ("groq", "llama", 0.02, "ep-real-guid"),
            )
            await db.conn.commit()

        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/costs")
            assert resp.status == 200
            data = await resp.json()
            # total includes both rows
            assert abs(data["total"] - 0.03) < 0.0001
            # by_episode only contains the real-guid row
            ep_guids = [e["guid"] for e in data["by_episode"]]
            assert None not in ep_guids
            assert "ep-real-guid" in ep_guids
            assert len(ep_guids) == 1

    async def test_costs_unknown_feed_slug_returns_empty_response(self, tmp_path: Path) -> None:
        """GET /api/v1/db/costs?feed=does-not-exist returns empty zero response."""
        app, _ = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/db/costs?feed=does-not-exist")
            assert resp.status == 200
            data = await resp.json()
            assert data["total"] == 0.0
            assert data["by_model"] == []
            assert data["by_episode"] == []


# ---------------------------------------------------------------------------
# _is_complete helper — None-guard tests (WR-02)
# ---------------------------------------------------------------------------


class TestIsCompleteHelper:
    def test_returns_false_when_title_is_none(self, tmp_path: Path) -> None:
        assert _is_complete("2024-01-01T00:00:00Z", None, "Podcast", tmp_path) is False

    def test_returns_false_when_podcast_is_none(self, tmp_path: Path) -> None:
        assert _is_complete("2024-01-01T00:00:00Z", "Title", None, tmp_path) is False

    def test_returns_false_when_pubdate_is_none(self, tmp_path: Path) -> None:
        assert _is_complete(None, "Title", "Podcast", tmp_path) is False
