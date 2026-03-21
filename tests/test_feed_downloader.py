"""Tests for FeedDownloader — HTTP feed retrieval."""

from __future__ import annotations

from unittest.mock import MagicMock

import aiohttp
import pytest
from aioresponses import aioresponses

from components.feed_downloader import FeedDownloader
from config.config_loader import FeedConfig

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

FEED_A = FeedConfig(title="Feed A", url="https://example.com/a.rss", enabled=True, episodes_to_keep=10)
FEED_B = FeedConfig(title="Feed B", url="https://example.com/b.rss", enabled=True, episodes_to_keep=5)

XML_A = "<rss><channel><title>Feed A</title></channel></rss>"
XML_B = "<rss><channel><title>Feed B</title></channel></rss>"


@pytest.fixture
def downloader() -> FeedDownloader:
    return FeedDownloader(config=MagicMock())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_download_all_success(downloader: FeedDownloader) -> None:
    with aioresponses() as m:
        m.get(FEED_A.url, status=200, body=XML_A)
        m.get(FEED_B.url, status=200, body=XML_B)
        results = await downloader.download_all([FEED_A, FEED_B])

    assert results == [(FEED_A, XML_A), (FEED_B, XML_B)]


async def test_download_all_skips_non_200(downloader: FeedDownloader) -> None:
    with aioresponses() as m:
        m.get(FEED_A.url, status=404)
        results = await downloader.download_all([FEED_A])

    assert results == []


async def test_download_all_skips_on_network_error(downloader: FeedDownloader) -> None:
    with aioresponses() as m:
        m.get(FEED_A.url, exception=aiohttp.ClientError("connection failed"))
        results = await downloader.download_all([FEED_A])

    assert results == []


async def test_download_all_returns_empty_on_all_failures(downloader: FeedDownloader) -> None:
    with aioresponses() as m:
        m.get(FEED_A.url, status=500)
        m.get(FEED_B.url, status=503)
        results = await downloader.download_all([FEED_A, FEED_B])

    assert results == []


async def test_download_all_partial_success(downloader: FeedDownloader) -> None:
    """Successful feeds are returned even when others fail."""
    with aioresponses() as m:
        m.get(FEED_A.url, status=200, body=XML_A)
        m.get(FEED_B.url, status=404)
        results = await downloader.download_all([FEED_A, FEED_B])

    assert results == [(FEED_A, XML_A)]
