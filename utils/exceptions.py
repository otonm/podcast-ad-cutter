"""Custom exceptions for podcast-ad-cutter."""

from __future__ import annotations


class PodcastAdCutterError(Exception):
    """Base exception for all podcast-ad-cutter errors."""


class ConfigError(PodcastAdCutterError):
    """Raised when configuration loading or validation fails."""


class AudioProbeError(PodcastAdCutterError):
    """Raised when ffprobe fails to extract audio metadata from a file."""


class FfmpegError(PodcastAdCutterError):
    """Raised when ffmpeg exits with a non-zero return code."""
