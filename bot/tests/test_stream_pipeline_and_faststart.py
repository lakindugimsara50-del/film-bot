"""
test_stream_pipeline_and_faststart.py — Deep verification tests for:
1. FastStart (+faststart) Remux in Bot Pipeline (video_service.apply_faststart)
2. Multi-Connection Parallel MTProto Chunk Pipelining (session_pool.stream_media_chunks)
3. 16MB RAM Header Cache and HTTP 206 / Edge Cache Headers (stream_server.py)
"""

import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = REPO_ROOT / "bot"
for p in (str(REPO_ROOT), str(BOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.video_service import apply_faststart
from streaming.session_pool import CHUNK_SIZE, TelegramStreamPool
import streaming.stream_server as stream_server


@pytest.mark.asyncio
async def test_apply_faststart_invokes_ffmpeg_correctly(tmp_path):
    in_file = tmp_path / "sample.mp4"
    in_file.write_bytes(b"testmp4data" * 100)
    out_file = tmp_path / "out_faststart.mp4"

    captured_cmds = []

    class FakeProc:
        returncode = 0
        async def wait(self):
            out_file.write_bytes(b"faststart_mp4_bytes")
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        return FakeProc()

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await apply_faststart(str(in_file), str(out_file))

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert "-movflags" in cmd and cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert str(in_file) in cmd
    assert str(out_file) in cmd


@pytest.mark.asyncio
async def test_apply_faststart_missing_file_returns_false(tmp_path):
    missing_file = tmp_path / "does_not_exist.mp4"
    out_file = tmp_path / "out.mp4"

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"):
        ok = await apply_faststart(str(missing_file), str(out_file))
    assert ok is False


@pytest.mark.asyncio
async def test_session_pool_parallel_pipelined_chunks():
    pool = TelegramStreamPool()

    # Create mock clients
    mock_client1 = MagicMock()
    mock_client1.is_connected = True
    mock_client2 = MagicMock()
    mock_client2.is_connected = True

    chunk_data = {
        0: b"A" * CHUNK_SIZE,
        1: b"B" * CHUNK_SIZE,
        2: b"C" * CHUNK_SIZE,
        3: b"D" * CHUNK_SIZE,
    }

    async def fake_stream_media(media, offset=0, limit=1):
        yield chunk_data.get(offset, b"Z" * CHUNK_SIZE)

    mock_client1.stream_media = MagicMock(side_effect=fake_stream_media)
    mock_client2.stream_media = MagicMock(side_effect=fake_stream_media)

    pool.clients = [mock_client1, mock_client2]

    # Stream bytes from middle of chunk 0 (offset 500) to end of chunk 2 (start of chunk 3)
    start = 500
    end = (2 * CHUNK_SIZE) + 1000  # covers chunks 0, 1, 2
    expected_length = end - start + 1

    chunks_received = []
    async for chunk in pool.stream_media_chunks("fake_file_id", start, end):
        chunks_received.append(chunk)

    total_bytes = b"".join(chunks_received)
    assert len(total_bytes) == expected_length
    # First chunk starts with 'A' (offset 500)
    assert total_bytes.startswith(b"A" * 100)
    # Middle chunk contains 'B'
    assert b"B" * 100 in total_bytes
    # Last chunk contains 'C'
    assert total_bytes.endswith(b"C" * 100)


@pytest.mark.asyncio
async def test_session_pool_single_chunk_slicing():
    pool = TelegramStreamPool()
    mock_client = MagicMock()
    mock_client.is_connected = True

    async def fake_stream_media(media, offset=0, limit=1):
        yield b"0123456789" * 1000

    mock_client.stream_media = MagicMock(side_effect=fake_stream_media)
    pool.clients = [mock_client]

    start = 5
    end = 24  # 20 bytes
    chunks = []
    async for c in pool.stream_media_chunks("fake_file_id", start, end):
        chunks.append(c)

    combined = b"".join(chunks)
    assert len(combined) == 20
    assert combined == (b"0123456789" * 1000)[start:end + 1]


@pytest.mark.asyncio
async def test_16mb_header_cache_save_and_retrieve():
    stream_server._HEADER_CACHE.clear()

    key = "test_chat:100"
    part1 = b"X" * (4 * 1024 * 1024)   # 4 MiB
    part2 = b"Y" * (8 * 1024 * 1024)   # 8 MiB

    await stream_server._save_to_header_cache(key, part1, offset=0)
    cached_first = await stream_server._get_from_header_cache(key, 0, (4 * 1024 * 1024) - 1)
    assert cached_first == part1

    # Extend cache with second part at offset 4 MiB
    await stream_server._save_to_header_cache(key, part2, offset=len(part1))
    full_cached = await stream_server._get_from_header_cache(key, 0, (12 * 1024 * 1024) - 1)
    assert full_cached == part1 + part2
    assert len(full_cached) == 12 * 1024 * 1024


@pytest.mark.asyncio
async def test_stream_status_reflects_16mb_capacity():
    status = await stream_server.stream_status()
    assert status["max_cache_mb"] == stream_server.MAX_HEADER_CACHE_SIZE * 16
    assert status["chunk_size_bytes"] == 1024 * 1024


def test_parse_range_helper():
    file_size = 50 * 1024 * 1024  # 50 MB
    start, end = stream_server._parse_range("bytes=0-1048575", file_size)
    assert start == 0
    assert end == 1048575

    start2, end2 = stream_server._parse_range(None, file_size)
    assert start2 == 0
    assert end2 == file_size - 1

    start3, end3 = stream_server._parse_range("bytes=1000-", file_size)
    assert start3 == 1000
    assert end3 == file_size - 1
