"""Pipeline — top-level orchestrator for the podcast ad-cutting workflow."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from slugify import slugify

from components.ad_detector import AdDetector
from components.ad_parser import AdParser
from components.audio_editor import AudioEditor
from components.audio_preprocessor import AudioPreprocessor
from components.audio_prober import AudioProber
from components.episode_downloader import EpisodeDownloader
from components.episode_transcriptor import EpisodeTranscriptor
from components.feed_downloader import FeedDownloader
from components.feed_parser import FeedParser
from components.feed_publisher import FeedPublisher
from components.topic_extractor import TopicExtractor
from config.config_loader import PROVIDER_KEY_MAP
from database.ad_store import AdStore
from database.audio_metadata_store import AudioMetadataStore
from database.connection import Database
from database.cost_tracking_store import CostTrackingStore
from database.episode_store import EpisodeStore
from database.topic_store import TopicStore
from database.transcription_store import TranscriptionStore
from models.ad_detection import AdSegment
from models.feed import AudioMetadata, Episode, FeedParseInput, ParsedFeed, PublisherInput

if TYPE_CHECKING:
    from pathlib import Path

    from config.config_loader import Config, FeedConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Stores:
    episode: EpisodeStore
    transcription: TranscriptionStore
    audio_metadata: AudioMetadataStore
    cost: CostTrackingStore
    topic: TopicStore
    ad: AdStore
    transcribed_guids: set[str]   # mutated per episode; shared across all episodes in feed
    extracted_guids: set[str]
    ad_detected_guids: set[str]


class Pipeline:
    """Coordinates each stage of the podcast ad-cutting workflow.

    Pipeline is the sole owner of :class:`Config`.  It extracts the plain
    data each component needs and passes it through their APIs — no component
    below Pipeline imports from the config module.

    Each episode is processed by a while-loop state machine that checks what
    is missing and performs exactly one step per iteration, persisting results
    to the database so subsequent iterations pick up from the new state.

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
        self._audio_preprocessor = AudioPreprocessor(config.app.paths.cache_dir)
        transcription_cfg = config.app.models.transcription
        self._transcriptor = EpisodeTranscriptor(
            provider=transcription_cfg.provider,
            model=transcription_cfg.model,
            api_key=getattr(config.credentials, PROVIDER_KEY_MAP[transcription_cfg.provider]),
        )
        context_cfg = config.app.models.context_extraction
        self._topic_extractor = TopicExtractor(
            provider=context_cfg.provider,
            model=context_cfg.model,
            api_key=getattr(config.credentials, PROVIDER_KEY_MAP[context_cfg.provider]),
        )
        ad_cfg = config.app.models.ad_detection
        self._ad_detector = AdDetector(
            provider=ad_cfg.provider,
            model=ad_cfg.model,
            api_key=getattr(config.credentials, PROVIDER_KEY_MAP[ad_cfg.provider]),
        )
        self._ad_parser = AdParser()
        self._audio_editor = AudioEditor(
            output_dir=config.app.paths.output_dir,
            file_type=config.app.output.file_type,
            bitrate=config.app.output.bitrate,
        )

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
                cfg = feed_cfg_map[feed.config_title]
                rss_path = self._output_rss_path(feed)

                existing_guids = await store.get_guids_for_feed(feed.config_title)
                new_guids = {ep.guid for ep in feed.episodes} - existing_guids

                if rss_path.exists() and not new_guids:
                    logger.info(f"[{feed.config_title}] no new items — skipping feed")
                    continue

                await store.save_episodes(feed.config_title, feed.episodes)

                episodes = list(await store.get_episodes_for_feed(
                    feed.config_title, cfg.episodes_to_keep
                ))

                output_path = await self._publish_feed(feed, episodes)
                logger.info(f"Feed '{feed.config_title}' published to {output_path}")

                if not episodes:
                    continue

                feed_slug = slugify(feed.title)
                output_feed_dir = self._config.app.paths.output_dir / feed_slug

                t_store = TranscriptionStore(db.conn)
                topic_store = TopicStore(db.conn)
                ad_store = AdStore(db.conn)
                stores = _Stores(
                    episode=store,
                    transcription=t_store,
                    audio_metadata=AudioMetadataStore(db.conn),
                    cost=CostTrackingStore(db.conn),
                    topic=topic_store,
                    ad=ad_store,
                    transcribed_guids=await t_store.get_transcribed_guids(),
                    extracted_guids=await topic_store.get_extracted_guids(),
                    ad_detected_guids=await ad_store.get_detected_guids(),
                )

                for episode in episodes:
                    try:
                        await self._process_episode_until_final(
                            episode=episode,
                            feed=feed,
                            feed_slug=feed_slug,
                            output_feed_dir=output_feed_dir,
                            stores=stores,
                        )
                    except Exception:
                        logger.exception(f"Episode '{episode.guid}': error, skipping")

        return parsed_feeds

    async def _publish_feed(self, feed: ParsedFeed, episodes: list[Episode]) -> Path:
        """Build a PublisherInput from feed metadata and publish the RSS file.

        Args:
            feed: Parsed feed supplying channel metadata.
            episodes: Episode list to include in the published feed.

        Returns:
            Path to the written RSS file.

        """
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
        return await self._feed_publisher.publish(publisher_input)

    async def _process_episode_until_final(  # noqa: PLR0915
        self,
        *,
        episode: Episode,
        feed: ParsedFeed,
        feed_slug: str,
        output_feed_dir: Path,
        stores: _Stores,
    ) -> None:
        """Process one episode via a while-loop state machine.

        Each iteration checks what is already done (filesystem + DB) and
        performs exactly one step, persisting results so the next iteration
        picks up from the new state.  The loop exits when either the final
        output file exists or it is determined that no cuts are needed.

        Guard order per iteration:
            1. Final file exists  → update URL, return.
            2. Ad segments detected → export edited audio (return or continue).
            3. Topic extracted    → run ad detection, continue.
            4. Transcript exists  → extract topic, continue.
            (bottom) Download + preprocess + transcribe, continue.

        """
        pub_date_str = episode.pub_date.strftime("%d.%m.%Y")
        title_slug = slugify(episode.title)
        cache_dir = self._config.app.paths.cache_dir
        raw_path: Path | None = None
        meta: AudioMetadata | None = None

        logger.info(f"Processing episode '{episode.title}' [{episode.guid}]")

        try:
            while True:
                # Guard 1: final file exists → update URL and done.
                existing_audio = next(
                    (p for p in output_feed_dir.glob(f"{pub_date_str}-{title_slug}.*")),  # noqa: ASYNC240
                    None,
                )
                if existing_audio is not None:
                    logger.info(
                        f"Episode '{episode.guid}': output already exists at {existing_audio}, skipping"
                    )
                    ext = existing_audio.suffix.lstrip(".")
                    new_url = FeedPublisher.episode_url(
                        self._config.app.base_url, feed_slug, episode.pub_date, episode.title, ext
                    )
                    await stores.episode.update_episode_url(episode.guid, new_url)
                    await self._feed_publisher.update_episode_url(feed.title, episode.guid, new_url)
                    return

                # Guard 2: ad segments detected → export edited audio.
                if episode.guid in stores.ad_detected_guids:
                    ad_segments = await stores.ad.get_segments_for_guid(episode.guid)
                    logger.info(
                        f"Episode '{episode.guid}': ad detection cached, "
                        f"loading {len(ad_segments)} segment(s) from DB"
                    )
                    cut_ranges = self._ad_parser.parse(
                        ad_segments,
                        min_duration_ms=self._config.app.ad_detection.min_duration,
                        min_confidence=self._config.app.ad_detection.min_confidence,
                    )
                    if raw_path is None:
                        # Transcript was cached from a previous run — download now.
                        logger.info(
                            f"Episode '{episode.guid}': transcription cached, no output; re-downloading"
                        )
                        raw_path = await self._episode_downloader.download(
                            episode.guid, episode.url, on_progress=self._on_download_progress
                        )
                        meta = await self._audio_prober.probe(episode.guid, raw_path)
                        await stores.audio_metadata.save_all([meta])
                    output_path = await self._audio_editor.edit(
                        episode.guid,
                        raw_path,
                        cut_ranges,
                        feed_slug,
                        episode.pub_date,
                        episode.title,
                        total_duration_s=meta.duration,  # type: ignore[union-attr]
                    )
                    if output_path is not None:
                        new_url = FeedPublisher.episode_url(
                            self._config.app.base_url, feed_slug, episode.pub_date, episode.title,
                            self._config.app.output.file_type,
                        )
                        await stores.episode.update_episode_url(episode.guid, new_url)
                        await self._feed_publisher.update_episode_url(feed.title, episode.guid, new_url)
                        return
                    logger.info(
                        f"Episode '{episode.guid}': no qualifying ads — original audio unchanged"
                    )
                    return

                # Guard 3: topic extracted → detect ads.
                if episode.guid in stores.extracted_guids:
                    topic = await stores.topic.get_topic_for_guid(episode.guid)
                    t_segments = await stores.transcription.get_segments_for_guid(episode.guid)
                    logger.info(
                        f"Episode '{episode.guid}': loaded {len(t_segments)} transcription segment(s) from DB"
                    )
                    logger.info(
                        f"Episode '{episode.guid}': topic context "
                        f"{'available' if topic else 'unavailable'}"
                    )
                    segment_map = dict(enumerate(t_segments))
                    _, detections, ad_cost = await self._ad_detector.detect(
                        episode.guid, t_segments, topic
                    )
                    ad_segments = [
                        AdSegment(
                            guid=episode.guid,
                            start_ms=min(segment_map[i].start_ms for i in valid_indices),
                            end_ms=max(segment_map[i].end_ms for i in valid_indices),
                            confidence=d.confidence,
                            sponsor=d.sponsor,
                            ad_topic=d.ad_topic,
                            indices=valid_indices,
                        )
                        for d in detections
                        if (valid_indices := [i for i in d.indices if i in segment_map])
                    ]
                    await stores.ad.save_segments(episode.guid, ad_segments)
                    await stores.ad.mark_detected(episode.guid)
                    await stores.cost.save_cost(ad_cost)
                    stores.ad_detected_guids.add(episode.guid)
                    continue

                # Guard 4: transcript exists → extract topic.
                if episode.guid in stores.transcribed_guids:
                    transcription_text = await stores.transcription.get_transcription_text(episode.guid)
                    _, topic_obj, topic_cost = await self._topic_extractor.extract(
                        episode.guid,
                        feed.config_title,
                        episode.title,
                        feed.title,
                        transcription_text,
                        episode.description,
                    )
                    await stores.topic.save_topic(topic_obj)
                    await stores.cost.save_cost(topic_cost)
                    stores.extracted_guids.add(episode.guid)
                    continue

                # Bottom: nothing cached — download, preprocess, transcribe.
                cached_audio = next(
                    (p for p in cache_dir.glob(f"{episode.guid}.*")),
                    None,
                )
                if cached_audio is not None:
                    logger.info(
                        f"Episode '{episode.guid}': cached audio found, transcription missing"
                    )
                    raw_path = cached_audio
                else:
                    logger.info(f"Episode '{episode.guid}' is new")
                    raw_path = await self._episode_downloader.download(
                        episode.guid, episode.url, on_progress=self._on_download_progress
                    )
                meta = await self._audio_prober.probe(episode.guid, raw_path)
                await stores.audio_metadata.save_all([meta])
                mono_path = await self._audio_preprocessor.preprocess(
                    episode.guid, raw_path, meta.duration, on_progress=self._on_preprocess_progress
                )
                try:
                    _, transcription, t_segments, cost = await self._transcriptor.transcribe(
                        episode.guid, mono_path
                    )
                finally:
                    mono_path.unlink(missing_ok=True)
                    logger.debug(f"Episode '{episode.guid}': removed mono file {mono_path}")
                await stores.transcription.save_transcription(transcription)
                await stores.transcription.save_segments(t_segments)
                await stores.cost.save_cost(cost)
                stores.transcribed_guids.add(episode.guid)
                # loop → guard 4 fires on next iteration
        finally:
            if raw_path is not None:
                raw_path.unlink(missing_ok=True)
                logger.debug(f"Episode '{episode.guid}': removed cached audio {raw_path}")

    def _output_rss_path(self, feed: ParsedFeed) -> Path:
        """Return the expected RSS output path for a parsed feed.

        Args:
            feed: The parsed feed whose output path is needed.

        Returns:
            ``output_dir/{feed_slug}.rss`` as a :class:`~pathlib.Path`.

        """
        return self._config.app.paths.output_dir / f"{slugify(feed.title)}.rss"

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
            logger.info(f"Forcing feed '{self._feed_name}' (enabled override)")
            return selected

        selected = [f for f in all_feeds if f.enabled]
        logger.info(
            f"Processing {len(selected)} enabled feed(s) of {len(all_feeds)} total"
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
            sys.stderr.write("\n")
            sys.stderr.flush()
            logger.debug(f"Episode '{guid}' downloaded.")
        else:
            sys.stderr.write(f"\r  Episode '{guid}': {percent:.0%}")
            sys.stderr.flush()

    async def _on_preprocess_progress(self, guid: str, percent: float) -> None:
        """Log episode preprocessing progress.

        Args:
            guid: Episode GUID being preprocessed.
            percent: Progress in ``[0.0, 1.0]``.  ``0.0`` means starting;
                ``1.0`` means complete.

        """
        if percent == 0.0:
            logger.debug(f"Preprocessing episode '{guid}' \u2026")
        elif percent == 1.0:
            sys.stderr.write("\n")
            sys.stderr.flush()
            logger.debug(f"Episode '{guid}' preprocessed.")
        else:
            sys.stderr.write(f"\r  Episode '{guid}': {percent:.0%}")
            sys.stderr.flush()
