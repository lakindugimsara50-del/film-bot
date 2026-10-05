"""
test_stream_pipeline_and_faststart.py — Deep verification tests for:
1. FastStart (+faststart) Remux in Bot Pipeline (video_service.apply_faststart)
2. Multi-Connection Parallel MTProto Chunk Pipelining (session_pool.stream_media_chunks)
3. 16MB RAM Header Cache and HTTP 206 / Edge Cache Headers (stream_server.py)
"""

import asyncio
import time
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
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
async def test_stream_status_reflects_cache_capacity():
    status = await stream_server.stream_status()
    # max_cache_mb = MAX_HEADER_CACHE_SIZE * (MAX_HEADER_CACHE_BYTES // (1024*1024))
    expected_mb = stream_server.MAX_HEADER_CACHE_SIZE * (stream_server.MAX_HEADER_CACHE_BYTES // (1024 * 1024))
    assert status["max_cache_mb"] == expected_mb
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


@pytest.mark.asyncio
async def test_download_channel_message_serves_full_file_without_content_range():
    file_size = 50 * 1024 * 1024  # 50 MiB
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "sample_movie.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        req = MagicMock(spec=Request)
        req.headers = {}
        req.method = "GET"
        resp = await stream_server.download_channel_message("-100123456", 1, req)

        assert resp.status_code == 200
        assert resp.headers.get("Content-Length") == str(file_size)
        assert "Content-Range" not in resp.headers
        assert "attachment" in resp.headers.get("Content-Disposition", "")


@pytest.mark.asyncio
async def test_stream_channel_message_range_and_prebuffer_headers():
    file_size = 50 * 1024 * 1024
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "movie.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info):
        # 1. Request with explicit range
        req1 = MagicMock(spec=Request)
        req1.headers = {"range": "bytes=0-1048575"}
        req1.method = "GET"
        resp1 = await stream_server.stream_channel_message("-100123456", 1, req1)
        assert resp1.status_code == 206
        assert resp1.headers.get("Content-Range").startswith("bytes 0-")

        # 2. Request without range -> serves 8MB initial pre-buffer with 206 Partial Content
        req2 = MagicMock(spec=Request)
        req2.headers = {}
        req2.method = "GET"
        resp2 = await stream_server.stream_channel_message("-100123456", 1, req2)
        assert resp2.status_code == 206
        assert resp2.headers.get("Content-Length") == str(8 * 1024 * 1024)
        assert resp2.headers.get("Content-Range") == f"bytes 0-{8 * 1024 * 1024 - 1}/{file_size}"


@pytest.mark.asyncio
async def test_stream_by_file_id_uses_header_cache():
    stream_server._HEADER_CACHE.clear()
    file_id = "test_file_id_xyz"
    cache_key = f"file:{file_id}"

    # Pre-cache 1 MB
    await stream_server._save_to_header_cache(cache_key, b"Q" * (1024 * 1024), offset=0)

    req = MagicMock(spec=Request)
    req.headers = {"range": "bytes=0-1048575"}
    req.method = "GET"
    resp = await stream_server.stream_by_file_id(file_id, req, size=10 * 1024 * 1024)
    assert resp.status_code == 206
    assert resp.headers.get("Content-Range") == f"bytes 0-{8 * 1024 * 1024 - 1}/{10 * 1024 * 1024}"


@pytest.mark.asyncio
async def test_stream_safari_probe_range_not_expanded():
    """Verify that Safari/iOS Range: bytes=0-1 probe requests are NOT expanded to 8MB."""
    stream_server._HEADER_CACHE.clear()
    file_id = "test_safari_probe_id"
    cache_key = f"file:{file_id}"
    await stream_server._save_to_header_cache(cache_key, b"SAFARI_PROBE_DATA_12345678", offset=0)

    req = MagicMock(spec=Request)
    req.headers = {"range": "bytes=0-1"}
    req.method = "GET"
    resp = await stream_server.stream_by_file_id(file_id, req, size=10 * 1024 * 1024)
    assert resp.status_code == 206
    assert resp.headers.get("Content-Range") == f"bytes 0-1/{10 * 1024 * 1024}"
    assert resp.headers.get("Content-Length") == "2"


@pytest.mark.asyncio
async def test_apply_faststart_fallback_on_initial_failure(tmp_path):
    in_file = tmp_path / "in.mkv"
    in_file.write_bytes(b"mkvcontent" * 50)
    out_file = tmp_path / "out.mp4"

    call_count = 0

    class FakeProcFail:
        returncode = 1
        async def wait(self):
            return 1
        def terminate(self): pass
        def kill(self): pass

    class FakeProcSuccess:
        returncode = 0
        async def wait(self):
            out_file.write_bytes(b"fallback_mp4_bytes")
            return 0
        def terminate(self): pass
        def kill(self): pass

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeProcFail()
        return FakeProcSuccess()

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await apply_faststart(str(in_file), str(out_file))

    assert ok is True
    assert call_count == 2
    assert out_file.read_bytes() == b"fallback_mp4_bytes"


@pytest.mark.asyncio
async def test_lru_cache_ordering_with_move_to_end():
    stream_server._HEADER_CACHE.clear()

    # Insert 3 keys
    await stream_server._save_to_header_cache("key1", b"1" * 100, offset=0)
    await stream_server._save_to_header_cache("key2", b"2" * 100, offset=0)
    await stream_server._save_to_header_cache("key3", b"3" * 100, offset=0)

    # Access key1 via get -> key1 should be moved to the end (most recently used)
    await stream_server._get_from_header_cache("key1", 0, 99)

    keys = list(stream_server._HEADER_CACHE.keys())
    assert keys == ["key2", "key3", "key1"]


@pytest.mark.asyncio
async def test_cached_stream_generator_preserves_bytes_on_cancellation():
    stream_server._HEADER_CACHE.clear()
    key = "chat_cancel:101"

    async def fake_stream_chunks(*args, **kwargs):
        yield b"CHUNK_ALPHA"
        yield b"CHUNK_BETA"
        raise asyncio.CancelledError()

    with patch.object(stream_server.stream_pool, "stream_media_chunks", side_effect=fake_stream_chunks):
        gen = stream_server._cached_stream_generator(key, "dummy_media", 0, 1000)
        received = []
        try:
            async for chunk in gen:
                received.append(chunk)
        except asyncio.CancelledError:
            pass

        assert len(received) == 2
        # Verify that accumulated chunks were saved in header cache despite cancellation
        cached = await stream_server._get_from_header_cache(key, 0, len(b"CHUNK_ALPHACHUNK_BETA") - 1)
        assert cached == b"CHUNK_ALPHACHUNK_BETA"


@pytest.mark.asyncio
async def test_get_client_user_id_fallback_to_storage():
    """Verify get_client_user_id extracts UID from storage when c.me is None."""
    pool = stream_server.stream_pool
    dummy_client = MagicMock()
    dummy_client._user_id = None
    dummy_client.me = None
    dummy_storage = MagicMock()
    dummy_storage.user_id = AsyncMock(return_value="1967609462")
    dummy_client.storage = dummy_storage

    uid = await pool.get_client_user_id(dummy_client)
    assert uid == 1967609462
    assert dummy_client._user_id == 1967609462


@pytest.mark.asyncio
async def test_fetch_chunk_selects_admin_userbot_when_c_me_is_none():
    """Verify _fetch_chunk prioritizes admin userbot even when c.me is None."""
    pool = stream_server.stream_pool
    admin_uid = 1967609462

    admin_client = MagicMock()
    admin_client.is_connected = True
    admin_client.me = None
    admin_client._user_id = admin_uid
    admin_client.name = "admin_userbot_session"

    async def fake_stream_media(media, offset=0, limit=1):
        yield b"USERBOT_CHUNK_DATA_1MB"

    admin_client.stream_media = fake_stream_media
    admin_client.get_messages = AsyncMock(return_value=MagicMock())

    main_client = MagicMock()
    main_client.is_connected = True
    main_client.me = MagicMock(id=99999999)
    main_client._user_id = 99999999

    pool.clients = [admin_client]
    pool._main_client = main_client
    pool._admin_uids_cache = {-1004325759505: (time.time(), {admin_uid})}

    dummy_chat = MagicMock()
    dummy_chat.id = -1004325759505
    dummy_msg = MagicMock()
    dummy_msg.chat = dummy_chat
    dummy_msg._client = main_client
    dummy_msg.id = 280

    chunk = await pool._fetch_chunk(dummy_msg, 0)
    assert chunk == b"USERBOT_CHUNK_DATA_1MB"


@pytest.mark.asyncio
async def test_warmup_channel_message_populates_header_cache():
    """Verify warmup_channel_message fetches chunks and populates _HEADER_CACHE."""
    key = stream_server._get_cache_key("-1004325759505", 280)
    async with stream_server._CACHE_LOCK:
        stream_server._HEADER_CACHE.pop(key, None)
    stream_server._WARMING_UP.discard(key)

    mock_msg = MagicMock()
    fake_info = {
        "message": mock_msg,
        "file_size": 20 * 1024 * 1024,
        "mime_type": "video/mp4",
        "file_name": "test.mp4",
    }

    async def fake_stream_chunks(msg, start, end):
        # Yield two 1MB chunks
        yield b"CHUNK_0_WARMUP_DATA_" + b"0" * (1024 * 1024 - 20)
        yield b"CHUNK_1_WARMUP_DATA_" + b"1" * (1024 * 1024 - 20)

    with patch.object(stream_server.stream_pool, "get_media_info", AsyncMock(return_value=fake_info)), \
         patch.object(stream_server.stream_pool, "stream_media_chunks", side_effect=fake_stream_chunks):
        res = await stream_server.warmup_channel_message("-1004325759505", 280, target_bytes=2 * 1024 * 1024)
        assert res is True
        # Allow background task to execute
        await asyncio.sleep(0.1)

    async with stream_server._CACHE_LOCK:
        assert key in stream_server._HEADER_CACHE
        cached = stream_server._HEADER_CACHE[key]
        assert len(cached) >= 2 * 1024 * 1024
        assert cached.startswith(b"CHUNK_0_WARMUP_DATA_")


@pytest.mark.asyncio
async def test_warmup_endpoint_triggers_warmup():
    """Verify HTTP GET /stream/warmup/{chat_id}/{message_id} returns warming status."""
    with patch("streaming.stream_server.warmup_channel_message", AsyncMock(return_value=True)):
        from starlette.testclient import TestClient
        client = TestClient(stream_server.app)
        resp = client.get("/stream/warmup/-1004325759505/280")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "warming"
        assert data["chat_id"] == "-1004325759505"
        assert data["message_id"] == 280


@pytest.mark.asyncio
async def test_stream_pool_admin_status_diagnostics():
    """Verify stream_pool.get_status() includes admin_clients_count and active_admin_sessions."""
    pool = TelegramStreamPool()
    admin_c1 = MagicMock()
    admin_c1.is_connected = True
    admin_c2 = MagicMock()
    admin_c2.is_connected = False
    bot_c = MagicMock()
    bot_c.is_connected = True

    pool.clients = [bot_c, admin_c1, admin_c2]
    pool._admin_clients = [admin_c1, admin_c2]

    st = pool.get_status()
    assert st["total_clients"] == 3
    assert st["connected_clients"] == 2
    assert st["admin_clients_count"] == 2
    assert st["active_admin_sessions"] == 1
    assert st["sessions_supported"] == "up_to_100"


@pytest.mark.asyncio
async def test_explicit_closed_range_not_expanded_to_min_serve():
    """Verify RFC 7233 compliance: explicit closed range (e.g. 1MB) is not expanded to 8MB."""
    file_size = 100 * 1024 * 1024
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "movie.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info), \
         patch.object(stream_server.stream_pool, "stream_media_chunks", return_value=MagicMock()):
        req = MagicMock(spec=Request)
        req.headers = {"range": "bytes=0-1048575"}  # 1 MiB requested
        req.method = "GET"
        resp = await stream_server.stream_channel_message("-100123", 1, req)

        assert resp.status_code == 206
        assert resp.headers.get("Content-Range") == f"bytes 0-1048575/{file_size}"
        assert resp.headers.get("Content-Length") == "1048576"


@pytest.mark.asyncio
async def test_open_ended_range_served_min_serve():
    """Verify open-ended range (e.g. bytes=0-) serves first 8 MiB slice for rapid burst playback."""
    file_size = 100 * 1024 * 1024
    dummy_info = {
        "file_size": file_size,
        "message": MagicMock(),
        "mime_type": "video/mp4",
        "file_name": "movie.mp4",
    }
    with patch.object(stream_server.stream_pool, "get_media_info", return_value=dummy_info), \
         patch.object(stream_server.stream_pool, "stream_media_chunks", return_value=MagicMock()):
        req = MagicMock(spec=Request)
        req.headers = {"range": "bytes=0-"}
        req.method = "GET"
        resp = await stream_server.stream_channel_message("-100123", 1, req)

        assert resp.status_code == 206
        expected_end = (8 * 1024 * 1024) - 1
        assert resp.headers.get("Content-Range") == f"bytes 0-{expected_end}/{file_size}"
        assert resp.headers.get("Content-Length") == str(8 * 1024 * 1024)


@pytest.mark.asyncio
async def test_fetch_chunk_direct_persistent_session_success():
    """Verify _fetch_chunk returns bytes directly from _fetch_chunk_direct without calling stream_media."""
    pool = TelegramStreamPool()
    client = MagicMock()
    client.is_connected = True
    client.name = "test_admin_client"
    pool.clients = [client]
    pool._admin_clients = [client]

    mock_msg = MagicMock()
    mock_msg.chat.id = -1004325759505
    mock_msg.id = 280

    with patch.object(pool, "_get_client_media", AsyncMock(return_value=mock_msg)), \
         patch.object(pool, "_fetch_chunk_direct", AsyncMock(return_value=b"DIRECT_PERSISTENT_CHUNK_DATA")):
        chunk = await pool._fetch_chunk(mock_msg, 0)
        assert chunk == b"DIRECT_PERSISTENT_CHUNK_DATA"
        # stream_media should NOT be called when direct fetch succeeds
        client.stream_media.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_chunk_handles_file_reference_expired_and_refreshes():
    """Verify _fetch_chunk catches FileReferenceExpired, refreshes media, and returns chunk."""
    from pyrogram.errors import FileReferenceExpired
    pool = TelegramStreamPool()
    client = MagicMock()
    client.is_connected = True
    client.name = "test_admin_client"
    pool.clients = [client]
    pool._admin_clients = [client]

    mock_msg = MagicMock()
    mock_msg.chat.id = -1004325759505
    mock_msg.id = 280

    call_count = 0
    async def mock_fetch_direct(c, media, idx):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise FileReferenceExpired()
        return b"FRESH_REF_CHUNK_DATA"

    with patch.object(pool, "_get_client_media", AsyncMock(return_value=mock_msg)) as mock_get_media, \
         patch.object(pool, "_fetch_chunk_direct", side_effect=mock_fetch_direct):
        chunk = await pool._fetch_chunk(mock_msg, 0)
        assert chunk == b"FRESH_REF_CHUNK_DATA"
        assert call_count == 2
        # Verify force_refresh=True was requested on second get_media call
        mock_get_media.assert_any_call(client, mock_msg, force_refresh=True)


@pytest.mark.asyncio
async def test_stream_media_chunks_concurrency_tuned_to_six():
    """Verify concurrency is capped at 6 workers even with 26 connected admin userbots."""
    pool = TelegramStreamPool()
    # Create 26 mock admin clients
    admin_clients = [MagicMock(is_connected=True, name=f"admin_{i}") for i in range(26)]
    pool.clients = list(admin_clients)
    pool._admin_clients = list(admin_clients)

    mock_msg = MagicMock()
    mock_msg.chat.id = -1004325759505
    mock_msg.id = 280

    concurrent_launches = 0
    max_concurrent = 0

    async def fake_fetch(media, idx):
        nonlocal concurrent_launches, max_concurrent
        concurrent_launches += 1
        max_concurrent = max(max_concurrent, concurrent_launches)
        await asyncio.sleep(0.01)
        concurrent_launches -= 1
        return b"X" * CHUNK_SIZE

    with patch.object(pool, "_fetch_chunk", side_effect=fake_fetch):
        # Request 20 chunks (20 MB)
        chunks = []
        async for chunk in pool.stream_media_chunks(mock_msg, 0, (20 * CHUNK_SIZE) - 1):
            chunks.append(chunk)

        assert len(chunks) == 20
        # Maximum concurrent workers should be capped around concurrency + 2 <= 8
        assert max_concurrent <= 8


@pytest.mark.asyncio
async def test_media_info_cache_hits_and_ttl():
    """Verify get_media_info caches results to prevent repeated Telegram RPC calls."""
    pool = TelegramStreamPool()
    mock_main = MagicMock()
    mock_main.is_connected = True
    pool._main_client = mock_main

    mock_msg = MagicMock()
    mock_msg.empty = False
    mock_media = MagicMock()
    mock_media.file_size = 50 * 1024 * 1024
    mock_media.file_name = "test_movie.mp4"
    mock_media.mime_type = "video/mp4"
    mock_msg.video = mock_media
    mock_msg.document = None

    mock_main.get_messages = AsyncMock(return_value=mock_msg)

    # First call: should query main client and cache the result
    info1 = await pool.get_media_info("-1004325759505", 280)
    assert info1["file_size"] == 50 * 1024 * 1024
    assert mock_main.get_messages.call_count == 1

    # Second call: should serve from cache without calling get_messages
    info2 = await pool.get_media_info("-1004325759505", 280)
    assert info2["file_size"] == 50 * 1024 * 1024
    assert mock_main.get_messages.call_count == 1

    # Third call with force_refresh=True: should bypass cache
    info3 = await pool.get_media_info("-1004325759505", 280, force_refresh=True)
    assert info3["file_size"] == 50 * 1024 * 1024
    assert mock_main.get_messages.call_count == 2


@pytest.mark.asyncio
async def test_warmup_channel_stream_endpoint():
    """Verify /stream/warmup/{chat_id}/{message_id} endpoint triggers background warmup."""
    with patch.object(stream_server, "warmup_channel_message", AsyncMock(return_value=True)) as mock_warmup:
        res = await stream_server.warmup_channel_stream("-1004325759505", 280)
        assert res["status"] == "warming"
        assert res["chat_id"] == "-1004325759505"
        assert res["message_id"] == 280
        mock_warmup.assert_called_once_with("-1004325759505", 280)





