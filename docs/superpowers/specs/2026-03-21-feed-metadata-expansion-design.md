# Feed Metadata Expansion — Design Spec

**Date:** 2026-03-21
**Status:** Approved

---

## Goal

Expand the RSS parsing layer to capture full podcast metadata at both the channel (feed) and episode level, covering both standard RSS 2.0 fields and the iTunes podcast namespace.

---

## Context

`ParsedFeed` currently carries only `config_title`, `feed_url`, `title`, and `episodes`. `Episode` carries only `guid`, `url`, `title`, and `pub_date`. Downstream stages (ad detection, audio cutting, feed publishing) will need richer metadata — cover art, description, author, duration — without going back to the raw XML.

---

## Approach

Extend the existing `Episode` and `ParsedFeed` dataclasses in-place with `None`-defaulted optional fields (and `default_factory` for `datetime` and `list` fields). All new fields have defaults so no existing constructor call breaks. The parser gains an iTunes namespace constant and two small helpers; no structural changes to `FeedParser`.

---

## Data Model Changes — `models/feed.py`

### Runtime import of `datetime`

`datetime` is currently guarded behind `TYPE_CHECKING`. The new `default_factory` lambdas (`lambda: datetime.now().astimezone()`) execute at runtime, so `datetime` **must be promoted to a real runtime import**:

```python
# Remove from TYPE_CHECKING block, make it a top-level import:
from datetime import datetime
```

### `ParsedFeed` — new fields

| Field | Type | Default | RSS source |
|---|---|---|---|
| `description` | `str \| None` | `None` | `<description>` → `<itunes:summary>` fallback |
| `link` | `str \| None` | `None` | `<link>` |
| `language` | `str \| None` | `None` | `<language>` |
| `copyright` | `str \| None` | `None` | `<copyright>` |
| `author` | `str \| None` | `None` | `<itunes:author>` → `<managingEditor>` fallback |
| `image_url` | `str \| None` | `None` | `<image><url>` → `<itunes:image href>` fallback |
| `categories` | `list[str]` | `[]` | `<itunes:category label>` (top-level and one sub-level, matching the iTunes spec) |
| `explicit` | `bool \| None` | `None` | `<itunes:explicit>`, normalised |
| `pub_date` | `datetime` | `now().astimezone()` | `<pubDate>`; falls back to current local datetime if absent or unparseable |
| `last_build_date` | `datetime` | `now().astimezone()` | `<lastBuildDate>`; falls back to current local datetime if absent or unparseable |

**Intentional asymmetry:** Feed-level `pub_date` / `last_build_date` are always `datetime` (never `None`) because a feed without a publication date is treated as "published now". Episode-level `pub_date` stays `datetime | None` because an episode without a date is genuinely unknown and callers must handle that case.

### `Episode` — new fields

| Field | Type | Default | RSS source |
|---|---|---|---|
| `description` | `str \| None` | `None` | `<description>` → `<itunes:summary>` fallback |
| `explicit` | `bool \| None` | `None` | `<itunes:explicit>`, normalised |
| `duration` | `str \| None` | `None` | `<itunes:duration>`, raw string (typed later); `None` if absent or blank after stripping |

---

## Parser Changes — `components/feed_parser.py`

### iTunes namespace constant

```python
_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
```

All iTunes element lookups use `f"{{{_ITUNES}}}tag"` with `findtext` / `find`.

### New helpers

Both helpers are **module-level functions** (not methods on `FeedParser`). Tests import them directly: `from components.feed_parser import _parse_explicit, _parse_date`.

**`_parse_explicit(text: str | None) -> bool | None`**

Normalises the various `<itunes:explicit>` spellings:
- `"yes"` / `"true"` → `True`
- `"no"` / `"false"` / `"clean"` → `False`
- Anything else (absent, blank, unrecognised) → `None`

**`_parse_date(text: str | None) -> datetime`**

Parses an RFC 2822 date string via `parsedate_to_datetime`. Returns `datetime.now().astimezone()` when `text` is `None`, empty, blank (whitespace-only), or raises `TypeError`/`ValueError`. The guard is `if not text or not text.strip()`. Used for feed-level `pub_date` and `last_build_date` only.

**Refactor:** The existing inline date-parsing logic in `_parse_episode` (for `Episode.pub_date`) is **not** unified with `_parse_date` — that logic returns `datetime | None` on failure whereas `_parse_date` returns `datetime.now()`. They differ intentionally. No changes to `_parse_episode`'s date handling.

### `_parse_one` additions (channel-level)

```
description     = (channel.findtext("description") or
                   channel.findtext(f"{{{_ITUNES}}}summary") or None)

link            = channel.findtext("link") or None
language        = channel.findtext("language") or None
copyright       = channel.findtext("copyright") or None

author          = (channel.findtext(f"{{{_ITUNES}}}author") or
                   channel.findtext("managingEditor") or None)

# image_url: standard RSS <image><url> first; iTunes fallback covers both
# "no <image>" and "<image> present but <url> child missing/empty"
image_el        = channel.find("image")
image_url       = (image_el.findtext("url") or None) if image_el is not None else None
if not image_url:
    itunes_img  = channel.find(f"{{{_ITUNES}}}image")
    image_url   = (itunes_img.get("href") or None) if itunes_img is not None else None

# categories: direct channel children only (avoid bleeding episode-level tags)
categories      = [
    el.get("label")
    for el in channel.findall(f"{{{_ITUNES}}}category")
    if el.get("label")
]
# include nested sub-categories within each top-level category element
# by iterating their children as well:
for top_cat in channel.findall(f"{{{_ITUNES}}}category"):
    for sub in top_cat.findall(f"{{{_ITUNES}}}category"):
        if sub.get("label"):
            categories.append(sub.get("label"))

explicit        = _parse_explicit(channel.findtext(f"{{{_ITUNES}}}explicit"))
pub_date        = _parse_date(channel.findtext("pubDate"))
last_build_date = _parse_date(channel.findtext("lastBuildDate"))
```

### `_parse_episode` additions

```
description = (item.findtext("description") or
               item.findtext(f"{{{_ITUNES}}}summary") or None)

explicit    = _parse_explicit(item.findtext(f"{{{_ITUNES}}}explicit"))

raw_dur     = item.findtext(f"{{{_ITUNES}}}duration")
duration    = raw_dur.strip() if raw_dur and raw_dur.strip() else None
```

---

## Fallback Rules (summary)

| Field | Primary | Fallback |
|---|---|---|
| Feed `description` | `<description>` | `<itunes:summary>` |
| Feed `author` | `<itunes:author>` | `<managingEditor>` |
| Feed `image_url` | `<image><url>` (non-empty) | `<itunes:image href>` |
| Feed `image_url` (no `<url>` child) | `<image>` present but `<url>` absent/empty | `<itunes:image href>` |
| Feed `pub_date` | `<pubDate>` (RFC 2822) | `datetime.now().astimezone()` |
| Feed `last_build_date` | `<lastBuildDate>` (RFC 2822) | `datetime.now().astimezone()` |
| Episode `description` | `<description>` | `<itunes:summary>` |

---

## Testing

### Fixture strategy

`VALID_XML` is extended with the iTunes namespace declaration and one representative value for every new channel and episode field. Tests that verify fallback behaviour or edge cases use **separate minimal XML strings** constructed inline in each test — they do not manipulate the shared `VALID_XML`. This keeps existing tests stable and fallback tests self-contained.

### Tests to add (`tests/test_feed_parser.py`)

**`_parse_explicit` helper (unit tests):**
- `"yes"` → `True`
- `"true"` → `True`
- `"no"` → `False`
- `"false"` → `False`
- `"clean"` → `False`
- `None` → `None`
- blank string → `None`
- unrecognised string → `None`

**`_parse_date` helper (unit tests):**
- Valid RFC 2822 string → correct `datetime`
- `None` → returns a `datetime` (not `None`), close to now
- `""` (empty string, as returned by `findtext` on a blank element) → returns a `datetime` (not `None`)
- Invalid / unparseable string → returns a `datetime` (not `None`)

**Feed channel fields (via `_parse_one`):**
- `description` parsed from `<description>`
- `description` falls back to `<itunes:summary>` when `<description>` absent
- `link` parsed
- `language` parsed
- `copyright` parsed
- `author` from `<itunes:author>`
- `author` falls back to `<managingEditor>` when iTunes author absent
- `image_url` from `<image><url>`
- `image_url` falls back to `<itunes:image href>` when `<image>` absent
- `image_url` falls back to `<itunes:image href>` when `<image>` present but `<url>` child empty/absent
- `categories` collected from top-level `<itunes:category>` labels
- `categories` includes nested sub-category labels
- Episode-level `<itunes:category>` tags do **not** appear in feed `categories`
- `explicit` → `True` when `"yes"`
- `explicit` → `False` when `"no"`
- `explicit` → `None` when absent
- `pub_date` parsed from `<pubDate>`
- `pub_date` is a `datetime` (not `None`) when `<pubDate>` absent
- `last_build_date` parsed from `<lastBuildDate>`
- `last_build_date` is a `datetime` (not `None`) when `<lastBuildDate>` absent

**Episode fields (via `_parse_episode`):**
- `description` parsed from `<description>`
- `description` falls back to `<itunes:summary>` when `<description>` absent
- `duration` parsed from `<itunes:duration>`
- `duration` is `None` when `<itunes:duration>` absent
- `duration` is `None` when `<itunes:duration>` contains only whitespace
- `explicit` → `True` / `False` / `None` (one test each)

**Existing tests:** all pass unchanged — new fields all have defaults.

---

## Files Modified

| File | Change |
|---|---|
| `models/feed.py` | Promote `datetime` to runtime import; add fields to `Episode` and `ParsedFeed` |
| `components/feed_parser.py` | Add `_ITUNES`, `_parse_explicit`, `_parse_date`; extend `_parse_one` and `_parse_episode` |
| `tests/test_feed_parser.py` | Extend `VALID_XML`; add ~30 new focused tests using inline XML for fallback cases |
