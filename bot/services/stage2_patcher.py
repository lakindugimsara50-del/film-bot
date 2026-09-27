"""
stage2_patcher.py — Patch movies.json after Telegram upload completes.

After Stage 2 upload finishes, this patches the existing movies.json entry
(which was created by Stage 1 with embed-only streams and empty downloads)
to add real Telegram download buttons and update telegram_status.
"""
import logging
import os
import urllib.parse
from typing import Optional

from services import github_service

log = logging.getLogger(__name__)


async def patch_movie_downloads(
    movie_slug: str,
    upload_results: dict,
    subtitle_url: str = "",
):
    """
    Patch an existing movies.json entry with real Telegram download buttons.
    
    upload_results format:
    {
        "1080p": {"message_id": 1234, "file_id": "BQACAg...", "size_bytes": 1234567890},
        "720p":  {"message_id": 1235, "file_id": "BQACAg...", "size_bytes": 400000000},
        "480p":  {...},
        "360p":  {...},
    }
    """
    data, sha = await github_service.get_movies_json()
    movies = data.get("movies", [])
    
    target = None
    for m in movies:
        if m.get("slug") == movie_slug or m.get("id") == movie_slug:
            target = m
            break
    
    if not target:
        log.warning("[Stage2Patcher] Movie slug '%s' not found in movies.json", movie_slug)
        return False
    
    # Build download entries
    downloads = target.get("downloads", [])
    quality_labels = {
        "1080p": "1080p Full HD (Sinhala Sub Merged)",
        "720p": "720p HD (Sinhala Sub Merged)",
        "480p": "480p SD",
        "360p": "360p Mobile",
    }
    
    for quality in ["1080p", "720p", "480p", "360p"]:
        result = upload_results.get(quality)
        if not result:
            continue
        size_bytes = result.get("size_bytes", 0)
        size_str = _human_size(size_bytes)
        msg_id = result.get("message_id", 0)
        file_id = result.get("file_id", "")
        title_enc = urllib.parse.quote(target.get("title", "Film"))
        
        downloads.append({
            "quality": quality,
            "label": quality_labels.get(quality, quality),
            "size": size_str,
            "size_bytes": size_bytes,
            "file_id": file_id,
            "message_id": msg_id,
            "telegram_url": f"https://t.me/{os.environ.get('CHANNEL_USERNAME', 'c')}/{msg_id}",
            "format": "MP4",
            "host": "Telegram",
            "sub_merged": True,
            "subtitle_merged": True,
        })
    
    target["downloads"] = downloads
    target["telegram_status"] = "complete"
    
    # Add Telegram Super Player as first stream (Server 0)
    best = upload_results.get("1080p") or upload_results.get("720p")
    if best and best.get("file_id"):
        tg_stream = {
            "server": "Server 0",
            "label": "⚡ Super Player (Telegram Cloud HD • Auto Sinhala Sub)",
            "type": "video/mp4",
            "mode": "super_chunk",
            "file_id": best["file_id"],
            "message_id": best["message_id"],
            "quality": "1080p",
        }
        # Prepend Telegram player as first server
        existing = [s for s in target.get("streams", []) if s.get("mode") != "super_chunk"]
        target["streams"] = [tg_stream] + existing
        target["file_id"] = best["file_id"]
        target["message_id"] = best["message_id"]
    
    if subtitle_url:
        target["subtitle_url"] = subtitle_url
        # Update VidLink stream with sub injection
        for stream in target["streams"]:
            url = stream.get("stream_url", "")
            if "vidlink.pro" in url and "sub.Sinhala" not in url:
                stream["stream_url"] = f"{url}?sub.Sinhala={urllib.parse.quote(subtitle_url, safe='')}"
    
    # Commit to GitHub
    await github_service._commit_movies_json(
        data, sha,
        f"feat(downloads): add Telegram downloads for {movie_slug}"
    )
    log.info("[Stage2Patcher] Patched downloads for '%s'", movie_slug)
    return True


def _human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "Unknown"
    gb = size_bytes / (1024 ** 3)
    if gb >= 0.1:
        return f"{gb:.2f} GB"
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.1f} MB"
