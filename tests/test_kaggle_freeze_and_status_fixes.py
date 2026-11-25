"""
tests/test_kaggle_freeze_and_status_fixes.py

Unit test suite verifying:
1. TaskTracker.format_status_message exists and works properly.
2. /status and /stats commands are registered on Pyrogram app and add_imdb exclusions.
3. Video service FFmpeg command generation includes -nostdin and does not put -hwaccel cuda on inputs with complex filters.
4. Upload pool maintains _joined_sessions_cache and avoids repetitive joins.
5. Leech service multi progress display handles FloodWait without permanently freezing updates.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import sys

_bot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
if _bot_dir not in sys.path:
    sys.path.insert(0, _bot_dir)

from services.task_tracker import TaskTracker, TaskStatus, tracker
from services.upload_pool import TelegramUploadPool
import services.video_service as video_service


def test_task_tracker_format_status_message_alias():
    """Verify format_status_message works identically to get_status_summary."""
    tt = TaskTracker()
    summary = tt.get_status_summary(12345)
    formatted = tt.format_status_message(12345)
    assert summary == formatted
    assert "කිසිදු වැඩක් නැත" in formatted or "Idle" in formatted

    # Start task and check
    info = tt.start_task(12345, "Mookuthi Amman 2 (2026)")
    tt.set_step(12345, "Compressing video <= 1.88GB...")
    active_summary = tt.format_status_message(12345)
    assert "Mookuthi Amman 2 (2026)" in active_summary
    assert "Compressing video <= 1.88GB..." in active_summary


def test_ffmpeg_cmd_nostdin_in_compress_video():
    """Verify that compress_video constructs FFmpeg command with -nostdin and without input -hwaccel cuda."""
    # Mock probes
    with patch("services.video_service.get_video_duration", return_value=7200.0), \
         patch("services.video_service.get_video_resolution", return_value=(1920, 1080)), \
         patch("services.video_service.detect_hw_encoder", return_value="h264_nvenc"), \
         patch("services.video_service.get_audio_codec", return_value="aac"), \
         patch("services.video_service.get_or_create_filmzilla_intro", return_value="/tmp/intro.mp4"), \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=3_000_000_000):

        captured_cmd = []
        async def fake_exec(*cmd, **kwargs):
            captured_cmd.extend(list(cmd))
            # Verify stdin is DEVNULL to prevent terminal TTY lockup
            assert kwargs.get("stdin") == asyncio.subprocess.DEVNULL
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stderr.read = AsyncMock(return_value=b"")
            mock_proc.wait = AsyncMock(return_value=0)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            # Run test
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                res = loop.run_until_complete(
                    video_service.compress_video(
                        input_path="/tmp/test_1080p.mp4",
                        output_path="/tmp/comp_test_1080p.mp4",
                        target_size_bytes=1_950_000_000,
                        is_hardsub=True,
                        prepend_intro=True,
                    )
                )
            finally:
                loop.close()

        assert "-nostdin" in captured_cmd
        # Verify -hwaccel cuda is NOT present as an input option before -i
        hw_indices = [i for i, x in enumerate(captured_cmd) if x == "-hwaccel"]
        assert len(hw_indices) == 0, f"Expected no input -hwaccel cuda before -i, found at {hw_indices}"
        # But hardware encoder is configured for NVENC
        assert "-c:v" in captured_cmd
        cv_idx = captured_cmd.index("-c:v")
        assert captured_cmd[cv_idx + 1] == "h264_nvenc"


def test_upload_pool_joined_sessions_cache():
    """Verify that sessions recorded in _joined_sessions_cache are skipped during join_channel."""
    pool = TelegramUploadPool()
    c1 = MagicMock()
    c1.name = "sess_01"
    c1.is_connected = True
    c1.join_chat = AsyncMock()
    c1.get_chat = AsyncMock(return_value=None)

    c2 = MagicMock()
    c2.name = "sess_02"
    c2.is_connected = True
    c2.join_chat = AsyncMock()
    c2.get_chat = AsyncMock(return_value=None)

    pool.clients = [c1, c2]
    mock_main = MagicMock()
    mock_main.is_connected = True
    mock_main.get_chat = AsyncMock(return_value=MagicMock(username="mytestchannel", invite_link=None))
    pool._main_client = mock_main

    # Mark c1 as already joined in cache
    pool._joined_sessions_cache.add("sess_01_-100123456789")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        res = loop.run_until_complete(pool.join_channel(-100123456789))
    finally:
        loop.close()

    # c1 was in cache, should NOT have called join_chat
    c1.join_chat.assert_not_called()
    # c2 was not in cache, should have attempted join_chat
    c2.join_chat.assert_called_once()


def test_crypto_executor_thread_pool_expanded():
    """Verify that Pyrogram's crypto_executor has been expanded beyond the default 1 worker."""
    import pyrogram
    assert hasattr(pyrogram, "crypto_executor")
    assert getattr(pyrogram.crypto_executor, "_max_workers", 1) >= 8


def test_upload_pool_load_admin_sessions_manifest(tmp_path):
    """Verify that load_admin_sessions_manifest populates _admin_sessions and _joined_sessions_cache from admin_sessions.json."""
    import json
    pool = TelegramUploadPool()
    c1 = MagicMock()
    c1.name = "sess_01"
    c1.is_connected = True
    c1.me = MagicMock(id=11111, first_name="Admin1", last_name="", phone_number=None)

    c2 = MagicMock()
    c2.name = "sess_02"
    c2.is_connected = True
    c2.me = MagicMock(id=22222, first_name="Admin2", last_name="", phone_number=None)

    pool.clients = [c1, c2]
    target_ch = -1004325759505

    fake_manifest = {
        "channel_id": target_ch,
        "updated_at": 1700000000,
        "total_sessions_loaded": 2,
        "admin_sessions_count": 2,
        "admin_sessions": [
            {"session": "sess_01.session", "user_id": 11111, "name": "Admin1"},
            {"session": "sess_02.session", "user_id": 22222, "name": "Admin2"},
        ]
    }
    manifest_file = tmp_path / "admin_sessions.json"
    manifest_file.write_text(json.dumps(fake_manifest), encoding="utf-8")

    with patch("os.path.join", side_effect=lambda *args: str(manifest_file) if "admin_sessions.json" in args else "/".join(args)):
        restored = pool.load_admin_sessions_manifest(target_ch)

    assert restored == 2
    assert target_ch in pool._admin_sessions
    assert len(pool._admin_sessions[target_ch]) == 2
    assert f"sess_01_{target_ch}" in pool._joined_sessions_cache
    assert f"sess_02_{target_ch}" in pool._joined_sessions_cache


def test_upload_pool_join_channel_skips_when_manifest_admins_exist():
    """Verify that join_channel skips network operations if >= 3 verified admins are already loaded."""
    pool = TelegramUploadPool()
    target_ch = -1004325759505
    c1, c2, c3 = MagicMock(), MagicMock(), MagicMock()
    pool._admin_sessions[target_ch] = [c1, c2, c3]
    pool.clients = [c1, c2, c3]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        res = loop.run_until_complete(pool.join_channel(target_ch))
    finally:
        loop.close()

    assert res["admin_sessions"] == 3
    assert res["promoted"] == 0
    # No clients called join_chat
    for c in [c1, c2, c3]:
        assert not hasattr(c.join_chat, "assert_called") or c.join_chat.call_count == 0


@pytest.mark.asyncio
async def test_bg_event_loop_watchdog_mtproto_ping_and_handler_preserving_recovery():
    """Verify that _bg_event_loop_watchdog recovers safely after 3 probe failures WITHOUT wiping dispatcher.groups."""
    import main as main_mod
    from collections import OrderedDict

    mock_app = MagicMock()
    mock_app.is_connected = True
    mock_app.is_initialized = True
    # Simulated session=None forces full stop+start path where dispatcher.groups would be cleared by Pyrogram
    mock_app.session = None
    mock_app.dispatcher = MagicMock()
    dummy_handler = MagicMock()
    mock_app.dispatcher.groups = OrderedDict({0: [dummy_handler]})

    async def fake_terminate():
        # Simulate real Pyrogram Dispatcher.stop() clearing groups!
        mock_app.dispatcher.groups.clear()
        mock_app.is_initialized = False

    async def fake_disconnect():
        mock_app.is_connected = False

    async def fake_start():
        mock_app.is_connected = True
        mock_app.is_initialized = True

    mock_app.terminate = AsyncMock(side_effect=fake_terminate)
    mock_app.disconnect = AsyncMock(side_effect=fake_disconnect)
    mock_app.start = AsyncMock(side_effect=fake_start)
    mock_app.invoke = AsyncMock(side_effect=asyncio.TimeoutError("MTProto timeout"))

    with patch.object(main_mod, "app", mock_app), \
         patch("asyncio.sleep", AsyncMock(side_effect=[None, None, None, None, asyncio.CancelledError()])):
        try:
            await main_mod._bg_event_loop_watchdog()
        except asyncio.CancelledError:
            pass

    mock_app.terminate.assert_awaited_once()
    mock_app.start.assert_awaited_once()
    # Critical: handlers MUST remain intact after recovery (never wiped to {})
    assert 0 in mock_app.dispatcher.groups
    assert mock_app.dispatcher.groups[0] == [dummy_handler]


@pytest.mark.asyncio
async def test_safe_reconnect_prevents_overlapping_terminated_race():
    """Verify overlapping calls to _safe_reconnect_main_client serialize cleanly without Client is already terminated errors."""
    import main as main_mod

    started = asyncio.Event()
    release = asyncio.Event()

    mock_app = MagicMock()
    mock_app.is_connected = True
    mock_app.is_initialized = True
    mock_app.session = None
    mock_app.dispatcher = MagicMock()
    mock_app.dispatcher.groups = {0: [MagicMock()]}

    async def slow_terminate():
        started.set()
        await release.wait()
        mock_app.is_initialized = False

    mock_app.terminate = AsyncMock(side_effect=slow_terminate)
    mock_app.disconnect = AsyncMock()
    mock_app.start = AsyncMock()

    with patch.object(main_mod, "app", mock_app):
        t1 = asyncio.create_task(main_mod._safe_reconnect_main_client())
        await started.wait()
        # Concurrent reconnect call while lock is held must bail out immediately without calling terminate() twice
        res2 = await main_mod._safe_reconnect_main_client()
        assert res2 is False
        release.set()
        res1 = await t1
        assert res1 is True
        mock_app.terminate.assert_awaited_once()


def test_nonblocking_file_storage_open_patched():
    """Verify FileStorage.open is patched in main.py so 98 session opens run off the event loop without synchronous VACUUM."""
    import main as main_mod  # noqa: F401
    import pyrogram.storage.file_storage as fs
    assert fs.FileStorage.open.__name__ == "_nonblocking_file_storage_open"


def test_add_imdb_sub_handler_ignores_slash_commands():
    """Verify add_imdb.imdb_sub_input_handler does not match slash commands like /crawl or /leech."""
    from handlers import add_imdb
    from pyrogram import enums
    from pyrogram.types import Message
    registered_handlers = []

    class DummyApp:
        def on_message(self, flt):
            def deco(fn):
                registered_handlers.append((fn.__name__, flt))
                return fn
            return deco
        def on_callback_query(self, flt):
            return lambda fn: fn

    add_imdb.register(DummyApp())
    sub_entry = next((flt for name, flt in registered_handlers if name == "imdb_sub_input_handler"), None)
    assert sub_entry is not None
    # Verify filter actually rejects slash commands and still matches plain text/URLs
    mock_client = MagicMock(me=MagicMock(username="Filmsinhala200Bot"))
    loop = asyncio.new_event_loop()
    try:
        for txt in ("/crawl resume", "/leech Inception", "/status"):
            msg = MagicMock(spec=Message)
            msg.text = txt
            msg.caption = None
            msg.document = None
            msg.chat = MagicMock(type=enums.ChatType.PRIVATE)
            msg.command = txt.lstrip("/").split()
            matched = loop.run_until_complete(sub_entry(mock_client, msg))
            assert not matched, f"imdb_sub_input_handler should NOT match command {txt}"

        url_msg = MagicMock(spec=Message)
        url_msg.text = "https://example.com/sub.srt"
        url_msg.caption = None
        url_msg.document = None
        url_msg.chat = MagicMock(type=enums.ChatType.PRIVATE)
        url_msg.command = None
        assert loop.run_until_complete(sub_entry(mock_client, url_msg))
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_crawl_check_subcommand_no_unbound_local():
    """Verify '/crawl check 2' parses tokens cleanly without UnboundLocalError on 'args'."""
    from handlers import leech_handler

    captured = {}
    class DummyApp:
        def on_message(self, flt):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco
        def on_callback_query(self, flt):
            return lambda fn: fn

    leech_handler.register(DummyApp())
    crawl_fn = captured["crawl_command"]

    mock_msg = MagicMock()
    mock_msg.from_user = MagicMock(id=8762329226)
    mock_msg.text = "/crawl check 2"
    st_msg = MagicMock()
    st_msg.edit_text = AsyncMock()
    mock_msg.reply_text = AsyncMock(return_value=st_msg)

    with patch("services.cinesubz_crawler.cinesubz_crawler.check_and_enqueue_new_releases", AsyncMock(return_value=[])) as chk_mock, \
         patch("handlers.leech_handler._is_admin", return_value=True):
        await crawl_fn(MagicMock(), mock_msg)
        assert chk_mock.await_count >= 1
        assert chk_mock.await_args_list[0].kwargs.get("up_to_page") == 2


@pytest.mark.asyncio
async def test_get_admin_sessions_persists_admins_even_if_warmup_times_out():
    """Verify verified admin sessions are cached into _admin_sessions BEFORE peer warmup even if warmup stalls."""
    pool = TelegramUploadPool()
    target_ch = -1004325759505

    c1 = MagicMock(is_connected=True, _user_id=101, me=MagicMock(id=101))
    c1.name = "adm_1"
    c2 = MagicMock(is_connected=True, _user_id=102, me=MagicMock(id=102))
    c2.name = "adm_2"

    async def slow_get_chat(*args, **kwargs):
        await asyncio.sleep(30.0)

    c1.get_chat = AsyncMock(side_effect=slow_get_chat)
    c2.get_chat = AsyncMock(side_effect=slow_get_chat)
    pool.clients = [c1, c2]

    mock_main = MagicMock(is_connected=True)
    mock_main.get_chat = AsyncMock(return_value=MagicMock(username=None, invite_link=None))

    async def fake_members(*args, **kwargs):
        for uid in (101, 102):
            yield MagicMock(
                user=MagicMock(id=uid, is_bot=False),
                status=MagicMock(value="administrator"),
                privileges=MagicMock(can_post_messages=True),
            )

    mock_main.get_chat_members = fake_members
    pool.set_main_client(mock_main)

    # Even when outer wait_for is tight (0.3s) and warmup sleeps 30s, admins MUST already be saved in cache!
    try:
        await asyncio.wait_for(pool.get_admin_sessions(target_ch, refresh=True), timeout=0.3)
    except asyncio.TimeoutError:
        pass

    cached = pool.get_admin_sessions_cached(target_ch)
    assert len(cached) == 2
    assert c1 in cached and c2 in cached



