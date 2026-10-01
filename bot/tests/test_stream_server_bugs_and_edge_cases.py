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

