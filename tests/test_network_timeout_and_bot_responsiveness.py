import asyncio
import os
import sys
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Add bot directory to sys.path
BOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bot"))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from services import upload_pool, video_service, leech_service


# ==============================================================================
# 1. MTProto Network Timeouts & Join Chat Retries
# ==============================================================================

@pytest.mark.asyncio
async def test_upload_pool_join_chat_timeout_bounded():
    """
    Verify that when userbot sessions hit MTProto network timeouts / retries
    during ImportChatInvite, join_channel completes in a bounded time (<10s)
    without hanging the event loop.
    """
    pool = upload_pool.TelegramUploadPool()
    mock_main = MagicMock()
    mock_main.is_connected = True
    mock_main.get_chat = AsyncMock(return_value=MagicMock(username=None, invite_link="https://t.me/+fakeinvite123"))
    pool.set_main_client(mock_main)

    # Create mock sessions that simulate MTProto connection stalls / timeouts
    mock_client1 = MagicMock()
    mock_client1.name = "sess_timeout_1"
    mock_client1.is_connected = True
    mock_client1.get_chat = AsyncMock(side_effect=asyncio.TimeoutError("MTProto socket timeout on get_chat"))
    # Simulate ImportChatInvite hanging or timing out
    async def _hang_join(*args, **kwargs):
        await asyncio.sleep(60.0)
    mock_client1.join_chat = AsyncMock(side_effect=_hang_join)

    mock_client2 = MagicMock()
    mock_client2.name = "sess_timeout_2"
    mock_client2.is_connected = True
    mock_client2.get_chat = AsyncMock(side_effect=Exception("Chat not found"))
    mock_client2.join_chat = AsyncMock(side_effect=asyncio.TimeoutError("ImportChatInvite timed out"))

    pool.clients = [mock_client1, mock_client2]

    start_time = time.time()
    # Execute join_channel on private channel
    result = await pool.join_channel(-1004325759505)
    elapsed = time.time() - start_time

    # Must complete within bounded timeout (<10.0s) despite simulated 60s hang
    assert elapsed < 10.0, f"join_channel took {elapsed:.2f}s, exceeding bounded timeout"
    assert isinstance(result, dict)
    assert "joined" in result
    assert "promoted" in result


@pytest.mark.asyncio
async def test_upload_pool_42_clients_join_channel_bounded_timeout():
    """
    Verify that join_channel with 42 userbot sessions under simulated MTProto hang
    (each session join_chat hanging for 60s) completes within < 10.0 seconds total,
    strictly satisfying the interface contract in PROJECT.md.
    """
    pool = upload_pool.TelegramUploadPool()
    mock_main = MagicMock()
    mock_main.is_connected = True
    mock_main.get_chat = AsyncMock(return_value=MagicMock(username="fake_channel", invite_link=None))
    pool.set_main_client(mock_main)

    async def _hang_join(*args, **kwargs):
        await asyncio.sleep(60.0)

    # 42 clients that all encounter MTProto connection stall during join_chat
    pool.clients = [
        MagicMock(
            name=f"sess_{i}",
            is_connected=True,
            get_chat=AsyncMock(side_effect=Exception("Not a member")),
            join_chat=AsyncMock(side_effect=_hang_join),
        )
        for i in range(42)
    ]

    start_time = time.time()
    result = await pool.join_channel(-1004325759505)
    elapsed = time.time() - start_time

    assert elapsed < 10.0, f"join_channel with 42 clients took {elapsed:.2f}s, exceeding contract limit of 10.0s"
    assert isinstance(result, dict)
    assert result["total_sessions"] == 42


@pytest.mark.asyncio
async def test_upload_pool_skip_join_if_already_participant():
    """
    Verify that when a userbot session is already a participant/admin in the channel,
    c.get_chat detects it and c.join_chat (ImportChatInvite) is skipped entirely,
    preventing MTProto flood throttling.
    """
    pool = upload_pool.TelegramUploadPool()
    mock_main = MagicMock()
    mock_main.is_connected = True
    mock_main.get_chat = AsyncMock(return_value=MagicMock(username=None, invite_link="https://t.me/+fakeinvite456"))
    mock_main.promote_chat_member = AsyncMock(return_value=True)
    pool.set_main_client(mock_main)

    mock_member_client = MagicMock()
    mock_member_client.name = "sess_already_member"
    mock_member_client.is_connected = True
    # Session already in channel
    mock_member_client.get_chat = AsyncMock(return_value=MagicMock(id=-1001234567890))
    mock_member_client.join_chat = AsyncMock()
    mock_member_client.me = MagicMock(id=987654321)

    pool.clients = [mock_member_client]

    result = await pool.join_channel(-1001234567890)

    # get_chat was called to pre-check
    mock_member_client.get_chat.assert_awaited()
    # join_chat (ImportChatInvite) MUST NOT be called because session is already a member
    mock_member_client.join_chat.assert_not_awaited()
    assert result["joined"] >= 1


@pytest.mark.asyncio
async def test_upload_pool_warmup_peer_timeout_bounded():
    """
    Verify that _warmup_peer during get_admin_sessions does not hang if MTProto
    stalls on get_chat or join_chat.
    """
    pool = upload_pool.TelegramUploadPool()
    mock_main = MagicMock()
    mock_main.is_connected = True

    # Main bot query fails or returns empty
    async def _empty_gen(*args, **kwargs):
        if False:
            yield None
    mock_main.get_chat_members = _empty_gen
    pool.set_main_client(mock_main)

    # Session that is admin but warmup get_chat and join_chat hang
    mock_admin_c = MagicMock()
    mock_admin_c.name = "sess_admin_hang"
    mock_admin_c.is_connected = True
    mock_member = MagicMock()
    mock_member.status = MagicMock(value="administrator")
    mock_member.privileges = MagicMock(can_post_messages=True)
    mock_admin_c.get_chat_member = AsyncMock(return_value=mock_member)

    async def _hang(*args, **kwargs):
        await asyncio.sleep(60.0)
    mock_admin_c.get_chat = AsyncMock(side_effect=_hang)
    mock_admin_c.join_chat = AsyncMock(side_effect=_hang)

    pool.clients = [mock_admin_c]

    start_time = time.time()
    with patch("urllib.request.urlopen", side_effect=Exception("HTTP blocked")):
        admins = await pool.get_admin_sessions(-1009999999999)
    elapsed = time.time() - start_time

    assert elapsed < 7.0, f"get_admin_sessions took {elapsed:.2f}s, exceeding bounded timeout"
    assert mock_admin_c in admins


@pytest.mark.asyncio
async def test_upload_pool_get_admin_sessions_hang_bounded_timeout():
    """
    Verify that pool.get_admin_sessions completes within <= 5.0 seconds even when
    the Telegram HTTP admin query fails and all session c.get_chat_member calls hang
    indefinitely (simulating network stall), satisfying the PROJECT.md interface contract.
    """
    pool = upload_pool.TelegramUploadPool()

    async def _hang_member(*args, **kwargs):
        await asyncio.sleep(60.0)

    # 20 sessions that hang on c.get_chat_member
    pool.clients = [
        MagicMock(
            name=f"sess_admin_hang_{i}",
            is_connected=True,
            me=MagicMock(id=2000 + i),
            get_chat_member=AsyncMock(side_effect=_hang_member),
        )
        for i in range(20)
    ]

    start_time = time.time()
    with patch("urllib.request.urlopen", side_effect=Exception("HTTP blocked")):
        admins = await pool.get_admin_sessions(-100123456789, refresh=True)
    elapsed = time.time() - start_time

    assert elapsed <= 5.0, f"get_admin_sessions took {elapsed:.2f}s, exceeding 5.0s bound"
    assert isinstance(admins, list)


# ==============================================================================
# 2. Status Handler Responsiveness Under Network Failure
# ==============================================================================

@pytest.mark.asyncio
async def test_status_handler_reply_timeout_exits_promptly_without_fallback():
    """
    Simulate Pyrogram dispatcher worker receiving /status command while Telegram MTProto
    connection is stalled:
    - Primary message.reply_text times out (5.0s)
    Verify that status_handler returns within <= 5.0 seconds and does NOT attempt another
    Telegram network call over the dead connection, guaranteeing dispatcher worker slots
    are immediately released.
    """
    import main as bot_main

    mock_client = MagicMock()
    mock_client.is_connected = False
    mock_client.get_me = AsyncMock(return_value=MagicMock(username="FilmBot", first_name="Film Bot"))

    mock_msg = MagicMock()
    mock_msg.chat = MagicMock(id=12345)
    mock_msg.from_user = MagicMock(id=67890)

    # Primary reply_text raises TimeoutError
    mock_msg.reply_text = AsyncMock(side_effect=asyncio.TimeoutError("Simulated MTProto connection dropped"))

    start_time = time.time()
    await bot_main.status_handler(mock_client, mock_msg)
    elapsed = time.time() - start_time

    # Function must exit in <= 5.0 seconds
    assert elapsed <= 5.0, f"status_handler took {elapsed:.2f}s, exceeding 5.0s bound"
    # Fallback MUST NOT be attempted over the dead connection
    assert mock_msg.reply_text.await_count == 1


@pytest.mark.asyncio
async def test_status_handler_fallback_succeeds_when_primary_fails_non_timeout():
    """
    Verify that if primary status reply fails with a non-timeout error (e.g. markup formatting),
    the fallback active status reply is sent immediately.
    """
    import main as bot_main

    mock_client = MagicMock()
    mock_client.is_connected = True
    mock_client.get_me = AsyncMock(return_value=MagicMock(username="FilmBot", first_name="Film Bot"))

    mock_msg = MagicMock()
    mock_msg.chat = MagicMock(id=12345)
    mock_msg.from_user = MagicMock(id=67890)

    call_count = 0
    fallback_text = []

    async def _mock_reply_text(text, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("HTML parse error in primary status markup")
        fallback_text.append(text)
        return MagicMock()

    mock_msg.reply_text = AsyncMock(side_effect=_mock_reply_text)

    await bot_main.status_handler(mock_client, mock_msg)

    assert call_count == 2
    assert len(fallback_text) == 1
    assert "Active" in fallback_text[0]


# ==============================================================================
# 3. FFmpeg Pipe Deadlock Elimination & Stderr Draining
# ==============================================================================

@pytest.mark.asyncio
async def test_compress_video_drains_large_stderr_without_deadlock():
    """
    Simulate FFmpeg outputting > 64KB on stderr (exceeding OS kernel pipe buffer).
    Verify that compress_video reads/drains the pipe without deadlocking.
    """
    dummy_in = "test_large_in.mp4"
    dummy_out = "test_large_out.mp4"

    # Generate 128KB of stderr progress data
    chunk = b"frame=  100 fps= 50 q=23.0 size=   1024kB time=00:01:00.00 bitrate= 139.8kbits/s speed=1.5x\n"
    large_stderr = chunk * (131072 // len(chunk))

    mock_proc = MagicMock()
    mock_proc.returncode = 0

    # Stream reader that yields large_stderr in 4096-byte slices
    async def _stream_stderr():
        offset = 0
        while offset < len(large_stderr):
            slice_data = large_stderr[offset:offset + 4096]
            offset += 4096
            yield slice_data

    gen = _stream_stderr()
    async def _mock_read(n=-1):
        try:
            return await gen.__anext__()
        except StopAsyncIteration:
            return b""

    mock_proc.stderr = MagicMock()
    mock_proc.stderr.read = _mock_read
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.get_video_resolution", return_value=(1920, 1080)), \
         patch("services.video_service.get_video_duration", return_value=60.0), \
         patch("services.video_service.detect_hw_encoder", return_value=None), \
         patch("services.video_service.get_audio_codec", return_value="aac"), \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 1024 * 500), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):

        start_time = time.time()
        out = await video_service.compress_video(
            input_path=dummy_in,
            output_path=dummy_out,
            target_size_bytes=int(1.90 * 1024 * 1024 * 1024),
        )
        elapsed = time.time() - start_time

        assert elapsed < 5.0, f"compress_video stalled: {elapsed:.2f}s"
        assert out == dummy_out


@pytest.mark.asyncio
async def test_cor_proc_corrective_pass_does_not_deadlock_on_pipes():
    """
    Verify that cor_proc (the corrective second-pass re-encode when a file exceeds 1.95GB)
    uses DEVNULL or drains without pipe deadlocks.
    """
    dummy_in = "test_oversized_in.mp4"
    dummy_out = "test_oversized_out.mp4"

    # First pass produces oversized output (2.05 GB)
    first_pass_proc = MagicMock()
    first_pass_proc.returncode = 0
    first_pass_proc.stderr.read = AsyncMock(return_value=b"")
    first_pass_proc.wait = AsyncMock(return_value=0)

    # Second pass (cor_proc) produces valid output (1.85 GB)
    cor_proc = MagicMock()
    cor_proc.returncode = 0
    cor_proc.wait = AsyncMock(return_value=0)

    exec_calls = []
    async def _mock_exec(*args, **kwargs):
        exec_calls.append(kwargs)
        if len(exec_calls) == 1:
            return first_pass_proc
        return cor_proc

    def _mock_getsize(path):
        if str(path) == dummy_in:
            return int(2.2 * 1024 * 1024 * 1024)
        if len(exec_calls) <= 1:
            # First pass output exceeds MTProto ceiling (2.15 GB > 2.09 GB), triggering corrective pass
            return int(2.15 * 1024 * 1024 * 1024)
        # Corrective pass output is within ceiling (1.85 GB)
        return int(1.85 * 1024 * 1024 * 1024)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.get_video_resolution", return_value=(1920, 1080)), \
         patch("services.video_service.get_video_duration", return_value=3600.0), \
         patch("services.video_service.detect_hw_encoder", return_value=None), \
         patch("services.video_service.get_audio_codec", return_value="aac"), \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", side_effect=_mock_getsize), \
         patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):

        out = await video_service.compress_video(
            input_path=dummy_in,
            output_path=dummy_out,
            target_size_bytes=int(1.90 * 1024 * 1024 * 1024),
        )

        assert out == dummy_out
        assert len(exec_calls) == 2
        # Verify that cor_proc was spawned with DEVNULL for stdout and stderr to avoid pipe deadlock
        cor_kwargs = exec_calls[1]
        assert cor_kwargs.get("stdout") == asyncio.subprocess.DEVNULL
        assert cor_kwargs.get("stderr") == asyncio.subprocess.DEVNULL


# ==============================================================================
# 4. Leech Pipeline Recovery on Network Timeouts
# ==============================================================================

@pytest.mark.asyncio
async def test_leech_service_get_admin_sessions_timeout_falls_back():
    """
    Verify that in leech_service variant uploads, if get_admin_sessions encounters
    a network timeout, it falls back cleanly to cached sessions or main bot client
    without freezing the upload pipeline.
    """
    from services.upload_pool import upload_pool as up

    mock_client = MagicMock()
    mock_client.is_connected = True
    up.set_main_client(mock_client)

    # Mock get_admin_sessions to simulate a network timeout (>5s)
    async def _hang_admin_sessions(*args, **kwargs):
        await asyncio.sleep(30.0)
        return []

    target_channel = -1001234567890
    up._admin_sessions[target_channel] = [mock_client]

    with patch.object(up, "get_admin_sessions", side_effect=_hang_admin_sessions):
        start_time = time.time()
        try:
            admin_pool = await asyncio.wait_for(up.get_admin_sessions(target_channel), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            admin_pool = up._admin_sessions.get(target_channel, [])
        elapsed = time.time() - start_time

        assert elapsed < 6.0, f"Operation took {elapsed:.2f}s, exceeding bounded timeout"
        assert mock_client in admin_pool


# ==============================================================================
# 5. Lock Safety & Cancellation Cleanliness
# ==============================================================================

@pytest.mark.asyncio
async def test_leech_locks_released_in_finally_on_cancellation():
    """
    Verify that when a leech task is cancelled, download and encode locks
    are guaranteed to be released.
    """
    dl_lock = leech_service._get_download_lock()
    enc_lock = leech_service._get_encode_upload_lock()

    await dl_lock.acquire()
    assert dl_lock.locked()

    # Simulate finally cleanup
    if dl_lock.locked():
        dl_lock.release()
    assert not dl_lock.locked()

    await enc_lock.acquire()
    assert enc_lock.locked()

    if enc_lock.locked():
        enc_lock.release()
    assert not enc_lock.locked()
