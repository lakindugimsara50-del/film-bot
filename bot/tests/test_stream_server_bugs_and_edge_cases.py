"""
test_stream_server_bugs_and_edge_cases.py — Deep verification tests for:
1. HTTP 206 Partial Content & Byte-Range Requests (RFC 7233):
   - Initial probe (bytes=0-1)
   - Open-ended range (bytes=0-, bytes=5000-)
   - Middle seek (bytes=52428800-104857599)
   - Suffix byte ranges (bytes=-500)
   - Out-of-bounds ranges returning 416 Range Not Satisfiable (Content-Range: bytes */TOTAL)
   - Inverted and zero-length ranges returning 416
2. Client Disconnects & Scrubbing:
   - Cancellation / ConnectionResetError / ClientDisconnect in _cached_stream_generator
3. Multi-Session Pipelining & Concurrency:
   - Round-robin candidate rotation across pool clients
   - Timeout and candidate fallback in _fetch_chunk
   - Sliding window task cancellation cleanup
4. Instant Non-Blocking Health Checks:
   - GET and HEAD to /health, /ping, /stream/health, /stream/ping
"""

import asyncio
from pathlib import Path
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from starlette.requests import ClientDisconnect
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = REPO_ROOT / "bot"
for p in (str(REPO_ROOT), str(BOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from streaming.session_pool import CHUNK_SIZE, TelegramStreamPool
import streaming.stream_server as stream_server
from streaming.stream_server import RangeNotSatisfiableError


# ── 1. HTTP 206 Byte-Range & RFC 7233 Parsing Tests ──────────────────────────

def test_parse_range_initial_probe():
    file_size = 50 * 1024 * 1024
    start, end = stream_server._parse_range("bytes=0-1", file_size)
    assert start == 0
    assert end == 1


def test_parse_range_open_ended():
    file_size = 100 * 1024 * 1024
    start, end = stream_server._parse_range("bytes=0-", file_size)
    assert start == 0
    assert end == file_size - 1

    start2, end2 = stream_server._parse_range("bytes=52428800-", file_size)
    assert start2 == 52428800
    assert end2 == file_size - 1


def test_parse_range_middle_seek():
    file_size = 200 * 1024 * 1024
    start, end = stream_server._parse_range("bytes=52428800-104857599", file_size)
    assert start == 52428800
    assert end == 104857599


def test_parse_range_suffix():
    file_size = 50 * 1024 * 1024
    # Last 500 bytes
    start, end = stream_server._parse_range("bytes=-500", file_size)
    assert start == file_size - 500
    assert end == file_size - 1


def test_parse_range_out_of_bounds_raises_416():
    file_size = 1000
    with pytest.raises(RangeNotSatisfiableError):
        stream_server._parse_range("bytes=5000-", file_size)

    with pytest.raises(RangeNotSatisfiableError):
        stream_server._parse_range("bytes=1000-2000", file_size)

    with pytest.raises(RangeNotSatisfiableError):
        stream_server._parse_range("bytes=500-200", file_size)

    with pytest.raises(RangeNotSatisfiableError):
        stream_server._parse_range("bytes=-0", file_size)

    with pytest.raises(RangeNotSatisfiableError):
        stream_server._parse_range("bytes=0-10", 0)


@pytest.mark.asyncio
async def test_stream_channel_message_416_response():
    file_size = 10 * 1024 * 1024
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "movie.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {"range": "bytes=99999999-"}
        req.method = "GET"
        resp = await stream_server.stream_channel_message("-100123", 1, req)

        assert resp.status_code == 416
        assert resp.headers.get("Content-Range") == f"bytes */{file_size}"
        assert resp.headers.get("Accept-Ranges") == "bytes"


@pytest.mark.asyncio
async def test_stream_by_file_id_416_response():
    file_size = 5 * 1024 * 1024
    req = MagicMock(spec=Request)
    req.headers = {"range": "bytes=6000000-7000000"}
    req.method = "GET"
    resp = await stream_server.stream_by_file_id("fake_id", req, size=file_size)

    assert resp.status_code == 416
    assert resp.headers.get("Content-Range") == f"bytes */{file_size}"
    assert resp.headers.get("Accept-Ranges") == "bytes"


# ── 2. Client Disconnects & Scrubbing Tests ──────────────────────────────────

@pytest.mark.asyncio
async def test_cached_stream_generator_handles_connection_reset_gracefully():
    stream_server._HEADER_CACHE.clear()
    key = "chat_reset:200"

    async def fake_chunks(*args, **kwargs):
        yield b"CHUNK_1"
        raise ConnectionResetError("Connection lost")

    with patch.object(stream_server.stream_pool, "stream_media_chunks", side_effect=fake_chunks):
        gen = stream_server._cached_stream_generator(key, "media", 0, 1000)
        chunks = []
        async for c in gen:
            chunks.append(c)
        assert chunks == [b"CHUNK_1"]


@pytest.mark.asyncio
async def test_cached_stream_generator_handles_client_disconnect_gracefully():
    stream_server._HEADER_CACHE.clear()
    key = "chat_disconnect:201"

    async def fake_chunks(*args, **kwargs):
        yield b"CHUNK_1"
        raise ClientDisconnect()

    with patch.object(stream_server.stream_pool, "stream_media_chunks", side_effect=fake_chunks):
        gen = stream_server._cached_stream_generator(key, "media", 0, 1000)
        chunks = []
        async for c in gen:
            chunks.append(c)
        assert chunks == [b"CHUNK_1"]


# ── 3. Multi-Session Pipelining & Concurrency Tests ──────────────────────────

@pytest.mark.asyncio
async def test_session_pool_rotates_candidates_by_chunk_idx():
    pool = TelegramStreamPool()

    client0 = MagicMock()
    client0.is_connected = True
    client0.name = "client0"

    client1 = MagicMock()
    client1.is_connected = True
    client1.name = "client1"

    client2 = MagicMock()
    client2.is_connected = True
    client2.name = "client2"

    pool.clients = [client0, client1, client2]
    pool._main_client = client0

    called_clients = []

    def make_stream_media(c_name):
        async def _stream(media, offset=0, limit=1):
            called_clients.append((offset, c_name))
            yield b"X" * CHUNK_SIZE
        return _stream

    client0.stream_media = MagicMock(side_effect=make_stream_media("client0"))
    client1.stream_media = MagicMock(side_effect=make_stream_media("client1"))
    client2.stream_media = MagicMock(side_effect=make_stream_media("client2"))

    # Fetch chunk 0 -> should try client0 first
    await pool._fetch_chunk("dummy_media", 0)
    assert called_clients[-1] == (0, "client0")

    # Fetch chunk 1 -> should rotate to client1 first
    await pool._fetch_chunk("dummy_media", 1)
    assert called_clients[-1] == (1, "client1")

    # Fetch chunk 2 -> should rotate to client2 first
    await pool._fetch_chunk("dummy_media", 2)
    assert called_clients[-1] == (2, "client2")


@pytest.mark.asyncio
async def test_session_pool_fallback_on_client_failure():
    pool = TelegramStreamPool()

    failing_client = MagicMock()
    failing_client.is_connected = True
    failing_client.name = "failing_client"

    async def fail_stream(media, offset=0, limit=1):
        raise RuntimeError("MTProto connection dropped")
        yield b""

    failing_client.stream_media = MagicMock(side_effect=fail_stream)

    backup_client = MagicMock()
    backup_client.is_connected = True
    backup_client.name = "backup_client"

    async def ok_stream(media, offset=0, limit=1):
        yield b"SUCCESS_CHUNK"

    backup_client.stream_media = MagicMock(side_effect=ok_stream)

    pool.clients = [failing_client, backup_client]
    pool._main_client = backup_client

    # Chunk 0 tries failing_client first, fails, then falls back to backup_client
    data = await pool._fetch_chunk("dummy_media", 0)
    assert data == b"SUCCESS_CHUNK"


@pytest.mark.asyncio
async def test_session_pool_sliding_window_cancels_cleanly_on_early_break():
    pool = TelegramStreamPool()

    client = MagicMock()
    client.is_connected = True

    async def fast_stream(media, offset=0, limit=1):
        yield b"DATA" * (CHUNK_SIZE // 4)

    client.stream_media = MagicMock(side_effect=fast_stream)
    pool.clients = [client]
    pool._main_client = client

    # Request range covering 4 chunks
    start = 0
    end = 4 * CHUNK_SIZE - 1

    chunks_read = 0
    async for c in pool.stream_media_chunks("dummy_media", start, end):
        chunks_read += 1
        if chunks_read == 1:
            # Client aborts / seeks after 1 chunk
            break

    assert chunks_read == 1


# ── 4. Instant Health Checks ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_ping_and_health_instant_responses():
    res = await stream_server.stream_ping()
    assert res["status"] == "pong"
    assert res["ready"] is True
    assert res["service"] == "stream"


def test_parse_range_case_insensitive():
    file_size = 50 * 1024 * 1024
    start, end = stream_server._parse_range("BYTES=100-500", file_size)
    assert start == 100
    assert end == 500


@pytest.mark.asyncio
async def test_download_channel_message_head_request():
    file_size = 20 * 1024 * 1024
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "sample_movie.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {}
        req.method = "HEAD"
        resp = await stream_server.download_channel_message("-100123", 42, req)

        assert resp.status_code == 200
        assert resp.headers.get("Content-Length") == str(file_size)
        assert resp.headers.get("Accept-Ranges") == "bytes"
        assert 'attachment; filename="sample_movie.mp4"' in resp.headers.get("Content-Disposition", "")


@pytest.mark.asyncio
async def test_session_pool_cooldown_on_floodwait():
    pool = TelegramStreamPool()

    client0 = MagicMock()
    client0.is_connected = True
    client0.name = "client0"

    client1 = MagicMock()
    client1.is_connected = True
    client1.name = "client1"

    pool.clients = [client0, client1]
    pool._main_client = client0

    from pyrogram.errors import FloodWait

    async def flood_stream(media, offset=0, limit=1):
        fw = FloodWait(value=10)
        fw.value = 10
        raise fw
        yield b""

    async def ok_stream(media, offset=0, limit=1):
        yield b"RECOVERY_CHUNK"

    client0.stream_media = MagicMock(side_effect=flood_stream)
    client1.stream_media = MagicMock(side_effect=ok_stream)

    # Chunk 0 hits client0 with FloodWait, sets cooldown, and falls back to client1
    data = await pool._fetch_chunk("dummy_media", 0)
    assert data == b"RECOVERY_CHUNK"
    # Verify client0 is on cooldown
    assert pool._client_cooldowns.get(client0, 0) > time.time()


@pytest.mark.asyncio
async def test_stream_media_chunks_invalid_range_guard():
    pool = TelegramStreamPool()
    chunks = []
    # start > end
    async for c in pool.stream_media_chunks("dummy_media", 100, 50):
        chunks.append(c)
    assert chunks == []

    # negative start
    async for c in pool.stream_media_chunks("dummy_media", -10, 50):
        chunks.append(c)
    assert chunks == []


# ── 5. Direct Download Full File & RFC 7233 Compliance Tests ────────────────

@pytest.mark.asyncio
async def test_stream_channel_message_download_returns_200_without_range():
    """Non-range download must return HTTP 200 OK, full Content-Length, and NO Content-Range (RFC 7233)."""
    file_size = 561202527  # ~535 MB
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "Furious_S01E06.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {}
        req.query_params = {"dl": "1"}
        req.url = MagicMock()
        req.url.path = "/stream/channel/-1004325759505/354"
        req.method = "GET"

        resp = await stream_server.stream_channel_message("-1004325759505", 354, req, dl=1)

        assert resp.status_code == 200
        assert resp.headers.get("Content-Length") == str(file_size)
        assert resp.headers.get("Accept-Ranges") == "bytes"
        assert "Content-Range" not in resp.headers
        assert 'attachment; filename="Furious_S01E06.mp4"' in resp.headers.get("Content-Disposition", "")


@pytest.mark.asyncio
async def test_stream_channel_message_download_with_open_range_never_capped_at_8mb():
    """Download requests with open-ended Range (bytes=0-) must serve full file size, not 8MB cap."""
    file_size = 561202527
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "Furious_S01E06.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {"range": "bytes=0-"}
        req.query_params = {"dl": "1"}
        req.url = MagicMock()
        req.url.path = "/stream/channel/-1004325759505/354"
        req.method = "GET"

        resp = await stream_server.stream_channel_message("-1004325759505", 354, req, dl=1)

        assert resp.status_code == 206
        assert resp.headers.get("Content-Length") == str(file_size)
        assert resp.headers.get("Content-Range") == f"bytes 0-{file_size - 1}/{file_size}"
        assert 'attachment; filename="Furious_S01E06.mp4"' in resp.headers.get("Content-Disposition", "")


@pytest.mark.asyncio
async def test_stream_channel_message_download_resuming_from_offset():
    """Resuming download from byte 50,000,000 must serve to the end of the file."""
    file_size = 561202527
    start_offset = 50000000
    expected_len = file_size - start_offset
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "Furious_S01E06.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {"range": f"bytes={start_offset}-"}
        req.query_params = {"dl": "1"}
        req.url = MagicMock()
        req.url.path = "/stream/channel/-1004325759505/354"
        req.method = "GET"

        resp = await stream_server.stream_channel_message("-1004325759505", 354, req, dl=1)

        assert resp.status_code == 206
        assert resp.headers.get("Content-Length") == str(expected_len)
        assert resp.headers.get("Content-Range") == f"bytes {start_offset}-{file_size - 1}/{file_size}"


@pytest.mark.asyncio
async def test_stream_by_file_id_download_returns_200_and_full_size():
    """stream_by_file_id with dl=1 must return 200 OK and Content-Disposition."""
    file_size = 300 * 1024 * 1024
    req = MagicMock(spec=Request)
    req.headers = {}
    req.query_params = {"dl": "1"}
    req.url = MagicMock()
    req.url.path = "/stream/file/AgACAgIAAxkBAAICam..."
    req.method = "GET"

    resp = await stream_server.stream_by_file_id("AgACAgIAAxkBAAICam", req, size=file_size)

    assert resp.status_code == 200
    assert resp.headers.get("Content-Length") == str(file_size)
    assert "Content-Range" not in resp.headers
    assert "attachment;" in resp.headers.get("Content-Disposition", "")


@pytest.mark.asyncio
async def test_download_by_file_id_endpoint():
    """Verify download_by_file_id route serves full file with attachment Content-Disposition."""
    file_size = 250 * 1024 * 1024
    req = MagicMock(spec=Request)
    req.headers = {}
    req.query_params = {}
    req.url = MagicMock()
    req.url.path = "/stream/download/TestFileId123"
    req.method = "GET"

    resp = await stream_server.download_by_file_id("TestFileId123", req, size=file_size)

    assert resp.status_code == 200
    assert resp.headers.get("Content-Length") == str(file_size)
    assert "attachment;" in resp.headers.get("Content-Disposition", "")
    assert "Content-Range" not in resp.headers


@pytest.mark.asyncio
async def test_stream_channel_message_download_detected_by_downloader_ua():
    """Downloader user-agents (IDM, curl, aria2) must trigger full download without 8MB cap."""
    file_size = 400 * 1024 * 1024
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "Inception.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {
            "range": "bytes=0-",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (IDM 6.42/2)",
        }
        req.query_params = {}
        req.url = MagicMock()
        req.url.path = "/stream/channel/-100123/456"
        req.method = "GET"

        resp = await stream_server.stream_channel_message("-100123", 456, req)

        assert resp.status_code == 206
        assert resp.headers.get("Content-Length") == str(file_size)
        assert resp.headers.get("Content-Range") == f"bytes 0-{file_size - 1}/{file_size}"
        assert 'attachment; filename="Inception.mp4"' in resp.headers.get("Content-Disposition", "")


@pytest.mark.asyncio
async def test_stream_channel_message_download_detected_by_sec_fetch_dest():
    """Browser download navigation (Sec-Fetch-Dest: document) must trigger full download."""
    file_size = 600 * 1024 * 1024
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "Interstellar.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {
            "sec-fetch-dest": "document",
        }
        req.query_params = {}
        req.url = MagicMock()
        req.url.path = "/stream/channel/-100123/789"
        req.method = "GET"

        resp = await stream_server.stream_channel_message("-100123", 789, req)

        assert resp.status_code == 200
        assert resp.headers.get("Content-Length") == str(file_size)
        assert "Content-Range" not in resp.headers
        assert 'attachment; filename="Interstellar.mp4"' in resp.headers.get("Content-Disposition", "")


def test_method3_ddl_resolve_direct_url():
    """Verify method3_ddl.resolve_direct_url resolves PixelDrain (standard and for_download) and GDrive."""
    from services.scrapers import method3_ddl

    # PixelDrain view link default
    pd_url = "https://pixeldrain.com/u/abc12345"
    assert method3_ddl.resolve_direct_url(pd_url) == "https://pixeldrain.com/api/file/abc12345"
    assert method3_ddl.resolve_direct_url(pd_url, for_download=True) == "https://pixeldrain.com/api/file/abc12345?download"

    # PixelDrain api file link
    pd_api = "https://pixeldrain.com/api/file/xyz987"
    assert method3_ddl.resolve_direct_url(pd_api) == "https://pixeldrain.com/api/file/xyz987"
    assert method3_ddl.resolve_direct_url(pd_api, for_download=True) == "https://pixeldrain.com/api/file/xyz987?download"

    # Google Drive view link
    gd_url = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OIvE2up0/view"
    assert "drive.usercontent.google.com/download" in method3_ddl.resolve_direct_url(gd_url)
    assert "confirm=t" in method3_ddl.resolve_direct_url(gd_url)


@pytest.mark.asyncio
async def test_downloader_ensures_dl_and_pixeldrain_params():
    """Verify download_http automatically appends dl=1 to /stream/ URLs and ?download to pixeldrain."""
    from services import downloader

    captured_urls = []

    async def fake_download_httpx(url, *args, **kwargs):
        captured_urls.append(url)
        return "/tmp/fake.mp4"

    with patch.object(downloader, "find_aria2c", return_value=None), \
         patch.object(downloader, "_download_httpx", side_effect=fake_download_httpx):

        # /stream/ URL without query
        await downloader.download_http("https://example.com/stream/channel/-100123/456", "/tmp")
        assert captured_urls[-1] == "https://example.com/stream/channel/-100123/456?dl=1"

        # /stream/ URL with existing query
        await downloader.download_http("https://example.com/stream/channel/-100123/456?token=abc", "/tmp")
        assert captured_urls[-1] == "https://example.com/stream/channel/-100123/456?token=abc&dl=1"

        # PixelDrain API URL
        await downloader.download_http("https://pixeldrain.com/api/file/abc12345", "/tmp")
        assert captured_urls[-1] == "https://pixeldrain.com/api/file/abc12345?download"


@pytest.mark.asyncio
async def test_downloader_range_resumption_on_interrupted_stream(tmp_path):
    """Verify _download_httpx resumes seamlessly with Range header when stream cuts off prematurely."""
    import os
    import tempfile
    from services import downloader

    dest_dir = str(tmp_path)
    url = "https://example.com/stream/video.mp4"
    total_size = 1000  # 1000 bytes

    # Stream attempt 1: yields first 400 bytes, then drops
    # Stream attempt 2: receives Range: bytes=400-, yields remaining 600 bytes
    call_count = 0
    requested_ranges = []

    class FakeResponse:
        def __init__(self, start_b, end_b):
            self.status_code = 206 if start_b > 0 else 200
            self.headers = {
                "content-length": str(end_b - start_b),
                "content-range": f"bytes {start_b}-{end_b - 1}/{total_size}",
                "content-disposition": 'attachment; filename="test_resumed.mp4"',
            }
            self._start = start_b
            self._end = end_b

        def raise_for_status(self):
            pass

        async def aiter_bytes(self, chunk_size):
            yield b"X" * (self._end - self._start)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.headers = kwargs.get("headers", {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-length": str(total_size)}
            return resp

        def stream(self, method, stream_url):
            nonlocal call_count
            call_count += 1
            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)

            class StreamContext:
                async def __aenter__(self_ctx):
                    if call_count == 1:
                        # Initial request without range or bytes=0- -> only yields 400 bytes (e.g. proxy cutoff)
                        return FakeResponse(0, 400)
                    else:
                        # Resumed request from byte 400 -> yields remaining 600 bytes
                        return FakeResponse(400, 1000)

                async def __aexit__(self_ctx, *args):
                    pass

            return StreamContext()

    with patch("httpx.AsyncClient", FakeAsyncClient), \
         patch.object(downloader, "is_valid_downloaded_video", return_value=True):

        res_path = await downloader._download_httpx(url, dest_dir, "test_resumed.mp4", None)

        assert os.path.exists(res_path)
        assert os.path.getsize(res_path) == 1000
        assert call_count == 2
        assert requested_ranges[0] == ""
        assert requested_ranges[1] == "bytes=400-"




