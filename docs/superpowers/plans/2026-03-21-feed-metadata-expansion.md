# Feed Metadata Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `Episode` and `ParsedFeed` with full RSS 2.0 + iTunes podcast metadata and update `FeedParser` to extract it.

**Architecture:** Plain dataclass fields with `None` defaults are added to the existing models; the parser gains two module-level helper functions (`_parse_explicit`, `_parse_date`) and an iTunes namespace constant, then populates the new fields in `_parse_one` and `_parse_episode`. No structural changes to `FeedParser`.

**Tech Stack:** Python 3.12, `xml.etree.ElementTree`, `email.utils.parsedate_to_datetime`, `dataclasses`.

**Spec:** `docs/superpowers/specs/2026-03-21-feed-metadata-expansion-design.md`

---

## File Map

| Action | Path | Change |
|---|---|---|
| Modify | `models/feed.py` | Promote `datetime` to runtime import; add 3 fields to `Episode`, 10 fields to `ParsedFeed` |
| Modify | `components/feed_parser.py` | Add `_ITUNES` constant, `_parse_explicit`, `_parse_date`; extend `_parse_one` and `_parse_episode` |
| Modify | `tests/test_feed_parser.py` | Extend `VALID_XML`; add ~30 new focused tests |

---

## Task 1: Extend data models

**Files:**
- Modify: `models/feed.py`
- Test: `tests/test_feed_parser.py`

- [ ] **Step 1: Write failing tests for new model fields**

Add to `tests/test_feed_parser.py` (after the existing model tests):

```python
def test_episode_new_fields_default_to_none() -> None:
    ep = Episode(guid="g1", url="https://example.com/ep.mp3")
    assert ep.description is None
    assert ep.explicit is None
    assert ep.duration is None


def test_parsed_feed_new_optional_fields_default_to_none() -> None:
    pf = ParsedFeed(config_title="Pod", feed_url="https://example.com/feed.rss", title="Pod")
    assert pf.description is None
    assert pf.link is None
    assert pf.language is None
    assert pf.copyright is None
    assert pf.author is None
    assert pf.image_url is None
    assert pf.categories == []
    assert pf.explicit is None


def test_parsed_feed_pub_dates_default_to_datetime() -> None:
    pf = ParsedFeed(config_title="Pod", feed_url="https://example.com/feed.rss", title="Pod")
    assert isinstance(pf.pub_date, datetime)
    assert isinstance(pf.last_build_date, datetime)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_feed_parser.py::test_episode_new_fields_default_to_none tests/test_feed_parser.py::test_parsed_feed_new_optional_fields_default_to_none tests/test_feed_parser.py::test_parsed_feed_pub_dates_default_to_datetime -v
```

Expected: FAIL — `Episode` and `ParsedFeed` do not have these fields yet.

- [ ] **Step 3: Update `models/feed.py`**

Replace the entire file with:

```python
"""Domain transfer objects for the feed pipeline.

These are plain dataclasses — no config module dependency.  Pipeline is the
sole owner of Config and is responsible for extracting the fields each
component needs and passing them here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Episode:
    """A single podcast episode extracted from an RSS feed."""

    guid: str
    url: str
    title: str = ""
    pub_date: datetime | None = None
    description: str | None = None
    explicit: bool | None = None
    # Raw string (e.g. "01:23:45"); will be typed to a duration type in a future task.
    duration: str | None = None


@dataclass
class FeedParseInput:
    """Input contract for FeedParser.parse_all() — plain data, no config types."""

    config_title: str  # configured feed title: used as fallback and identifier
    feed_url: str  # original feed URL, threaded into ParsedFeed for downstream stages
    episodes_to_keep: int
    xml_text: str  # raw RSS XML (named xml_text to avoid shadowing the stdlib xml module)


@dataclass
class ParsedFeed:
    """Result of parsing one RSS feed."""

    config_title: str  # matches FeedParseInput.config_title
    feed_url: str  # original feed URL (preserved so downstream stages don't need config)
    title: str  # parsed from RSS <channel><title>; may differ from config_title
    episodes: list[Episode] = field(default_factory=list)
    description: str | None = None
    link: str | None = None
    language: str | None = None
    copyright: str | None = None
    author: str | None = None
    image_url: str | None = None
    categories: list[str] = field(default_factory=list)
    explicit: bool | None = None
    # Feed-level dates always resolve to a concrete datetime (absent → current local time).
    # This differs from Episode.pub_date which stays None when the date is unknown.
    pub_date: datetime = field(default_factory=lambda: datetime.now().astimezone())
    last_build_date: datetime = field(default_factory=lambda: datetime.now().astimezone())
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_feed_parser.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```bash
uv run ruff check models/feed.py tests/test_feed_parser.py
```

Expected: no errors.

- [ ] **Step 6: Full suite**

```bash
uv run pytest
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add models/feed.py tests/test_feed_parser.py
git commit -m "feat: add metadata fields to Episode and ParsedFeed models"
```

---

## Task 2: Add `_parse_explicit` helper

**Files:**
- Modify: `components/feed_parser.py`
- Modify: `tests/test_feed_parser.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_feed_parser.py` (add import at top: `from components.feed_parser import _parse_explicit`):

```python
# ---------------------------------------------------------------------------
# _parse_explicit
# ---------------------------------------------------------------------------


def test_parse_explicit_yes() -> None:
    assert _parse_explicit("yes") is True


def test_parse_explicit_true() -> None:
    assert _parse_explicit("true") is True


def test_parse_explicit_no() -> None:
    assert _parse_explicit("no") is False


def test_parse_explicit_false() -> None:
    assert _parse_explicit("false") is False


def test_parse_explicit_clean() -> None:
    assert _parse_explicit("clean") is False


def test_parse_explicit_none() -> None:
    assert _parse_explicit(None) is None


def test_parse_explicit_blank() -> None:
    assert _parse_explicit("   ") is None


def test_parse_explicit_unknown_value() -> None:
    assert _parse_explicit("maybe") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_feed_parser.py -k "parse_explicit" -v
```

Expected: ImportError — `_parse_explicit` does not exist yet.

- [ ] **Step 3: Add `_parse_explicit` to `feed_parser.py`**

Add the `_ITUNES` constant and `_parse_explicit` function at module level, before the `FeedParser` class:

```python
_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _parse_explicit(text: str | None) -> bool | None:
    """Normalise <itunes:explicit> text to bool or None.

    Returns True for "yes"/"true", False for "no"/"false"/"clean",
    and None for absent, blank, or any unrecognised value.
    """
    if not text or not text.strip():
        return None
    normalized = text.strip().lower()
    if normalized in ("yes", "true"):
        return True
    if normalized in ("no", "false", "clean"):
        return False
    return None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_feed_parser.py -k "parse_explicit" -v
```

Expected: all 8 pass.

- [ ] **Step 5: Lint**

```bash
uv run ruff check components/feed_parser.py tests/test_feed_parser.py
```

- [ ] **Step 6: Full suite**

```bash
uv run pytest
```

- [ ] **Step 7: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: add _parse_explicit helper for itunes:explicit normalisation"
```

---

## Task 3: Add `_parse_date` helper

**Files:**
- Modify: `components/feed_parser.py`
- Modify: `tests/test_feed_parser.py`

- [ ] **Step 1: Write failing tests**

Add import at top of test file: `from components.feed_parser import _parse_date`

Add tests:

```python
# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


def test_parse_date_valid_rfc2822() -> None:
    result = _parse_date("Mon, 01 Jan 2024 12:00:00 +0000")
    assert result == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_parse_date_none_returns_datetime() -> None:
    result = _parse_date(None)
    assert isinstance(result, datetime)


def test_parse_date_empty_string_returns_datetime() -> None:
    # xml.etree.ElementTree.findtext() returns "" for <pubDate></pubDate>
    result = _parse_date("")
    assert isinstance(result, datetime)


def test_parse_date_invalid_string_returns_datetime() -> None:
    result = _parse_date("not a date at all")
    assert isinstance(result, datetime)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_feed_parser.py -k "parse_date" -v
```

Expected: ImportError — `_parse_date` does not exist yet.

- [ ] **Step 3: Add `_parse_date` to `feed_parser.py`**

Add after `_parse_explicit`, before the `FeedParser` class:

```python
def _parse_date(text: str | None) -> datetime:
    """Parse an RFC 2822 date string, falling back to the current local datetime.

    Used for feed-level dates only. Episode dates use a separate path that
    returns None on failure (see _parse_episode).

    Args:
        text: Raw date string from XML, or None if the element was absent.

    Returns:
        Parsed datetime, or datetime.now().astimezone() if text is absent,
        empty, blank, or unparseable.

    """
    if not text or not text.strip():
        return datetime.now().astimezone()
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return datetime.now().astimezone()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_feed_parser.py -k "parse_date" -v
```

Expected: all 4 pass.

- [ ] **Step 5: Lint and full suite**

```bash
uv run ruff check components/feed_parser.py tests/test_feed_parser.py && uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: add _parse_date helper for feed-level RFC 2822 date parsing"
```

---

## Task 4: Channel metadata in `_parse_one`

**Files:**
- Modify: `components/feed_parser.py`
- Modify: `tests/test_feed_parser.py`

- [ ] **Step 1: Update `VALID_XML` and write failing tests**

Replace `VALID_XML` in `tests/test_feed_parser.py` with the iTunes-extended version, then add all channel-field tests.

**New `VALID_XML`:**

```python
VALID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Pod</title>
    <description>A great podcast about testing.</description>
    <link>https://testpod.example.com</link>
    <language>en</language>
    <copyright>&#169; 2024 Test Pod</copyright>
    <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    <lastBuildDate>Tue, 02 Jan 2024 12:00:00 +0000</lastBuildDate>
    <image>
      <url>https://example.com/cover.jpg</url>
      <title>Test Pod</title>
      <link>https://testpod.example.com</link>
    </image>
    <itunes:author>Test Author</itunes:author>
    <itunes:image href="https://example.com/itunes-cover.jpg"/>
    <itunes:explicit>yes</itunes:explicit>
    <itunes:category label="Technology">
      <itunes:category label="Tech News"/>
    </itunes:category>
    <item>
      <guid>ep1</guid>
      <title>Episode 1</title>
      <description>Episode 1 description.</description>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg" length="1000"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <itunes:duration>01:23:45</itunes:duration>
      <itunes:explicit>no</itunes:explicit>
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
```

**New tests** (add after existing `_parse_one` tests):

```python
# ---------------------------------------------------------------------------
# _parse_one — channel metadata
# ---------------------------------------------------------------------------


def test_feed_description_from_description_element() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.description == "A great podcast about testing."


def test_feed_description_falls_back_to_itunes_summary() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        "<itunes:summary>iTunes summary fallback.</itunes:summary>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.description == "iTunes summary fallback."


def test_feed_link() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.link == "https://testpod.example.com"


def test_feed_language() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.language == "en"


def test_feed_copyright() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.copyright == "\u00a9 2024 Test Pod"


def test_feed_author_from_itunes_author() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.author == "Test Author"


def test_feed_author_falls_back_to_managing_editor() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        "<managingEditor>editor@example.com</managingEditor>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.author == "editor@example.com"


def test_feed_image_url_from_image_element() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.image_url == "https://example.com/cover.jpg"


def test_feed_image_url_falls_back_to_itunes_image_when_no_image_element() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        '<itunes:image href="https://example.com/itunes.jpg"/>'
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.image_url == "https://example.com/itunes.jpg"


def test_feed_image_url_falls_back_to_itunes_image_when_url_child_missing() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        "<image><title>No url child here</title></image>"
        '<itunes:image href="https://example.com/itunes.jpg"/>'
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.image_url == "https://example.com/itunes.jpg"


def test_feed_categories_top_level() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert "Technology" in result.categories


def test_feed_categories_includes_subcategories() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert "Tech News" in result.categories


def test_feed_categories_exclude_episode_level_tags() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        '<itunes:category label="FeedCat"/>'
        "<item>"
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        '<itunes:category label="EpisodeCat"/>'
        "</item>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert "FeedCat" in result.categories
    assert "EpisodeCat" not in result.categories


def test_feed_explicit_true() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.explicit is True


def test_feed_explicit_false() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title><itunes:explicit>no</itunes:explicit></channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.explicit is False


def test_feed_explicit_none_when_absent() -> None:
    parser = FeedParser()
    xml = "<rss version='2.0'><channel><title>Pod</title></channel></rss>"
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.explicit is None


def test_feed_pub_date_parsed() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.pub_date == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_feed_pub_date_is_datetime_when_absent() -> None:
    parser = FeedParser()
    xml = "<rss version='2.0'><channel><title>Pod</title></channel></rss>"
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert isinstance(result.pub_date, datetime)


def test_feed_last_build_date_parsed() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.last_build_date == datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_feed_last_build_date_is_datetime_when_absent() -> None:
    parser = FeedParser()
    xml = "<rss version='2.0'><channel><title>Pod</title></channel></rss>"
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert isinstance(result.last_build_date, datetime)
```

- [ ] **Step 2: Run tests to confirm the new ones fail**

```bash
uv run pytest tests/test_feed_parser.py -k "feed_description or feed_link or feed_language or feed_copyright or feed_author or feed_image or feed_categories or feed_explicit or feed_pub_date or feed_last_build" -v
```

Expected: all new tests FAIL (fields are `None`); existing tests PASS.

- [ ] **Step 3: Implement channel metadata in `_parse_one`**

Replace the body of `_parse_one` in `components/feed_parser.py` with:

```python
def _parse_one(self, feed_input: FeedParseInput) -> ParsedFeed | None:
    """Parse a single XML blob into a ParsedFeed.

    Returns ``None`` if the XML is malformed or has no ``<channel>`` element.
    """
    try:
        root = ET.fromstring(feed_input.xml_text)  # noqa: S314 — feed XML is from configured trusted sources
    except ET.ParseError:
        logger.warning(f"Failed to parse XML for feed '{feed_input.config_title}'")
        return None

    channel = root.find("channel")
    if channel is None:
        logger.warning(f"No <channel> element in feed '{feed_input.config_title}'")
        return None

    title = (channel.findtext("title") or feed_input.config_title).strip()

    # Standard text fields — strip and convert empty string to None
    description_raw = channel.findtext("description") or channel.findtext(f"{{{_ITUNES}}}summary")
    description = description_raw.strip() or None if description_raw else None

    link_raw = channel.findtext("link")
    link = link_raw.strip() or None if link_raw else None

    language_raw = channel.findtext("language")
    language = language_raw.strip() or None if language_raw else None

    copyright_raw = channel.findtext("copyright")
    copyright_ = copyright_raw.strip() or None if copyright_raw else None

    # Author: iTunes author preferred, managingEditor as fallback
    author_raw = channel.findtext(f"{{{_ITUNES}}}author") or channel.findtext("managingEditor")
    author = author_raw.strip() or None if author_raw else None

    # Cover art: <image><url> preferred; iTunes image as fallback when
    # <image> is absent OR when <image> is present but has no <url> child.
    image_el = channel.find("image")
    image_url_raw = image_el.findtext("url") if image_el is not None else None
    image_url = image_url_raw.strip() or None if image_url_raw else None
    if not image_url:
        itunes_img = channel.find(f"{{{_ITUNES}}}image")
        href = itunes_img.get("href") if itunes_img is not None else None
        image_url = href.strip() or None if href else None

    # Categories: top-level channel children + one sub-level (matches iTunes spec)
    categories: list[str] = []
    for top_cat in channel.findall(f"{{{_ITUNES}}}category"):
        label = top_cat.get("label")
        if label:
            categories.append(label)
        for sub in top_cat.findall(f"{{{_ITUNES}}}category"):
            sub_label = sub.get("label")
            if sub_label:
                categories.append(sub_label)

    explicit = _parse_explicit(channel.findtext(f"{{{_ITUNES}}}explicit"))
    pub_date = _parse_date(channel.findtext("pubDate"))
    last_build_date = _parse_date(channel.findtext("lastBuildDate"))

    # RSS feeds are newest-first by convention; stop as soon as we have N valid episodes.
    episodes: list[Episode] = []
    for item in channel.findall("item"):
        if len(episodes) >= feed_input.episodes_to_keep:
            break
        episode = self._parse_episode(item)
        if episode is None:
            logger.debug(
                f"Skipping item without valid enclosure in feed '{feed_input.config_title}'"
            )
        else:
            episodes.append(episode)

    return ParsedFeed(
        config_title=feed_input.config_title,
        feed_url=feed_input.feed_url,
        title=title,
        episodes=episodes,
        description=description,
        link=link,
        language=language,
        copyright=copyright_,
        author=author,
        image_url=image_url,
        categories=categories,
        explicit=explicit,
        pub_date=pub_date,
        last_build_date=last_build_date,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_feed_parser.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```bash
uv run ruff check components/feed_parser.py tests/test_feed_parser.py
```

- [ ] **Step 6: Full suite**

```bash
uv run pytest
```

- [ ] **Step 7: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: parse full channel metadata in FeedParser._parse_one"
```

---

## Task 5: Episode metadata in `_parse_episode`

**Files:**
- Modify: `components/feed_parser.py`
- Modify: `tests/test_feed_parser.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_feed_parser.py` (after the existing `_parse_episode` tests):

```python
# ---------------------------------------------------------------------------
# _parse_episode — new metadata fields
# ---------------------------------------------------------------------------


def test_episode_description_from_description_element() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<description>Episode description.</description>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.description == "Episode description."


def test_episode_description_falls_back_to_itunes_summary() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:summary>iTunes episode summary.</itunes:summary>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.description == "iTunes episode summary."


def test_episode_duration_parsed() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:duration>01:23:45</itunes:duration>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.duration == "01:23:45"


def test_episode_duration_none_when_absent() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.duration is None


def test_episode_duration_none_when_whitespace_only() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:duration>   </itunes:duration>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.duration is None


def test_episode_explicit_true() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:explicit>yes</itunes:explicit>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.explicit is True


def test_episode_explicit_false() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:explicit>no</itunes:explicit>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.explicit is False


def test_episode_explicit_none_when_absent() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.explicit is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_feed_parser.py -k "episode_description or episode_duration or episode_explicit" -v
```

Expected: all 8 FAIL — fields are `None` or not extracted yet.

- [ ] **Step 3: Extend `_parse_episode` in `feed_parser.py`**

Replace the body of `_parse_episode` with:

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
        except (TypeError, ValueError):
            # TypeError: parsedate_tz returned None (unrecognised format)
            # ValueError: date components out of range
            pub_date = datetime.now().astimezone()
            logger.debug(
                f"Could not parse pubDate {pub_date_str!r} — falling back to current local datetime"
            )

    description_raw = item.findtext("description") or item.findtext(f"{{{_ITUNES}}}summary")
    description = description_raw.strip() or None if description_raw else None

    explicit = _parse_explicit(item.findtext(f"{{{_ITUNES}}}explicit"))

    raw_dur = item.findtext(f"{{{_ITUNES}}}duration")
    duration = raw_dur.strip() if raw_dur and raw_dur.strip() else None

    return Episode(
        guid=guid,
        title=title,
        url=url,
        pub_date=pub_date,
        description=description,
        explicit=explicit,
        duration=duration,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_feed_parser.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```bash
uv run ruff check components/feed_parser.py tests/test_feed_parser.py
```

- [ ] **Step 6: Full suite**

```bash
uv run pytest
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add components/feed_parser.py tests/test_feed_parser.py
git commit -m "feat: parse episode metadata (description, explicit, duration) in FeedParser"
```

---

## Verification

```bash
uv run pytest -v       # all tests green
uv run ruff check .    # no lint errors
```

Spot-check with a real feed (requires a valid `config.yaml`):

```bash
uv run python -c "
import asyncio
from components.feed_downloader import FeedDownloader
from components.feed_parser import FeedParser
from models.feed import FeedParseInput

async def main():
    dl = FeedDownloader()
    results = await dl.download_all([('Test', 'https://feeds.simplecast.com/54nAGcIl')])
    if results:
        fi = FeedParseInput('Test', results[0][1], 3, results[0][1])
        pf = FeedParser()._parse_one(fi)
        if pf:
            print('author:', pf.author)
            print('categories:', pf.categories)
            print('explicit:', pf.explicit)
            print('ep0 duration:', pf.episodes[0].duration if pf.episodes else None)

asyncio.run(main())
"
```
