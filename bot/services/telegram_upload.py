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
from typing import Optional, Union

import aiofiles
import concurrent.futures
import hashlib
import httpx
import math
import struct
from typing import BinaryIO, Callable, Optional, Union
from pyrogram import Client, raw
from pyrogram.errors import FloodWait, RPCError
from pyrogram.session import Session

# Eliminate MTProto parallel connection handshake timeouts on Cloud/Kaggle runners
# Keep WAIT_TIMEOUT an integer for Pyrogram PingDelayDisconnect Int.write (.to_bytes)
try:
    Session.START_TIMEOUT = 15
    Session.WAIT_TIMEOUT = 30
except Exception:
    pass


def _patch_pyrogram_invoke_timeouts() -> None:
    """
    Pyrogram binds ``timeout: float = WAIT_TIMEOUT`` (15s) into function ``__defaults__``
    when the library is first imported, so changing ``Session.WAIT_TIMEOUT`` later has no
    effect on ``Client.invoke`` / ``Session.invoke`` / ``Session.send``.
    For 1 GB - 2 GB videos, Telegram needs 30s - 180s+ AFTER 100% upload to commit
    ``messages.SendMedia``; cutting it off at 15s falsely causes retries and 0% restarts.
    """
    def _bump_timeout_default(fn_obj, new_default: int = 45) -> None:
        defs = getattr(fn_obj, "__defaults__", None)
        if isinstance(defs, tuple) and 15 in defs:
            try:
                fn_obj.__defaults__ = tuple(new_default if v == 15 else v for v in defs)
            except Exception:
                pass

    try:
        _bump_timeout_default(Session.send, 45)
        _bump_timeout_default(Session.invoke, 45)
        _bump_timeout_default(Client.invoke, 45)
    except Exception:
        pass

    if not getattr(Client.invoke, "_fz_long_media_invoke_patched", False):
        _orig_client_invoke = Client.invoke

        async def _fz_client_invoke(self, query, retries=Session.MAX_RETRIES, timeout=45, sleep_threshold=None):
            inner = getattr(query, "query", query)
            inner = getattr(inner, "query", inner)
            is_media_commit = isinstance(
                inner,
                (
                    raw.functions.messages.SendMedia,
                    raw.functions.messages.SendMultiMedia,
                    raw.functions.messages.UploadMedia,
                ),
            )
            eff_timeout = timeout
            eff_retries = retries
            if is_media_commit:
                try:
                    eff_timeout = max(float(timeout or 0.0), 240.0)
                except (TypeError, ValueError):
                    eff_timeout = 240.0
                try:
                    eff_retries = max(int(retries or 0), 4)
                except (TypeError, ValueError):
                    eff_retries = 4
            return await _orig_client_invoke(
                self,
                query,
                retries=eff_retries,
                timeout=eff_timeout,
                sleep_threshold=sleep_threshold,
            )

        _fz_client_invoke._fz_long_media_invoke_patched = True
        Client.invoke = _fz_client_invoke


try:
    _patch_pyrogram_invoke_timeouts()
except Exception:
    pass

# Scale Pyrogram crypto thread pool from default 1 thread to multi-core pool (up to 32 workers)
try:
    import pyrogram
    pyrogram.crypto_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(32, (os.cpu_count() or 4) * 4),
        thread_name_prefix="PyrogramCrypto"
    )
except Exception:
    pass

# Hardware/C AES-256-IGE acceleration (tgcrypto -> cryptg -> OpenSSL libcrypto ctypes -> ECB fallback)
try:
    from services.crypto_accel import install_fast_aes_ige as _install_fast_aes_ige
    _install_fast_aes_ige()
except Exception:
    pass

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

# Reuse already-uploaded MTProto InputFile/InputFileBig handles across transient
# FloodWait/timeout retries or send_video -> send_document fallbacks on the same client.
# Skip files < 512 KB because Telegram thumbnails cannot be reused across calls.
_SAVED_FILE_CACHE: dict[tuple[int, str], tuple[int, int, object, float]] = {}
_SAVED_FILE_CACHE_TTL_SECONDS = 600.0
_MIN_CACHEABLE_UPLOAD_BYTES = 512 * 1024


def _resolve_upload_cache_key(bot_client: object, path: object) -> Optional[tuple[int, str, int, int]]:
    """Return ``(id(bot_client), abs_path, size, mtime_ns)`` for cacheable disk uploads."""
    real_path: Optional[str] = None
    if isinstance(path, (str, os.PathLike)):
        real_path = os.fspath(path)
    elif hasattr(path, "file_path") and isinstance(getattr(path, "file_path", None), str):
        real_path = path.file_path

    if not real_path:
        return None
    try:
        abs_path = os.path.abspath(real_path)
        st = os.stat(abs_path)
    except OSError:
        return None

    if st.st_size < _MIN_CACHEABLE_UPLOAD_BYTES:
        return None
    return (id(bot_client), abs_path, int(st.st_size), int(st.st_mtime_ns))


def _get_cached_saved_file(bot_client: object, path: object) -> Optional[object]:
    """Return a valid cached InputFile/InputFileBig handle if chunks were already uploaded."""
    key_info = _resolve_upload_cache_key(bot_client, path)
    if key_info is None:
        return None

    now = time.monotonic()
    stale_keys = [
        k for k, v in _SAVED_FILE_CACHE.items()
        if (now - v[3]) > _SAVED_FILE_CACHE_TTL_SECONDS
    ]
    for k in stale_keys:
        _SAVED_FILE_CACHE.pop(k, None)

    cache_key = (key_info[0], key_info[1])
    entry = _SAVED_FILE_CACHE.get(cache_key)
    if entry is None:
        return None
    c_size, c_mtime_ns, cached_input_file, _ = entry
    if c_size != key_info[2] or c_mtime_ns != key_info[3]:
        _SAVED_FILE_CACHE.pop(cache_key, None)
        return None
    return cached_input_file


def _put_cached_saved_file(bot_client: object, path: object, saved_input_file: object) -> None:
    """Record a completed upload handle so immediate retries/fallbacks do not re-upload 0% -> 100%."""
    if saved_input_file is None:
        return
    key_info = _resolve_upload_cache_key(bot_client, path)
    if key_info is None:
        return
    _SAVED_FILE_CACHE[(key_info[0], key_info[1])] = (
        key_info[2],
        key_info[3],
        saved_input_file,
        time.monotonic(),
    )


def _clear_cached_saved_file(bot_client: object, path: object) -> None:
    """Evict a cached upload handle after final completion or part expiry."""
    key_info = _resolve_upload_cache_key(bot_client, path)
    if key_info is None:
        return
    _SAVED_FILE_CACHE.pop((key_info[0], key_info[1]), None)


def _extract_flood_wait_seconds(exc: Exception) -> Optional[int]:
    """Return FloodWait seconds from a Pyrogram FloodWait or formatted RPC error message."""
    if isinstance(exc, FloodWait):
        try:
            return int(getattr(exc, "value", 0) or 0)
        except (TypeError, ValueError):
            return None
    m = re.search(r"FLOOD_WAIT_(\d+)", str(exc).upper())
    return int(m.group(1)) if m else None


def _is_stale_file_part_error(exc: Exception) -> bool:
    """True when Telegram rejected previously uploaded part IDs (cache must be refreshed)."""
    msg = str(exc).upper()
    return any(tok in msg for tok in ("FILE_PARTS_INVALID", "FILE_ID_INVALID", "FILE_REFERENCE"))


def _is_transient_send_media_error(exc: Exception) -> bool:
    """True for transient socket/RPC failures during final SendMedia commit."""
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, asyncio.TimeoutError)):
        return True
    msg = str(exc).upper()
    return any(
        tok in msg
        for tok in (
            "TIMEOUT",
            "TIMED OUT",
            "TIMED_OUT",
            "CONNECTION",
            "RESET BY PEER",
            "BROKEN PIPE",
            "EOF",
            "RPC_CALL_FAIL",
            "INTERNAL_SERVER_ERROR",
            "SERVICE_UNAVAILABLE",
            "WORKER_BUSY_TOO_LONG",
            "RETRY_LIMIT",
        )
    )


async def _find_already_posted_media_in_chat(
    bot_client: object,
    chat_id: Union[int, str],
    file_name: str,
    file_size: int,
    min_timestamp: float = 0.0,
) -> Optional[object]:
    """
    Check the most recent channel messages after a post-100% SendMedia timeout/hiccup.
    Telegram often commits a 1.5-2 GB file right after Pyrogram's RPC timeout fires;
    recovering that posted Message prevents duplicate uploads and 0% restarts.
    """
    get_hist = getattr(bot_client, "get_chat_history", None)
    if not callable(get_hist):
        return None
    target_name = os.path.basename(str(file_name or "")).strip().lower()
    size_tol = max(8192, int(file_size * 0.002)) if file_size > 0 else 8192

    async def _scan():
        hist_iter = get_hist(chat_id, limit=10)
        if not hasattr(hist_iter, "__aiter__"):
            return None
        async for msg in hist_iter:
            if not msg:
                continue
            msg_date = getattr(msg, "date", None)
            if msg_date and hasattr(msg_date, "timestamp") and min_timestamp > 0:
                try:
                    if msg_date.timestamp() < (min_timestamp - 120.0):
                        continue
                except Exception:
                    pass
            media = getattr(msg, "video", None) or getattr(msg, "document", None)
            if not media or not getattr(media, "file_id", None):
                continue
            m_size = int(getattr(media, "file_size", 0) or 0)
            m_name = os.path.basename(str(getattr(media, "file_name", "") or "")).strip().lower()
            size_match = file_size > 0 and abs(m_size - int(file_size)) <= size_tol
            name_match = bool(target_name and m_name and m_name == target_name)
            if (size_match and (name_match or file_size >= 512 * 1024)) or (name_match and m_size > 0):
                return msg
        return None

    try:
        return await asyncio.wait_for(_scan(), timeout=10.0)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Ultra-Fast MTProto Parallel Multi-Connection Chunk Uploader (FastPyrogram)
# ─────────────────────────────────────────────────────────────────────────────
async def fast_save_file(
    self: "Client",
    path: Union[str, BinaryIO, os.PathLike],
    file_id: int = None,
    file_part: int = 0,
    progress: Callable = None,
    progress_args: tuple = (),
):
    """
    Wrapper around ``_fast_save_file_impl`` that reuses recently uploaded MTProto
    chunks across transient retries and ``send_video`` -> ``send_document`` fallbacks.
    """
    if file_id is None and file_part == 0:
        cached_file = _get_cached_saved_file(self, path)
        if cached_file is not None:
            key_info = _resolve_upload_cache_key(self, path)
            cached_size = key_info[2] if key_info else 0
            log.info(
                "[FastUpload] Reusing already-uploaded MTProto chunks (%d bytes, 0 bytes re-uploaded) for %s",
                cached_size,
                os.path.basename(key_info[1]) if key_info else str(path),
            )
            if progress and cached_size > 0:
                try:
                    if inspect.iscoroutinefunction(progress):
                        await progress(cached_size, cached_size, *progress_args)
                    else:
                        progress(cached_size, cached_size, *progress_args)
                except Exception:
                    pass
            return cached_file

    saved = await _fast_save_file_impl(
        self,
        path,
        file_id=file_id,
        file_part=file_part,
        progress=progress,
        progress_args=progress_args,
    )
    if file_id is None and file_part == 0 and saved is not None:
        _put_cached_saved_file(self, path, saved)
    return saved


async def _fast_save_file_impl(
    self: "Client",
    path: Union[str, BinaryIO, os.PathLike],
    file_id: int = None,
    file_part: int = 0,
    progress: Callable = None,
    progress_args: tuple = ()
):
    """
    High-speed multi-connection MTProto parallel chunk uploader for Pyrogram.
    Uses up to 8 parallel TCP media sessions to Telegram MTProto datacenters
    with a pre-buffered chunk queue and worker pool to eliminate single-socket speed caps.
    """
    if path is None:
        return None

    if isinstance(path, (str, os.PathLike)):
        fp = open(path, "rb")
        should_close = True
    elif isinstance(path, io.IOBase):
        fp = path
        should_close = False
    else:
        raise ValueError("Invalid file. Expected a file path as string or a binary file pointer")

    file_name = getattr(fp, "name", "file.mp4")
    fp.seek(0, os.SEEK_END)
    file_size = fp.tell()
    fp.seek(0)

    if file_size == 0:
        if should_close:
            try:
                fp.close()
            except Exception:
                pass
        raise ValueError("File size equals to 0 B")

    is_premium = getattr(getattr(self, "me", None), "is_premium", False)
    file_size_limit_mib = 4000 if is_premium else 2000
    if file_size > file_size_limit_mib * 1024 * 1024:
        if should_close:
            try:
                fp.close()
            except Exception:
                pass
        raise ValueError(f"Can't upload files bigger than {file_size_limit_mib} MiB")

    part_size = 512 * 1024  # 512 KB MTProto part
    file_total_parts = int(math.ceil(file_size / part_size))
    is_big = file_size > 10 * 1024 * 1024
    is_missing_part = file_id is not None
    file_id = file_id or self.rnd_id()
    md5_sum = hashlib.md5() if not is_big and not is_missing_part else None

    dc_id = await self.storage.dc_id()
    auth_key = await self.storage.auth_key()
    test_mode = await self.storage.test_mode()

    # Handle missing single part retry (e.g. from FilePartMissing)
    if is_missing_part:
        fp.seek(part_size * file_part)
        chunk = fp.read(part_size)
        if is_big:
            rpc = raw.functions.upload.SaveBigFilePart(
                file_id=file_id,
                file_part=file_part,
                file_total_parts=file_total_parts,
                bytes=chunk,
            )
        else:
            rpc = raw.functions.upload.SaveFilePart(
                file_id=file_id,
                file_part=file_part,
                bytes=chunk,
            )
        s = Session(self, dc_id, auth_key, test_mode, is_media=True)
        await s.start()
        try:
            await s.invoke(rpc)
        finally:
            await s.stop()
            if should_close:
                try:
                    fp.close()
                except Exception:
                    pass
        return

    # Scale parallel MTProto connections and worker tasks based on file size and runtime.
    # On Kaggle/Colab cloud runners, default to 8 parallel media sockets (up to 32 async pipelined workers)
    # connected concurrently to saturate high-RTT DC5 links; locally default to 4 sockets.
    _on_cloud_runner = (os.name != "nt") and (
        os.path.exists("/kaggle")
        or os.path.exists("/content")
        or bool(os.getenv("KAGGLE_KERNEL_RUN_TYPE"))
    )
    _default_sess = "8" if _on_cloud_runner else "4"
    try:
        max_parallel_sessions = int(os.getenv("FAST_UPLOAD_SESSIONS", _default_sess))
    except (ValueError, TypeError):
        max_parallel_sessions = int(_default_sess)
    max_parallel_sessions = max(1, min(max_parallel_sessions, 12))

    if file_size > 50 * 1024 * 1024:
        num_sessions = max_parallel_sessions
        workers_per_sess = 4 if _on_cloud_runner else 3
    elif file_size > 10 * 1024 * 1024:
        num_sessions = min(max_parallel_sessions, 4 if _on_cloud_runner else 3)
        workers_per_sess = 3 if _on_cloud_runner else 2
    else:
        num_sessions = 1
        workers_per_sess = 2

    sessions = [Session(self, dc_id, auth_key, test_mode, is_media=True) for _ in range(num_sessions)]

    async def _start_media_session(idx: int, sess_obj: Session):
        if idx > 0:
            await asyncio.sleep(0.02 * idx)
        try:
            await sess_obj.start()
            return sess_obj
        except Exception as s_err:
            log.warning(
                "[FastUpload] Error starting parallel session %d/%d (%s)",
                idx + 1, num_sessions, s_err
            )
            try:
                await sess_obj.stop()
            except Exception:
                pass
            return None

    started_results = await asyncio.gather(
        *[_start_media_session(i, s) for i, s in enumerate(sessions)],
        return_exceptions=False,
    )
    started_sessions = [s for s in started_results if s is not None]

    if not started_sessions:
        log.warning(
            "[FastUpload] All %d parallel sessions failed to start; attempting single fallback session",
            num_sessions
        )
        fallback_sess = Session(self, dc_id, auth_key, test_mode, is_media=True)
        await fallback_sess.start()
        started_sessions.append(fallback_sess)
        num_workers = 4
    else:
        num_workers = max(2, len(started_sessions) * workers_per_sess)
    sessions = started_sessions
    num_sessions = len(sessions)

    queue = asyncio.Queue(maxsize=max(64, num_workers * 4))
    transferred_bytes = [file_part * part_size]
    transferred_lock = asyncio.Lock()
    upload_error = None

    async def worker(worker_id: int):
        nonlocal upload_error
        sess = sessions[worker_id % len(sessions)]
        while True:
            item = await queue.get()
            if item is None:
                queue.task_done()
                return

            if upload_error is not None:
                queue.task_done()
                continue

            part_num, rpc, chunk_len = item
            success = False
            for retry in range(7):
                try:
                    await sess.invoke(rpc)
                    success = True
                    break
                except FloodWait as fw:
                    log.warning("[FastUpload] FloodWait %ds in worker %d", fw.value, worker_id)
                    await asyncio.sleep(fw.value)
                except Exception as exc:
                    if retry >= 6:
                        upload_error = exc
                        log.error("[FastUpload] Worker %d chunk %d failed after 7 retries: %s", worker_id, part_num, exc)
                        break
                    await asyncio.sleep(min(10.0, 0.8 * (2 ** min(retry, 3))))
                    if (
                        isinstance(exc, (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError))
                        and hasattr(sess, "is_started")
                        and not getattr(sess, "is_started", True)
                    ):
                        try:
                            await sess.stop()
                        except Exception:
                            pass
                        try:
                            await sess.start()
                        except Exception:
                            pass

            if success:
                async with transferred_lock:
                    transferred_bytes[0] += chunk_len
                    cur_transferred = transferred_bytes[0]
                if progress:
                    try:
                        p_call = progress(min(cur_transferred, file_size), file_size, *progress_args)
                        if inspect.iscoroutine(p_call):
                            await p_call
                    except Exception:
                        pass
            queue.task_done()

    worker_tasks = [self.loop.create_task(worker(i)) for i in range(num_workers)]
    fp.seek(part_size * file_part)
    cur_part = file_part
    use_thread_read = bool(should_close and file_size > 10 * 1024 * 1024)

    try:
        while True:
            if upload_error is not None:
                raise upload_error

            chunk = await asyncio.to_thread(fp.read, part_size) if use_thread_read else fp.read(part_size)
            if not chunk:
                if not is_big and not is_missing_part and md5_sum:
                    md5_hex = "".join([hex(b)[2:].zfill(2) for b in md5_sum.digest()])
                break

            if is_big:
                rpc = raw.functions.upload.SaveBigFilePart(
                    file_id=file_id,
                    file_part=cur_part,
                    file_total_parts=file_total_parts,
                    bytes=chunk,
                )
            else:
                rpc = raw.functions.upload.SaveFilePart(
                    file_id=file_id,
                    file_part=cur_part,
                    bytes=chunk,
                )
                if md5_sum:
                    md5_sum.update(chunk)

            await queue.put((cur_part, rpc, len(chunk)))
            cur_part += 1

        await queue.join()

        if upload_error is not None:
            raise upload_error

    finally:
        for _ in worker_tasks:
            try:
                await queue.put(None)
            except Exception:
                pass
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        await asyncio.gather(*[s.stop() for s in sessions], return_exceptions=True)

        if should_close:
            try:
                fp.close()
            except Exception:
                pass

    if is_big:
        return raw.types.InputFileBig(
            id=file_id,
            parts=file_total_parts,
            name=os.path.basename(file_name),
        )
    else:
        return raw.types.InputFile(
            id=file_id,
            parts=file_total_parts,
            name=os.path.basename(file_name),
            md5_checksum=md5_hex if 'md5_hex' in locals() else "",
        )


# Apply the high-speed parallel MTProto uploader patch globally to Pyrogram
try:
    import pyrogram.methods.advanced.save_file
    pyrogram.methods.advanced.save_file.SaveFile.save_file = fast_save_file
    Client.save_file = fast_save_file
    log.info("[TelegramUpload] Successfully installed high-speed parallel MTProto uploader patch!")
except Exception as patch_err:
    log.warning("[TelegramUpload] Note while installing fast_save_file patch: %s", patch_err)


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
        self.mode = "rb"
        self._file = open(file_path, "rb")
        self._fd = self._file.fileno()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

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
        from services.branding_service import format_branded_filename
        file_name = format_branded_filename(title="", original_name=os.path.basename(local_path))
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
                file_name=file_name,
            )

        # Userbot upload fallback if bot_client not provided
        from services.branding_service import get_branded_thumbnail
        from services.video_service import get_video_metadata, generate_video_thumbnail
        meta = get_video_metadata(local_path)
        width = meta.get("width", 1280)
        height = meta.get("height", 720)
        duration = meta.get("duration", 0)

        thumb_temp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        thumb_temp.close()
        thumb_path = get_branded_thumbnail(thumb_temp.name)
        if not (thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 500):
            thumb_path = generate_video_thumbnail(local_path, thumb_path=thumb_temp.name, duration=duration)
        if not (thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 500):
            thumb_path = None
            if os.path.exists(thumb_temp.name):
                try:
                    os.remove(thumb_temp.name)
                except Exception:
                    pass

        userbot = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            max_concurrent_transmissions=getattr(config, "TG_MAX_CONCURRENT_TRANSMISSIONS", 10),
        )
        try:
            async with userbot:
                with LowRamFileReader(local_path) as reader:
                    try:
                        message = await userbot.send_video(
                            chat_id=target_chat,
                            video=reader,
                            file_name=file_name,
                            duration=duration,
                            width=width,
                            height=height,
                            thumb=thumb_path,
                            supports_streaming=True,
                            disable_notification=True,
                        )
                    except Exception as vid_err:
                        log.warning("[TelegramUpload] userbot send_video failed (%s). Retrying send_document...", vid_err)
                        reader.seek(0)
                        message = await userbot.send_document(
                            chat_id=target_chat,
                            document=reader,
                            file_name=file_name,
                            thumb=thumb_path,
                            force_document=True,
                            disable_notification=True,
                        )
        finally:
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass

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
    file_name: str = "",
    reply_markup=None,
) -> dict:
    """
    Directly upload a local video file to the specified Telegram chat/channel.
    Supports progress callbacks with calculated speed and ETA decoupled from the chunk upload loop.
    Falls back to document upload and/or fallback_chat if channel permissions fail.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found at: {file_path}")

    from services.branding_service import format_branded_filename, get_branded_thumbnail
    raw_name = file_name or os.path.basename(file_path)
    file_name = format_branded_filename(title="", original_name=raw_name)
    file_size = os.path.getsize(file_path)
    target = target_chat if target_chat != 0 else (PRIVATE_CHANNEL_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0))
    try:
        target = int(target)
    except (ValueError, TypeError):
        pass

    log.info("[TelegramUpload] Uploading '%s' (%d bytes) to chat %s", file_name, file_size, target)

    # ── Probe video metadata & generate 16:9 widescreen thumbnail off event loop ──
    from services.video_service import get_video_metadata, generate_video_thumbnail
    meta = await asyncio.to_thread(get_video_metadata, file_path)
    width = meta.get("width", 1280)
    height = meta.get("height", 720)
    duration = meta.get("duration", 0)

    thumb_temp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    thumb_temp.close()
    thumb_path = await asyncio.to_thread(generate_video_thumbnail, file_path, thumb_path=thumb_temp.name, duration=duration)
    if not (thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 500):
        thumb_path = get_branded_thumbnail(thumb_temp.name)
    if not (thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 500):
        thumb_path = None
        if os.path.exists(thumb_temp.name):
            try:
                os.remove(thumb_temp.name)
            except Exception:
                pass

    # Enable multi-chunk parallel MTProto pipelining for ultra-fast upload
    if hasattr(bot_client, "max_concurrent_transmissions"):
        bot_client.max_concurrent_transmissions = getattr(config, "TG_MAX_CONCURRENT_TRANSMISSIONS", 10)

    start_time = time.time()
    progress_state = {
        "current": 0,
        "total": file_size,
        "is_done": False,
        "last_reported_bytes": -1,
    }
    _monitor_done_ev = asyncio.Event()

    def _reset_progress_state():
        nonlocal start_time
        start_time = time.time()
        progress_state["current"] = 0
        progress_state["last_reported_bytes"] = -1

    def _pyrogram_progress(current: int, total: int) -> None:
        """
        Fast in-memory synchronous update called from Pyrogram's chunk upload loop.
        Zero coroutine overhead: does NOT block or make network/Telegram calls in-band.
        """
        progress_state["current"] = current
        progress_state["total"] = total

    async def _progress_monitor() -> None:
        """
        Lightweight background task updating the Telegram message every 3.5 seconds.
        Decoupled from Pyrogram's MTProto chunk upload loop to eliminate event loop contention.
        Shows 'Finalizing...' heartbeats at 100% while Telegram commits large files.
        """
        from services.downloader import format_bytes
        last_finalize_ping = 0.0

        while not progress_state["is_done"] and not _monitor_done_ev.is_set():
            try:
                await asyncio.wait_for(_monitor_done_ev.wait(), timeout=3.5)
                break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

            if not progress_callback:
                continue

            cur = progress_state["current"]
            tot = progress_state["total"]
            now = time.time()
            is_at_100 = (tot > 0 and cur >= tot)
            if tot <= 0:
                continue
            if cur == progress_state["last_reported_bytes"]:
                if not is_at_100 or (now - last_finalize_ping) < 12.0:
                    continue

            progress_state["last_reported_bytes"] = cur
            if is_at_100:
                last_finalize_ping = now
            elapsed = max(0.001, now - start_time)
            speed_bytes = cur / elapsed
            speed_str = f"{format_bytes(speed_bytes)}/s"
            pct = min(100.0, (cur / tot) * 100.0)
            if is_at_100:
                eta_str = "Telegram Finalizing..."
            else:
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

        if hasattr(bot_client, "max_concurrent_transmissions"):
            bot_client.max_concurrent_transmissions = 16

        try:
            resolved = await bot_client.get_chat(chat_id)
            log.info("[TelegramUpload] Confirmed target peer: '%s' (ID: %s)", getattr(resolved, "title", "Channel"), chat_id)
        except Exception as gc_err:
            log.warning("[TelegramUpload] Pre-resolving chat %s warning: %s", chat_id, gc_err)

        upload_started_ts = time.time()
        _reset_progress_state()
        _is_cloud_runner = (os.name != "nt") and (
            os.path.exists('/kaggle')
            or os.path.exists('/content')
            or os.path.isdir('/dev/shm')
            or os.getenv("KAGGLE_KERNEL_RUN_TYPE") is not None
        )
        _use_low_ram = False if _is_cloud_runner else ((not os.path.exists('/content')) and (os.name != 'nt'))
        if _use_low_ram:
            try:
                import psutil
                if psutil.virtual_memory().total > 1.2 * 1024 * 1024 * 1024:
                    _use_low_ram = False
            except Exception:
                pass

        async def _send_once(as_document: bool):
            if _use_low_ram:
                with LowRamFileReader(file_path) as reader:
                    if as_document:
                        return await bot_client.send_document(
                            chat_id=chat_id,
                            document=reader,
                            file_name=file_name,
                            caption=caption,
                            thumb=thumb_path,
                            force_document=True,
                            reply_markup=reply_markup,
                            progress=_pyrogram_progress,
                            disable_notification=True,
                        )
                    return await bot_client.send_video(
                        chat_id=chat_id,
                        video=reader,
                        file_name=file_name,
                        caption=caption,
                        duration=duration,
                        width=width,
                        height=height,
                        thumb=thumb_path,
                        supports_streaming=True,
                        reply_markup=reply_markup,
                        progress=_pyrogram_progress,
                        disable_notification=True,
                    )
            if as_document:
                return await bot_client.send_document(
                    chat_id=chat_id,
                    document=file_path,
                    file_name=file_name,
                    caption=caption,
                    thumb=thumb_path,
                    force_document=True,
                    reply_markup=reply_markup,
                    progress=_pyrogram_progress,
                    disable_notification=True,
                )
            return await bot_client.send_video(
                chat_id=chat_id,
                video=file_path,
                file_name=file_name,
                caption=caption,
                duration=duration,
                width=width,
                height=height,
                thumb=thumb_path,
                supports_streaming=True,
                reply_markup=reply_markup,
                progress=_pyrogram_progress,
                disable_notification=True,
            )

        async def _run_send_with_reuse(as_document: bool):
            max_media_retries = 5
            last_err: Optional[Exception] = None
            for attempt in range(max_media_retries):
                try:
                    return await _send_once(as_document)
                except Exception as send_err:
                    last_err = send_err
                    chunks_uploaded = (
                        _get_cached_saved_file(bot_client, file_path) is not None
                        or (file_size > 0 and progress_state["current"] >= file_size * 0.98)
                    )
                    if _is_stale_file_part_error(send_err):
                        # Wait briefly & check if Telegram already committed the previous SendMedia call
                        await asyncio.sleep(4.0)
                        recovered = await _find_already_posted_media_in_chat(
                            bot_client, chat_id, file_name, file_size, upload_started_ts
                        )
                        if recovered is not None:
                            log.info("[TelegramUpload] Recovered already-posted media after FILE_PARTS_INVALID: msg_id=%s", getattr(recovered, "id", "?"))
                            return recovered
                        _clear_cached_saved_file(bot_client, file_path)
                        if attempt + 1 < max_media_retries:
                            log.warning(
                                "[TelegramUpload] Uploaded file parts invalid/expired (%s). Retrying fresh upload (%d/%d)...",
                                send_err, attempt + 1, max_media_retries,
                            )
                            _reset_progress_state()
                            continue
                    wait_s = _extract_flood_wait_seconds(send_err)
                    if wait_s is not None and wait_s <= 60 and attempt + 1 < max_media_retries:
                        sleep_s = max(1, wait_s) + 1
                        log.warning(
                            "[TelegramUpload] FloodWait %ss on %s (%s). Waiting %ss & reusing uploaded chunks (retry %d/%d)...",
                            wait_s, "send_document" if as_document else "send_video",
                            send_err, sleep_s, attempt + 1, max_media_retries,
                        )
                        await asyncio.sleep(sleep_s)
                        continue
                    if _is_transient_send_media_error(send_err) and chunks_uploaded:
                        wait_commit_s = min(30.0, 6.0 * (attempt + 1))
                        log.warning(
                            "[TelegramUpload] Post-100%% SendMedia still committing/transient error on %s (%s). "
                            "Waiting %.0fs & verifying channel before retry %d/%d (keeping uploaded chunks)...",
                            "send_document" if as_document else "send_video",
                            send_err, wait_commit_s, attempt + 1, max_media_retries,
                        )
                        await asyncio.sleep(wait_commit_s)
                        recovered = await _find_already_posted_media_in_chat(
                            bot_client, chat_id, file_name, file_size, upload_started_ts
                        )
                        if recovered is not None:
                            log.info(
                                "[TelegramUpload] Verified message committed on Telegram after wait (msg_id=%s) — 0 bytes re-uploaded!",
                                getattr(recovered, "id", "?"),
                            )
                            return recovered
                        if not getattr(bot_client, "is_connected", True):
                            try:
                                await bot_client.connect()
                            except Exception:
                                pass
                        if attempt + 1 < max_media_retries:
                            continue
                    raise
            if last_err is not None:
                raise last_err

        try:
            return await _run_send_with_reuse(as_document=False)
        except Exception as vid_err:
            if file_size > 0 and progress_state["current"] >= file_size * 0.98:
                await asyncio.sleep(4.0)
                recovered = await _find_already_posted_media_in_chat(
                    bot_client, chat_id, file_name, file_size, upload_started_ts
                )
                if recovered is not None:
                    log.info("[TelegramUpload] Recovered posted video before send_document fallback: msg_id=%s", getattr(recovered, "id", "?"))
                    return recovered
            log.warning("[TelegramUpload] send_video failed (%s). Falling back to send_document...", vid_err)
            if _get_cached_saved_file(bot_client, file_path) is None:
                _reset_progress_state()
            return await _run_send_with_reuse(as_document=True)

    monitor_task = asyncio.create_task(_progress_monitor())
    try:
        message = await _do_send(target)
    except Exception as exc:
        log.error("[TelegramUpload] Upload to channel %s failed: %s", target, exc, exc_info=True)
        # Only fallback if target was 0/unset; never dump a movie into user DM if channel was specified
        if fallback_chat and target == 0:
            log.warning("[TelegramUpload] Target chat unset. Falling back to chat %s", fallback_chat)
            _reset_progress_state()
            message = await _do_send(fallback_chat)
        else:
            raise RuntimeError(f"Telegram upload to channel {target} failed: {exc}") from exc
    finally:
        _clear_cached_saved_file(bot_client, file_path)
        progress_state["is_done"] = True
        _monitor_done_ev.set()
        monitor_task.cancel()
        try:
            await asyncio.wait_for(monitor_task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass

    # Ensure a final 100% progress update is dispatched if not already reported
    if progress_callback and file_size > 0:
        now = time.time()
        elapsed = max(0.001, now - start_time)
        speed_bytes = file_size / elapsed
        from services.downloader import format_bytes
        speed_str = f"{format_bytes(speed_bytes)}/s"
        try:
            if inspect.iscoroutinefunction(progress_callback):
                await progress_callback(100.0, format_bytes(file_size), format_bytes(file_size), speed_str, "0s")
            else:
                progress_callback(100.0, format_bytes(file_size), format_bytes(file_size), speed_str, "0s")
        except Exception as p_err:
            log.debug("[TelegramUpload] Final progress callback error: %s", p_err)

    file_id: str = message.video.file_id if message.video else (message.document.file_id if message.document else "")
    message_id: int = message.id
    stream_url = get_file_stream_url(file_id, chat_id=target, message_id=message_id)

    log.info("[TelegramUpload] Upload success. file_id=%s stream_url=%s", file_id, stream_url)
    return {
        "file_id": file_id,
        "message_id": message_id,
        "file_name": file_name,
        "file_size": file_size,
        "stream_url": stream_url,
    }



# ─────────────────────────────────────────────────────────────────────────────
def get_file_stream_url(file_id: str, chat_id: Union[int, str] = 0, message_id: int = 0) -> str:
    """
    Build and return the public stream URL for a Telegram file_id or channel message.

    Example:
        get_file_stream_url("BQACAgIAA...", chat_id=-1004325759505, message_id=14) →
        "https://film-bot-2.onrender.com/stream/channel/-1004325759505/14"
    """
    base = (STREAM_BASE_URL or "").rstrip("/")
    if chat_id and message_id:
        return f"{base}/stream/channel/{chat_id}/{message_id}"
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
                        try:
                            if inspect.iscoroutinefunction(progress_callback):
                                await progress_callback(downloaded, total)
                            else:
                                progress_callback(downloaded, total)
                        except Exception:
                            pass

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
