# feed_parser.py Design Spec

**Date:** 2026-03-21
**Status:** Approved

---

## Context

The pipeline currently ends at `FeedDownloader.download_all()`, returning raw XML blobs as `list[tuple[FeedConfig, str]]`. The next stage is to transform each XML blob into structured data that downstream stages (audio download, transcription, ad detection) can consume. `FeedParser` is that stage.

The module is also responsible for enforcing the `episodes_to_keep` limit defined per feed in the config.

---

## Data Models

Defined in `components/feed_parser.py`. Both are Pydantic `BaseModel` subclasses, consistent with the rest of the project.

```python
class Episode(BaseModel):
    guid: str           # unique identifier; falls back to enclosure URL if <guid> absent
    title: str = ""     # episode title; default "" if <title> absent — must be explicit so Pydantic does not raise
    url: str            # audio file URL from <enclosure url="...">; required
    pub_date: datetime | None  # parsed from RFC 2822 <pubDate>; None if absent or unparseable

class ParsedFeed(BaseModel):
    feed_config: FeedConfig   # original config reference (title, url, enabled, episodes_to_keep)
    title: str                # from <channel><title>; fallback: feed_config.title
    episodes: list[Episode]   # already limited to feed_config.episodes_to_keep
```

### Field notes

- `Episode.url` is the only truly required field. Any `<item>` without a valid enclosure URL is silently skipped.
- `Episode.guid` falls back to the enclosure URL so every episode has a stable identifier even for feeds that omit `<guid>`.
- `pub_date` is parsed with `email.utils.parsedate_to_datetime()` (stdlib, RFC 2822). On any exception it becomes `None` — it is optional for downstream.
- `ParsedFeed.episodes` is sliced to `feed_config.episodes_to_keep` **after** invalid items are filtered. RSS feeds are newest-first by convention, so taking the first N gives the most recent N episodes.

---

## FeedParser Class

```
components/feed_parser.py
```

Stateless — no constructor args. All inputs arrive via `parse_all()`.

```python
class FeedParser:
    def parse_all(
        self,
        downloads: list[tuple[FeedConfig, str]],
    ) -> list[ParsedFeed]:
        """Parse all downloaded XML blobs.

        Failed feeds are omitted from the result list (logged at WARNING).
        """

    def _parse_one(
        self,
        feed_config: FeedConfig,
        xml_text: str,
    ) -> ParsedFeed | None:
        """Parse a single XML blob. Returns None on any parse failure."""

    def _parse_episode(self, item: ET.Element) -> Episode | None:
        """Parse a single <item> element. Returns None if no enclosure URL."""
```

### XML library

`xml.etree.ElementTree` (stdlib). No new dependencies required.

The currently extracted fields (title, guid, pubDate, enclosure) are all in the default RSS namespace — no namespace prefix handling needed.

### Error handling

Mirrors `FeedDownloader`: failures are non-fatal. Errors are logged and problematic items are excluded from results.

| Failure | Scope | Action |
|---------|-------|--------|
| `ET.ParseError` | Entire feed | Log WARNING, skip feed |
| No `<channel>` element | Entire feed | Log WARNING, skip feed |
| No `<enclosure>` or empty `url` | Single episode | Log DEBUG, skip episode |
| Unparseable `<pubDate>` | Single field | `pub_date = None`, continue |

---

## Pipeline Integration

`components/pipeline.py` changes:

1. Import `FeedParser` and `ParsedFeed` from `components.feed_parser`.
2. `Pipeline.__init__` adds `self._feed_parser = FeedParser()`.
3. `Pipeline.run()` passes the download results to the parser and returns `list[ParsedFeed]`.
4. Return type annotation changes from `list[tuple[FeedConfig, str]]` to `list[ParsedFeed]`.

`main.py`: `await pipeline.run()` now returns `list[ParsedFeed]`. No further changes required at this stage.

---

## Files Changed

| File | Change |
|------|--------|
| `components/feed_parser.py` | **Create** — `Episode`, `ParsedFeed` models, `FeedParser` class |
| `components/pipeline.py` | **Modify** — instantiate `FeedParser`, call `parse_all`, update return type |
| `tests/test_feed_parser.py` | **Create** — 12 tests covering all cases |
| `tests/test_pipeline.py` | **Modify** — add 1 test for parser integration |

---

## Tests (`tests/test_feed_parser.py`)

All tests use `pytest-asyncio` (`asyncio_mode = "auto"` in pyproject.toml). The parser itself is synchronous, but the test module follows project conventions.

| Test | Description |
|------|-------------|
| `test_parse_all_success` | Valid RSS XML → correct `ParsedFeed` with all fields populated |
| `test_episodes_limited_to_keep` | 5 episodes in XML, `episodes_to_keep=3` → 3 episodes returned |
| `test_malformed_xml_skipped` | `ET.ParseError` → empty list, no exception raised |
| `test_no_channel_element_skipped` | XML root with no `<channel>` child → feed skipped |
| `test_episode_without_enclosure_skipped` | `<item>` with no `<enclosure>` → that episode excluded |
| `test_episode_empty_enclosure_url_skipped` | `<enclosure url="">` → that episode excluded |
| `test_pub_date_parsed_to_datetime` | Valid RFC 2822 `<pubDate>` → `datetime` object |
| `test_pub_date_missing_is_none` | No `<pubDate>` element → `pub_date` is `None` |
| `test_guid_falls_back_to_url` | No `<guid>` element → enclosure URL used as guid |
| `test_feed_title_falls_back_to_config_title` | No `<title>` in channel → `feed_config.title` used |
| `test_parse_all_empty_input` | Empty `downloads` list → empty `list[ParsedFeed]` |
| `test_parse_all_mixed_feeds` | One valid + one malformed feed → only valid feed in result |

**Pipeline test added** (`tests/test_pipeline.py`):

| Test | Description |
|------|-------------|
| `test_run_calls_feed_parser` | `Pipeline.run()` calls `FeedParser.parse_all` with download results and returns its output |

---

## Verification

```bash
uv run pytest tests/test_feed_parser.py -v   # all 12 tests pass
uv run pytest tests/test_pipeline.py -v      # existing + 1 new test pass
uv run pytest                                 # full suite green
uv run ruff check                             # no lint errors
uv run mypy components/feed_parser.py components/pipeline.py
```
