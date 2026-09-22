"""
stream_server.py — FastAPI streaming proxy that fetches video bytes from
Telegram (via Pyrogram & stream_pool) and serves them to browsers.

Supports HTTP Range requests so that Video.js and mobile browsers can seek,
preload, and stream videos smoothly without re-downloading the entire file.
"""

import asyncio
import logging
from typing import Optional, Union

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from streaming.session_pool import stream_pool

log = logging.getLogger(__name__)

# APIRouter for inclusion in main.py web_app
stream_router = APIRouter(tags=["streaming"])

# Standalone FastAPI app for testing or independent worker deployment
app = FastAPI(
    title="Telegram Stream Server",
    description="High-speed streaming proxy from Telegram to web browsers.",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)


def _parse_range(range_header: Optional[str], file_size: int) -> tuple[int, int]:
    """Parse HTTP Range header e.g. 'bytes=0-1048575'."""
    if not range_header or not range_header.startswith("bytes="):
        return 0, max(file_size - 1, 0)
    try:
        val = range_header[len("bytes="):]
        s_str, _, e_str = val.partition("-")
        start = int(s_str) if s_str else 0
        end = int(e_str) if e_str else file_size - 1
        start = max(0, min(start, file_size - 1))
        end = max(start, min(end, file_size - 1))
        return start, end
    except ValueError:
        return 0, max(file_size - 1, 0)


@stream_router.options("/stream/{path:path}")
async def options_stream(path: str) -> Response:
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, Authorization",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        },
    )


@stream_router.get("/stream/status")
async def stream_status() -> dict:
    """Return streaming pool status and readiness."""
    status = stream_pool.get_status()
    status["service"] = "Telegram Cloud Streaming Edge Proxy"
    status["status"] = "ready" if status["connected_clients"] > 0 else "initializing"
    return status


@stream_router.get("/stream/ping")
async def stream_ping() -> dict:
    return {"status": "pong", "service": "stream"}


@stream_router.get("/stream/channel/{chat_id}/{message_id}")
@stream_router.head("/stream/channel/{chat_id}/{message_id}")
async def stream_channel_message(chat_id: str, message_id: int, request: Request) -> Response:
    """Stream a video stored in a Telegram channel message."""
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

    range_header = request.headers.get("range")
    start, end = _parse_range(range_header, file_size)
    content_length = end - start + 1

    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        "Cache-Control": "public, max-age=86400",
    }

    if request.method == "HEAD":
        return Response(status_code=200 if not range_header else 206, headers=headers)

    return StreamingResponse(
        stream_pool.stream_media_chunks(msg, start, end),
        status_code=206 if range_header else 200,
        headers=headers,
        media_type="video/mp4",
    )


@stream_router.get("/stream/file/{file_id}")
@stream_router.get("/stream/{file_id}")
@stream_router.head("/stream/file/{file_id}")
@stream_router.head("/stream/{file_id}")
async def stream_by_file_id(file_id: str, request: Request, size: Optional[int] = None) -> Response:
    """Stream directly by Telegram file_id."""
    file_size = size or 1563733824

    range_header = request.headers.get("range")
    start, end = _parse_range(range_header, file_size)
    content_length = end - start + 1

    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        "Cache-Control": "public, max-age=86400",
    }

    if request.method == "HEAD":
        return Response(status_code=200 if not range_header else 206, headers=headers)

    return StreamingResponse(
        stream_pool.stream_media_chunks(file_id, start, end),
        status_code=206 if range_header else 200,
        headers=headers,
        media_type="video/mp4",
    )


# Attach router to standalone app as well
app.include_router(stream_router)
