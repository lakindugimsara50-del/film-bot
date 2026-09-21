"""
stream_server.py — FastAPI streaming proxy that fetches video bytes from
Telegram (via Pyrogram) and serves them to the browser.

Handles Range requests so that Video.js and native <video> players can seek.

Run with:
    uvicorn streaming.stream_server:app --host 0.0.0.0 --port 8080
"""

import asyncio
import logging
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pyrogram import Client
from pyrogram.errors import FloodWait

from config import API_ID, API_HASH, SESSION_NAME

log = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Telegram Stream Server",
    description="Proxy that streams Telegram video files to browsers.",
    version="1.0.0",
)

# Global Pyrogram userbot client — shared across all requests
pyrogram_client: Client = None

# Size of each chunk yielded to the browser (256 KB)
_READ_CHUNK = 256 * 1024


# ─────────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup() -> None:
    """Start the Pyrogram userbot when FastAPI starts."""
    global pyrogram_client
    log.info("Starting Pyrogram userbot session '%s' …", SESSION_NAME)
    try:
        pyrogram_client = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
        )
        await pyrogram_client.start()
        log.info("Pyrogram userbot started.")
    except Exception as exc:
        log.warning("Pyrogram userbot startup deferred: %s. Note: Streaming proxy requires an active user session.", exc)


@app.on_event("shutdown")
async def shutdown() -> None:
    """Gracefully stop the Pyrogram userbot on FastAPI shutdown."""
    global pyrogram_client
    if pyrogram_client and pyrogram_client.is_connected:
        try:
            await pyrogram_client.stop()
            log.info("Pyrogram userbot stopped.")
        except Exception as exc:
            log.warning("Error stopping Pyrogram userbot: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
@app.get("/stream/{file_id}")
async def stream_file(file_id: str, request: Request) -> StreamingResponse:
    """
    Stream a Telegram file to the client.

    Supports HTTP Range requests so that video players can seek without
    re-downloading the whole file.

    Args:
        file_id: Telegram file_id of the video stored in the private channel.
        request: FastAPI Request object (used to read Range header).
    """
    if not pyrogram_client or not pyrogram_client.is_connected:
        raise HTTPException(status_code=503, detail="Stream server not ready.")

    # ── Resolve file size via Telegram ────────────────────────────────────────
    try:
        file_info = await _get_file_info(file_id)
    except Exception as exc:
        log.error("Cannot resolve file_id '%s': %s", file_id, exc)
        raise HTTPException(status_code=404, detail="File not found on Telegram.") from exc

    file_size: int = file_info["file_size"]
    mime_type: str = file_info.get("mime_type", "video/mp4")

    # ── Parse Range header (e.g. "bytes=0-1048575") ───────────────────────────
    range_header = request.headers.get("range")
    start, end = _parse_range(range_header, file_size)
    content_length = end - start + 1

    # ── Build response headers ────────────────────────────────────────────────
    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        # Allow embedding in Video.js iframes
        "Access-Control-Allow-Origin": "*",
    }

    status_code = 206 if range_header else 200

    return StreamingResponse(
        _stream_generator(file_id, start, end),
        status_code=status_code,
        headers=headers,
        media_type=mime_type,
    )


# ─────────────────────────────────────────────────────────────────────────────
@app.get("/")
async def root() -> dict:
    """Root status endpoint for Hugging Face Spaces healthcheck."""
    connected = pyrogram_client.is_connected if pyrogram_client else False
    return {
        "status": "online",
        "service": "Film Sub Bot - Streaming Proxy & Bot API",
        "pyrogram_connected": connected,
    }


@app.get("/health")
async def health() -> dict:
    """Liveness probe — returns 200 when the server is up."""
    connected = pyrogram_client.is_connected if pyrogram_client else False
    return {"status": "ok", "pyrogram_connected": connected}


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_file_info(file_id: str) -> dict:
    """
    Ask Telegram (via Pyrogram) for the file's size and MIME type.
    Retries once after a FloodWait if needed.
    """
    for attempt in range(2):
        try:
            tg_file = await pyrogram_client.get_messages(
                # We need any chat where this file_id is accessible.
                # Using the userbot's own saved messages (chat_id=0 / "me")
                # works for files the userbot has access to via a forwarded msg.
                # A more reliable approach: store (channel_id, message_id) and
                # use those here.  For now we use the generic helper.
                chat_id="me",
                message_ids=0,  # Placeholder — see note above
            )
            break
        except FloodWait as fw:
            log.warning("FloodWait %ds during file info fetch.", fw.value)
            await asyncio.sleep(fw.value)

    # Pyrogram's get_file gives us size and location
    tg_file = await pyrogram_client.get_file(file_id)
    return {
        "file_size": tg_file.file_size or 0,
        "mime_type": "video/mp4",  # Assume MP4; extend if needed
    }


async def _stream_generator(
    file_id: str, start: int, end: int
) -> AsyncGenerator[bytes, None]:
    """
    Async generator that yields *_READ_CHUNK*-sized byte chunks from Telegram
    between byte offsets *start* and *end* (inclusive).
    """
    try:
        offset = start
        remaining = end - start + 1

        async for chunk in pyrogram_client.stream_media(
            file_id,
            offset=start // _READ_CHUNK,
            limit=(remaining + _READ_CHUNK - 1) // _READ_CHUNK,
        ):
            if offset > end:
                break
            # Clip the first chunk if the range doesn't start on a boundary
            if offset < start:
                clip = start - offset
                chunk = chunk[clip:]
                offset = start
            # Clip the last chunk
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            yield chunk
            remaining -= len(chunk)
            offset += len(chunk)
            if remaining <= 0:
                break

    except FloodWait as fw:
        log.warning("FloodWait %ds while streaming file_id='%s'", fw.value, file_id)
        await asyncio.sleep(fw.value)
    except Exception as exc:
        log.error("Error streaming file_id='%s': %s", file_id, exc)
        # Gracefully close — the browser will show a stall/error


def _parse_range(range_header: str | None, file_size: int) -> tuple[int, int]:
    """
    Parse an HTTP Range header and return (start, end) byte offsets.

    Falls back to (0, file_size - 1) when no Range header is present.
    """
    if not range_header or not range_header.startswith("bytes="):
        return 0, max(file_size - 1, 0)

    try:
        range_value = range_header[len("bytes="):]
        start_str, _, end_str = range_value.partition("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        # Clamp to valid range
        start = max(0, min(start, file_size - 1))
        end = max(start, min(end, file_size - 1))
        return start, end
    except ValueError:
        return 0, file_size - 1
