"""FeedParser — parses downloaded RSS/Atom XML into structured data."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TypedDict

from models.feed import Episode, FeedParseInput, ParsedFeed

logger = logging.getLogger(__name__)

_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_CONTENT = "http://purl.org/rss/1.0/modules/content/"


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


def _parse_categories(channel: ET.Element) -> list[str]:
    """Collect iTunes category labels from a channel element.

    Includes top-level <itunes:category> labels and one level of nested
    sub-categories, matching the iTunes podcast spec.  Only direct children of
    ``channel`` are examined so episode-level tags are never included.
    """
    cats: list[str] = []
    for top_cat in channel.findall(f"{{{_ITUNES}}}category"):
        label = top_cat.get("text")
        if label:
            cats.append(label)
        for sub in top_cat.findall(f"{{{_ITUNES}}}category"):
            sub_label = sub.get("text")
            if sub_label:
                cats.append(sub_label)
    logger.debug(f"Parsed {len(cats)} category/categories: {cats}")
    return cats


def _resolve_text(element: ET.Element, primary: str, fallback: str, fallback_note: str) -> str | None:
    """Return stripped text from ``primary`` tag, or ``fallback`` tag with a debug log.

    Returns ``None`` when both tags are absent or contain only whitespace.
    """
    value = element.findtext(primary)
    if value and value.strip():
        return value.strip()
    value = element.findtext(fallback)
    if value and value.strip():
        logger.debug(fallback_note)
        return value.strip()
    return None


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
        logger.debug("Date field absent or blank — falling back to current local datetime")
        return datetime.now().astimezone()
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        logger.debug(f"Could not parse date {text!r} — falling back to current local datetime")
        return datetime.now().astimezone()


class _ChannelExtras(TypedDict):
    """Extended iTunes/content fields parsed from the RSS <channel> element."""

    itunes_type: str | None
    itunes_subtitle: str | None
    itunes_summary: str | None
    owner_name: str | None
    owner_email: str | None
    image_title: str | None
    image_link: str | None
    content_encoded: str | None
    itunes_new_feed_url: str | None
    itunes_complete: bool


class _EpisodeExtras(TypedDict):
    """Extended iTunes/content fields parsed from an RSS <item> element."""

    episode_type: str | None
    itunes_author: str | None
    itunes_subtitle: str | None
    itunes_summary: str | None
    content_encoded: str | None
    link: str | None
    author: str | None
    itunes_title: str | None
    episode_number: int | None
    season_number: int | None
    itunes_block: bool


def _strip_or_none(raw: str | None) -> str | None:
    """Return stripped text, or None when absent or whitespace-only."""
    return raw.strip() or None if raw else None


def _parse_int_field(raw: str | None, field_name: str, episode_title: str) -> int | None:
    """Parse a text value as an integer, returning None when absent or non-numeric.

    Logs a debug message when the value is present but cannot be converted.
    """
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        logger.debug(f"Episode '{episode_title}': non-numeric <{field_name}> value {raw!r}")
        return None


def _parse_channel_extras(channel: ET.Element, image_el: ET.Element | None) -> _ChannelExtras:
    """Extract the extended iTunes/content channel fields into a typed dict.

    Separated from ``_parse_one`` to keep that method's statement count within
    linting limits.  ``image_el`` is passed in because ``_parse_one`` already
    located it for the primary image URL.
    """
    owner_el = channel.find(f"{{{_ITUNES}}}owner")

    complete_text = channel.findtext(f"{{{_ITUNES}}}complete")
    itunes_complete = bool(complete_text and complete_text.strip().lower() == "yes")

    return _ChannelExtras(
        itunes_type=_strip_or_none(channel.findtext(f"{{{_ITUNES}}}type")),
        itunes_subtitle=_strip_or_none(channel.findtext(f"{{{_ITUNES}}}subtitle")),
        # itunes:summary is INDEPENDENT of description — no fallback in either direction
        itunes_summary=_strip_or_none(channel.findtext(f"{{{_ITUNES}}}summary")),
        owner_name=_strip_or_none(
            owner_el.findtext(f"{{{_ITUNES}}}name") if owner_el is not None else None
        ),
        owner_email=_strip_or_none(
            owner_el.findtext(f"{{{_ITUNES}}}email") if owner_el is not None else None
        ),
        image_title=_strip_or_none(image_el.findtext("title") if image_el is not None else None),
        image_link=_strip_or_none(image_el.findtext("link") if image_el is not None else None),
        content_encoded=_strip_or_none(channel.findtext(f"{{{_CONTENT}}}encoded")),
        itunes_new_feed_url=_strip_or_none(channel.findtext(f"{{{_ITUNES}}}new-feed-url")),
        itunes_complete=itunes_complete,
    )


def _parse_episode_extras(item: ET.Element, title: str) -> _EpisodeExtras:
    """Extract the extended iTunes/content episode fields into a typed dict.

    Separated from ``_parse_episode`` to keep that method's statement count
    within linting limits.  ``title`` is passed in for debug log messages.
    """
    block_text = item.findtext(f"{{{_ITUNES}}}block")
    itunes_block = bool(block_text and block_text.strip().lower() == "yes")

    return _EpisodeExtras(
        episode_type=_strip_or_none(item.findtext(f"{{{_ITUNES}}}episodeType")),
        itunes_author=_strip_or_none(item.findtext(f"{{{_ITUNES}}}author")),
        itunes_subtitle=_strip_or_none(item.findtext(f"{{{_ITUNES}}}subtitle")),
        # itunes:summary is INDEPENDENT of description — no fallback in either direction
        itunes_summary=_strip_or_none(item.findtext(f"{{{_ITUNES}}}summary")),
        content_encoded=_strip_or_none(item.findtext(f"{{{_CONTENT}}}encoded")),
        link=_strip_or_none(item.findtext("link")),
        author=_strip_or_none(item.findtext("author")),
        itunes_title=_strip_or_none(item.findtext(f"{{{_ITUNES}}}title")),
        episode_number=_parse_int_field(
            item.findtext(f"{{{_ITUNES}}}episode"), "itunes:episode", title
        ),
        season_number=_parse_int_field(
            item.findtext(f"{{{_ITUNES}}}season"), "itunes:season", title
        ),
        itunes_block=itunes_block,
    )


class FeedParser:
    """Stateless RSS/Atom XML parser. No constructor args."""

    def parse_all(self, inputs: list[FeedParseInput]) -> list[ParsedFeed]:
        """Parse all downloaded XML blobs.

        Failed feeds are omitted from the result list (logged at WARNING level
        inside ``_parse_one``).

        Args:
            inputs: List of :class:`FeedParseInput` objects, each carrying the
                raw XML and the metadata needed to parse it.

        Returns:
            List of successfully parsed feeds in input order.

        """
        results: list[ParsedFeed] = []
        for feed_input in inputs:
            parsed = self._parse_one(feed_input)
            if parsed is not None:
                results.append(parsed)
        logger.info(
            f"Feed parsing complete: {len(results)}/{len(inputs)} feed(s) parsed successfully"
        )
        return results

    def _parse_one(self, feed_input: FeedParseInput) -> ParsedFeed | None:
        """Parse a single XML blob into a ParsedFeed.

        Returns ``None`` if the XML is malformed or has no ``<channel>`` element.
        """
        logger.debug(f"Parsing feed '{feed_input.config_title}'")
        try:
            root = ET.fromstring(feed_input.xml_text)  # noqa: S314 — feed XML is from configured trusted sources
        except ET.ParseError:
            logger.warning(f"Failed to parse XML for feed '{feed_input.config_title}'")
            return None

        channel = root.find("channel")
        if channel is None:
            logger.warning(f"No <channel> element in feed '{feed_input.config_title}'")
            return None

        raw_title = channel.findtext("title")
        if not raw_title:
            logger.debug(
                f"Feed '{feed_input.config_title}': no <title> element, using config title as fallback"
            )
        title = (raw_title or feed_input.config_title).strip()

        # Standard text fields — strip and convert empty string to None
        description = _resolve_text(
            channel, "description", f"{{{_ITUNES}}}summary",
            f"Feed '{feed_input.config_title}': using <itunes:summary> for description",
        )

        link_raw = channel.findtext("link")
        link = link_raw.strip() or None if link_raw else None

        language_raw = channel.findtext("language")
        language = language_raw.strip() or None if language_raw else None

        copyright_raw = channel.findtext("copyright")
        copyright_ = copyright_raw.strip() or None if copyright_raw else None

        # Author: iTunes author preferred, managingEditor as fallback
        author = _resolve_text(
            channel, f"{{{_ITUNES}}}author", "managingEditor",
            f"Feed '{feed_input.config_title}': using <managingEditor> for author",
        )

        # Cover art: <image><url> preferred; iTunes image as fallback when
        # <image> is absent OR when <image> is present but has no <url> child.
        image_el = channel.find("image")
        image_url_raw = image_el.findtext("url") if image_el is not None else None
        image_url = image_url_raw.strip() or None if image_url_raw else None
        if not image_url:
            itunes_img = channel.find(f"{{{_ITUNES}}}image")
            href = itunes_img.get("href") if itunes_img is not None else None
            image_url = href.strip() or None if href else None
            if image_url:
                logger.debug(f"Feed '{feed_input.config_title}': using <itunes:image> for cover art")

        # Extended iTunes/content channel fields (extracted to keep statement count in check)
        extras = _parse_channel_extras(channel, image_el)

        # Categories: top-level channel children + one sub-level (matches iTunes spec)
        categories = _parse_categories(channel)

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

        logger.debug(
            f"Feed '{feed_input.config_title}' parsed: title={title!r}, "
            f"episodes={len(episodes)}, author={author!r}, categories={categories}"
        )
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
            **extras,
        )

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
        if not guid_text:
            logger.debug(f"Episode '{title}': no <guid>, falling back to enclosure URL")

        pub_date = _parse_date(item.findtext("pubDate"))
        logger.debug(
            f"Episode '{title}': guid={guid!r}, pub_date={pub_date.isoformat()}"
        )

        description_raw = item.findtext("description") or item.findtext(f"{{{_ITUNES}}}summary")
        description = description_raw.strip() or None if description_raw else None

        explicit = _parse_explicit(item.findtext(f"{{{_ITUNES}}}explicit"))

        raw_dur = item.findtext(f"{{{_ITUNES}}}duration")
        duration = raw_dur.strip() if raw_dur and raw_dur.strip() else None

        itunes_img = item.find(f"{{{_ITUNES}}}image")
        image_href = itunes_img.get("href") if itunes_img is not None else None
        image_url = image_href.strip() or None if image_href else None

        # Extended iTunes/content episode fields (extracted to keep statement count in check)
        ep_extras = _parse_episode_extras(item, title)

        logger.debug(
            f"Episode '{title}': explicit={explicit!r}, duration={duration!r}, "
            f"image_url={'set' if image_url else 'absent'}"
        )
        return Episode(
            guid=guid,
            title=title,
            url=url,
            pub_date=pub_date,
            description=description,
            explicit=explicit,
            duration=duration,
            image_url=image_url,
            **ep_extras,
        )
