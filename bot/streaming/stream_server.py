"""
stream_server.py — High-Performance Telegram Cloud Streaming & Download Server.

Architecture:
1. In-Memory 8MB Header Cache (_HEADER_CACHE):
   Caches the initial 8MB (moov atom + first video frames) in memory so Video.js
   starts playing in <300ms with ZERO initial buffering latency!
2. Pre-Buffered HTTP 206 Partial Content (Range requests):
   Ensures seamless timeline seeking, scrubbing, and preloading on Laptop, PC, Mobile & Tablet.
3. Clean Generator Cancellation:
   Prevents MTProto worker thread locking when users scrub or pause.
4. One-Click Direct Binary Download:
   Routes via /stream/download/{chat_id}/{message_id} with Content-Disposition headers.
"""

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import logging
import os
import re
from typing import AsyncGenerator, Optional, Union

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from starlette.requests import ClientDisconnect

from streaming.session_pool import stream_pool

log = logging.getLogger(__name__)


class RangeNotSatisfiableError(Exception):
    """Raised when an HTTP Range request cannot be satisfied (RFC 7233 / RFC 9110)."""
    pass


# APIRouter for inclusion in main.py web_app
stream_router = APIRouter(tags=["streaming"])

_standalone_client = None


@asynccontextmanager
async def stream_server_lifespan(app_instance: FastAPI):
    """Initialize streaming pool when stream_server.py is run directly via Uvicorn."""
    global _standalone_client
    try:
        import config
        api_id = getattr(config, "API_ID", None) or int(os.getenv("TG_API_ID", 0))
        api_hash = getattr(config, "API_HASH", None) or os.getenv("TG_API_HASH", "")
        bot_token = getattr(config, "BOT_TOKEN", None) or os.getenv("BOT_TOKEN", "")
        if api_id and api_hash:
            if not stream_pool._main_client and bot_token:
                try:
                    from pyrogram import Client
                    _standalone_client = Client(
                        name="stream_server_bot",
                        api_id=api_id,
                        api_hash=api_hash,
                        bot_token=bot_token,
                        in_memory=True,
                        no_updates=True,
                        max_concurrent_transmissions=10,
                    )
                    await _standalone_client.start()
                    stream_pool.set_main_client(_standalone_client)
                    log.info("[StreamServer] Standalone main bot client started for streaming.")
                except Exception as b_err:
                    log.warning("[StreamServer] Could not start standalone bot client: %s", b_err)
            await stream_pool.init_extra_sessions(api_id, api_hash)
            log.info("[StreamServer] Standalone stream pool initialized (%d active sessions).", len(stream_pool.clients))
    except Exception as exc:
        log.warning("[StreamServer] Standalone startup note: %s", exc)

    yield

    if _standalone_client and getattr(_standalone_client, "is_connected", False):
        try:
            await _standalone_client.stop()
        except Exception:
            pass


# Standalone FastAPI app
app = FastAPI(
    title="Telegram Cloud Stream & Download Server",
    description="Ultra-smooth streaming proxy from Telegram MTProto to web browsers.",
    version="3.0.0",
    lifespan=stream_server_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges", "Content-Disposition", "X-Stream-Cached"],
)

# ── 16MB In-Memory Header Cache ───────────────────────────────────────────────
# LRU Cache storing up to 40 movies × 16MB (~640MB RAM max on VPS/Colab)
# and 8 movies × 16MB (~128MB RAM on 512MB Render instances) to prevent OOM.
_is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_NAME") or os.getenv("RENDER_SERVICE_ID"))
MAX_HEADER_CACHE_SIZE = int(os.getenv("MAX_HEADER_CACHE_SIZE", 8 if _is_render else 40))
MAX_HEADER_CACHE_BYTES = 16 * 1024 * 1024  # 16 MiB per movie (<50ms first-frame response)
_HEADER_CACHE: OrderedDict[str, bytearray] = OrderedDict()
_CACHE_LOCK = asyncio.Lock()


def _get_cache_key(chat_id: Union[int, str], message_id: int) -> str:
    return f"{chat_id}:{message_id}"


async def _save_to_header_cache(key: str, data: bytes, offset: int = 0) -> None:
    if not data or offset < 0 or offset >= MAX_HEADER_CACHE_BYTES:
        return
    async with _CACHE_LOCK:
        if key in _HEADER_CACHE:
            _HEADER_CACHE.move_to_end(key)
            buf = _HEADER_CACHE[key]
            if offset <= len(buf) and len(buf) < MAX_HEADER_CACHE_BYTES:
                data_to_add = data[len(buf) - offset:]
                if data_to_add:
                    remaining = MAX_HEADER_CACHE_BYTES - len(buf)
                    buf.extend(data_to_add[:remaining])
        else:
            if offset == 0:
                if len(_HEADER_CACHE) >= MAX_HEADER_CACHE_SIZE:
                    _HEADER_CACHE.popitem(last=False)
                _HEADER_CACHE[key] = bytearray(data[:MAX_HEADER_CACHE_BYTES])


async def _get_from_header_cache(key: str, start: int, end: int) -> Optional[bytes]:
    if start < 0 or start > end:
        return None
    async with _CACHE_LOCK:
        if key not in _HEADER_CACHE:
            return None
        _HEADER_CACHE.move_to_end(key)
        buf = _HEADER_CACHE[key]
        if start < len(buf):
            slice_end = min(end + 1, len(buf))
            return bytes(buf[start:slice_end])
        return None


def _parse_range(range_header: Optional[str], file_size: int) -> tuple[int, int]:
    """
    Parse HTTP Range header e.g. 'bytes=0-1048575', 'bytes=0-', 'bytes=-500'.
    Returns (start, end) inclusive.
    Raises RangeNotSatisfiableError if the range is unsatisfiable (RFC 7233 / RFC 9110).
    """
    if not range_header or not range_header.strip():
        return 0, max(file_size - 1, 0)

    val = range_header.strip()
    if not val.lower().startswith("bytes="):
        # Syntactically invalid unit -> ignore Range header per RFC 7233
        return 0, max(file_size - 1, 0)

    val = val[len("bytes="):].strip()
    if not val:
        return 0, max(file_size - 1, 0)

    # If multipart/multiple ranges e.g. "bytes=0-10, 20-30", process first range
    if "," in val:
        val = val.split(",")[0].strip()

    s_str, sep, e_str = val.partition("-")
    if not sep:
        return 0, max(file_size - 1, 0)

    s_str = s_str.strip()
    e_str = e_str.strip()

    if file_size <= 0:
        raise RangeNotSatisfiableError("File size is zero or negative")

    try:
        if not s_str and e_str:
            # Suffix range: bytes=-500 -> last 500 bytes of representation
            suffix_len = int(e_str)
            if suffix_len <= 0:
                raise RangeNotSatisfiableError("Suffix length must be greater than zero")
            start = max(0, file_size - suffix_len)
            end = file_size - 1
            return start, end

        elif s_str and not e_str:
            # Open-ended range: bytes=500- -> from 500 to EOF
            start = int(s_str)
            if start < 0 or start >= file_size:
                raise RangeNotSatisfiableError(f"Start byte {start} out of bounds for size {file_size}")
            end = file_size - 1
            return start, end

        elif s_str and e_str:
            # Closed range: bytes=0-1024
            start = int(s_str)
            end = int(e_str)
            if start < 0 or start > end or start >= file_size:
                raise RangeNotSatisfiableError(f"Range {start}-{end} unsatisfiable for size {file_size}")
            end = min(end, file_size - 1)
            return start, end

        else:
            # bytes=- -> invalid syntax, ignore
            return 0, max(file_size - 1, 0)

    except ValueError:
        # Non-integer values -> ignore Range header per RFC 7233
        return 0, max(file_size - 1, 0)


def _is_explicit_end(range_header: Optional[str]) -> bool:
    """Return True if Range header explicitly specified an end offset or suffix bound (e.g. 'bytes=0-1', 'bytes=-500')."""
    if not range_header:
        return False
    val = range_header.strip()
    if not val.lower().startswith("bytes="):
        return False
    val = val[len("bytes="):].strip()
    if "," in val:
        val = val.split(",")[0].strip()
    s_str, sep, e_str = val.partition("-")
    # Explicit end if either:
    # 1. Closed range '0-1' (s_str and e_str)
    # 2. Suffix range '-500' (not s_str and e_str)
    return bool(sep and (s_str.strip() and e_str.strip() or (not s_str.strip() and e_str.strip())))


@stream_router.options("/stream/{path:path}")
async def options_stream(path: str) -> Response:
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, Authorization",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, Content-Disposition",
        },
    )


@stream_router.get("/stream/status")
async def stream_status() -> dict:
    """Return streaming pool status and readiness."""
    status = stream_pool.get_status()
    status["service"] = "Telegram Cloud Streaming Edge Proxy (Ultra-Smooth v3.0)"
    status["status"] = "ready" if status["connected_clients"] > 0 else "initializing"
    status["header_cache_entries"] = len(_HEADER_CACHE)
    status["max_cache_mb"] = MAX_HEADER_CACHE_SIZE * (MAX_HEADER_CACHE_BYTES // (1024 * 1024))
    return status


@stream_router.get("/stream/ping")
@stream_router.get("/stream/health")
@stream_router.get("/health")
@stream_router.get("/ping")
@stream_router.head("/stream/ping")
@stream_router.head("/stream/health")
@stream_router.head("/health")
@stream_router.head("/ping")
async def stream_ping() -> dict:
    return {"status": "pong", "service": "stream", "mode": "telegram_cloud", "ready": True}


async def _cached_stream_generator(
    cache_key: str,
    msg_obj,
    start: int,
    end: int,
) -> AsyncGenerator[bytes, None]:
    """
    Yields video bytes for range [start, end].
    If the start range is cached in memory, yields from RAM instantly (<50ms),
    then streams any remaining bytes directly from Telegram MTProto with parallel pipelining.
    Populates RAM header cache in real-time as chunks arrive.
    Gracefully handles client disconnects, scrubbing, and cancellations without leaking tasks or error spam.
    """
    cur_pos = start
    try:
        cached_part = await _get_from_header_cache(cache_key, cur_pos, end)
        if cached_part:
            yield cached_part
            cur_pos += len(cached_part)

        if cur_pos <= end:
            async for chunk in stream_pool.stream_media_chunks(msg_obj, cur_pos, end):
                if not chunk:
                    break
                # Populate 16MB RAM cache immediately in real-time if reading within initial header
                if cur_pos < MAX_HEADER_CACHE_BYTES:
                    try:
                        await _save_to_header_cache(cache_key, chunk, offset=cur_pos)
                    except Exception:
                        pass
                yield chunk
                cur_pos += len(chunk)
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError) as abort_exc:
        log.debug("[StreamServer] Client aborted stream range %d-%d for %s (%s)", start, end, cache_key, type(abort_exc).__name__)
        return
    except Exception as exc:
        exc_name = exc.__class__.__name__
        if exc_name in ("ClientDisconnect", "EndOfStream") or "Disconnect" in exc_name or "Cancelled" in exc_name:
            log.debug("[StreamServer] Client abort (%s) on stream range %d-%d for %s", exc_name, start, end, cache_key)
            return
        log.error("[StreamServer] Streaming error for %s: %s", cache_key, exc)


@stream_router.get("/stream/channel/{chat_id}/{message_id}")
@stream_router.head("/stream/channel/{chat_id}/{message_id}")
async def stream_channel_message(
    chat_id: str,
    message_id: int,
    request: Request,
    dl: Optional[int] = None,
    s: Optional[int] = None,
) -> Response:
    """
    Stream a video stored in a Telegram channel message.
    Supports HTTP 206 Range requests for seeking and fast buffering.
    """
    try:
        info = await stream_pool.get_media_info(chat_id, message_id)
    except RuntimeError as r_err:
        log.warning("[Stream] Stream pool not ready: %s", r_err)
        raise HTTPException(status_code=503, detail="Telegram stream client initializing. Please retry in a few seconds.")
    except Exception as exc:
        log.error("[Stream] Cannot load message %s from chat %s: %s", message_id, chat_id, exc)
        raise HTTPException(status_code=404, detail=f"Video not found on Telegram: {exc}")

    file_size = info["file_size"]
    msg = info["message"]
    mime_type = info.get("mime_type", "video/mp4")
    file_name = info.get("file_name", f"movie_{message_id}.mp4")

    range_header = request.headers.get("range")
    try:
        start, end = _parse_range(range_header, file_size)
    except RangeNotSatisfiableError:
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Range, Content-Type",
                "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, Content-Disposition",
            },
        )

    explicit_end = _is_explicit_end(range_header)
    MIN_SERVE = 8 * 1024 * 1024  # 8 MiB minimum response for unconstrained initial requests
    is_probe_range = explicit_end and (end - start + 1) <= 128

    if dl == 1:
        # Full file one-click download: serve entire remaining file unless client gave an explicit range
        if not range_header:
            start = 0
            end = max(file_size - 1, 0)
    elif not range_header:
        # Initial request without range -> serve first 8 MiB
        end = min(start + MIN_SERVE - 1, max(file_size - 1, 0))
    elif not is_probe_range and (end - start + 1) < MIN_SERVE and end < file_size - 1:
        # Expand small chunk requests to at least 8 MiB for fast pre-buffering (unless tiny metadata probe)
        end = min(start + MIN_SERVE - 1, file_size - 1)

    content_length = max(0, end - start + 1)
    cache_key = _get_cache_key(chat_id, message_id)
    is_partial = bool(range_header) or (not dl and end < file_size - 1)

    is_cached = False
    async with _CACHE_LOCK:
        if cache_key in _HEADER_CACHE and start < len(_HEADER_CACHE[cache_key]):
            is_cached = True

    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, Content-Disposition, X-Stream-Cached",
        "Cache-Control": "public, max-age=2592000, stale-while-revalidate=86400",
        "X-Stream-Cached": "HIT" if is_cached else "MISS",
    }

    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if dl == 1:
        clean_name = re.sub(r'[^\w\s\-\.\(\)]', '', file_name).strip() or f"movie_{message_id}.mp4"
        headers["Content-Disposition"] = f'attachment; filename="{clean_name}"'

    if request.method == "HEAD":
        return Response(status_code=206 if is_partial else 200, headers=headers)

    return StreamingResponse(
        _cached_stream_generator(cache_key, msg, start, end),
        status_code=206 if is_partial else 200,
        headers=headers,
        media_type=mime_type,
    )


@stream_router.get("/stream/download/{chat_id}/{message_id}")
@stream_router.head("/stream/download/{chat_id}/{message_id}")
async def download_channel_message(chat_id: str, message_id: int, request: Request) -> Response:
    """One-click binary download proxy with Content-Disposition header."""
    return await stream_channel_message(chat_id, message_id, request, dl=1)


@stream_router.get("/stream/file/{file_id}")
@stream_router.get("/stream/{file_id}")
@stream_router.head("/stream/file/{file_id}")
@stream_router.head("/stream/{file_id}")
async def stream_by_file_id(file_id: str, request: Request, size: Optional[int] = None) -> Response:
    """Stream directly by Telegram file_id with 16MB RAM header caching."""
    file_size = size or 1563733824

    range_header = request.headers.get("range")
    try:
        start, end = _parse_range(range_header, file_size)
    except RangeNotSatisfiableError:
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Range, Content-Type",
                "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
            },
        )

    explicit_end = _is_explicit_end(range_header)
    MIN_SERVE = 8 * 1024 * 1024  # 8 MiB minimum response
    is_probe_range = explicit_end and (end - start + 1) <= 128

    if not range_header:
        end = min(start + MIN_SERVE - 1, max(file_size - 1, 0))
    elif not is_probe_range and (end - start + 1) < MIN_SERVE and end < file_size - 1:
        end = min(start + MIN_SERVE - 1, file_size - 1)

    content_length = max(0, end - start + 1)
    cache_key = f"file:{file_id}"
    is_partial = bool(range_header) or (end < file_size - 1)

    is_cached = False
    async with _CACHE_LOCK:
        if cache_key in _HEADER_CACHE and start < len(_HEADER_CACHE[cache_key]):
            is_cached = True

    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, X-Stream-Cached",
        "Cache-Control": "public, max-age=2592000, stale-while-revalidate=86400",
        "X-Stream-Cached": "HIT" if is_cached else "MISS",
    }

    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        return Response(status_code=206 if is_partial else 200, headers=headers)

    return StreamingResponse(
        _cached_stream_generator(cache_key, file_id, start, end),
        status_code=206 if is_partial else 200,
        headers=headers,
        media_type="video/mp4",
    )


# Attach router to standalone app as well
app.include_router(stream_router)

