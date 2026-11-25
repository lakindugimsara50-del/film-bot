"""
tests/test_gpu_and_upload_turbo_optimizations.py

Verification tests for full Kaggle GPU acceleration, 1080p ~2GB HQ NVENC settings,
multi-session C/AES-NI MTProto uploads, decoupled pipeline locks, and overlapping task safety.
"""

import asyncio
import importlib
import io
import os
import sys
import types
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BOT_DIR = os.path.join(_ROOT, "bot")
if _BOT_DIR not in sys.path:
    sys.path.insert(0, _BOT_DIR)

import services.crypto_accel as crypto_accel
import services.downloader as downloader
import services.telegram_upload as telegram_upload
import services.video_service as video_service
from services.queue_service import QueueItem, QueueService
from services.task_tracker import TaskStatus, TaskTracker
from services.upload_pool import TelegramUploadPool


# ---------------------------------------------------------------------------
# 1. Crypto Acceleration & Pyrogram Session Integer Timeouts
# ---------------------------------------------------------------------------

def test_crypto_accel_ige_roundtrip_and_backend_fallback(monkeypatch):
    """Verify AES-256-IGE ECB/C wrappers roundtrip identically and handle missing tgcrypto."""
    import pyrogram.crypto.aes as pyro_aes

    orig_enc = pyro_aes.ige256_encrypt
    orig_dec = pyro_aes.ige256_decrypt
    try:
        py_enc, py_dec = crypto_accel._build_py_ecb_ige()
        assert py_enc is not None and py_dec is not None
        assert crypto_accel._verify_ige_pair(py_enc, py_dec, py_enc, py_dec) is True

        c_enc, c_dec = crypto_accel._try_build_libcrypto_ige()
        if c_enc and c_dec:
            assert crypto_accel._verify_ige_pair(c_enc, c_dec, py_enc, py_dec) is True

        monkeypatch.setattr(crypto_accel, "_INSTALLED_BACKEND", None)
        monkeypatch.setattr(pyro_aes, "ige256_encrypt", None)
        monkeypatch.setattr(pyro_aes, "ige256_decrypt", None)
        monkeypatch.setitem(sys.modules, "tgcrypto", None)
        monkeypatch.setitem(sys.modules, "cryptg", None)

        backend = crypto_accel.install_fast_aes_ige(force=True)
        assert backend in ("openssl_libcrypto", "py_ecb")
        assert callable(pyro_aes.ige256_encrypt)
        assert callable(pyro_aes.ige256_decrypt)

        sample = os.urandom(64)
        key = os.urandom(32)
        iv = os.urandom(32)
        assert pyro_aes.ige256_decrypt(pyro_aes.ige256_encrypt(sample, key, iv), key, iv) == sample
    finally:
        pyro_aes.ige256_encrypt = orig_enc
        pyro_aes.ige256_decrypt = orig_dec


def test_pyrogram_session_timeouts_are_strictly_ints():
    """Session.START_TIMEOUT and Session.WAIT_TIMEOUT must stay int so PingDelayDisconnect never crashes on to_bytes()."""
    from pyrogram.session import Session

    assert isinstance(Session.START_TIMEOUT, int)
    assert isinstance(Session.WAIT_TIMEOUT, int)
    assert Session.WAIT_TIMEOUT == 30


# ---------------------------------------------------------------------------
# 2. Fast Parallel MTProto Upload Scaling on Linux Cloud Runners
# ---------------------------------------------------------------------------

def test_fast_save_file_uses_8_concurrent_sockets_on_cloud_linux(monkeypatch):
    """On Linux cloud runners with >50MB file, fast_save_file should spawn 8 parallel media sessions."""
    async def _run():
        sessions_created = []

        class _MockSession:
            def __init__(self, client, dc_id, auth_key, test_mode, is_media=False):
                self.is_media = is_media
                sessions_created.append(self)

            async def start(self):
                return None

            async def stop(self):
                return None

            async def invoke(self, rpc):
                return None

        fake_client = MagicMock()
        fake_client.loop = asyncio.get_running_loop()
        fake_client.me = types.SimpleNamespace(is_premium=False)
        fake_client.rnd_id = lambda: 987654321
        fake_client.storage = MagicMock()
        fake_client.storage.dc_id = AsyncMock(return_value=5)
        fake_client.storage.auth_key = AsyncMock(return_value=b"k" * 256)
        fake_client.storage.test_mode = AsyncMock(return_value=False)

        payload = io.BytesIO(b"A" * (60 * 1024 * 1024))

        monkeypatch.setattr(telegram_upload, "Session", _MockSession)
        monkeypatch.setenv("KAGGLE_KERNEL_RUN_TYPE", "Interactive")
        monkeypatch.delenv("FAST_UPLOAD_SESSIONS", raising=False)
        monkeypatch.setattr(telegram_upload.os, "name", "posix")
        try:
            result = await asyncio.wait_for(telegram_upload.fast_save_file(fake_client, payload), timeout=10.0)
        finally:
            monkeypatch.undo()
        assert result is not None
        assert len(sessions_created) == 8
        assert all(s.is_media for s in sessions_created)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Multi-GPU Load Balancing, HQ NVENC Options, & Pure GPU CUDA Pipeline
# ---------------------------------------------------------------------------

def test_gpu_device_pool_load_balances_heavy_vs_light_encodes(monkeypatch):
    """Heavy 1080p encode (weight=3) claims GPU 0 while concurrent 720p+480p claim lighter GPU 1."""
    async def _run():
        pool = video_service.GPUDevicePool()
        monkeypatch.setattr(pool, "_discover_devices", lambda: [0, 1, 0, 1])

        chosen = {}
        async with pool.acquire(timeout=2.0, weight=3) as g_1080:
            chosen["1080p"] = g_1080
            async with pool.acquire(timeout=2.0, weight=2) as g_720:
                chosen["720p"] = g_720
                async with pool.acquire(timeout=2.0, weight=1) as g_480:
                    chosen["480p"] = g_480

        assert chosen["1080p"] == 0
        assert chosen["720p"] == 1
        assert chosen["480p"] == 1
        assert pool._gpu_weights[0] == 0
        assert pool._gpu_weights[1] == 0

    asyncio.run(_run())


def test_build_nvenc_encode_opts_turing_hq_and_qres():
    """Verify NVENC HQ encoder preset p4 + tune hq + spatial/temporal AQ + lookahead 16."""
    opts = video_service._build_nvenc_encode_opts(
        v_bitrate_k=2050,
        maxrate_k=2706,
        bufsize_k=4100,
        cq=19,
        preset="p4",
        for_cuda_frames=True,
        caps={"nvenc_hq": True, "nvenc_qres": True},
    )
    assert "-c:v" in opts and opts[opts.index("-c:v") + 1] == "h264_nvenc"
    assert "-preset" in opts and opts[opts.index("-preset") + 1] == "p4"
    assert "-tune" in opts and opts[opts.index("-tune") + 1] == "hq"
    assert "-multipass" in opts and opts[opts.index("-multipass") + 1] == "qres"
    assert "-cq" in opts and opts[opts.index("-cq") + 1] == "19"
    assert "-spatial-aq" in opts and opts[opts.index("-spatial-aq") + 1] == "1"
    assert "-temporal-aq" in opts and opts[opts.index("-temporal-aq") + 1] == "1"
    assert "-rc-lookahead" in opts and opts[opts.index("-rc-lookahead") + 1] == "16"
    assert "-pix_fmt" not in opts  # zero CPU download for CUDA frame pipeline


def test_pure_gpu_cuda_pipeline_builds_overlay_cuda_and_ts_intro_concat(tmp_path, monkeypatch):
    """Verify _execute_pure_gpu_cuda_pipeline keeps badge, 10s trim, and 5s TS intro while running on GPU."""
    async def _run():
        in_vid = tmp_path / "movie_1080p.mp4"
        in_vid.write_bytes(b"\x00" * 4096)
        out_vid = tmp_path / "out_1080p.mp4"
        intro_ts = tmp_path / "intro_1920x1080.ts"
        intro_ts.write_bytes(b"\x47" * 60000)
        badge_png = tmp_path / "badge.png"
        badge_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
        sub_srt = tmp_path / "si.srt"
        sub_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nආයුබෝවන්\n", encoding="utf-8")

        exec_cmds = []

        class _FakeProc:
            def __init__(self, target_out):
                self.returncode = 0
                self.stderr = self
                self._target = target_out
                self._read_once = False

            async def read(self, n=4096):
                if not self._read_once:
                    self._read_once = True
                    return b"frame=120 fps=450.0 time=00:00:05.00 speed=18.5x\n"
                return b""

            async def wait(self):
                with open(self._target, "wb") as f:
                    f.write(b"\x00" * 4096)
                return 0

            async def communicate(self):
                with open(self._target, "wb") as f:
                    f.write(b"\x00" * 8192)
                return b"", b""

        async def _fake_exec(*args, **kwargs):
            cmd = list(args)
            exec_cmds.append(cmd)
            return _FakeProc(cmd[-1])

        class _FakeSlot:
            async def __aenter__(self):
                return 0

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(video_service.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(video_service._GPU_POOL, "acquire", lambda **kw: _FakeSlot())
        monkeypatch.setattr(video_service, "get_or_create_filmzilla_intro_ts", lambda *a, **k: str(intro_ts))

        caps = {
            "ffmpeg_bin": "ffmpeg",
            "hwaccel_cuda": True,
            "scale_cuda": True,
            "overlay_cuda": True,
            "cuda_main_fmt": "nv12",
            "nvenc_hq": True,
            "nvenc_qres": False,
        }
        media_info = {
            "width": 1920,
            "height": 1080,
            "duration": 3600.0,
            "fps_str": "24000/1001",
            "vcodec": "h264",
            "acodec": "aac",
            "sample_rate": 48000,
            "channels": 2,
        }

        res = await video_service._execute_pure_gpu_cuda_pipeline(
            input_path=str(in_vid),
            output_path=str(out_vid),
            ffmpeg_bin="ffmpeg",
            caps=caps,
            media_info=media_info,
            v_bitrate_k=2100,
            maxrate_k=2772,
            bufsize_k=4200,
            cq=19,
            nvenc_preset="p4",
            cover_watermark=True,
            badge_path=str(badge_png),
            trim_start=10.0,
            prepend_intro=True,
            sub_path=str(sub_srt),
        )

        assert res == str(out_vid)
        assert len(exec_cmds) == 2

        gpu_cmd, remux_cmd = exec_cmds
        assert "-ss" in gpu_cmd and gpu_cmd[gpu_cmd.index("-ss") + 1] == "10.0"
        assert "-hwaccel" in gpu_cmd and gpu_cmd[gpu_cmd.index("-hwaccel") + 1] == "cuda"
        assert "-hwaccel_output_format" in gpu_cmd and gpu_cmd[gpu_cmd.index("-hwaccel_output_format") + 1] == "cuda"
        fc = gpu_cmd[gpu_cmd.index("-filter_complex") + 1]
        assert "scale_cuda" in fc and "overlay_cuda" in fc and "repeatlast=1" in fc
        assert "-c:a" in gpu_cmd and gpu_cmd[gpu_cmd.index("-c:a") + 1] == "copy"

        assert "-f" in remux_cmd and remux_cmd[remux_cmd.index("-f") + 1] == "concat"
        assert "-bsf:a" in remux_cmd and remux_cmd[remux_cmd.index("-bsf:a") + 1] == "aac_adtstoasc"
        assert "-movflags" in remux_cmd and "+faststart" in remux_cmd

    asyncio.run(_run())


def test_target_compress_size_preserves_headroom_below_telegram_2gb():
    """TARGET_COMPRESS_SIZE must stay safely under Telegram's 2000 MiB bot file limit."""
    assert video_service.TARGET_COMPRESS_SIZE >= int(1.90 * 1024 * 1024 * 1024)
    assert video_service.TARGET_COMPRESS_SIZE < 2000 * 1024 * 1024


# ---------------------------------------------------------------------------
# 4. Upload Pool Session Prioritization & Admin Fleet Expansion
# ---------------------------------------------------------------------------

def test_upload_pool_prioritizes_idle_healthy_fastest_sessions():
    """Unlocked non-cooldown fastest EMA session beats busy, cooling, and fallback sessions."""
    async def _run():
        pool = TelegramUploadPool()

        def _mk_client(uid: int):
            c = MagicMock()
            c._user_id = uid
            c.name = f"sess_{uid}"
            return c

        busy_c = _mk_client(101)
        cool_c = _mk_client(102)
        slow_idle = _mk_client(103)
        fast_idle = _mk_client(104)
        fb_idle = _mk_client(105)

        await pool.get_client_lock(busy_c).acquire()
        pool._session_cooldown_until[102] = time.time() + 120.0
        pool._session_ema_mbps[103] = 8.0
        pool._session_ema_mbps[104] = 28.0
        pool._session_ema_mbps[105] = 35.0

        ordered = pool._prioritize_idle_healthy([busy_c, cool_c, slow_idle, fast_idle], [fb_idle])
        assert ordered[0] is fast_idle
        assert ordered[1] is slow_idle
        assert ordered[2] is fb_idle
        assert ordered[-1] is cool_c

    asyncio.run(_run())


def test_expand_admin_pool_background_promotes_connected_sessions():
    """expand_admin_pool_background gently promotes non-admin connected sessions up to target_admins."""
    async def _run():
        pool = TelegramUploadPool()
        main_client = MagicMock()
        main_client.is_connected = True
        main_client.get_chat = AsyncMock(return_value=types.SimpleNamespace(username="filmhost_priv"))
        main_client.promote_chat_member = AsyncMock(return_value=True)
        pool._main_client = main_client

        admins = []
        extras = []
        for idx in range(5):
            c = MagicMock()
            c._user_id = 2000 + idx
            c.name = f"acc_{idx}"
            c.is_connected = True
            c.get_chat = AsyncMock(return_value=True)
            if idx < 2:
                admins.append(c)
            else:
                extras.append(c)

        pool.clients = admins + extras
        pool._admin_sessions[-100123] = list(admins)

        await pool.expand_admin_pool_background(-100123, target_admins=5)
        assert len(pool._admin_sessions[-100123]) == 5
        assert main_client.promote_chat_member.await_count == 3

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. Pipeline Overlap, QueueStage Tracking, TaskTracker Multi-Task Cancel, Async Check
# ---------------------------------------------------------------------------

def test_queue_service_pipelined_overlap_respects_cloud_and_stage(monkeypatch):
    """QueueService.can_accept_next_pipelined returns True on cloud only when active item is in 'uploading'."""
    async def _run():
        qs = QueueService()
        item = QueueItem(
            item_id="q1",
            user_id=42,
            query_text="Movie (2026)",
            reply_media=None,
            status_msg=MagicMock(),
            client=MagicMock(),
            title="Movie (2026)",
        )

        async def _sleeper():
            await asyncio.sleep(5)

        t = asyncio.create_task(_sleeper())
        try:
            qs._active_tasks[t] = item
            qs._active_item = item

            # Serial check remains strictly False
            assert qs.can_accept_next() is False

            # Even on Linux cloud, encoding/downloading must NOT overlap
            monkeypatch.setattr("services.queue_service.os.name", "posix")
            monkeypatch.setenv("KAGGLE_KERNEL_RUN_TYPE", "Interactive")
            assert qs.can_accept_next_pipelined() is False

            # Transition to uploading inside active task -> allow Movie N+1
            qs.mark_item_stage(42, "uploading")
            assert item.stage == "uploading"
            assert qs.can_accept_next_pipelined() is True

            # On Windows local desktop, keep strict serialization
            monkeypatch.setattr("services.queue_service.os.name", "nt")
            monkeypatch.delenv("KAGGLE_KERNEL_RUN_TYPE", raising=False)
            assert qs.can_accept_next_pipelined() is False
        finally:
            monkeypatch.undo()
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


def test_task_tracker_cancel_task_cancels_overlapping_tasks_for_user(tmp_path):
    """TaskTracker.cancel_task must cancel ALL active overlapping TaskInfos belonging to the same user."""
    async def _run():
        tt = TaskTracker()

        async def _worker():
            await asyncio.sleep(10)

        t1 = asyncio.create_task(_worker())
        t2 = asyncio.create_task(_worker())

        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()

        info1 = tt.start_task(777, "Movie 1", task=t1)
        info1.temp_dir = str(d1)
        info2 = tt.start_task(777, "Movie 2", task=t2)
        info2.temp_dir = str(d2)

        assert tt.cancel_task(777) is True
        await asyncio.sleep(0.02)

        assert info1.status == TaskStatus.CANCELLED
        assert info2.status == TaskStatus.CANCELLED
        assert t1.cancelled() and t2.cancelled()
        assert not d1.exists() and not d2.exists()

    asyncio.run(_run())


def test_async_is_valid_downloaded_video_delegates_to_thread(monkeypatch, tmp_path):
    """downloader.async_is_valid_downloaded_video checks video cleanly without blocking event loop."""
    async def _run():
        sample = tmp_path / "sample.mp4"
        sample.write_bytes(b"\x00" * 1024)
        called_with = []

        def _fake_sync(fp, min_size_mb=15):
            called_with.append((fp, min_size_mb))
            return True

        monkeypatch.setattr(downloader, "is_valid_downloaded_video", _fake_sync)
        ok = await downloader.async_is_valid_downloaded_video(str(sample), min_size_mb=10)
        assert ok is True
        assert called_with == [(str(sample), 10)]

    asyncio.run(_run())
