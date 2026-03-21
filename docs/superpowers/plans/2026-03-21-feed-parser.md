# feed_parser.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stateless `FeedParser` component that parses downloaded RSS XML into typed `ParsedFeed` / `Episode` Pydantic models and integrate it into the `Pipeline`.

**Architecture:** `FeedParser` (no constructor args) exposes `parse_all()` → `_parse_one()` → `_parse_episode()`. `Pipeline.__init__` creates a `FeedParser()` instance; `Pipeline.run()` calls `parse_all()` on the download results and returns `list[ParsedFeed]`. Episode count is limited to `feed_config.episodes_to_keep` inside `_parse_one`.

**Tech Stack:** Python 3.12, Pydantic v2, `xml.etree.ElementTree` (stdlib), `email.utils.parsedate_to_datetime` (stdlib), pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `components/feed_parser.py` | **Create** | `Episode`, `ParsedFeed` Pydantic models; `FeedParser` class |
| `tests/test_feed_parser.py` | **Create** | 12 tests covering all parsing cases |
| `components/pipeline.py` | **Modify** | Import and instantiate `FeedParser`, call `parse_all`, update return type |
| `tests/test_pipeline.py` | **Modify** | Update 1 existing test (return-value test), add 1 new test (parser integration) |

---

### Task 1: Data models scaffold

**Files:**
- Create: `components/feed_parser.py`
- Create: `tests/test_feed_parser.py`

- [ ] **Step 1: Create the test file with model instantiation tests**

```python
# tests/test_feed_parser.py
"""Tests for the FeedParser component."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from components.feed_parser import Episode, FeedParser, ParsedFeed
from config.config_loader import FeedConfig

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FEED_CFG = FeedConfig(
    title="Test Pod",
    url="https://example.com/feed.rss",
    enabled=True,
    episodes_to_keep=3,
)

# Minimal valid RSS 2.0 with 3 episodes
VALID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Pod</title>
    <item>
      <guid>ep1</guid>
      <title>Episode 1</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg" length="1000"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <guid>ep2</guid>
      <title>Episode 2</title>
      <enclosure url="https://example.com/ep2.mp3" type="audio/mpeg" length="2000"/>
      <pubDate>Tue, 02 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <guid>ep3</guid>
      <title>Episode 3</title>
      <enclosure url="https://example.com/ep3.mp3" type="audio/mpeg" length="3000"/>
      <pubDate>Wed, 03 Jan 2024 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


def test_episode_model_instantiation() -> None:
    ep = Episode(
        guid="ep1",
        title="Episode 1",
        url="https://example.com/ep1.mp3",
        pub_date=None,
    )
    assert ep.guid == "ep1"
    assert ep.title == "Episode 1"
    assert ep.url == "https://example.com/ep1.mp3"
    assert ep.pub_date is None


def test_parsed_feed_model_instantiation() -> None:
    ep = Episode(guid="ep1", url="https://example.com/ep1.mp3", pub_date=None)
    pf = ParsedFeed(feed_config=FEED_CFG, title="Test Pod", episodes=[ep])
    assert pf.title == "Test Pod"
    assert len(pf.episodes) == 1
    assert pf.feed_config is FEED_CFG
```

- [ ] **Step 2: Run tests to confirm ImportError**

```bash
uv run pytest tests/test_feed_parser.py -v
```

Expected: `ImportError` (module doesn't exist yet).

- [ ] **Step 3: Create `components/feed_parser.py` with models only**

```python
"""FeedParser — parses downloaded RSS/Atom XML into structured data."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from pydantic import BaseModel

from config.config_loader import FeedConfig

logger = logging.getLogger(__name__)


class Episode(BaseModel):
    guid: str
    title: str = ""  # default "" if <title> absent — must be explicit so Pydantic does not raise
    url: str
    pub_date: datetime | None


class ParsedFeed(BaseModel):
    feed_config: FeedConfig
    title: str
    episodes: list[Episode]


class FeedParser:
    """Stateless RSS/Atom XML parser. No constructor args."""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_feed_parser.py::test_episode_model_instantiation tests/test_feed_parser.py::test_parsed_feed_model_instantiation -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: add Episode, ParsedFeed models and FeedParser scaffold"
```

---

### Task 2: `_parse_episode` — enclosure URL validation

**Files:**
- Modify: `tests/test_feed_parser.py`
- Modify: `components/feed_parser.py`

- [ ] **Step 1: Append failing tests to `tests/test_feed_parser.py`**

```python
# ---------------------------------------------------------------------------
# _parse_episode — enclosure / url
# ---------------------------------------------------------------------------

def test_episode_without_enclosure_skipped() -> None:
    """An <item> with no <enclosure> element must return None."""
    parser = FeedParser()
    item = ET.fromstring("<item><guid>ep1</guid><title>Episode 1</title></item>")
    assert parser._parse_episode(item) is None


def test_episode_empty_enclosure_url_skipped() -> None:
    """An <item> with <enclosure url=""> must return None."""
    parser = FeedParser()
    item = ET.fromstring('<item><enclosure url="" type="audio/mpeg" length="0"/></item>')
    assert parser._parse_episode(item) is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_feed_parser.py::test_episode_without_enclosure_skipped tests/test_feed_parser.py::test_episode_empty_enclosure_url_skipped -v
```

Expected: `AttributeError` — `_parse_episode` not defined yet.

- [ ] **Step 3: Implement `_parse_episode` inside `FeedParser`**

```python
    def _parse_episode(self, item: ET.Element) -> Episode | None:
        """Parse a single <item> element.

        Returns ``None`` if the item has no valid enclosure URL.
        """
        enclosure = item.find("enclosure")
        if enclosure is None:
            return None
        url = (enclosure.get("url") or "").strip()
        if not url:
            return None

        title = (item.findtext("title") or "").strip()

        guid_el = item.find("guid")
        guid_text = (guid_el.text or "").strip() if guid_el is not None else ""
        guid = guid_text or url  # fall back to URL when guid is absent or blank

        pub_date: datetime | None = None
        pub_date_str = item.findtext("pubDate")
        if pub_date_str:
            try:
                pub_date = parsedate_to_datetime(pub_date_str)
            except Exception:  # noqa: BLE001
                logger.debug(f"Could not parse pubDate {pub_date_str!r} — setting to None")

        return Episode(guid=guid, title=title, url=url, pub_date=pub_date)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_feed_parser.py::test_episode_without_enclosure_skipped tests/test_feed_parser.py::test_episode_empty_enclosure_url_skipped -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: implement _parse_episode with enclosure URL validation"
```

---

### Task 3: `_parse_episode` — guid fallback and pub_date parsing

**Files:**
- Modify: `tests/test_feed_parser.py`
- Modify: `components/feed_parser.py` (no changes needed — already handles these cases)

- [ ] **Step 1: Append tests to `tests/test_feed_parser.py`**

```python
# ---------------------------------------------------------------------------
# _parse_episode — guid, pub_date
# ---------------------------------------------------------------------------

def test_guid_falls_back_to_url() -> None:
    """When <guid> is absent, the enclosure URL is used as guid."""
    parser = FeedParser()
    item = ET.fromstring(
        "<item>"
        "<title>Ep</title>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="100"/>'
        "</item>"
    )
    episode = parser._parse_episode(item)
    assert episode is not None
    assert episode.guid == "https://example.com/ep.mp3"


def test_pub_date_parsed_to_datetime() -> None:
    """A valid RFC 2822 <pubDate> is parsed into a timezone-aware datetime."""
    parser = FeedParser()
    item = ET.fromstring(
        "<item>"
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="100"/>'
        "<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>"
        "</item>"
    )
    episode = parser._parse_episode(item)
    assert episode is not None
    assert isinstance(episode.pub_date, datetime)
    assert episode.pub_date == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_pub_date_missing_is_none() -> None:
    """When <pubDate> is absent, pub_date is None."""
    parser = FeedParser()
    item = ET.fromstring(
        "<item>"
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="100"/>'
        "</item>"
    )
    episode = parser._parse_episode(item)
    assert episode is not None
    assert episode.pub_date is None
```

- [ ] **Step 2: Run tests — should pass immediately**

```bash
uv run pytest tests/test_feed_parser.py::test_guid_falls_back_to_url tests/test_feed_parser.py::test_pub_date_parsed_to_datetime tests/test_feed_parser.py::test_pub_date_missing_is_none -v
```

Expected: 3 PASS (implementation already covers these cases).

- [ ] **Step 3: Commit**

```bash
git add tests/test_feed_parser.py
git commit -m "test: add guid fallback and pub_date parsing tests"
```

---

### Task 4: `_parse_one` — XML parsing, channel validation, title fallback, episode limiting

**Files:**
- Modify: `tests/test_feed_parser.py`
- Modify: `components/feed_parser.py`

- [ ] **Step 1: Append failing tests to `tests/test_feed_parser.py`**

```python
# ---------------------------------------------------------------------------
# _parse_one
# ---------------------------------------------------------------------------

def test_malformed_xml_skipped() -> None:
    """Malformed XML returns None without raising."""
    parser = FeedParser()
    assert parser._parse_one(FEED_CFG, "<<<not xml>>>") is None


def test_no_channel_element_skipped() -> None:
    """An XML document without a <channel> child returns None."""
    parser = FeedParser()
    assert parser._parse_one(FEED_CFG, "<rss version='2.0'><notchannel/></rss>") is None


def test_feed_title_falls_back_to_config_title() -> None:
    """When the channel has no <title>, feed_config.title is used."""
    parser = FeedParser()
    xml = (
        "<rss version='2.0'><channel>"
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
        "</channel></rss>"
    )
    result = parser._parse_one(FEED_CFG, xml)
    assert result is not None
    assert result.title == FEED_CFG.title


def test_episodes_limited_to_keep() -> None:
    """Episodes are sliced to feed_config.episodes_to_keep after parsing."""
    # FEED_CFG.episodes_to_keep = 3; add 3 more items to VALID_XML (6 total)
    extra = "".join(
        f"<item><guid>ep{i}</guid><title>Episode {i}</title>"
        f'<enclosure url="https://example.com/ep{i}.mp3" type="audio/mpeg" length="{i}"/>'
        f"<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>"
        f"</item>"
        for i in range(4, 7)
    )
    xml_6eps = VALID_XML.replace("</channel>", extra + "</channel>")
    parser = FeedParser()
    result = parser._parse_one(FEED_CFG, xml_6eps)
    assert result is not None
    assert len(result.episodes) == 3
    assert result.episodes[0].guid == "ep1"  # document order preserved
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_feed_parser.py::test_malformed_xml_skipped tests/test_feed_parser.py::test_no_channel_element_skipped tests/test_feed_parser.py::test_feed_title_falls_back_to_config_title tests/test_feed_parser.py::test_episodes_limited_to_keep -v
```

Expected: `AttributeError` — `_parse_one` not defined yet.

- [ ] **Step 3: Implement `_parse_one` inside `FeedParser`**

```python
    def _parse_one(self, feed_config: FeedConfig, xml_text: str) -> ParsedFeed | None:
        """Parse a single XML blob into a ParsedFeed.

        Returns ``None`` if the XML is malformed or has no ``<channel>`` element.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.warning(f"Failed to parse XML for feed '{feed_config.title}'")
            return None

        channel = root.find("channel")
        if channel is None:
            logger.warning(f"No <channel> element in feed '{feed_config.title}'")
            return None

        title = (channel.findtext("title") or feed_config.title).strip()

        episodes: list[Episode] = []
        for item in channel.findall("item"):
            episode = self._parse_episode(item)
            if episode is None:
                logger.debug(
                    f"Skipping item without valid enclosure in feed '{feed_config.title}'"
                )
            else:
                episodes.append(episode)

        # RSS feeds are newest-first by convention; take the first N.
        episodes = episodes[: feed_config.episodes_to_keep]

        return ParsedFeed(feed_config=feed_config, title=title, episodes=episodes)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_feed_parser.py::test_malformed_xml_skipped tests/test_feed_parser.py::test_no_channel_element_skipped tests/test_feed_parser.py::test_feed_title_falls_back_to_config_title tests/test_feed_parser.py::test_episodes_limited_to_keep -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: implement _parse_one with XML parsing, channel validation, episode limiting"
```

---

### Task 5: `parse_all` — remaining tests and full suite

**Files:**
- Modify: `tests/test_feed_parser.py`
- Modify: `components/feed_parser.py`

- [ ] **Step 1: Append failing tests to `tests/test_feed_parser.py`**

```python
# ---------------------------------------------------------------------------
# parse_all
# ---------------------------------------------------------------------------

def test_parse_all_success() -> None:
    """Valid XML produces a ParsedFeed with all fields populated correctly."""
    parser = FeedParser()
    results = parser.parse_all([(FEED_CFG, VALID_XML)])
    assert len(results) == 1
    pf = results[0]
    assert pf.title == "Test Pod"
    assert pf.feed_config is FEED_CFG
    assert len(pf.episodes) == 3
    ep = pf.episodes[0]
    assert ep.guid == "ep1"
    assert ep.title == "Episode 1"
    assert ep.url == "https://example.com/ep1.mp3"
    assert ep.pub_date == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_all_empty_input() -> None:
    """An empty downloads list returns an empty results list."""
    assert FeedParser().parse_all([]) == []


def test_parse_all_mixed_feeds() -> None:
    """One valid + one malformed feed: only the valid one appears in results."""
    bad_feed = FeedConfig(
        title="Bad Pod",
        url="https://bad.example.com/feed.rss",
        enabled=True,
        episodes_to_keep=5,
    )
    parser = FeedParser()
    results = parser.parse_all([(FEED_CFG, VALID_XML), (bad_feed, "<<<not xml>>>")])
    assert len(results) == 1
    assert results[0].feed_config is FEED_CFG
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_feed_parser.py::test_parse_all_success tests/test_feed_parser.py::test_parse_all_empty_input tests/test_feed_parser.py::test_parse_all_mixed_feeds -v
```

Expected: `AttributeError` — `parse_all` not defined yet.

- [ ] **Step 3: Implement `parse_all` inside `FeedParser`**

```python
    def parse_all(self, downloads: list[tuple[FeedConfig, str]]) -> list[ParsedFeed]:
        """Parse all downloaded XML blobs.

        Failed feeds are omitted from the result list (logged at WARNING level
        inside ``_parse_one``).

        Args:
            downloads: List of ``(feed_config, xml_text)`` tuples, as returned
                by ``FeedDownloader.download_all()``.

        Returns:
            List of successfully parsed feeds in input order.
        """
        results: list[ParsedFeed] = []
        for feed_config, xml_text in downloads:
            parsed = self._parse_one(feed_config, xml_text)
            if parsed is not None:
                results.append(parsed)
        logger.info(
            f"Feed parsing complete: {len(results)}/{len(downloads)} feed(s) parsed successfully"
        )
        return results
```

- [ ] **Step 4: Run the full test suite for feed_parser**

```bash
uv run pytest tests/test_feed_parser.py -v
```

Expected: 14 PASS (12 from spec + 2 scaffolding model tests added in Task 1).

- [ ] **Step 5: Run ruff and mypy**

```bash
uv run ruff check components/feed_parser.py tests/test_feed_parser.py
uv run mypy components/feed_parser.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: implement parse_all and complete FeedParser"
```

---

### Task 6: Pipeline integration

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `components/pipeline.py`

The existing test `test_run_returns_downloader_result` asserts that `run()` returns the raw download tuples. After this task, `run()` passes downloads through `FeedParser.parse_all()` — so that test must be updated to patch `FeedParser` too, and assert on the parser's output instead.

- [ ] **Step 1: In `tests/test_pipeline.py`, RENAME `test_run_returns_downloader_result` → `test_run_returns_parser_result` and update its body; then add `test_run_calls_feed_parser`**

Note: `test_run_returns_downloader_result` currently asserts `result == [(feed, "<rss/>")]` (the raw download tuple). That assertion will fail once the pipeline routes results through `FeedParser`. The rename makes the intent clear and the updated body mocks both `FeedDownloader` and `FeedParser`.

Replace `test_run_returns_downloader_result`:

```python
async def test_run_returns_parser_result() -> None:
    """Pipeline.run() returns what FeedParser.parse_all() returns."""
    feed = make_feed("test")
    downloads = [(feed, "<rss/>")]
    parsed = [MagicMock()]  # simulated list[ParsedFeed]
    config = make_config([feed])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=downloads)
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        pipeline = Pipeline(config)
        result = await pipeline.run()

    assert result == parsed
```

Add new test:

```python
async def test_run_calls_feed_parser() -> None:
    """Pipeline.run() passes download results to FeedParser.parse_all."""
    feed = make_feed("test")
    downloads = [(feed, "<xml/>")]
    config = make_config([feed])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=downloads)
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=[])
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_fp.parse_all.assert_called_once_with(downloads)
```

Also add to the imports at the top of `tests/test_pipeline.py` (if not present):

```python
from unittest.mock import AsyncMock, MagicMock, patch  # already present
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_pipeline.py::test_run_returns_parser_result tests/test_pipeline.py::test_run_calls_feed_parser -v
```

Expected: FAIL — `FeedParser` not imported in `pipeline.py` yet.

- [ ] **Step 3: Modify `components/pipeline.py`**

Add import after the existing `FeedDownloader` import:

```python
from components.feed_parser import FeedParser, ParsedFeed
```

In `__init__`, add after `self._feed_downloader = FeedDownloader(config)`:

```python
self._feed_parser = FeedParser()
```

Update `run()` signature and body — replace:

```python
async def run(self) -> list[tuple[FeedConfig, str]]:
```

with:

```python
async def run(self) -> list[ParsedFeed]:
```

Replace the last two lines of `run()`:

```python
        results = await self._feed_downloader.download_all(selected)
        logger.info(f"Feed download complete: {len(results)} feed(s) retrieved")
        return results
```

with:

```python
        results = await self._feed_downloader.download_all(selected)
        logger.info(f"Feed download complete: {len(results)} feed(s) retrieved")
        return self._feed_parser.parse_all(results)
```

Also remove `FeedConfig` from the `TYPE_CHECKING` import if it is no longer needed at runtime in `pipeline.py` (check: `FeedConfig` is only used in the return type annotation and feed filtering — with `from __future__ import annotations` it stays as a string at runtime, so keep it in `TYPE_CHECKING`).

- [ ] **Step 4: Run all tests**

```bash
uv run pytest -v
```

Expected: all tests pass (no regressions).

- [ ] **Step 5: Run ruff and mypy**

```bash
uv run ruff check
uv run mypy components/pipeline.py components/feed_parser.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add components/pipeline.py tests/test_pipeline.py
git commit -m "feat: integrate FeedParser into Pipeline, update return type to list[ParsedFeed]"
```
