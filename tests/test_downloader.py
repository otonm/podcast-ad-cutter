import httpx
import respx

from models.episode import Episode


@respx.mock
async def test_download_episode(tmp_path):
    from pipeline.downloader import download_episode

    ep = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    audio_bytes = b"\xff\xfb\x90\x00" * 1000  # fake MP3 bytes
    respx.get("https://example.com/ep1.mp3").respond(200, content=audio_bytes)

    async with httpx.AsyncClient() as client:
        path = await download_episode(ep, output_dir=tmp_path, client=client)

    assert path.exists()
    assert path.stat().st_size == len(audio_bytes)
    assert path.suffix == ".mp3"


@respx.mock
async def test_download_follows_redirect(tmp_path):
    from pipeline.downloader import download_episode

    ep = Episode(
        guid="ep-002",
        feed_title="Test Pod",
        title="Episode 2",
        audio_url="https://example.com/ep2.mp3",
        published="2025-01-01T00:00:00Z",
    )
    audio_bytes = b"\xff\xfb\x90\x00" * 500
    respx.get("https://example.com/ep2.mp3").respond(
        302, headers={"Location": "https://cdn.example.com/ep2.mp3"}
    )
    respx.get("https://cdn.example.com/ep2.mp3").respond(200, content=audio_bytes)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        path = await download_episode(ep, output_dir=tmp_path, client=client)

    assert path.exists()
    assert path.stat().st_size == len(audio_bytes)
