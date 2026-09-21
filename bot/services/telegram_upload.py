"""
telegram_upload.py — Download a remote video file and upload it to a private
Telegram channel using a Pyrogram userbot session.

Provides:
  download_and_upload(url, progress_callback) → dict with file_id, stream_url …
  get_file_stream_url(file_id)                → stream URL string
"""

import asyncio
import logging
import os
import re
import tempfile

import aiofiles
import httpx
from pyrogram import Client

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
        log.info("Uploading '%s' to private channel %s …", file_name, PRIVATE_CHANNEL_ID)

        def _pyrogram_progress(current: int, total: int) -> None:
            """Bridge Pyrogram's sync progress callback to our async one."""
            if progress_callback:
                asyncio.get_event_loop().create_task(
                    progress_callback(current, total)
                )

        target_chat = PRIVATE_CHANNEL_ID if PRIVATE_CHANNEL_ID != 0 else (fallback_chat_id or config.ADMIN_IDS[0])

        try:
            if bot_client:
                message = await bot_client.send_video(
                    chat_id=target_chat,
                    video=local_path,
                    file_name=file_name,
                    progress=_pyrogram_progress,
                    disable_notification=True,
                )
            else:
                userbot = Client(
                    SESSION_NAME,
                    api_id=API_ID,
                    api_hash=API_HASH,
                )
                async with userbot:
                    message = await userbot.send_video(
                        chat_id=target_chat,
                        video=local_path,
                        file_name=file_name,
                        progress=_pyrogram_progress,
                        disable_notification=True,
                    )
        except Exception as exc:
            if fallback_chat_id and target_chat != fallback_chat_id and ("PEER" in str(exc).upper() or "CHANNEL" in str(exc).upper()):
                log.warning("Channel %s failed (%s). Falling back to chat %s", target_chat, exc, fallback_chat_id)
                message = await bot_client.send_video(
                    chat_id=fallback_chat_id,
                    video=local_path,
                    file_name=file_name,
                    progress=_pyrogram_progress,
                    disable_notification=True,
                )
            else:
                raise

        # ── 3. Extract file_id from the sent message ───────────────────────
        file_id: str = message.video.file_id if message.video else ""
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
    Supports progress callbacks with calculated speed and ETA.
    Falls back to document upload and/or fallback_chat if channel permissions fail.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found at: {file_path}")

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    target = target_chat if target_chat != 0 else (PRIVATE_CHANNEL_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0))

    log.info("[TelegramUpload] Uploading '%s' (%d bytes) to chat %s", file_name, file_size, target)

    import time
    start_time = time.time()
    last_notify = 0.0

    def _pyrogram_progress(current: int, total: int) -> None:
        nonlocal last_notify
        if not progress_callback or not total:
            return
        now = time.time()
        if now - last_notify >= 2.0 or current == total:
            last_notify = now
            elapsed = max(0.001, now - start_time)
            speed_bytes = current / elapsed
            from services.downloader import format_bytes
            speed_str = f"{format_bytes(speed_bytes)}/s"
            pct = (current / total) * 100.0
            eta_seconds = int((total - current) / speed_bytes) if speed_bytes > 0 else 0
            eta_str = f"{eta_seconds}s" if eta_seconds < 60 else f"{eta_seconds // 60}m {eta_seconds % 60}s"
            asyncio.create_task(
                progress_callback(pct, format_bytes(current), format_bytes(total), speed_str, eta_str)
            )

    async def _do_send(chat_id: int):
        try:
            return await bot_client.send_video(
                chat_id=chat_id,
                video=file_path,
                file_name=file_name,
                caption=caption,
                progress=_pyrogram_progress,
                disable_notification=True,
            )
        except Exception as vid_err:
            log.warning("[TelegramUpload] send_video failed (%s). Falling back to send_document...", vid_err)
            return await bot_client.send_document(
                chat_id=chat_id,
                document=file_path,
                file_name=file_name,
                caption=caption,
                force_document=True,
                progress=_pyrogram_progress,
                disable_notification=True,
            )

    try:
        message = await _do_send(target)
    except Exception as exc:
        if fallback_chat and target != fallback_chat and ("PEER" in str(exc).upper() or "CHANNEL" in str(exc).upper() or "CHAT_ADMIN" in str(exc).upper()):
            log.warning("[TelegramUpload] Channel %s failed (%s). Falling back to chat %s", target, exc, fallback_chat)
            message = await _do_send(fallback_chat)
        else:
            log.error("[TelegramUpload] Failed to upload video: %s", exc)
            raise

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
