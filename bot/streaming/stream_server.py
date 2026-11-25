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
import struct
import tempfile
import time
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

            async def _on_pool_chunk(c_id, m_id, chunk_bytes, offset):
                k = _get_cache_key(c_id, m_id)
                await _save_to_header_cache(k, chunk_bytes, offset=offset)

            stream_pool.register_chunk_cache_callback(_on_pool_chunk)

            async def _bg_warmup_catalog():
                # Allow userbot sessions to fully connect before starting background warmup
                await asyncio.sleep(15.0)
                try:
                    await preload_catalog_headers(max_movies=3)
                except Exception as w_err:
                    log.warning("[StreamServer] Proactive catalog pre-warming notice: %s", w_err)
            asyncio.create_task(_bg_warmup_catalog())
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

# ── 32MB In-Memory & Persistent NVMe Disk Header Cache ────────────────────────
# LRU Cache storing up to 40 movies × 32MB (~1.2GB RAM max on VPS/Colab)
# and 8 movies × 16MB (~128MB RAM on 512MB Render instances) to prevent OOM.
_is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_NAME") or os.getenv("RENDER_SERVICE_ID"))
MAX_HEADER_CACHE_SIZE = int(os.getenv("MAX_HEADER_CACHE_SIZE", 8 if _is_render else 40))
MAX_HEADER_CACHE_BYTES = 32 * 1024 * 1024  # 32 MiB per movie — caches moov atom + first ~2 min of 1080p (<50ms seeks)
_HEADER_CACHE: OrderedDict[str, bytearray] = OrderedDict()
_CACHE_LOCK = asyncio.Lock()

STREAM_DISK_CACHE_DIR = os.getenv(
    "STREAM_DISK_CACHE_DIR",
    os.path.join(tempfile.gettempdir(), "tg_stream_headers")
)
try:
    os.makedirs(STREAM_DISK_CACHE_DIR, exist_ok=True)
except Exception:
    pass


def _disk_cache_path(key: str) -> str:
    safe = str(key).replace("-", "neg").replace(":", "_").replace("/", "_")
    return os.path.join(STREAM_DISK_CACHE_DIR, f"{safe}.bin")


def _is_real_cache_key(key: str) -> bool:
    try:
        parts = str(key).split(":")
        return len(parts) == 2 and parts[0].lstrip("-").isdigit() and parts[1].isdigit()
    except Exception:
        return False


def _read_disk_cache(key: str) -> Optional[bytearray]:
    if not _is_real_cache_key(key):
        return None
    p = _disk_cache_path(key)
    try:
        if os.path.isfile(p):
            with open(p, "rb") as f:
                data = f.read(MAX_HEADER_CACHE_BYTES)
                if data:
                    return bytearray(data)
    except Exception:
        pass
    return None


def _write_disk_cache(key: str, data: bytes, offset: int = 0) -> None:
    if not _is_real_cache_key(key):
        return
    p = _disk_cache_path(key)
    try:
        if offset == 0 or not os.path.isfile(p):
            mode = "wb" if offset == 0 else "w+b"
            with open(p, mode) as f:
                if offset > 0:
                    f.seek(offset)
                f.write(data[:MAX_HEADER_CACHE_BYTES - offset])
        else:
            with open(p, "r+b") as f:
                f.seek(offset)
                f.write(data[:MAX_HEADER_CACHE_BYTES - offset])
    except Exception:
        pass


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
            elif offset > len(buf) and offset < MAX_HEADER_CACHE_BYTES:
                buf.extend(b"\x00" * (offset - len(buf)))
                remaining = MAX_HEADER_CACHE_BYTES - len(buf)
                buf.extend(data[:remaining])
        else:
            if len(_HEADER_CACHE) >= MAX_HEADER_CACHE_SIZE:
                _HEADER_CACHE.popitem(last=False)
            if offset == 0:
                _HEADER_CACHE[key] = bytearray(data[:MAX_HEADER_CACHE_BYTES])
            else:
                buf = bytearray(b"\x00" * offset)
                buf.extend(data[:MAX_HEADER_CACHE_BYTES - offset])
                _HEADER_CACHE[key] = buf

    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write_disk_cache, key, data, offset)
    except Exception:
        _write_disk_cache(key, data, offset)


async def _get_from_header_cache(key: str, start: int, end: int) -> Optional[bytes]:
    if start < 0 or start > end:
        return None
    async with _CACHE_LOCK:
        if key not in _HEADER_CACHE:
            disk_buf = _read_disk_cache(key)
            if disk_buf:
                if len(_HEADER_CACHE) >= MAX_HEADER_CACHE_SIZE:
                    _HEADER_CACHE.popitem(last=False)
                _HEADER_CACHE[key] = disk_buf
            else:
                return None
        _HEADER_CACHE.move_to_end(key)
        buf = _HEADER_CACHE[key]
        if start < len(buf):
            slice_end = min(end + 1, len(buf))
            return bytes(buf[start:slice_end])
        return None


_WARMING_UP: set[str] = set()
_WARMUP_SEMAPHORE = asyncio.Semaphore(1)
_TAIL_CACHE: OrderedDict[str, tuple[int, bytes]] = OrderedDict()  # key -> (tail_start_offset, bytes)
_MAX_TAIL_CACHE_BYTES = 2 * 1024 * 1024
_SEEK_INDEX_CACHE: OrderedDict[str, dict] = OrderedDict()


async def _get_from_tail_cache(key: str, file_size: int, start: int, end: int) -> Optional[bytes]:
    if file_size <= 0 or start < max(0, file_size - _MAX_TAIL_CACHE_BYTES):
        return None
    async with _CACHE_LOCK:
        entry = _TAIL_CACHE.get(key)
        if not entry:
            return None
        tail_start, buf = entry
        if start >= tail_start and start < tail_start + len(buf):
            _TAIL_CACHE.move_to_end(key)
            rel_s = start - tail_start
            rel_e = min(len(buf), end - tail_start + 1)
            return buf[rel_s:rel_e]
    return None


async def _save_to_tail_cache(key: str, tail_start: int, data: bytes) -> None:
    if not data or tail_start < 0:
        return
    async with _CACHE_LOCK:
        _TAIL_CACHE[key] = (tail_start, data)
        _TAIL_CACHE.move_to_end(key)
        while len(_TAIL_CACHE) > MAX_HEADER_CACHE_SIZE:
            _TAIL_CACHE.popitem(last=False)


def _iter_mp4_boxes(buf: bytes, start: int, end: int):
    pos = start
    n = min(end, len(buf))
    while pos + 8 <= n:
        sz = struct.unpack(">I", buf[pos:pos + 4])[0]
        btype = buf[pos + 4:pos + 8]
        hdr = 8
        if sz == 1:
            if pos + 16 > n:
                break
            sz = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            hdr = 16
        elif sz == 0:
            sz = n - pos
        if sz < hdr or pos + sz > n:
            yield btype, pos + hdr, n, False
            break
        yield btype, pos + hdr, pos + sz, True
        pos += sz


def _parse_mp4_seek_index(header_bytes: bytes, file_size: int) -> dict:
    """
    Zero-subprocess pure-Python MP4 moov parser extracting exact video duration
    and sampled keyframe byte offsets (~2-4s spacing) for instant seeks & quality switches.
    """
    result = {"duration": 0.0, "file_size": file_size, "anchors": []}
    if not header_bytes or len(header_bytes) < 64:
        return result

    moov_range = None
    for btype, d_s, d_e, complete in _iter_mp4_boxes(header_bytes, 0, len(header_bytes)):
        if btype == b"moov":
            moov_range = (d_s, d_e)
            break
    if not moov_range:
        return result

    duration = 0.0
    best_anchors: list[dict] = []

    for btype, d_s, d_e, _ in _iter_mp4_boxes(header_bytes, moov_range[0], moov_range[1]):
        if btype == b"mvhd" and d_s + 20 <= d_e:
            ver = header_bytes[d_s]
            if ver == 0 and d_s + 20 <= d_e:
                ts, dur = struct.unpack(">II", header_bytes[d_s + 12:d_s + 20])
                if ts > 0:
                    duration = dur / ts
            elif ver == 1 and d_s + 32 <= d_e:
                ts, dur = struct.unpack(">IQ", header_bytes[d_s + 20:d_s + 32])
                if ts > 0:
                    duration = dur / ts
        elif btype == b"trak":
            mdia_r = None
            for tb, ts_p, te_p, _ in _iter_mp4_boxes(header_bytes, d_s, d_e):
                if tb == b"mdia":
                    mdia_r = (ts_p, te_p)
                    break
            if not mdia_r:
                continue
            timescale = 0
            is_video = False
            stbl_r = None
            for mb, ms_p, me_p, _ in _iter_mp4_boxes(header_bytes, mdia_r[0], mdia_r[1]):
                if mb == b"mdhd" and ms_p + 20 <= me_p:
                    ver = header_bytes[ms_p]
                    if ver == 0 and ms_p + 16 <= me_p:
                        timescale = struct.unpack(">I", header_bytes[ms_p + 12:ms_p + 16])[0]
                    elif ver == 1 and ms_p + 24 <= me_p:
                        timescale = struct.unpack(">I", header_bytes[ms_p + 20:ms_p + 24])[0]
                elif mb == b"hdlr" and ms_p + 12 <= me_p:
                    if header_bytes[ms_p + 8:ms_p + 12] == b"vide":
                        is_video = True
                elif mb == b"minf":
                    for nb, ns_p, ne_p, _ in _iter_mp4_boxes(header_bytes, ms_p, me_p):
                        if nb == b"stbl":
                            stbl_r = (ns_p, ne_p)
                            break
            if not is_video or not stbl_r or timescale <= 0:
                continue

            stts_entries = []
            stss_samples = None
            stsc_entries = []
            stco_offsets = []
            sample_sizes = []
            const_sample_size = 0

            for sb, ss_p, se_p, complete in _iter_mp4_boxes(header_bytes, stbl_r[0], stbl_r[1]):
                if not complete:
                    continue
                if sb == b"stts" and ss_p + 8 <= se_p:
                    cnt = struct.unpack(">I", header_bytes[ss_p + 4:ss_p + 8])[0]
                    pos = ss_p + 8
                    for _i in range(min(cnt, (se_p - pos) // 8)):
                        sc, sd = struct.unpack(">II", header_bytes[pos:pos + 8])
                        stts_entries.append((sc, sd))
                        pos += 8
                elif sb == b"stss" and ss_p + 8 <= se_p:
                    cnt = struct.unpack(">I", header_bytes[ss_p + 4:ss_p + 8])[0]
                    pos = ss_p + 8
                    stss_samples = [
                        struct.unpack(">I", header_bytes[pos + j * 4:pos + (j + 1) * 4])[0]
                        for j in range(min(cnt, (se_p - pos) // 4))
                    ]
                elif sb == b"stsc" and ss_p + 8 <= se_p:
                    cnt = struct.unpack(">I", header_bytes[ss_p + 4:ss_p + 8])[0]
                    pos = ss_p + 8
                    for _i in range(min(cnt, (se_p - pos) // 12)):
                        fc, spc, _sdi = struct.unpack(">III", header_bytes[pos:pos + 12])
                        stsc_entries.append((fc, spc))
                        pos += 12
                elif sb == b"stco" and ss_p + 8 <= se_p:
                    cnt = struct.unpack(">I", header_bytes[ss_p + 4:ss_p + 8])[0]
                    pos = ss_p + 8
                    stco_offsets = [
                        struct.unpack(">I", header_bytes[pos + j * 4:pos + (j + 1) * 4])[0]
                        for j in range(min(cnt, (se_p - pos) // 4))
                    ]
                elif sb == b"co64" and ss_p + 8 <= se_p:
                    cnt = struct.unpack(">I", header_bytes[ss_p + 4:ss_p + 8])[0]
                    pos = ss_p + 8
                    stco_offsets = [
                        struct.unpack(">Q", header_bytes[pos + j * 8:pos + (j + 1) * 8])[0]
                        for j in range(min(cnt, (se_p - pos) // 8))
                    ]
                elif sb == b"stsz" and ss_p + 12 <= se_p:
                    const_sample_size, cnt = struct.unpack(">II", header_bytes[ss_p + 4:ss_p + 12])
                    if const_sample_size == 0:
                        pos = ss_p + 12
                        sample_sizes = [
                            struct.unpack(">I", header_bytes[pos + j * 4:pos + (j + 1) * 4])[0]
                            for j in range(min(cnt, (se_p - pos) // 4))
                        ]

            if stco_offsets and stts_entries:
                # Build sampled ~1-per-chunk / keyframe time-to-offset anchors directly
                total_chunks = len(stco_offsets)
                chunk_first_sample: list[int] = [1] * total_chunks
                cur_samp = 1
                stsc_idx = 0
                for c_idx in range(total_chunks):
                    c_num = c_idx + 1
                    while stsc_idx + 1 < len(stsc_entries) and stsc_entries[stsc_idx + 1][0] <= c_num:
                        stsc_idx += 1
                    spc = stsc_entries[stsc_idx][1] if stsc_entries else 1
                    chunk_first_sample[c_idx] = cur_samp
                    cur_samp += spc

                # Map sample index -> timestamp
                def _sample_to_sec(target_s: int) -> float:
                    acc_s = 1
                    acc_ticks = 0
                    for sc, sd in stts_entries:
                        if target_s < acc_s + sc:
                            return (acc_ticks + (target_s - acc_s) * sd) / timescale
                        acc_s += sc
                        acc_ticks += sc * sd
                    return acc_ticks / timescale

                step = max(1, total_chunks // 250)
                anchors: list[dict] = []
                for c_idx in range(0, total_chunks, step):
                    t_sec = round(_sample_to_sec(chunk_first_sample[c_idx]), 2)
                    anchors.append({"t": t_sec, "offset": int(stco_offsets[c_idx])})
                if stco_offsets and (not anchors or anchors[-1]["offset"] != int(stco_offsets[-1])):
                    anchors.append({"t": round(_sample_to_sec(chunk_first_sample[-1]), 2), "offset": int(stco_offsets[-1])})
                if anchors:
                    best_anchors = anchors
                    break

    result["duration"] = round(duration, 2)
    result["anchors"] = best_anchors
    return result


async def warmup_channel_message(chat_id: Union[int, str], message_id: int, target_bytes: int = 2 * 1024 * 1024, start_offset_req: int = 0) -> bool:
    """
    Pre-buffers video bytes into RAM/Chunk cache in background.
    Supports both initial moov warming (start_offset_req=0) and middle-seek range warming.
    """
    cache_key = _get_cache_key(chat_id, message_id)
    if start_offset_req > 0:
        async def _do_seek_warmup():
            async with _WARMUP_SEMAPHORE:
                try:
                    info = await stream_pool.get_media_info(chat_id, message_id)
                    msg = info["message"]
                    file_size = info["file_size"]
                    if start_offset_req < file_size:
                        await stream_pool.prewarm_chunk_range(msg, start_offset_req, total_bytes=min(target_bytes, 4 * 1024 * 1024))
                except Exception as sw_err:
                    log.debug("[StreamServer] Seek warmup note for %s@%d: %s", cache_key, start_offset_req, sw_err)
        asyncio.create_task(_do_seek_warmup())
        return True

    async with _CACHE_LOCK:
        if cache_key in _HEADER_CACHE and len(_HEADER_CACHE[cache_key]) >= min(target_bytes, MAX_HEADER_CACHE_BYTES):
            return False

    if cache_key in _WARMING_UP:
        return False
    _WARMING_UP.add(cache_key)

    async def _do_warmup():
        async with _WARMUP_SEMAPHORE:
            try:
                info = await stream_pool.get_media_info(chat_id, message_id)
                msg = info["message"]
                file_size = info["file_size"]
                warm_limit = min(target_bytes, file_size, MAX_HEADER_CACHE_BYTES) - 1
                if warm_limit <= 0:
                    return

                async with _CACHE_LOCK:
                    start_offset = len(_HEADER_CACHE[cache_key]) if cache_key in _HEADER_CACHE else 0

                if start_offset >= warm_limit:
                    return

                log.info("[StreamServer] Background warmup started for %s (%d-%d bytes)...", cache_key, start_offset, warm_limit)
                cur = start_offset
                async for chunk in stream_pool.stream_media_chunks(msg, start_offset, warm_limit):
                    if not chunk:
                        break
                    await _save_to_header_cache(cache_key, chunk, offset=cur)
                    cur += len(chunk)
                    if cur > warm_limit:
                        break
                log.info("[StreamServer] Background warmup completed for %s (%d bytes cached).", cache_key, cur)
            except Exception as err:
                log.warning("[StreamServer] Background warmup error for %s: %s", cache_key, err)
            finally:
                _WARMING_UP.discard(cache_key)

    asyncio.create_task(_do_warmup())
    return True


def _find_catalog_path() -> Optional[str]:
    """Locate website/data/movies.json across runtime environments."""
    env_path = os.getenv("CATALOG_JSON_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "..", "..", "website", "data", "movies.json"),
        os.path.join(base_dir, "..", "data", "movies.json"),
        os.path.join(os.getcwd(), "website", "data", "movies.json"),
        os.path.join(os.getcwd(), "data", "movies.json"),
    ]
    for p in candidates:
        norm = os.path.normpath(p)
        if os.path.isfile(norm):
            return norm
    return None


async def preload_catalog_headers(max_movies: int = 3, bytes_per_stream: int = 2 * 1024 * 1024) -> int:
    """
    Proactively warms up initial 2MB for the top 3 newest movies into RAM _HEADER_CACHE.
    Runs gently and sequentially to ensure Telegram MTProto workers are never saturated.
    """
    cat_path = _find_catalog_path()
    if not cat_path:
        log.info("[StreamServer] No catalog movies.json found for proactive pre-warming.")
        return 0

    try:
        import json
        with open(cat_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        movies = data.get("movies", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    except Exception as e:
        log.warning("[StreamServer] Could not parse catalog for pre-warming: %s", e)
        return 0

    targets: list[tuple[Union[int, str], int]] = []
    movie_count = 0
    # Inspect movies in reverse order (newest first)
    for m in reversed(movies):
        if movie_count >= max_movies:
            break
        c_id = m.get("channel_chat_id") or "-1004325759505"
        m_id = m.get("message_id")
        vm = m.get("variant_media") or {}
        has_movie_target = False
        if isinstance(vm, dict):
            for q in ("720p", "1080p", "480p"):
                v = vm.get(q)
                if isinstance(v, dict) and v.get("message_id"):
                    targets.append((c_id, int(v["message_id"])))
                    has_movie_target = True
        if m_id and isinstance(m_id, int) and m_id > 0:
            targets.append((c_id, m_id))
            has_movie_target = True
        if has_movie_target:
            movie_count += 1

    seen = set()
    unique_targets = []
    for pair in targets:
        if pair not in seen:
            seen.add(pair)
            unique_targets.append(pair)

    warmed_count = 0
    for c_id, m_id in unique_targets:
        if stream_pool.has_recent_viewer():
            log.info("[StreamServer] Yielding catalog warmup to active viewer stream.")
            break
        started = await warmup_channel_message(c_id, m_id, target_bytes=bytes_per_stream)
        if started:
            warmed_count += 1
            await asyncio.sleep(1.0)

    log.info("[StreamServer] Proactive catalog warmup initiated for %d streams (%d movies).", warmed_count, movie_count)
    return warmed_count



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
@stream_router.head("/stream/status")
async def stream_status() -> dict:
    """Return streaming pool status and readiness."""
    status = stream_pool.get_status()
    status["service"] = "Telegram Cloud Streaming Edge Proxy (Ultra-Smooth v3.0)"
    status["status"] = "ready" if status["connected_clients"] > 0 else "initializing"
    status["header_cache_entries"] = len(_HEADER_CACHE)
    status["max_cache_mb"] = MAX_HEADER_CACHE_SIZE * (MAX_HEADER_CACHE_BYTES // (1024 * 1024))
    try:
        import subprocess
        commit = (await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL)).strip()
        status["git_commit"] = commit
    except Exception:
        status["git_commit"] = "unknown"
    return status


@stream_router.post("/stream/admin/restart")
@stream_router.get("/stream/admin/restart")
async def stream_restart() -> dict:
    """Trigger clean restart of bot process on Colab with git pull (exit code 42)."""
    async def _do_exit():
        await asyncio.sleep(0.5)
        import sys
        sys.exit(42)
    asyncio.create_task(_do_exit())
    return {"status": "restarting", "code": 42, "message": "Bot process restarting with latest code in 0.5s..."}



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


@stream_router.get("/stream/warmup/catalog")
@stream_router.post("/stream/warmup/catalog")
async def warmup_catalog_stream(limit: int = 10) -> dict:
    """Pre-warm headers for top movies in catalog into RAM."""
    warmed = await preload_catalog_headers(max_movies=limit)
    return {
        "status": "warming",
        "warmed_streams": warmed,
        "max_movies": limit,
    }


@stream_router.get("/stream/warmup/{chat_id}/{message_id}")
@stream_router.post("/stream/warmup/{chat_id}/{message_id}")
async def warmup_channel_stream(
    chat_id: str,
    message_id: int,
    start_byte: int = 0,
    bytes: int = 0,
) -> dict:
    """Preload video header (or target seek byte offset) into RAM cache for zero-buffering start/switch."""
    if start_byte <= 0 and bytes <= 0:
        started = await warmup_channel_message(chat_id, message_id)
    else:
        clamped_bytes = max(512 * 1024, min(int(bytes or 4 * 1024 * 1024), 8 * 1024 * 1024))
        started = await warmup_channel_message(
            chat_id,
            message_id,
            target_bytes=clamped_bytes,
            start_offset_req=max(0, int(start_byte)),
        )
    return {
        "status": "warming" if started else "already_cached_or_warming",
        "chat_id": chat_id,
        "message_id": message_id,
        "start_byte": max(0, int(start_byte)),
    }


@stream_router.get("/stream/seekinfo/{chat_id}/{message_id}")
@stream_router.head("/stream/seekinfo/{chat_id}/{message_id}")
async def stream_seek_info(chat_id: str, message_id: int) -> JSONResponse:
    """Return MP4 moov keyframe time->byte seek anchors for instant middle-seek & zero-reload quality switching."""
    cache_key = _get_cache_key(chat_id, message_id)
    if cache_key in _SEEK_INDEX_CACHE:
        _SEEK_INDEX_CACHE.move_to_end(cache_key)
        return JSONResponse(
            content=_SEEK_INDEX_CACHE[cache_key],
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=86400",
            },
        )

    try:
        info = await stream_pool.get_media_info(chat_id, message_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Video not found: {exc}")

    file_size = info["file_size"]
    msg = info["message"]
    probe_limit = min(file_size, 4 * 1024 * 1024)
    hdr_bytes = await _get_from_header_cache(cache_key, 0, probe_limit - 1) if probe_limit > 0 else b""
    if not hdr_bytes or len(hdr_bytes) < min(file_size, 512 * 1024):
        buf = bytearray()
        cur = 0
        async for chunk in stream_pool.stream_media_chunks(msg, 0, max(0, probe_limit - 1)):
            if not chunk:
                break
            await _save_to_header_cache(cache_key, chunk, offset=cur)
            buf.extend(chunk)
            cur += len(chunk)
            if cur >= probe_limit:
                break
        hdr_bytes = bytes(buf)

    parsed = await asyncio.to_thread(_parse_mp4_seek_index, hdr_bytes or b"", file_size)
    if parsed.get("anchors"):
        _SEEK_INDEX_CACHE[cache_key] = parsed
        _SEEK_INDEX_CACHE.move_to_end(cache_key)
        while len(_SEEK_INDEX_CACHE) > MAX_HEADER_CACHE_SIZE:
            _SEEK_INDEX_CACHE.popitem(last=False)

    return JSONResponse(
        content=parsed,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=86400",
        },
    )



async def _cached_stream_generator(
    cache_key: str,
    msg_obj,
    start: int,
    end: int,
    file_size: int = 0,
) -> AsyncGenerator[bytes, None]:
    """
    Yields video bytes for range [start, end].
    Serves from RAM header cache, tail metadata cache, or aligned 1 MiB chunk cache in <5ms,
    then streams remaining bytes via multi-session MTProto pipelining.
    """
    cur_pos = start
    is_tail_read = bool(file_size > _MAX_TAIL_CACHE_BYTES and start >= file_size - _MAX_TAIL_CACHE_BYTES)
    tail_buf = bytearray() if is_tail_read else None

    try:
        cached_part = await _get_from_header_cache(cache_key, cur_pos, end)
        if cached_part:
            yield cached_part
            cur_pos += len(cached_part)

        if cur_pos <= end and is_tail_read:
            tail_hit = await _get_from_tail_cache(cache_key, file_size, cur_pos, end)
            if tail_hit:
                yield tail_hit
                cur_pos += len(tail_hit)

        if cur_pos <= end:
            async for chunk in stream_pool.stream_media_chunks(msg_obj, cur_pos, end):
                if not chunk:
                    break
                # Populate 32MB RAM cache immediately in real-time if reading within initial header
                if cur_pos < MAX_HEADER_CACHE_BYTES:
                    try:
                        await _save_to_header_cache(cache_key, chunk, offset=cur_pos)
                    except Exception:
                        pass
                if tail_buf is not None and len(tail_buf) < _MAX_TAIL_CACHE_BYTES:
                    tail_buf.extend(chunk[:_MAX_TAIL_CACHE_BYTES - len(tail_buf)])
                yield chunk
                cur_pos += len(chunk)

        if tail_buf is not None and len(tail_buf) > 0 and cur_pos > end:
            await _save_to_tail_cache(cache_key, start, bytes(tail_buf))
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

    sec_dest = str(request.headers.get("sec-fetch-dest", "")).lower()
    ua = str(request.headers.get("user-agent", "")).lower()
    is_downloader_ua = any(tool in ua for tool in ("idm", "adm", "1dm", "fdm", "aria2", "curl", "wget", "python", "httpx", "aiohttp", "downlo"))

    is_download = bool(
        dl == 1
        or str(request.query_params.get("dl", "")).lower() in ("1", "true", "yes")
        or str(request.query_params.get("download", "")).lower() in ("1", "true", "yes")
        or "dl" in request.query_params
        or "/download/" in request.url.path
        or sec_dest in ("document", "download")
        or request.headers.get("x-download") == "1"
        or request.headers.get("x-purpose") == "download"
        or is_downloader_ua
    )

    effective_serve = MIN_SERVE
    try:
        q_params = getattr(request, "query_params", None)
        raw_burst = q_params.get("burst") if hasattr(q_params, "get") else None
        if raw_burst is not None and str(raw_burst).isdigit():
            effective_serve = max(1024 * 1024, min(int(raw_burst), 12 * 1024 * 1024))
    except Exception:
        effective_serve = MIN_SERVE

    if is_download:
        # Full file one-click download: serve entire remaining file unless client specified an explicit closed range
        if not range_header or not explicit_end:
            end = max(file_size - 1, 0)
        if not range_header:
            start = 0
    elif not range_header:
        # Initial request without range -> serve first 8 MiB (or requested adaptive burst)
        end = min(start + effective_serve - 1, max(file_size - 1, 0))
    elif not explicit_end:
        # Open-ended range (e.g. bytes=0- or seek bytes=900000000-) -> serve adaptive burst slice
        end = min(start + effective_serve - 1, file_size - 1)

    content_length = max(0, end - start + 1)
    cache_key = _get_cache_key(chat_id, message_id)
    is_partial = bool(range_header) or (not is_download and end < file_size - 1)

    if not is_download:
        stream_pool.note_viewer_activity(20.0)

    is_cached = False
    async with _CACHE_LOCK:
        if cache_key in _HEADER_CACHE and start < len(_HEADER_CACHE[cache_key]):
            is_cached = True
        elif cache_key in _TAIL_CACHE:
            t_start, t_buf = _TAIL_CACHE[cache_key]
            if t_start <= start < t_start + len(t_buf):
                is_cached = True
    if not is_cached and hasattr(stream_pool, "_get_chunk_cache"):
        from streaming.session_pool import CHUNK_SIZE as _POOL_CHUNK_SIZE
        if stream_pool._get_chunk_cache(cache_key, start // _POOL_CHUNK_SIZE) is not None:
            is_cached = True

    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, Content-Disposition, X-Stream-Cached",
        "Cache-Control": "public, max-age=86400, stale-while-revalidate=3600",
        "Vary": "Range",
        "X-Accel-Buffering": "no",  # prevent Cloudflare/Nginx proxy buffering (-300ms latency)
        "X-Stream-Cached": "HIT" if is_cached else "MISS",
    }

    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if is_download:
        clean_name = re.sub(r'[^\w\s\-\.\(\)]', '', file_name).strip() or f"movie_{message_id}.mp4"
        headers["Content-Disposition"] = f'attachment; filename="{clean_name}"'

    if request.method == "HEAD":
        return Response(status_code=206 if is_partial else 200, headers=headers)

    return StreamingResponse(
        _cached_stream_generator(cache_key, msg, start, end, file_size=file_size),
        status_code=206 if is_partial else 200,
        headers=headers,
        media_type=mime_type,
    )


@stream_router.get("/stream/download/{chat_id}/{message_id}")
@stream_router.head("/stream/download/{chat_id}/{message_id}")
async def download_channel_message(chat_id: str, message_id: int, request: Request) -> Response:
    """One-click binary download proxy with Content-Disposition header."""
    return await stream_channel_message(chat_id, message_id, request, dl=1)


@stream_router.get("/stream/download/{file_id}")
@stream_router.head("/stream/download/{file_id}")
async def download_by_file_id(file_id: str, request: Request, size: Optional[int] = None) -> Response:
    """One-click binary download proxy for file_id with Content-Disposition header."""
    return await stream_by_file_id(file_id, request, size=size, dl=1)


@stream_router.get("/stream/file/{file_id}")
@stream_router.get("/stream/{file_id}")
@stream_router.head("/stream/file/{file_id}")
@stream_router.head("/stream/{file_id}")
async def stream_by_file_id(file_id: str, request: Request, size: Optional[int] = None, dl: Optional[int] = None) -> Response:
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
                "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, Content-Disposition",
            },
        )

    explicit_end = _is_explicit_end(range_header)
    MIN_SERVE = 8 * 1024 * 1024  # 8 MiB minimum response
    is_probe_range = explicit_end and (end - start + 1) <= 128

    sec_dest = str(request.headers.get("sec-fetch-dest", "")).lower()
    ua = str(request.headers.get("user-agent", "")).lower()
    is_downloader_ua = any(tool in ua for tool in ("idm", "adm", "1dm", "fdm", "aria2", "curl", "wget", "python", "httpx", "aiohttp", "downlo"))

    is_download = bool(
        dl == 1
        or str(request.query_params.get("dl", "")).lower() in ("1", "true", "yes")
        or str(request.query_params.get("download", "")).lower() in ("1", "true", "yes")
        or "dl" in request.query_params
        or "/download/" in request.url.path
        or sec_dest in ("document", "download")
        or request.headers.get("x-download") == "1"
        or request.headers.get("x-purpose") == "download"
        or is_downloader_ua
    )

    if is_download:
        if not range_header or not explicit_end:
            end = max(file_size - 1, 0)
        if not range_header:
            start = 0
    elif not range_header:
        end = min(start + MIN_SERVE - 1, max(file_size - 1, 0))
    elif not is_probe_range and (end - start + 1) < MIN_SERVE and end < file_size - 1:
        end = min(start + MIN_SERVE - 1, file_size - 1)

    content_length = max(0, end - start + 1)
    cache_key = f"file:{file_id}"
    is_partial = bool(range_header) or (not is_download and end < file_size - 1)

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
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, Content-Disposition, X-Stream-Cached",
        "Cache-Control": "public, max-age=2592000, stale-while-revalidate=86400",
        "X-Stream-Cached": "HIT" if is_cached else "MISS",
    }

    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if is_download:
        headers["Content-Disposition"] = f'attachment; filename="video_{file_id[:10]}.mp4"'

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

