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

from slugify import slugify

if TYPE_CHECKING:
    from datetime import datetime

    from models.feed import Episode, PublisherInput


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
    return f"{base_url.rstrip('/')}/{feed_slug}/{episode_filename(pub_date, title, ext)}"

logger = logging.getLogger(__name__)

_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_ATOM = "http://www.w3.org/2005/Atom"

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

    async def publish(self, feed_input: PublisherInput) -> Path:
        """Write (or overwrite) the RSS feed file for the given feed.

        Args:
            feed_input: All metadata and episode data required to build the feed.

        Returns:
            Path to the written ``.rss`` file.

        """
        feed_slug = slugify(feed_input.title)
        output_path = self._output_dir / f"{feed_slug}.rss"
        xml_text = self._build_xml(feed_input, feed_slug)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.write_text, xml_text, encoding="utf-8")
        logger.info(f"Feed '{feed_input.title}' written to {output_path}")
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
        if not feed_path.exists():
            raise FileNotFoundError(f"Feed file not found: {feed_path}")

        xml_text = await asyncio.to_thread(feed_path.read_text, encoding="utf-8")
        root = ET.fromstring(xml_text)  # noqa: S314 — feed XML is our own output
        channel = root.find("channel")
        if channel is None:
            raise KeyError(guid)

        for item in channel.findall("item"):
            if item.findtext("guid") == guid:
                enclosure = item.find("enclosure")
                if enclosure is None:
                    enclosure = ET.SubElement(item, "enclosure")
                    enclosure.set("length", "0")
                enclosure.set("url", new_url)
                enclosure.set("type", _mime_type(new_url))
                ET.indent(root, space="  ")
                updated_xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
                await asyncio.to_thread(feed_path.write_text, updated_xml, encoding="utf-8")
                logger.info(f"Episode '{guid}': enclosure URL updated to {new_url!r} in {feed_path}")
                return feed_path

        raise KeyError(guid)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_xml(self, feed_input: PublisherInput, feed_slug: str) -> str:
        """Construct the full RSS XML string from the feed input."""
        rss = ET.Element("rss", {"version": "2.0"})
        channel = ET.SubElement(rss, "channel")

        _add_text(channel, "title", feed_input.title)

        # Original podcast website link (kept verbatim)
        if feed_input.link:
            _add_text(channel, "link", feed_input.link)

        # Atom self-link points to our published feed URL
        feed_url = f"{feed_input.base_url.rstrip('/')}/{feed_slug}.rss"
        atom_link = ET.SubElement(channel, f"{{{_ATOM}}}link")
        atom_link.set("rel", "self")
        atom_link.set("href", feed_url)
        atom_link.set("type", "application/rss+xml")

        if feed_input.description:
            _add_text(channel, "description", feed_input.description)
        if feed_input.language:
            _add_text(channel, "language", feed_input.language)
        if feed_input.copyright:
            _add_text(channel, "copyright", feed_input.copyright)
        if feed_input.author:
            _add_text(channel, f"{{{_ITUNES}}}author", feed_input.author)
        if feed_input.image_url:
            img = ET.SubElement(channel, f"{{{_ITUNES}}}image")
            img.set("href", feed_input.image_url)
        if feed_input.explicit is not None:
            _add_text(channel, f"{{{_ITUNES}}}explicit", "yes" if feed_input.explicit else "no")
        for category in feed_input.categories:
            cat_el = ET.SubElement(channel, f"{{{_ITUNES}}}category")
            cat_el.set("label", category)

        _add_text(channel, "pubDate", format_datetime(feed_input.pub_date))
        _add_text(channel, "lastBuildDate", format_datetime(feed_input.last_build_date))

        for episode in feed_input.episodes:
            self._add_item(channel, episode)

        ET.indent(rss, space="  ")
        return ET.tostring(rss, encoding="unicode", xml_declaration=True)

    def _add_item(self, channel: ET.Element, episode: Episode) -> None:
        """Append an <item> element for a single episode to the channel."""
        item = ET.SubElement(channel, "item")
        _add_text(item, "title", episode.title)

        guid_el = ET.SubElement(item, "guid")
        guid_el.set("isPermaLink", "false")
        guid_el.text = episode.guid

        _add_text(item, "pubDate", format_datetime(episode.pub_date))

        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", episode.url)
        enclosure.set("type", _mime_type(episode.url))
        enclosure.set("length", "0")

        if episode.description:
            _add_text(item, "description", episode.description)
        if episode.explicit is not None:
            _add_text(item, f"{{{_ITUNES}}}explicit", "yes" if episode.explicit else "no")
        if episode.duration:
            _add_text(item, f"{{{_ITUNES}}}duration", episode.duration)
        if episode.image_url:
            ep_img = ET.SubElement(item, f"{{{_ITUNES}}}image")
            ep_img.set("href", episode.image_url)


def _add_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    """Append a child element with the given text and return it."""
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def _mime_type(url: str) -> str:
    """Return the MIME type for the given URL based on its file extension."""
    ext = Path(url).suffix.lstrip(".").lower()
    return _MIME_MAP.get(ext, "audio/mpeg")
