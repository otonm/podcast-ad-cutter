import asyncio
import logging

import aiosqlite
import anyio
from slugify import slugify

from config.config_loader import AppConfig, FeedConfig
from db.connection import get_db
from db.repositories import AdSegmentRepository, EpisodeRepository
from db.repositories.llm_call_repo import LLMCallRepository
from models.ad_segment import AdSegment, TopicContext
from models.episode import Episode
from models.llm_call import CallType, LLMCall
from models.transcript import Transcript
from pipeline.ad_detector import detect_ads as detect_ads_impl
from pipeline.ad_detector import merge_segments
from pipeline.audio_editor import cut_ads
from pipeline.downloader import download_episode
from pipeline.feed_publisher import generate_feed_rss, prune_old_episodes
from pipeline.rss import fetch_episodes
from pipeline.topic_extractor import extract_topic
from pipeline.transcriber import transcribe_episode

logger = logging.getLogger(__name__)


def _feed_slug(title: str) -> str:
    """Return a URL-safe slug for a feed title (used for folder and RSS filename)."""
    return slugify(title, max_length=80)


def _episode_filename(episode: Episode, ext: str) -> str:
    """Return a URL-safe filename for a cleaned episode file."""
    date_str = episode.published.strftime("%d.%m.%Y")
    slug = slugify(episode.title, max_length=80)
    return f"{date_str}-{slug}.{ext}"


async def run_pipeline(cfg: AppConfig, *, dry_run: bool = False) -> None:
    """Run the full podcast ad cutting pipeline for all enabled feeds."""
    feeds = [f for f in cfg.feeds if f.enabled]
    if not feeds:
        logger.warning("No enabled feeds found in config")
        return

    for feed_cfg in feeds:
        logger.info(f"Processing feed: {feed_cfg.name}")
        await process_feed(feed_cfg, cfg, dry_run=dry_run)


async def process_feed(
    feed_cfg: FeedConfig,
    cfg: AppConfig,
    *,
    dry_run: bool = False,
) -> None:
    """Process a single feed: fetch episodes, process each one, then publish."""
    episodes = await fetch_episodes(feed_cfg, episodes_to_keep=cfg.episodes_to_keep)

    for episode in episodes:
        logger.info(f"Processing episode: {episode.title}")
        await _process_episode(episode, cfg, dry_run=dry_run)

    if not cfg.publishing.base_url:
        return

    feed_slug = _feed_slug(feed_cfg.name)
    ext = cfg.audio.output_format.value
    podcast_dir = cfg.paths.output_dir / feed_slug

    if cfg.publishing.max_episodes_per_feed is not None and podcast_dir.exists():
        max_ep = cfg.publishing.max_episodes_per_feed
        await asyncio.to_thread(
            prune_old_episodes, podcast_dir, ext, max_episodes=max_ep
        )

    await generate_feed_rss(feed_cfg, cfg, feed_slug=feed_slug, podcast_dir=podcast_dir)


async def _process_episode(
    episode: Episode,
    cfg: AppConfig,
    *,
    dry_run: bool = False,
) -> None:
    """Download, transcribe, detect ads, and cut a single episode."""
    ext = cfg.audio.output_format.value
    podcast_dir = cfg.paths.output_dir / _feed_slug(episode.feed_title)
    clean_path = podcast_dir / _episode_filename(episode, ext)
    await anyio.Path(podcast_dir).mkdir(parents=True, exist_ok=True)

    # Checkpoint 1 — final file already exists
    if await anyio.Path(clean_path).exists():
        logger.info(f"Clean file already exists, skipping: {clean_path.name}")
        return

    async with get_db(cfg.paths.database) as db:
        ep_repo = EpisodeRepository(db)
        await ep_repo.upsert(episode)

        ad_repo = AdSegmentRepository(db)

        # Checkpoint 2 — ad segments already detected, only need to cut
        cached_segments = [
            s for s in await ad_repo.get_by_episode(episode.guid)
            if s.confidence >= cfg.ad_detection.min_confidence
        ]
        if cached_segments:
            logger.debug(
                f"Ad segments cache hit for {episode.guid}: {len(cached_segments)} segments"
            )

            audio_path = await download_episode(episode)
            try:
                if not dry_run:
                    await cut_ads(audio_path, cached_segments, cfg, db, output_path=clean_path)
                else:
                    logger.info("Dry run: skipping audio cutting")
            finally:
                await anyio.Path(audio_path).unlink(missing_ok=True)
            return

        # Checkpoints 3 & 4: transcript and topic context have internal cache checks
        audio_path = await download_episode(episode)
        try:
            transcript = await transcribe_episode(episode, audio_path, cfg, db)
            if transcript is None:
                logger.error(f"Transcription returned None for {episode.guid}, aborting")
                return

            topic_context = await extract_topic(transcript, cfg, db)
            if topic_context is None:
                logger.error(f"Topic extraction failed for {episode.guid}, aborting")
                return

            ad_segments = await detect_ads(topic_context, transcript, cfg, db)

            if not dry_run and ad_segments:
                await cut_ads(audio_path, ad_segments, cfg, db, output_path=clean_path)
            elif dry_run:
                logger.info("Dry run: skipping audio cutting")
        finally:
            await anyio.Path(audio_path).unlink(missing_ok=True)


async def detect_ads(
    topic_context: TopicContext,
    transcript: Transcript,
    cfg: AppConfig,
    db: aiosqlite.Connection,
) -> list[AdSegment]:
    """Detect ad segments in the transcript using LLM."""
    ad_repo = AdSegmentRepository(db)
    llm_repo = LLMCallRepository(db)

    segments, total_cost = await detect_ads_impl(topic_context, transcript, cfg)
    merged = merge_segments(segments, cfg.ad_detection.merge_gap_sec)
    above_threshold = [s for s in merged if s.confidence >= cfg.ad_detection.min_confidence]
    total_ms = sum(s.end_ms - s.start_ms for s in above_threshold)
    logger.info(
        f"Ad detection complete: {len(merged)} segment(s) found, "
        f"{len(above_threshold)} above threshold "
        f"({total_ms / 1000:.1f}s to cut)"
    )
    await ad_repo.save_all(merged)
    await llm_repo.save(
        LLMCall(
            episode_guid=transcript.episode_guid,
            call_type=CallType.AD_DETECTION,
            model=cfg.interpretation.provider_model,
            cost_usd=total_cost,
        )
    )
    return above_threshold
