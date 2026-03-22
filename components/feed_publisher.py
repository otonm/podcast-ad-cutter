"""FeedPublisher — writes a conformant RSS 2.0 feed file to the output directory.

Slug generation and URL construction for feed assets are centralised here so that
no other module needs to import ``slugify`` directly.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from email.utils import format_datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from slugify import slugify

if TYPE_CHECKING:
    from datetime import datetime

    from models.feed import Episode, PublisherInput


logger = logging.getLogger(__name__)

_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_ATOM = "http://www.w3.org/2005/Atom"
_CONTENT = "http://purl.org/rss/1.0/modules/content/"

# Map common audio file extensions to MIME types for the enclosure element.
_MIME_MAP: dict[str, str] = {
    "mp3": "audio/mpeg",
    "m4a": "audio/x-m4a",
    "ogg": "audio/ogg",
    "opus": "audio/ogg; codecs=opus",
    "flac": "audio/flac",
}

# Register namespaces globally so ElementTree serialises them with clean prefixes.
ET.register_namespace("itunes", _ITUNES)
ET.register_namespace("atom", _ATOM)
ET.register_namespace("content", _CONTENT)


class FeedPublisher:
    """Writes an RSS 2.0 feed XML file to the output directory.

    This class is a pure writer: it receives all feed data from the pipeline
    and performs no database access.

    Args:
        output_dir: Directory where feed files are written.  The file is placed
            directly in this directory with the name ``{slug}.rss``.

    """

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def episode_filename(pub_date: datetime, title: str, ext: str) -> str:
        """Build the local filename for a processed episode audio file.

        Args:
            pub_date: Episode publication date; the date portion is formatted as
                DD.MM.YYYY in local time.
            title: Episode title; slugified to produce a URL-safe segment.
            ext: File extension without leading dot (e.g. ``"mp3"``).

        Returns:
            Filename string, e.g. ``"21.03.2026-my-episode-title.mp3"``.

        """
        date_str = pub_date.astimezone().strftime("%d.%m.%Y")
        return f"{date_str}-{slugify(title)}.{ext}"

    @staticmethod
    def episode_url(base_url: str, feed_slug: str, pub_date: datetime, title: str, ext: str) -> str:
        """Build the public URL for a processed episode audio file.

        Args:
            base_url: Server base URL (trailing slash is stripped).
            feed_slug: URL-safe feed title slug.
            pub_date: Episode publication date.
            title: Episode title; slugified to produce a URL-safe segment.
            ext: File extension without leading dot (e.g. ``"mp3"``).

        Returns:
            Full URL string, e.g.
            ``"https://podcasts.example.com/my-feed/21.03.2026-my-episode.mp3"``.

        """
        return f"{base_url.rstrip('/')}/{feed_slug}/{FeedPublisher.episode_filename(pub_date, title, ext)}"

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def publish(self, feed_input: PublisherInput) -> Path:
        """Write (or overwrite) the RSS feed file for the given feed.

        Args:
            feed_input: All metadata and episode data required to build the feed.

        Returns:
            Path to the written ``.rss`` file.

        """
        feed_slug = slugify(feed_input.title)
        output_path = self._output_dir / f"{feed_slug}.rss"
        logger.debug(
            f"Building RSS for '{feed_input.title}' "
            f"(slug={feed_slug!r}, {len(feed_input.episodes)} episode(s))"
        )
        xml_text = self._build_xml(feed_input, feed_slug)
        logger.debug(
            f"XML built: {len(xml_text)} chars, "
            f"{xml_text.count('<item>')} item element(s); writing to {output_path}"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.write_text, xml_text, encoding="utf-8")
        logger.info(
            f"Feed '{feed_input.title}' published: "
            f"{len(feed_input.episodes)} episode(s) at {output_path}"
        )
        return output_path

    async def update_episode_url(self, feed_title: str, guid: str, new_url: str) -> Path:
        """Update the enclosure URL for a single episode in an already-published feed.

        Reads the existing ``.rss`` file, patches the ``<enclosure url>`` and
        ``type`` attributes on the matching ``<item>``, and writes the file back.
        The rest of the feed is left untouched.

        Args:
            feed_title: Title of the feed (slugified internally to locate the file).
            guid: ``<guid>`` value that identifies the episode to update.
            new_url: New enclosure URL (e.g. the local processed-file URL).

        Returns:
            Path to the updated ``.rss`` file.

        Raises:
            FileNotFoundError: If the feed file does not exist yet.
            KeyError: If no ``<item>`` with the given guid is found in the feed.

        """
        feed_path = self._output_dir / f"{slugify(feed_title)}.rss"
        logger.debug(f"Patching enclosure URL for guid={guid!r} in '{feed_title}' ({feed_path})")
        xml_text = await asyncio.to_thread(feed_path.read_text, encoding="utf-8")
        root = ET.fromstring(xml_text)  # noqa: S314 — feed XML is our own output
        channel = root.find("channel")
        if channel is None:
            raise KeyError(guid)

        for item in channel.findall("item"):
            if item.findtext("guid") == guid:
                enclosure = item.find("enclosure")
                if enclosure is None:
                    logger.debug(f"No existing <enclosure> for guid={guid!r}; creating one")
                    enclosure = ET.SubElement(item, "enclosure")
                    enclosure.set("length", "0")
                enclosure.set("url", new_url)
                enclosure.set("type", self._mime_type(new_url))
                ET.indent(root, space="  ")
                updated_xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
                await asyncio.to_thread(feed_path.write_text, updated_xml, encoding="utf-8")
                logger.info(f"Episode '{guid}': enclosure URL updated to {new_url!r} in {feed_path}")
                return feed_path

        raise KeyError(guid)

    # ------------------------------------------------------------------
    # Private XML builders
    # ------------------------------------------------------------------

    def _build_xml(self, feed_input: PublisherInput, feed_slug: str) -> str:
        """Construct the full RSS XML string from the feed input."""
        logger.debug(
            f"Channel metadata: link={'set' if feed_input.link else 'absent'}, "
            f"image={'set' if feed_input.image_url else 'absent'}, "
            f"explicit={feed_input.explicit!r}, "
            f"categories={feed_input.categories or []}, "
            f"pub_date={feed_input.pub_date.isoformat()}"
        )
        rss = ET.Element("rss", {"version": "2.0"})
        channel = ET.SubElement(rss, "channel")

        self._add_text(channel, "title", feed_input.title)

        if feed_input.link:
            self._add_text(channel, "link", feed_input.link)

        # atom:link self-reference; required by the Atom namespace for feed discovery.
        feed_url = f"{feed_input.base_url.rstrip('/')}/{feed_slug}.rss"
        logger.debug(f"Atom self-link: {feed_url!r}")
        atom_link = ET.SubElement(channel, f"{{{_ATOM}}}link")
        atom_link.set("rel", "self")
        atom_link.set("href", feed_url)
        atom_link.set("type", "application/rss+xml")

        for tag, value in (
            ("description", feed_input.description),
            ("language", feed_input.language),
            ("copyright", feed_input.copyright),
            (f"{{{_ITUNES}}}author", feed_input.author),
        ):
            if value:
                self._add_text(channel, tag, value)

        # Standard RSS <image> block placed early per RSS convention; iTunes image shares the same URL.
        if feed_input.image_url:
            img_block = ET.SubElement(channel, "image")
            self._add_text(img_block, "url", feed_input.image_url)
            if feed_input.image_title:
                self._add_text(img_block, "title", feed_input.image_title)
            if feed_input.image_link:
                self._add_text(img_block, "link", feed_input.image_link)
            img = ET.SubElement(channel, f"{{{_ITUNES}}}image")
            img.set("href", feed_input.image_url)
        if feed_input.explicit is not None:
            self._add_text(channel, f"{{{_ITUNES}}}explicit", self._bool_to_xml(feed_input.explicit))

        self._add_channel_categories(channel, feed_input.categories)

        self._add_text(channel, "pubDate", format_datetime(feed_input.pub_date))
        self._add_text(channel, "lastBuildDate", format_datetime(feed_input.last_build_date))

        self._add_channel_extended_fields(channel, feed_input)

        for episode in feed_input.episodes:
            self._add_item(channel, episode)

        ET.indent(rss, space="  ")
        return ET.tostring(rss, encoding="unicode", xml_declaration=True)

    def _add_channel_extended_fields(self, channel: ET.Element, feed_input: PublisherInput) -> None:
        """Write extended iTunes and content namespace channel fields."""
        populated = [
            name
            for name, val in (
                ("itunes:type", feed_input.itunes_type),
                ("itunes:subtitle", feed_input.itunes_subtitle),
                ("itunes:summary", feed_input.itunes_summary),
                ("content:encoded", feed_input.content_encoded),
                ("itunes:new-feed-url", feed_input.itunes_new_feed_url),
            )
            if val
        ]
        logger.debug(
            f"Extended channel fields: "
            f"{', '.join(populated) if populated else 'none'}"
        )
        for tag, value in (
            (f"{{{_ITUNES}}}type", feed_input.itunes_type),
            (f"{{{_ITUNES}}}subtitle", feed_input.itunes_subtitle),
            (f"{{{_ITUNES}}}summary", feed_input.itunes_summary),
            (f"{{{_CONTENT}}}encoded", feed_input.content_encoded),
            (f"{{{_ITUNES}}}new-feed-url", feed_input.itunes_new_feed_url),
        ):
            if value:
                self._add_text(channel, tag, value)

        # <itunes:owner> block — written when at least one of name or email is present.
        logger.debug(
            f"Owner block: name={feed_input.owner_name!r}, email={feed_input.owner_email!r}"
        )
        if feed_input.owner_name or feed_input.owner_email:
            owner_el = ET.SubElement(channel, f"{{{_ITUNES}}}owner")
            if feed_input.owner_name:
                self._add_text(owner_el, f"{{{_ITUNES}}}name", feed_input.owner_name)
            if feed_input.owner_email:
                self._add_text(owner_el, f"{{{_ITUNES}}}email", feed_input.owner_email)

        if feed_input.itunes_complete:
            self._add_text(channel, f"{{{_ITUNES}}}complete", "yes")

    def _add_item(self, channel: ET.Element, episode: Episode) -> None:
        """Append an <item> element for a single episode to the channel."""
        logger.debug(
            f"Adding item: guid={episode.guid!r}, title={episode.title!r}, "
            f"pub_date={episode.pub_date.isoformat()}, "
            f"url={episode.url!r}, mime={self._mime_type(episode.url)!r}"
        )
        item = ET.SubElement(channel, "item")
        self._add_text(item, "title", episode.title)

        guid_el = ET.SubElement(item, "guid")
        guid_el.set("isPermaLink", "false")
        guid_el.text = episode.guid

        self._add_text(item, "pubDate", format_datetime(episode.pub_date))

        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", episode.url)
        enclosure.set("type", self._mime_type(episode.url))
        enclosure.set("length", "0")

        if episode.description:
            self._add_text(item, "description", episode.description)
        if episode.explicit is not None:
            self._add_text(item, f"{{{_ITUNES}}}explicit", self._bool_to_xml(episode.explicit))
        if episode.duration:
            self._add_text(item, f"{{{_ITUNES}}}duration", episode.duration)
        if episode.image_url:
            ep_img = ET.SubElement(item, f"{{{_ITUNES}}}image")
            ep_img.set("href", episode.image_url)

        self._add_item_extended_fields(item, episode)

    def _add_item_extended_fields(self, item: ET.Element, episode: Episode) -> None:
        """Write extended iTunes and content namespace episode fields."""
        populated = [
            name
            for name, val in (
                ("link", episode.link),
                ("author", episode.author),
                ("itunes:title", episode.itunes_title),
                ("itunes:episodeType", episode.episode_type),
                ("itunes:author", episode.itunes_author),
                ("itunes:subtitle", episode.itunes_subtitle),
                ("itunes:summary", episode.itunes_summary),
                ("content:encoded", episode.content_encoded),
            )
            if val
        ]
        logger.debug(
            f"Episode {episode.guid!r} extended fields: "
            f"{', '.join(populated) if populated else 'none'}; "
            f"episode_number={episode.episode_number!r}, "
            f"season_number={episode.season_number!r}, "
            f"block={episode.itunes_block}"
        )
        for tag, value in (
            ("link", episode.link),
            ("author", episode.author),
            (f"{{{_ITUNES}}}title", episode.itunes_title),
            (f"{{{_ITUNES}}}episodeType", episode.episode_type),
            (f"{{{_ITUNES}}}author", episode.itunes_author),
            (f"{{{_ITUNES}}}subtitle", episode.itunes_subtitle),
            (f"{{{_ITUNES}}}summary", episode.itunes_summary),
            (f"{{{_CONTENT}}}encoded", episode.content_encoded),
        ):
            if value:
                self._add_text(item, tag, value)

        # 0 is a valid episode/season number, so guard against None rather than falsiness.
        for tag, value in (
            (f"{{{_ITUNES}}}episode", episode.episode_number),
            (f"{{{_ITUNES}}}season", episode.season_number),
        ):
            if value is not None:
                self._add_text(item, tag, str(value))
        if episode.itunes_block:
            self._add_text(item, f"{{{_ITUNES}}}block", "yes")

    # ------------------------------------------------------------------
    # Private helpers (stateless)
    # ------------------------------------------------------------------

    @staticmethod
    def _add_channel_categories(channel: ET.Element, categories: list[str]) -> None:
        """Write itunes:category elements to the channel using the correct hierarchy.

        The first category becomes the parent element; all subsequent entries are
        nested as child ``<itunes:category>`` elements inside it.  Nothing is written
        when the list is empty.

        Args:
            channel: The ``<channel>`` element to append to.
            categories: Ordered list of category strings from ``PublisherInput``.

        """
        if not categories:
            logger.debug("No itunes:category elements to write")
            return
        logger.debug(f"Writing itunes:category hierarchy: {categories}")
        parent_cat = ET.SubElement(channel, f"{{{_ITUNES}}}category")
        parent_cat.set("text", categories[0])
        for subcategory in categories[1:]:
            child_cat = ET.SubElement(parent_cat, f"{{{_ITUNES}}}category")
            child_cat.set("text", subcategory)

    @staticmethod
    def _add_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
        """Append a child element with the given text and return it."""
        el = ET.SubElement(parent, tag)
        el.text = text
        return el

    @staticmethod
    def _bool_to_xml(value: bool) -> str:  # noqa: FBT001 — single-purpose bool→str helper
        """Convert a Python bool to the iTunes XML convention: ``"yes"`` or ``"no"``."""
        return "yes" if value else "no"

    @staticmethod
    def _mime_type(url: str) -> str:
        """Return the MIME type for the given URL based on its file extension.

        Query strings (e.g. ``?updated=123``) are stripped before the extension
        is extracted so that tracker-redirect URLs resolve correctly.
        """
        path = urlparse(url).path
        ext = Path(path).suffix.lstrip(".").lower()
        mime = _MIME_MAP.get(ext)
        if mime is None:
            logger.debug(f"Unrecognised extension {ext!r} in {url!r}; defaulting MIME type to 'audio/mpeg'")
            return "audio/mpeg"
        return mime
