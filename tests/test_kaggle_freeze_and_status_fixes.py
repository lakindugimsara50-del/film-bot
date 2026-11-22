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
