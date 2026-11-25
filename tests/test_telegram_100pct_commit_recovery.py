"""
tests/test_telegram_100pct_commit_recovery.py

Verifies fixes for Telegram uploads resetting 100% -> 0% on large files:
1. Pyrogram's frozen 15s default timeouts on Client.invoke / Session.invoke / Session.send
   are un-frozen, and SendMedia commits wait >= 240s instead of prematurely timing out.
2. Transient TimeoutError at 100% retries SendMedia reusing cached chunks (no 0% re-upload).
3. If Telegram already committed the message right after a SendMedia timeout / FILE_PARTS_INVALID,
   channel history inspection recovers it cleanly with zero re-upload and zero duplicates.
4. upload_pool.upload_with_pool checks channel history before rotating to a new session at 100%.
"""

import asyncio
import datetime
import inspect
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

_bot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
if _bot_dir not in sys.path:
    sys.path.insert(0, _bot_dir)

from pyrogram import Client, raw
from pyrogram.session import Session
import services.telegram_upload as telegram_upload
from services.upload_pool import TelegramUploadPool


def test_pyrogram_send_media_timeout_boosted_while_ints_preserved(monkeypatch):
    """Session.WAIT_TIMEOUT remains int 30 while SendMedia gets >=240s RPC commit timeout."""
    assert isinstance(Session.START_TIMEOUT, int)
    assert isinstance(Session.WAIT_TIMEOUT, int)
    assert Session.WAIT_TIMEOUT == 30
    assert 45 in (Session.send.__defaults__ or ())
    assert 45 in (Session.invoke.__defaults__ or ())

    captured = {}

    class _FakeSessionObj:
        async def invoke(self, query, retries, timeout, sleep_threshold):
            captured["query"] = query
            captured["retries"] = retries
            captured["timeout"] = timeout
            return "OK"

    fake_client = MagicMock(spec=Client)
    fake_client.is_connected = True
    fake_client.no_updates = False
    fake_client.takeout_id = None
    fake_client.sleep_threshold = 10
    fake_client.session = _FakeSessionObj()
    fake_client.fetch_peers = AsyncMock()

    send_media_rpc = raw.functions.messages.SendMedia(
        peer=raw.types.InputPeerSelf(),
        media=raw.types.InputMediaEmpty(),
        message="",
        random_id=123456,
    )
    asyncio.run(Client.invoke(fake_client, send_media_rpc))
    assert captured["timeout"] >= 240.0
    assert captured["retries"] >= 4

    # Regular lightweight RPC uses bumped >=45s default (no longer frozen 15s)
    ping_rpc = raw.functions.Ping(ping_id=1)
    asyncio.run(Client.invoke(fake_client, ping_rpc))
    assert captured["timeout"] >= 45


def test_upload_video_file_waits_and_retries_send_media_on_100pct_timeout(monkeypatch, tmp_path):
    """Post-100% TimeoutError on SendMedia reuses cached chunks and never restarts 0% or falls back prematurely."""
    async def _run():
        telegram_upload._SAVED_FILE_CACHE.clear()
        sleep_calls = []

        async def _fake_sleep(sec):
            sleep_calls.append(sec)

        monkeypatch.setattr(telegram_upload.asyncio, "sleep", _fake_sleep)

        vid = tmp_path / "movie_1080p.mp4"
        vid.write_bytes(b"Z" * (600 * 1024))

        impl_uploads = []

        async def _fake_impl(self_c, path, file_id=None, file_part=0, progress=None, progress_args=()):
            impl_uploads.append(str(path))
            if progress:
                progress(600 * 1024, 600 * 1024, *progress_args)
            return {"file_id": 777111}

        monkeypatch.setattr(telegram_upload, "_fast_save_file_impl", _fake_impl)

        msg = MagicMock()
        msg.id = 555
        msg.video = MagicMock(file_id="VID_AFTER_WAIT")
        msg.document = None

        client = MagicMock()
        client.is_connected = True
        client.get_chat = AsyncMock(return_value=MagicMock(title="Ch"))

        async def _empty_hist(*a, **kw):
            if False:
                yield None

        client.get_chat_history = _empty_hist

        sv_calls = 0

        async def _send_video_timeout_then_ok(**kwargs):
            nonlocal sv_calls
            sv_calls += 1
            await telegram_upload.fast_save_file(client, kwargs["video"], progress=kwargs.get("progress"))
            if sv_calls <= 2:
                raise TimeoutError("Request timed out")
            return msg

        client.send_video = AsyncMock(side_effect=_send_video_timeout_then_ok)
        client.send_document = AsyncMock()

        res = await telegram_upload.upload_video_file(client, str(vid), target_chat=-100999)
        assert res["file_id"] == "VID_AFTER_WAIT"
        assert sv_calls == 3
        assert len(impl_uploads) == 1  # Zero 0% chunk re-uploads!
        assert any(s >= 6.0 for s in sleep_calls)
        client.send_document.assert_not_called()

    asyncio.run(_run())


def test_upload_video_file_recovers_already_committed_message_after_timeout(monkeypatch, tmp_path):
    """If Telegram committed the 100% file right as SendMedia timed out, recover message instead of restarting 0%."""
    async def _run():
        telegram_upload._SAVED_FILE_CACHE.clear()
        monkeypatch.setattr(telegram_upload.asyncio, "sleep", AsyncMock())

        vid = tmp_path / "cult_2026_1080p.mp4"
        vid_bytes = b"V" * (700 * 1024)
        vid.write_bytes(vid_bytes)

        impl_uploads = []

        async def _fake_impl(self_c, path, file_id=None, file_part=0, progress=None, progress_args=()):
            impl_uploads.append(str(path))
            if progress:
                progress(len(vid_bytes), len(vid_bytes), *progress_args)
            return {"file_id": 888}

        monkeypatch.setattr(telegram_upload, "_fast_save_file_impl", _fake_impl)

        posted_msg = types.SimpleNamespace(
            id=839,
            date=datetime.datetime.now(),
            video=types.SimpleNamespace(
                file_id="RECOVERED_FID_839",
                file_size=len(vid_bytes),
                file_name="cult_2026_1080p [FilmSub.lk].mp4",
            ),
            document=None,
        )

        async def _hist(*a, **kw):
            yield posted_msg

        client = MagicMock()
        client.is_connected = True
        client.get_chat = AsyncMock(return_value=MagicMock(title="Ch"))
        client.get_chat_history = _hist

        async def _send_video_times_out(**kwargs):
            await telegram_upload.fast_save_file(client, kwargs["video"], progress=kwargs.get("progress"))
            raise TimeoutError("Request timed out")

        client.send_video = AsyncMock(side_effect=_send_video_times_out)
        client.send_document = AsyncMock()

        res = await telegram_upload.upload_video_file(
            client,
            str(vid),
            target_chat=-100999,
            file_name="cult_2026_1080p.mp4",
        )
        assert res["file_id"] == "RECOVERED_FID_839"
        assert res["message_id"] == 839
        assert len(impl_uploads) == 1
        client.send_document.assert_not_called()

    asyncio.run(_run())


def test_upload_pool_recovers_before_rotating_to_second_session(monkeypatch, tmp_path):
    """UploadPool verifies channel history when Session 1 fails after 100%, avoiding Session 2 0% restart."""
    async def _run():
        monkeypatch.setattr(telegram_upload.asyncio, "sleep", AsyncMock())
        pool = TelegramUploadPool()

        vid = tmp_path / "feature_1080p.mp4"
        vid_bytes = b"F" * (650 * 1024)
        vid.write_bytes(vid_bytes)

        s1 = MagicMock()
        s1._user_id = 1
        s1.name = "session_1"
        s1.is_connected = True

        s2 = MagicMock()
        s2._user_id = 2
        s2.name = "session_2"
        s2.is_connected = True

        pool.clients = [s1, s2]
        pool._admin_sessions[-100777] = [s1, s2]
        monkeypatch.setattr(pool, "get_admin_sessions", AsyncMock(return_value=[s1, s2]))

        posted_msg = types.SimpleNamespace(
            id=901,
            date=datetime.datetime.now(),
            video=types.SimpleNamespace(
                file_id="POOL_RECOVERED_901",
                file_size=len(vid_bytes),
                file_name="feature_1080p [FilmSub.lk].mp4",
            ),
            document=None,
        )

        async def _s1_upload_fails_at_100(**kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                await cb(100.0, "650 KB", "650 KB", "10 MB/s", "Finalizing...")
            raise RuntimeError("Telegram upload to channel -100777 failed: Request timed out")

        monkeypatch.setattr(telegram_upload, "upload_video_file", AsyncMock(side_effect=_s1_upload_fails_at_100))

        async def _empty_hist(*a, **kw):
            if False:
                yield None

        async def _found_hist(*a, **kw):
            yield posted_msg

        s1.get_chat_history = _empty_hist
        s2.get_chat_history = _found_hist

        res = await pool.upload_with_pool(
            file_path=str(vid),
            target_chat=-100777,
            quality="1080p",
            file_name="feature_1080p [FilmSub.lk].mp4",
        )
        assert res["file_id"] == "POOL_RECOVERED_901"
        assert res["message_id"] == 901
        # Session 2 was NEVER asked to restart upload from 0%!
        assert telegram_upload.upload_video_file.await_count == 1

    asyncio.run(_run())
