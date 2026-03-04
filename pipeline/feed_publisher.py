"""RSS feed publisher: patch enclosure URLs and write static .rss files."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from config.config_loader import AppConfig, FeedConfig
from pipeline.exceptions import FeedFetchError

logger = logging.getLogger(__name__)

_NAMESPACES: dict[str, str] = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
    "atom": "http://www.w3.org/2005/Atom",
}

_DATE_PREFIX_RE: re.Pattern[str] = re.compile(r"^(\d{2}\.\d{2}\.\d{4})-")

_PUB_DATE_FORMATS: tuple[str, ...] = (
    "%a, %d %b %Y %H:%M:%S %z",
    "%Y-%m-%dT%H:%M:%S%z",
)

_GMT_SUFFIX_RE: re.Pattern[str] = re.compile(r"\s+GMT$", re.IGNORECASE)


def _parse_pub_date(date_str: str | None) -> str:
    """Parse an RSS pubDate string to DD.MM.YYYY; fall back to today on failure."""
    if date_str:
        # Normalise bare "GMT" suffix to "+0000" so all formats include %z.
        normalised = _GMT_SUFFIX_RE.sub(" +0000", date_str.strip())
        for fmt in _PUB_DATE_FORMATS:
            try:
                # All formats in _PUB_DATE_FORMATS include %z; ruff cannot verify dynamically.
                return datetime.strptime(normalised, fmt).strftime("%d.%m.%Y")  # noqa: DTZ007
            except ValueError:
                continue
    return datetime.now(tz=UTC).strftime("%d.%m.%Y")


def prune_old_episodes(podcast_dir: Path, ext: str, *, max_episodes: int) -> None:
    """Delete oldest episode files in podcast_dir beyond max_episodes limit."""
    dated: list[tuple[datetime, Path]] = []
    for path in podcast_dir.glob(f"*.{ext}"):
        m = _DATE_PREFIX_RE.match(path.name)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1), "%d.%m.%Y").replace(tzinfo=UTC)
        except ValueError:
            continue
        dated.append((dt, path))

    dated.sort(key=lambda x: x[0], reverse=True)
    for _, path in dated[max_episodes:]:
        path.unlink()
        logger.info(f"Pruned old episode: {path.name}")


def _patch_feed_xml(
    xml_str: str,
    podcast_dir: Path,
    feed_slug: str,
    base_url: str,
    ext: str,
) -> str:
    """Parse RSS XML, replace enclosure URLs where local files exist, return patched string."""
    for prefix, uri in _NAMESPACES.items():
        ET.register_namespace(prefix, uri)

    root = ET.fromstring(xml_str)  # noqa: S314
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall("item")

    for item in items:
        pub_date_el = item.find("pubDate")
        date_str = _parse_pub_date(pub_date_el.text if pub_date_el is not None else None)

        matches = list(podcast_dir.glob(f"{date_str}-*.{ext}"))
        if not matches:
            continue

        local_file = matches[0]
        local_url = f"{base_url}/{feed_slug}/{local_file.name}"

        enclosure = item.find("enclosure")
        if enclosure is not None:
            enclosure.set("url", local_url)
            enclosure.set("length", str(local_file.stat().st_size))

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


async def generate_feed_rss(
    feed_cfg: FeedConfig,
    cfg: AppConfig,
    *,
    feed_slug: str,
    podcast_dir: Path,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Fetch the original feed, patch enclosure URLs, and write a static .rss file."""
    base_url = cfg.publishing.base_url
    if not base_url:
        return

    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)

    try:
        response = await client.get(feed_cfg.url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedFetchError(
            f"Failed to fetch feed '{feed_cfg.name}' for RSS generation: {exc}"
        ) from exc
    finally:
        if should_close:
            await client.aclose()

    ext = cfg.audio.output_format.value
    patched_xml = _patch_feed_xml(response.text, podcast_dir, feed_slug, base_url, ext)

    rss_path = cfg.paths.output_dir / f"{feed_slug}.rss"
    rss_path.parent.mkdir(parents=True, exist_ok=True)
    rss_path.write_text(
        f"<?xml version='1.0' encoding='utf-8'?>\n{patched_xml}",
        encoding="utf-8",
    )
    logger.info(f"RSS feed written: {rss_path}")
