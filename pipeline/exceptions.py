class PodcastAdCutterError(Exception):
    """Base exception for all podcast-ad-cutter errors."""


class ConfigError(PodcastAdCutterError):
    """Invalid or missing configuration."""


class DatabaseError(PodcastAdCutterError):
    """Database operation failed."""


class FeedFetchError(PodcastAdCutterError):
    """RSS feed fetch or parse failed."""


class DownloadError(PodcastAdCutterError):
    """Audio file download failed."""


class LLMError(PodcastAdCutterError):
    """LLM completion call failed."""


class TranscriptionError(PodcastAdCutterError):
    """Audio transcription failed."""


class AdDetectionError(PodcastAdCutterError):
    """Ad detection processing failed."""


class AudioEditError(PodcastAdCutterError):
    """Audio cutting or export failed."""
