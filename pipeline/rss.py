import logging
from datetime import datetime, timezone

import feedparser
import httpx

from config_loader import FeedConfig
from models.episode import Episode
from pipeline.exceptions import FeedFetchError

logger = logging.getLogger(__name__)


def parse_feed(xml: str, *, feed_name: str) -> list[Episode]:
    """Parse RSS XML and return episodes with audio URLs, newest first."""
    feed = feedparser.parse(xml)
    episodes: list[Episode] = []

    for entry in feed.entries:
        audio_url = _extract_audio_url(entry)
        if audio_url is None:
            logger.debug("Skipping entry %s — no audio URL", entry.get("id", "unknown"))
            continue

        guid = entry.get("id") or entry.get("guid", "")
        title = entry.get("title", "Untitled")
        published = _parse_date(entry)

        episodes.append(
            Episode(
                guid=guid,
                feed_title=feed_name,
                title=title,
                audio_url=audio_url,
                published=published,
            )
        )

    episodes.sort(key=lambda e: e.published, reverse=True)
    logger.info("Parsed %d episodes from feed '%s'", len(episodes), feed_name)
    return episodes


def _extract_audio_url(entry: feedparser.FeedParserDict) -> str | None:
    """Extract audio URL from <enclosure> or <media:content>."""
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and "audio" in link.get("type", ""):
            return link["href"]  # type: ignore[no-any-return]

    for enclosure in entry.get("enclosures", []):
        if "audio" in enclosure.get("type", ""):
            return enclosure["href"]  # type: ignore[no-any-return]

    media = entry.get("media_content", [])
    for m in media:
        if "audio" in m.get("type", ""):
            return m["url"]  # type: ignore[no-any-return]

    return None


def _parse_date(entry: feedparser.FeedParserDict) -> datetime:
    """Parse the published date from a feed entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        from time import mktime

        return datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


async def fetch_latest_episode(
    feed_cfg: FeedConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> Episode | None:
    """Fetch the RSS feed and return the most recent episode."""
    logger.info("Fetching feed: %s", feed_cfg.name)
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)

    try:
        response = await client.get(feed_cfg.url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedFetchError(f"Failed to fetch feed '{feed_cfg.name}': {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    episodes = parse_feed(response.text, feed_name=feed_cfg.name)
    if not episodes:
        logger.warning("No episodes found in feed '%s'", feed_cfg.name)
        return None

    logger.info("Latest episode: %s", episodes[0].title)
    return episodes[0]
