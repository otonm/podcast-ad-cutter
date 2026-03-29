"""AdParser — groups consecutive ad segment detections into merged time ranges."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.ad_detection import AdSegment

if TYPE_CHECKING:
    from models.ad_detection import AdSegmentDetection
    from models.transcription import TranscriptionSegment

logger = logging.getLogger(__name__)


class AdParser:
    """Groups consecutive AdSegmentDetection results into merged AdSegment objects.

    Pure logic — no I/O, no LLM, no database.

    """

    def parse(
        self,
        guid: str,
        detections: list[AdSegmentDetection],
        segments: list[TranscriptionSegment],
    ) -> list[AdSegment]:
        """Merge consecutive detected ad indexes into time-bounded AdSegment objects.

        Args:
            guid: Episode GUID propagated to each returned AdSegment.
            detections: Raw LLM detections from AdDetector.
            segments: All transcription segments (used to look up start/end times).

        Returns:
            List of :class:`AdSegment` objects.  Non-consecutive indexes produce
            separate segments.  Empty list when ``detections`` is empty or all
            indexes are out of range.

        """
        if not detections:
            return []

        segment_map = dict(enumerate(segments))

        valid = []
        for d in sorted(detections, key=lambda x: x.index):
            if d.index not in segment_map:
                logger.warning(f"Ad detection index {d.index} out of range for '{guid}', skipping")
                continue
            valid.append(d)

        if not valid:
            return []

        groups: list[list[AdSegmentDetection]] = []
        current_group = [valid[0]]
        for det in valid[1:]:
            if det.index == current_group[-1].index + 1:
                current_group.append(det)
            else:
                groups.append(current_group)
                current_group = [det]
        groups.append(current_group)

        result = []
        for group in groups:
            start_ms = min(segment_map[d.index].start_ms for d in group)
            end_ms = max(segment_map[d.index].end_ms for d in group)
            confidence = sum(d.confidence for d in group) / len(group)
            result.append(AdSegment(
                guid=guid,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=confidence,
                sponsor=group[0].sponsor,
                ad_topic=group[0].ad_topic,
            ))

        return result
