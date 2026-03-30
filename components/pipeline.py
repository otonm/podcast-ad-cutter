"""Pipeline — top-level orchestrator for the podcast ad-cutting workflow."""

from __future__ import annotations

import logging
import sys
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
from models.feed import Episode, FeedParseInput, ParsedFeed, PublisherInput

if TYPE_CHECKING:
    from pathlib import Path

    from config.config_loader import Config, FeedConfig

logger = logging.getLogger(__name__)


class Pipeline:
    """Coordinates each stage of the podcast ad-cutting workflow.

    Pipeline is the sole owner of :class:`Config`.  It extracts the plain
    data each component needs and passes it through their APIs — no component
    below Pipeline imports from the config module.

    Each episode is processed according to a decision tree that checks whether
    the transcription and the output audio file already exist, performing only
    the remaining work.

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

                # Determine which parsed episodes are new to the DB.
                existing_guids = await store.get_guids_for_feed(feed.config_title)
                new_guids = {ep.guid for ep in feed.episodes} - existing_guids

                if rss_path.exists() and not new_guids:
                    logger.info(f"[{feed.config_title}] no new items — skipping feed")
                    continue

                # Either the RSS file has never been written, or new episodes arrived.
                await store.save_episodes(feed.config_title, feed.episodes)

                episodes = list(await store.get_episodes_for_feed(
                    feed.config_title, cfg.episodes_to_keep
                ))
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

                if not episodes:
                    continue

                feed_slug = slugify(feed.title)
                output_feed_dir = self._config.app.paths.output_dir / feed_slug

                transcription_store = TranscriptionStore(db.conn)
                audio_metadata_store = AudioMetadataStore(db.conn)
                cost_store = CostTrackingStore(db.conn)
                topic_store = TopicStore(db.conn)
                ad_store = AdStore(db.conn)
                transcribed_guids = await transcription_store.get_transcribed_guids()
                extracted_guids = await topic_store.get_extracted_guids()
                ad_detected_guids = await ad_store.get_detected_guids()

                for episode in episodes:
                    try:
                        await self._process_episode(
                            episode=episode,
                            feed=feed,
                            feed_slug=feed_slug,
                            output_feed_dir=output_feed_dir,
                            store=store,
                            transcribed_guids=transcribed_guids,
                            transcription_store=transcription_store,
                            audio_metadata_store=audio_metadata_store,
                            cost_store=cost_store,
                            topic_store=topic_store,
                            extracted_guids=extracted_guids,
                            ad_store=ad_store,
                            ad_detected_guids=ad_detected_guids,
                        )
                    except Exception:
                        logger.exception(f"Episode '{episode.guid}': error, skipping")

        return parsed_feeds

    async def _process_episode(  # noqa: PLR0915
        self,
        *,
        episode: Episode,
        feed: ParsedFeed,
        feed_slug: str,
        output_feed_dir: Path,
        store: EpisodeStore,
        transcribed_guids: set[str],
        transcription_store: TranscriptionStore,
        audio_metadata_store: AudioMetadataStore,
        cost_store: CostTrackingStore,
        topic_store: TopicStore,
        extracted_guids: set[str],
        ad_store: AdStore,
        ad_detected_guids: set[str],
    ) -> None:
        """Process one episode according to its current state.

        Checks whether the output audio file already exists, and performs only
        the work that is still needed.

        Branches:
            A — output file exists on disk: compute URL only, return early.
            B — transcription exists, no output: download, probe, ad detection tail.
            C — cached audio exists, no transcription: probe, preprocess, transcribe, ad detection tail.
            D — nothing exists: full pipeline (download, probe, preprocess, transcribe, ad detection tail).

        Ad detection tail (Branches B, C, D):
            Load or run ad detection. Call AudioEditor.edit(). If Path returned,
            update episode URL. If None returned, keep original URL unchanged.

        """
        pub_date_str = episode.pub_date.strftime("%d.%m.%Y")
        title_slug = slugify(episode.title)
        existing_audio = next(
            (p for p in output_feed_dir.glob(f"{pub_date_str}-{title_slug}.*")),  # noqa: ASYNC240
            None,
        )
        cache_dir = self._config.app.paths.cache_dir
        cached_audio = next(
            (p for p in cache_dir.glob(f"{episode.guid}.*")),
            None,
        )
        transcription_exists = episode.guid in transcribed_guids
        output_exists = existing_audio is not None
        cached_audio_exists = cached_audio is not None

        logger.info(f"Processing episode '{episode.title}' [{episode.guid}]")

        if output_exists:
            # Branch A: output already produced — reconstruct URL from existing file.
            logger.debug(
                f"Episode '{episode.guid}': branch A — output already exists at {existing_audio}, skipping"
            )
            ext = existing_audio.suffix.lstrip(".")
            new_url = FeedPublisher.episode_url(
                self._config.app.base_url, feed_slug, episode.pub_date, episode.title, ext
            )
            await store.update_episode_url(episode.guid, new_url)
            await self._feed_publisher.update_episode_url(feed.title, episode.guid, new_url)
            return  # short-circuit — no further processing
        if transcription_exists:
            # Branch B: transcription OK, no output — re-download and probe; skip preprocess (D-05).
            logger.debug(
                f"Episode '{episode.guid}': branch B — transcription cached, no output; re-downloading"
            )
            raw_path = await self._episode_downloader.download(
                episode.guid, episode.url, on_progress=self._on_download_progress
            )
            meta = await self._audio_prober.probe(episode.guid, raw_path)
            await audio_metadata_store.save_all([meta])
            t_segments = await transcription_store.get_segments_for_guid(episode.guid)
            logger.debug(
                f"Episode '{episode.guid}': loaded {len(t_segments)} transcription segment(s) from DB"
            )
            topic = await topic_store.get_topic_for_guid(episode.guid)
            logger.debug(
                f"Episode '{episode.guid}': topic context {'available' if topic else 'unavailable'}"
            )
        elif cached_audio_exists:
            # Branch C: cached audio present, transcription missing — transcribe from cached file.
            logger.debug(
                f"Episode '{episode.guid}': branch C — cached audio found, transcription missing"
            )
            meta = await self._audio_prober.probe(episode.guid, cached_audio)
            await audio_metadata_store.save_all([meta])
            mono_path = await self._audio_preprocessor.preprocess(
                episode.guid, cached_audio, meta.duration, on_progress=self._on_preprocess_progress
            )
            _, transcription, t_segments, cost = await self._transcriptor.transcribe(
                episode.guid, mono_path
            )
            await transcription_store.save_transcription(transcription)
            await transcription_store.save_segments(t_segments)
            await cost_store.save_cost(cost)
            transcribed_guids.add(episode.guid)
            if episode.guid not in extracted_guids:
                _, topic_obj, topic_cost = await self._topic_extractor.extract(
                    episode.guid, feed.config_title, episode.title, transcription.text
                )
                await topic_store.save_topic(topic_obj)
                await cost_store.save_cost(topic_cost)
                extracted_guids.add(episode.guid)
                topic = topic_obj
            else:
                topic = await topic_store.get_topic_for_guid(episode.guid)
            raw_path = cached_audio  # AudioEditor receives the cached audio path in Branch C
        else:
            # Branch D: nothing exists — run the full pipeline.
            logger.debug(f"Episode '{episode.guid}': branch D — full pipeline (nothing cached)")
            raw_path = await self._episode_downloader.download(
                episode.guid, episode.url, on_progress=self._on_download_progress
            )
            meta = await self._audio_prober.probe(episode.guid, raw_path)
            await audio_metadata_store.save_all([meta])
            mono_path = await self._audio_preprocessor.preprocess(
                episode.guid, raw_path, meta.duration, on_progress=self._on_preprocess_progress
            )
            _, transcription, t_segments, cost = await self._transcriptor.transcribe(
                episode.guid, mono_path
            )
            await transcription_store.save_transcription(transcription)
            await transcription_store.save_segments(t_segments)
            await cost_store.save_cost(cost)
            transcribed_guids.add(episode.guid)
            if episode.guid not in extracted_guids:
                _, topic_obj, topic_cost = await self._topic_extractor.extract(
                    episode.guid, feed.config_title, episode.title, transcription.text
                )
                await topic_store.save_topic(topic_obj)
                await cost_store.save_cost(topic_cost)
                extracted_guids.add(episode.guid)
                topic = topic_obj
            else:
                topic = await topic_store.get_topic_for_guid(episode.guid)

        # Ad detection tail (Branches B, C, D)
        if episode.guid not in ad_detected_guids:
            _, detections, ad_cost = await self._ad_detector.detect(
                episode.guid, t_segments, topic
            )
            segments = self._ad_parser.parse(episode.guid, detections, t_segments)
            await ad_store.save_segments(episode.guid, segments)
            await ad_store.mark_detected(episode.guid)
            await cost_store.save_cost(ad_cost)
            ad_detected_guids.add(episode.guid)
        else:
            segments = await ad_store.get_segments_for_guid(episode.guid)
            logger.debug(
                f"Episode '{episode.guid}': ad detection cached, loading {len(segments)} segment(s) from DB"
            )

        output_path = await self._audio_editor.edit(
            episode.guid,
            raw_path,
            segments,
            feed_slug,
            episode.pub_date,
            episode.title,
            min_duration_ms=self._config.app.ad_detection.min_duration,
            min_confidence=self._config.app.ad_detection.min_confidence,
            total_duration_s=meta.duration,
        )

        if output_path is not None:
            new_url = FeedPublisher.episode_url(
                self._config.app.base_url, feed_slug, episode.pub_date, episode.title,
                self._config.app.output.file_type,
            )
            await store.update_episode_url(episode.guid, new_url)
            await self._feed_publisher.update_episode_url(feed.title, episode.guid, new_url)
        else:
            logger.info(f"Episode '{episode.guid}': no qualifying ads — original audio unchanged")

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
