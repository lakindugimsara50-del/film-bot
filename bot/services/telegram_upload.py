"""
telegram_upload.py — Download a remote video file and upload it to a private
Telegram channel using a Pyrogram userbot session.

Provides:
  download_and_upload(url, progress_callback) → dict with file_id, stream_url …
  get_file_stream_url(file_id)                → stream URL string
"""

import asyncio
import inspect
import io
import logging
import os
import re
import tempfile
import time

import aiofiles
import httpx
from pyrogram import Client

# Support 64-bit Telegram channel IDs (e.g. -1004325759505)
try:
    import pyrogram.utils
    pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999
    pyrogram.utils.MIN_CHAT_ID = -999999999999
except Exception:
    pass

import config
from config import (
    API_ID,
    API_HASH,
    SESSION_NAME,
    PRIVATE_CHANNEL_ID,
    STREAM_BASE_URL,
)

log = logging.getLogger(__name__)

# How many bytes to buffer while streaming a download to disk
_CHUNK_SIZE = 1024 * 1024  # 1 MB


# ─────────────────────────────────────────────────────────────────────────────
class LowRamFileReader(io.RawIOBase):
    """
    Binary file reader that evicts read chunks from Linux page cache using
    os.posix_fadvise(..., os.POSIX_FADV_DONTNEED).

    Prevents uploaded video files (1GB - 2GB) from accumulating in the Linux page cache,
    avoiding memory pressure and OOM restarts on memory-constrained containers (e.g., Render 512MB).
    Fully compatible with Pyrogram's Client.save_file / send_video / send_document.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.name = os.path.basename(file_path)
        self._file = open(file_path, "rb")
        self._fd = self._file.fileno()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()

    def fileno(self) -> int:
        return self._fd

    def readinto(self, b) -> int:
        pos = self._file.tell()
        n = self._file.readinto(b)
        if n and hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
            try:
                os.posix_fadvise(self._fd, pos, n, os.POSIX_FADV_DONTNEED)
            except Exception:
                pass
        return n

    def read(self, size: int = -1) -> bytes:
        pos = self._file.tell()
        data = self._file.read(size)
        if data and hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
            try:
                os.posix_fadvise(self._fd, pos, len(data), os.POSIX_FADV_DONTNEED)
            except Exception:
                pass
        return data

    def close(self) -> None:
        if not self.closed:
            super().close()
            try:
                self._file.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
async def download_and_upload(
    url: str,
    bot_client: Client = None,
    fallback_chat_id: int = 0,
    progress_callback=None,
) -> dict:
    """
    Download *url* to a temporary file, then upload it to the private Telegram
    channel using the bot client (or userbot).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        # ── 1. Download the file ────────────────────────────────────────────
        local_path = await _download_file(url, tmp_dir, progress_callback)
        file_name = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)
        log.info("Downloaded '%s'  (%d bytes)", file_name, file_size)

        # ── 2. Upload to Telegram private channel ──────────────────────────
        target_chat = PRIVATE_CHANNEL_ID if PRIVATE_CHANNEL_ID != 0 else (fallback_chat_id or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0))
        try:
            target_chat = int(target_chat)
        except (ValueError, TypeError):
            pass

        log.info("Uploading '%s' to private channel %s …", file_name, target_chat)

        if bot_client:
            return await upload_video_file(
                bot_client=bot_client,
                file_path=local_path,
                target_chat=target_chat,
                progress_callback=progress_callback,
                fallback_chat=0,
            )

        # Userbot upload fallback if bot_client not provided
        userbot = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
        )
        async with userbot:
            with LowRamFileReader(local_path) as reader:
                message = await userbot.send_video(
                    chat_id=target_chat,
                    video=reader,
                    file_name=file_name,
                    disable_notification=True,
                )

        file_id: str = message.video.file_id if message.video else (message.document.file_id if message.document else "")
        message_id: int = message.id
        stream_url = get_file_stream_url(file_id)

        log.info("Upload complete. file_id=%s  stream_url=%s", file_id, stream_url)
        return {
            "file_id": file_id,
            "message_id": message_id,
            "file_name": file_name,
            "file_size": file_size,
            "stream_url": stream_url,
        }


async def upload_video_file(
    bot_client: Client,
    file_path: str,
    target_chat: int = 0,
    caption: str = "",
    progress_callback=None,
    fallback_chat: int = 0,
) -> dict:
    """
    Directly upload a local video file to the specified Telegram chat/channel.
    Supports progress callbacks with calculated speed and ETA decoupled from the chunk upload loop.
    Falls back to document upload and/or fallback_chat if channel permissions fail.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found at: {file_path}")

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    target = target_chat if target_chat != 0 else (PRIVATE_CHANNEL_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0))
    try:
        target = int(target)
    except (ValueError, TypeError):
        pass

    log.info("[TelegramUpload] Uploading '%s' (%d bytes) to chat %s", file_name, file_size, target)

    start_time = time.time()
    progress_state = {
        "current": 0,
        "total": file_size,
        "is_done": False,
        "last_reported_bytes": -1,
    }

    async def _pyrogram_progress(current: int, total: int) -> None:
        """
        Fast in-memory update called from Pyrogram's chunk upload loop.
        Does NOT block or make network/Telegram calls in-band.
        """
        progress_state["current"] = current
        progress_state["total"] = total

    async def _progress_monitor() -> None:
        """
        Lightweight background task updating the Telegram message every 3.5 seconds.
        Decoupled from Pyrogram's MTProto chunk upload loop to eliminate event loop contention.
        """
        from services.downloader import format_bytes

        while not progress_state["is_done"]:
            try:
                await asyncio.sleep(3.5)
            except asyncio.CancelledError:
                break

            if not progress_callback:
                continue

            cur = progress_state["current"]
            tot = progress_state["total"]
            if tot <= 0 or cur == progress_state["last_reported_bytes"]:
                continue

            progress_state["last_reported_bytes"] = cur
            now = time.time()
            elapsed = max(0.001, now - start_time)
            speed_bytes = cur / elapsed
            speed_str = f"{format_bytes(speed_bytes)}/s"
            pct = min(100.0, (cur / tot) * 100.0)
            eta_seconds = int((tot - cur) / speed_bytes) if speed_bytes > 0 else 0
            eta_str = f"{eta_seconds}s" if eta_seconds < 60 else f"{eta_seconds // 60}m {eta_seconds % 60}s"

            try:
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(pct, format_bytes(cur), format_bytes(tot), speed_str, eta_str)
                else:
                    progress_callback(pct, format_bytes(cur), format_bytes(tot), speed_str, eta_str)
            except Exception as p_err:
                log.debug("[TelegramUpload] Decoupled progress callback error: %s", p_err)

    async def _do_send(chat_id: int):
        if not bot_client.is_connected:
            try:
                log.info("[TelegramUpload] bot_client not connected, reconnecting...")
                await bot_client.connect()
            except Exception as conn_err:
                log.warning("[TelegramUpload] bot_client.connect() warning: %s", conn_err)

        try:
            resolved = await bot_client.get_chat(chat_id)
            log.info("[TelegramUpload] Confirmed target peer: '%s' (ID: %s)", getattr(resolved, "title", "Channel"), chat_id)
        except Exception as gc_err:
            log.warning("[TelegramUpload] Pre-resolving chat %s warning: %s", chat_id, gc_err)

        with LowRamFileReader(file_path) as reader:
            try:
                return await bot_client.send_video(
                    chat_id=chat_id,
                    video=reader,
                    file_name=file_name,
                    caption=caption,
                    progress=_pyrogram_progress,
                    disable_notification=True,
                )
            except Exception as vid_err:
                log.warning("[TelegramUpload] send_video failed (%s). Falling back to send_document...", vid_err)
                reader.seek(0)
                return await bot_client.send_document(
                    chat_id=chat_id,
                    document=reader,
                    file_name=file_name,
                    caption=caption,
                    force_document=True,
                    progress=_pyrogram_progress,
                    disable_notification=True,
                )

    monitor_task = asyncio.create_task(_progress_monitor())
    try:
        message = await _do_send(target)
    except Exception as exc:
        log.error("[TelegramUpload] Upload to channel %s failed: %s", target, exc, exc_info=True)
        # Only fallback if target was 0/unset; never dump a movie into user DM if channel was specified
        if fallback_chat and target == 0:
            log.warning("[TelegramUpload] Target chat unset. Falling back to chat %s", fallback_chat)
            message = await _do_send(fallback_chat)
        else:
            raise RuntimeError(f"Telegram upload to channel {target} failed: {exc}") from exc
    finally:
        progress_state["is_done"] = True
        monitor_task.cancel()
        try:
            await asyncio.wait_for(monitor_task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    file_id: str = message.video.file_id if message.video else (message.document.file_id if message.document else "")
    message_id: int = message.id
    stream_url = get_file_stream_url(file_id)

    log.info("[TelegramUpload] Upload success. file_id=%s stream_url=%s", file_id, stream_url)
    return {
        "file_id": file_id,
        "message_id": message_id,
        "file_name": file_name,
        "file_size": file_size,
        "stream_url": stream_url,
    }



# ─────────────────────────────────────────────────────────────────────────────
def get_file_stream_url(file_id: str) -> str:
    """
    Build and return the public stream URL for a Telegram file_id.

    Example:
        get_file_stream_url("BQACAgIAA...") →
        "https://stream.yourdomain.workers.dev/stream/BQACAgIAA..."
    """
    base = (STREAM_BASE_URL or "").rstrip("/")
    return f"{base}/stream/{file_id}"


async def get_telegram_direct_url(file_id: str, bot_token: str) -> str:
    """
    Attempt to fetch the direct Telegram file URL via Telegram Bot API getFile.
    Supported by Telegram for files up to 20MB.
    """
    if not bot_token or not file_id:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{bot_token}/getFile",
                params={"file_id": file_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    res = data.get("result", {})
                    file_path = res.get("file_path")
                    if file_path:
                        direct = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                        log.info("Direct Telegram stream URL obtained: %s", direct)
                        return direct
    except Exception as exc:
        log.warning("Could not fetch direct Telegram file URL: %s", exc)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _download_file(url: str, dest_dir: str, progress_callback=None) -> str:
    """
    Route *url* to the appropriate download backend and return the local path.
    """
    if _is_google_drive(url):
        return await _download_gdrive(url, dest_dir)
    if _is_mega(url):
        return await _download_mega(url, dest_dir)
    # Default: direct HTTP download
    return await _download_http(url, dest_dir, progress_callback)


def _is_google_drive(url: str) -> bool:
    return "drive.google.com" in url or "docs.google.com" in url


def _is_mega(url: str) -> bool:
    return "mega.nz" in url or "mega.co.nz" in url


async def _download_http(
    url: str, dest_dir: str, progress_callback=None
) -> str:
    """Stream a direct HTTP/HTTPS URL to disk."""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=None,  # No timeout — large files can take a while
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()

            # Try to derive filename from Content-Disposition or the URL
            cd = resp.headers.get("content-disposition", "")
            name_match = re.search(r'filename="?([^";]+)"?', cd)
            file_name = (
                name_match.group(1).strip()
                if name_match
                else url.split("/")[-1].split("?")[0] or "video.mp4"
            )
            local_path = os.path.join(dest_dir, file_name)

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            async with aiofiles.open(local_path, "wb") as fh:
                async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    await fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        await progress_callback(downloaded, total)

    return local_path


async def _download_gdrive(url: str, dest_dir: str) -> str:
    """Download from Google Drive using the gdown library (runs in executor)."""
    try:
        import gdown  # Lazy import — optional dependency
    except ImportError:
        raise RuntimeError(
            "gdown is required for Google Drive downloads. "
            "Run: pip install gdown"
        )

    loop = asyncio.get_event_loop()
    # gdown.download is synchronous; run it in a thread pool
    local_path = await loop.run_in_executor(
        None,
        lambda: gdown.download(url, output=dest_dir + "/", fuzzy=True, quiet=True),
    )

    if not local_path or not os.path.isfile(local_path):
        raise RuntimeError(f"gdown failed to download: {url}")

    return local_path


async def _download_mega(url: str, dest_dir: str) -> str:
    """Download from Mega.nz using the mega.py library (runs in executor)."""
    try:
        from mega import Mega  # Lazy import — optional dependency
    except ImportError:
        raise RuntimeError(
            "mega.py is required for Mega downloads. "
            "Run: pip install mega.py"
        )

    loop = asyncio.get_event_loop()

    def _sync_download() -> str:
        m = Mega()
        m_client = m.login()  # Anonymous login
        downloaded = m_client.download_url(url, dest_path=dest_dir)
        return downloaded

    local_path = await loop.run_in_executor(None, _sync_download)

    if not local_path or not os.path.isfile(str(local_path)):
        raise RuntimeError(f"mega.py failed to download: {url}")

    return str(local_path)
