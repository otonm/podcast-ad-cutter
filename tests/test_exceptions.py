"""Tests for custom exception hierarchy."""

from __future__ import annotations

from utils.exceptions import (
    AdDetectionError,
    AudioProbeError,
    ConfigError,
    FfmpegError,
    PodcastAdCutterError,
    TranscriptionError,
)


def test_config_error_is_podcast_error() -> None:
    assert issubclass(ConfigError, PodcastAdCutterError)


def test_audio_probe_error_is_podcast_error() -> None:
    assert issubclass(AudioProbeError, PodcastAdCutterError)


def test_ffmpeg_error_is_podcast_error() -> None:
    assert issubclass(FfmpegError, PodcastAdCutterError)


def test_ffmpeg_error_message_without_stderr() -> None:
    exc = FfmpegError("ffmpeg exited with code 1")
    assert str(exc) == "ffmpeg exited with code 1"
    assert exc.message == "ffmpeg exited with code 1"
    assert exc.stderr == ""


def test_ffmpeg_error_message_with_stderr() -> None:
    exc = FfmpegError("ffmpeg exited with code 1", stderr="No such file or directory")
    assert "ffmpeg exited with code 1" in str(exc)
    assert "No such file or directory" in str(exc)
    assert exc.message == "ffmpeg exited with code 1"
    assert exc.stderr == "No such file or directory"


def test_ffmpeg_error_ignores_blank_stderr() -> None:
    exc = FfmpegError("ffmpeg exited with code 1", stderr="   \n  ")
    assert str(exc) == "ffmpeg exited with code 1"


def test_transcription_error_is_podcast_error() -> None:
    assert issubclass(TranscriptionError, PodcastAdCutterError)


def test_transcription_error_stores_message() -> None:
    exc = TranscriptionError("litellm failed")
    assert exc.message == "litellm failed"
    assert str(exc) == "litellm failed"


def test_ad_detection_error_is_podcast_error() -> None:
    assert issubclass(AdDetectionError, PodcastAdCutterError)


def test_ad_detection_error_stores_message() -> None:
    exc = AdDetectionError("ad detection failed")
    assert exc.message == "ad detection failed"
    assert str(exc) == "ad detection failed"
