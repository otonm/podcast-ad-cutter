import logging
from pathlib import Path

from config_loader import AppConfig
from db.connection import get_db
from db.repositories import AdSegmentRepository, EpisodeRepository, TranscriptRepository
from db.repositories.llm_call_repo import LLMCallRepository
from models.episode import Episode
from models.llm_call import CallType, LLMCall
from pipeline.audio_editor import cut_ads
from pipeline.downloader import download_episode
from pipeline.rss import fetch_latest_episode
from pipeline.topic_extractor import extract_topic
from pipeline.transcriber import transcribe_episode

logger = logging.getLogger(__name__)


async def run_pipeline(cfg: AppConfig, *, dry_run: bool = False) -> None:
    """Run the full podcast ad cutting pipeline for all enabled feeds."""
    feeds = [f for f in cfg.feeds if f.enabled]
    if not feeds:
        logger.warning("No enabled feeds found in config")
        return

    for feed_cfg in feeds:
        logger.info("Processing feed: %s", feed_cfg.name)
        await process_feed(feed_cfg, cfg, dry_run=dry_run)


async def process_feed(
    feed_cfg,
    cfg: AppConfig,
    *,
    dry_run: bool = False,
) -> None:
    """Process a single feed: fetch episode, download, transcribe, detect ads, cut."""
    episode = await fetch_latest_episode(feed_cfg)
    if episode is None:
        return

    async with get_db(cfg.paths.database) as db:
        ep_repo = EpisodeRepository(db)
        await ep_repo.upsert(episode)

        audio_path = await download_episode(episode, output_dir=cfg.paths.output_dir)

        transcript = await transcribe_episode(episode, audio_path, cfg, db)

        topic_context = await extract_topic(transcript, cfg, db)

        ad_segments = await detect_ads(topic_context, transcript, cfg, db)

        if not dry_run and ad_segments:
            await cut_ads(audio_path, ad_segments, cfg, db)
        elif dry_run:
            logger.info("Dry run: skipping audio cutting")


async def detect_ads(topic_context, transcript, cfg, db):
    """Detect ad segments in the transcript using LLM."""
    from pipeline.ad_detector import detect_ads as detect_ads_impl
    from pipeline.ad_detector import merge_segments

    ad_repo = AdSegmentRepository(db)
    llm_repo = LLMCallRepository(db)

    segments, total_cost = await detect_ads_impl(topic_context, transcript, cfg)
    merged = merge_segments(segments, cfg.ad_detection.merge_gap_sec)
    await ad_repo.save_all(merged)
    await llm_repo.save(
        LLMCall(
            episode_guid=transcript.episode_guid,
            call_type=CallType.AD_DETECTION,
            model=cfg.interpretation.provider_model,
            cost_usd=total_cost,
        )
    )
    return merged
