"""Pipeline — top-level orchestrator for the podcast ad-cutting workflow."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import TYPE_CHECKING

from components.audio_prober import AudioProber
from components.episode_downloader import EpisodeDownloader
from components.feed_downloader import FeedDownloader
from components.feed_parser import FeedParser
from components.feed_publisher import FeedPublisher
from database.audio_metadata_store import AudioMetadataStore
from database.connection import Database
from database.episode_store import EpisodeStore
from models.feed import FeedParseInput, ParsedFeed, PublisherInput

if TYPE_CHECKING:
    from pathlib import Path

    from config.config_loader import Config, FeedConfig

logger = logging.getLogger(__name__)


class Pipeline:
    """Coordinates each stage of the podcast ad-cutting workflow.

    Pipeline is the sole owner of :class:`Config`.  It extracts the plain
    data each component needs and passes it through their APIs — no component
    below Pipeline imports from the config module.

    Currently the pipeline performs two stages: downloading the RSS/Atom XML
    for the selected feeds, then parsing the XML into structured data.
    Further stages (transcription, ad detection, audio cutting) will be
    added here as new components.

    Args:
        config: Validated application config.
        feed_name: When set, process only the feed whose title matches this
            string exactly, regardless of its ``enabled`` flag.  When
            ``None`` (default), only feeds marked ``enabled: true`` are
            processed.

    """

    def __init__(self, config: Config, feed_name: str | None = None) -> None:
        self._config = config
        self._feed_name = feed_name
        self._db_path: Path = config.app.paths.data_dir / "data.db"
        self._feed_downloader = FeedDownloader()
        self._feed_parser = FeedParser()
        self._feed_publisher = FeedPublisher(config.app.paths.output_dir)
        self._episode_downloader = EpisodeDownloader(config.app.paths.cache_dir)
        self._audio_prober = AudioProber()

    async def run(self) -> list[ParsedFeed]:
        """Execute the pipeline for the selected feeds.

        Returns:
            List of parsed feeds for every feed that was downloaded and
            parsed successfully, in config order.

        Raises:
            ValueError: If ``feed_name`` was supplied but no feed with that
                exact title exists in the config.

        """
        selected = self._select_feeds()
        download_results = await self._download(selected)
        parse_inputs = self._build_parse_inputs(selected, download_results)
        parsed_feeds = self._feed_parser.parse_all(parse_inputs)

        feed_cfg_map = {f.title: f for f in selected}

        async with Database(self._db_path) as db:
            store = EpisodeStore(db.conn)
            for feed in parsed_feeds:
                await store.save_episodes(feed.config_title, feed.episodes)

            for feed in parsed_feeds:
                cfg = feed_cfg_map[feed.config_title]
                episodes = await store.get_episodes_for_feed(
                    feed.config_title, cfg.episodes_to_keep
                )
                logger.debug(
                    f"Building publisher input for '{feed.config_title}': "
                    f"{len(episodes)} episode(s), image_url={'set' if feed.image_url else 'absent'}, "
                    f"categories={feed.categories}"
                )
                publisher_input = PublisherInput(
                    base_url=self._config.app.base_url,
                    title=feed.title,
                    episodes=episodes,
                    description=feed.description,
                    link=feed.link,
                    language=feed.language,
                    copyright=feed.copyright,
                    author=feed.author,
                    image_url=feed.image_url,
                    categories=feed.categories,
                    explicit=feed.explicit,
                    pub_date=feed.pub_date,
                    last_build_date=datetime.now().astimezone(),
                    # Extended channel metadata — passed through verbatim from ParsedFeed.
                    itunes_type=feed.itunes_type,
                    itunes_subtitle=feed.itunes_subtitle,
                    itunes_summary=feed.itunes_summary,
                    owner_name=feed.owner_name,
                    owner_email=feed.owner_email,
                    image_title=feed.image_title,
                    image_link=feed.image_link,
                    content_encoded=feed.content_encoded,
                    itunes_new_feed_url=feed.itunes_new_feed_url,
                    itunes_complete=feed.itunes_complete,
                )
                output_path = await self._feed_publisher.publish(publisher_input)
                logger.info(f"Feed '{feed.config_title}' published to {output_path}")
                episode_pairs = [(ep.guid, ep.url) for ep in episodes]
                downloaded = await self._episode_downloader.download_all(
                    episode_pairs,
                    on_progress=self._on_download_progress,
                )
                if downloaded:
                    audio_metadata_store = AudioMetadataStore(db.conn)
                    probed_guids = await audio_metadata_store.get_probed_guids()
                    unprobed = [(g, p) for g, p in downloaded if g not in probed_guids]
                    skipped = len(downloaded) - len(unprobed)
                    logger.debug(
                        f"Probing {len(unprobed)} episode(s) for '{feed.config_title}' "
                        f"({skipped} already probed)"
                    )
                    probe_results = await self._audio_prober.probe_all(unprobed)
                    logger.debug(
                        f"Probe complete for '{feed.config_title}': "
                        f"{len(probe_results)} succeeded, "
                        f"{len(unprobed) - len(probe_results)} failed"
                    )
                    await audio_metadata_store.save_all(probe_results)

        return parsed_feeds

    def _select_feeds(self) -> list[FeedConfig]:
        """Return the feeds to process for this run.

        When ``feed_name`` is set, returns the single matching feed regardless
        of its ``enabled`` flag.  Otherwise returns all enabled feeds in config
        order.

        Raises:
            ValueError: If ``feed_name`` was supplied but no feed with that
                exact title exists in the config.

        """
        all_feeds = self._config.app.feeds

        if self._feed_name is not None:
            selected = [f for f in all_feeds if f.title == self._feed_name]
            if not selected:
                available = [f.title for f in all_feeds]
                msg = f"No feed titled {self._feed_name!r}. Available titles: {available}"
                raise ValueError(msg)
            logger.info(f"Pipeline starting: forcing feed '{self._feed_name}' (enabled override)")
            return selected

        selected = [f for f in all_feeds if f.enabled]
        logger.info(
            f"Pipeline starting: {len(selected)} enabled feed(s) of {len(all_feeds)} total"
        )
        return selected

    async def _download(self, feeds: list[FeedConfig]) -> list[tuple[str, str]]:
        """Extract (title, url) pairs and fetch the RSS XML for each feed.

        Args:
            feeds: Feeds selected for this run.

        Returns:
            ``(title, xml_text)`` pairs for every feed fetched successfully.

        """
        requests = [(f.title, f.url) for f in feeds]
        results = await self._feed_downloader.download_all(requests)
        logger.info(f"Feed download complete: {len(results)} feed(s) retrieved")
        return results

    def _build_parse_inputs(
        self,
        feeds: list[FeedConfig],
        download_results: list[tuple[str, str]],
    ) -> list[FeedParseInput]:
        """Join download results with config metadata to form parser inputs.

        Args:
            feeds: The feeds that were selected for this run (used as a lookup
                for ``episodes_to_keep`` and ``url``).
            download_results: ``(title, xml_text)`` pairs returned by the
                downloader.

        Returns:
            One :class:`FeedParseInput` per successful download, in result order.

        """
        # FeedConfig.title is treated as unique — the same assumption --feed relies on.
        feed_map = {f.title: f for f in feeds}
        return [
            FeedParseInput(
                config_title=title,
                feed_url=feed_map[title].url,
                episodes_to_keep=feed_map[title].episodes_to_keep,
                xml_text=xml_text,
            )
            for title, xml_text in download_results
        ]

    async def _on_download_progress(self, guid: str, percent: float) -> None:
        """Log episode download progress.

        Args:
            guid: Episode GUID being downloaded.
            percent: Progress in ``[0.0, 1.0]``.  ``0.0`` means starting;
                ``1.0`` means complete.

        """
        if percent == 0.0:
            logger.debug(f"Downloading episode '{guid}' \u2026")
        elif percent == 1.0:
            # End the in-place progress line before logging the completion event.
            sys.stderr.write("\n")
            sys.stderr.flush()
            logger.debug(f"Episode '{guid}' downloaded.")
        else:
            # Overwrite the current terminal line with updated progress — no newline.
            sys.stderr.write(f"\r  Episode '{guid}': {percent:.0%}")
            sys.stderr.flush()
