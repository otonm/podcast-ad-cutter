"""Pipeline — top-level orchestrator for the podcast ad-cutting workflow."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import TYPE_CHECKING

from slugify import slugify

from components.audio_preprocessor import AudioPreprocessor
from components.audio_prober import AudioProber
from components.episode_copier import EpisodeCopier
from components.episode_downloader import EpisodeDownloader
from components.episode_transcriptor import EpisodeTranscriptor
from components.feed_downloader import FeedDownloader
from components.feed_parser import FeedParser
from components.feed_publisher import FeedPublisher
from config.config_loader import PROVIDER_KEY_MAP
from database.audio_metadata_store import AudioMetadataStore
from database.connection import Database
from database.cost_tracking_store import CostTrackingStore
from database.episode_store import EpisodeStore
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
        self._episode_copier = EpisodeCopier(config.app.paths.output_dir, config.app.base_url)
        transcription_cfg = config.app.models.transcription
        self._transcriptor = EpisodeTranscriptor(
            provider=transcription_cfg.provider,
            model=transcription_cfg.model,
            api_key=getattr(config.credentials, PROVIDER_KEY_MAP[transcription_cfg.provider]),
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
                await store.save_episodes(feed.config_title, feed.episodes)

            for feed in parsed_feeds:
                cfg = feed_cfg_map[feed.config_title]
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
                transcribed_guids = await transcription_store.get_transcribed_guids()

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
                        )
                    except Exception:
                        logger.exception(f"Episode '{episode.guid}': error, skipping")

        return parsed_feeds

    async def _process_episode(
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
    ) -> None:
        """Process one episode according to its current state.

        Checks whether the transcription and the output audio file already exist,
        and performs only the work that is still needed.

        Branches:
            A — transcription + audio exist: compute URL only.
            B — transcription exists, no audio: download, probe, preprocess, copy.
            C — audio exists, no transcription: probe, preprocess, transcribe.
            D — neither exists: full pipeline (download, probe, preprocess, transcribe, copy).

        """
        pub_date_str = episode.pub_date.strftime("%d.%m.%Y")
        title_slug = slugify(episode.title)
        existing_audio = next(
            (p for p in output_feed_dir.glob(f"{pub_date_str}-{title_slug}.*")),  # noqa: ASYNC240
            None,
        )
        transcription_exists = episode.guid in transcribed_guids
        audio_exists = existing_audio is not None

        if transcription_exists and audio_exists:
            # Branch A: both exist — reconstruct the URL from the existing file.
            ext = existing_audio.suffix.lstrip(".")
            new_url = FeedPublisher.episode_url(
                self._config.app.base_url, feed_slug, episode.pub_date, episode.title, ext
            )
        elif transcription_exists:
            # Branch B: transcription OK, audio missing — re-download and copy.
            raw_path = await self._episode_downloader.download(
                episode.guid, episode.url, on_progress=self._on_download_progress
            )
            meta = await self._audio_prober.probe(episode.guid, raw_path)
            await audio_metadata_store.save_all([meta])
            await self._audio_preprocessor.preprocess(
                episode.guid, raw_path, meta.duration, on_progress=self._on_preprocess_progress
            )
            _, _, new_url = await self._episode_copier.copy(
                episode.guid, raw_path, feed_slug, episode.pub_date, episode.title
            )
        elif audio_exists:
            # Branch C: audio present, transcription missing — transcribe from output file.
            meta = await self._audio_prober.probe(episode.guid, existing_audio)
            await audio_metadata_store.save_all([meta])
            mono_path = await self._audio_preprocessor.preprocess(
                episode.guid, existing_audio, meta.duration, on_progress=self._on_preprocess_progress
            )
            _, transcription, segments, cost = await self._transcriptor.transcribe(
                episode.guid, mono_path
            )
            await transcription_store.save_transcription(transcription)
            await transcription_store.save_segments(segments)
            await cost_store.save_cost(cost)
            transcribed_guids.add(episode.guid)
            ext = existing_audio.suffix.lstrip(".")
            new_url = FeedPublisher.episode_url(
                self._config.app.base_url, feed_slug, episode.pub_date, episode.title, ext
            )
        else:
            # Branch D: nothing exists — run the full pipeline.
            raw_path = await self._episode_downloader.download(
                episode.guid, episode.url, on_progress=self._on_download_progress
            )
            meta = await self._audio_prober.probe(episode.guid, raw_path)
            await audio_metadata_store.save_all([meta])
            mono_path = await self._audio_preprocessor.preprocess(
                episode.guid, raw_path, meta.duration, on_progress=self._on_preprocess_progress
            )
            _, transcription, segments, cost = await self._transcriptor.transcribe(
                episode.guid, mono_path
            )
            await transcription_store.save_transcription(transcription)
            await transcription_store.save_segments(segments)
            await cost_store.save_cost(cost)
            transcribed_guids.add(episode.guid)
            _, _, new_url = await self._episode_copier.copy(
                episode.guid, raw_path, feed_slug, episode.pub_date, episode.title
            )

        await store.update_episode_url(episode.guid, new_url)
        await self._feed_publisher.update_episode_url(feed.title, episode.guid, new_url)

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
