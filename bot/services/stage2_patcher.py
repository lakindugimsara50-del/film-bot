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

import config
from services import github_service

log = logging.getLogger(__name__)


async def patch_movie_downloads(
    movie_slug: str,
    upload_results: dict,
    subtitle_url: str = "",
    client: Optional[object] = None,
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
    
    # Build download entries: preserve existing downloads that are not replaced
    incoming_qualities = {q for q in ["1080p", "720p", "480p", "360p"] if upload_results.get(q) and upload_results.get(q).get("message_id")}
    downloads = [d for d in target.get("downloads", []) if d.get("quality") not in incoming_qualities]
    quality_labels = {
        "1080p": "1080p Full HD (Sinhala Sub Merged)",
        "720p": "720p HD (Sinhala Sub Merged)",
        "480p": "480p SD",
        "360p": "360p Mobile",
    }
    
    for quality in ["1080p", "720p", "480p", "360p"]:
        result = upload_results.get(quality)
        if not result or not result.get("message_id"):
            continue
        msg_id = result.get("message_id", 0)
        if not msg_id or int(msg_id) <= 0:
            continue
        size_bytes = result.get("size_bytes", 0) or result.get("file_size", 0)
        size_str = _human_size(size_bytes)
        file_id = result.get("file_id", "")
        title_enc = urllib.parse.quote(target.get("title", "Film"))
        
        tg_ch_raw = str(abs(int(target.get("channel_chat_id") or getattr(config, "PUBLIC_CHANNEL_ID", 0) or getattr(config, "PRIVATE_CHANNEL_ID", 0))))
        if tg_ch_raw.startswith("100"):
            tg_ch_raw = tg_ch_raw[3:]
        ch_user = os.environ.get("CHANNEL_USERNAME", "").strip().lstrip("@")
        tg_url = f"https://t.me/{ch_user}/{msg_id}" if ch_user else f"https://t.me/c/{tg_ch_raw}/{msg_id}"
        
        downloads.append({
            "quality": quality,
            "label": quality_labels.get(quality, quality),
            "size": size_str,
            "size_bytes": size_bytes,
            "file_id": file_id,
            "message_id": msg_id,
            "url": tg_url,
            "telegram_url": tg_url,
            "format": "MP4",
            "host": "Telegram",
            "sub_merged": True,
            "subtitle_merged": True,
        })
    
    _q_order = {"1080p": 0, "720p": 1, "480p": 2, "360p": 3}
    downloads.sort(key=lambda d: _q_order.get(d.get("quality", "").split()[0], 99))
    target["downloads"] = downloads
    target["telegram_status"] = "complete"

    # Sync multi-quality variant_media so frontend web player has direct access
    target["variant_media"] = {
        q: {
            "file_id": upload_results[q].get("file_id", ""),
            "message_id": upload_results[q].get("message_id", 0),
            "stream_url": upload_results[q].get("stream_url", ""),
            "size_bytes": upload_results[q].get("size_bytes", 0) or upload_results[q].get("file_size", 0),
        }
        for q in ["1080p", "720p", "480p", "360p"]
        if upload_results.get(q) and (upload_results[q].get("file_id") or upload_results[q].get("message_id"))
    }

    best = upload_results.get("1080p") or upload_results.get("720p") or upload_results.get("480p")
    if best and best.get("file_id"):
        target["file_id"] = best["file_id"]
        target["message_id"] = best.get("message_id", 0)
    
    if subtitle_url:
        target["subtitle_url"] = subtitle_url
        target["has_sinhala_sub"] = True
        target["sub_merged"] = True
        target["subtitles"] = [
            {
                "language": "Sinhala",
                "label": "සිංහල",
                "url": subtitle_url,
                "default": True,
                "srclang": "si",
            }
        ]
        # Update VidLink stream with sub injection
        for stream in target.get("streams", []):
            url = stream.get("stream_url", "")
            if "vidlink.pro" in url and "sub_file=" not in url:
                sep = "&" if "?" in url else "?"
                stream["stream_url"] = f"{url}{sep}sub_file={urllib.parse.quote(subtitle_url, safe='')}&sub_label=Sinhala&sub=true"
    
    # Commit to GitHub
    await github_service._commit_movies_json(
        data, sha,
        f"feat(downloads): add Telegram downloads for {movie_slug}"
    )
    log.info("[Stage2Patcher] Patched downloads for '%s'", movie_slug)

    # Automatically update channel announcement post or create one if previously deferred
    ch_post_id = target.get("channel_post_id")
    target_ch_id = target.get("channel_chat_id") or getattr(config, "PUBLIC_CHANNEL_ID", 0) or getattr(config, "PRIVATE_CHANNEL_ID", 0)
    if client and target_ch_id:
        if ch_post_id:
            try:
                from handlers.announce import update_channel_post
                await update_channel_post(
                    client=client,
                    public_channel_id=target_ch_id,
                    message_id=ch_post_id,
                    movie=target,
                )
                log.info("[Stage2Patcher] Channel post %s in chat %s updated with download links for '%s'", ch_post_id, target_ch_id, movie_slug)
            except Exception as ann_err:
                log.warning("[Stage2Patcher] Failed to update channel announcement: %s", ann_err)
        else:
            try:
                from handlers.announce import post_to_channel
                ch_msg = await post_to_channel(client, target, target_ch_id)
                if ch_msg:
                    target["channel_post_id"] = getattr(ch_msg, "id", None)
                    target["channel_chat_id"] = target_ch_id
                    log.info("[Stage2Patcher] Deferred channel post %s created in chat %s for '%s'", target["channel_post_id"], target_ch_id, movie_slug)
                    cur_data, cur_sha = await github_service.get_movies_json()
                    for m in cur_data.get("movies", []):
                        if m.get("slug") == movie_slug or m.get("id") == movie_slug:
                            m["channel_post_id"] = target["channel_post_id"]
                            m["channel_chat_id"] = target_ch_id
                            break
                    await github_service._commit_movies_json(
                        cur_data, cur_sha,
                        f"feat(channel): record channel_post_id for {movie_slug}"
                    )
            except Exception as ann_err:
                log.warning("[Stage2Patcher] Failed to post deferred channel announcement: %s", ann_err)

    return True


def _human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "Unknown"
    gb = size_bytes / (1024 ** 3)
    if gb >= 0.1:
        return f"{gb:.2f} GB"
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.1f} MB"
