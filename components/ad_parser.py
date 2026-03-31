"""AdParser — groups time-proximity ad segment detections into cut ranges."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.ad_detection import CutRange

if TYPE_CHECKING:
    from models.ad_detection import AdSegment

logger = logging.getLogger(__name__)

_GAP_THRESHOLD_MS = 10_000


class AdParser:
    """Groups AdSegment objects into CutRange objects for audio editing.

    Receives already-stored :class:`~models.ad_detection.AdSegment` objects
    (which carry full detection metadata), applies confidence and duration
    filters, merges segments within :data:`_GAP_THRESHOLD_MS` of each other,
    and returns only the time ranges that ffmpeg needs to cut.
    """

    def parse(
        self,
        ad_segments: list[AdSegment],
        min_duration_ms: int,
        min_confidence: float,
    ) -> list[CutRange]:
        """Filter, group, and convert ad segments to cut ranges.

        Args:
            ad_segments: Stored ad segments with full detection metadata.
            min_duration_ms: Minimum merged group duration in milliseconds.
                Groups shorter than this are discarded.
            min_confidence: Minimum confidence score.  Segments below this
                threshold are excluded before grouping.

        Returns:
            List of :class:`~models.ad_detection.CutRange` objects ready for
            ffmpeg.  Empty list when no segments qualify.

        """
        if not ad_segments:
            logger.debug("No ad segments provided, returning empty list")
            return []

        qualifying = [s for s in ad_segments if s.confidence >= min_confidence]
        if not qualifying:
            logger.debug("All ad segments below confidence threshold, returning empty list")
            return []

        qualifying.sort(key=lambda s: s.start_ms)

        groups: list[list[AdSegment]] = []
        current_group: list[AdSegment] = [qualifying[0]]
        for seg in qualifying[1:]:
            gap = seg.start_ms - current_group[-1].end_ms
            if gap <= _GAP_THRESHOLD_MS:
                current_group.append(seg)
            else:
                groups.append(current_group)
                current_group = [seg]
        groups.append(current_group)
        logger.debug(f"Grouped {len(qualifying)} qualifying segments into {len(groups)} group(s)")

        result = []
        for group in groups:
            start_ms = min(s.start_ms for s in group)
            end_ms = max(s.end_ms for s in group)
            if end_ms - start_ms < min_duration_ms:
                logger.debug(
                    f"Ad group discarded: duration {end_ms - start_ms}ms < min {min_duration_ms}ms"
                )
                continue
            result.append(CutRange(start_ms=start_ms, end_ms=end_ms))

        logger.debug(f"Parsed {len(result)} cut range(s): {result}")
        return result
