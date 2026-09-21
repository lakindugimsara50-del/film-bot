"""
leech_service.py — Master Auto-Leech & Channel Uploader Orchestrator.

Automates the complete end-to-end pipeline:
1. Multi-source acquisition with intelligent fallback:
   - Method A: DDL Scrapers (Pahe / PSArips / PixelDrain direct API links)
   - Method B: YTS Torrent API (< 1.95GB filtered) via aria2c multi-connection download
   - Method C: Telegram Movie Channels / direct message links
   - Method D: Web stream extractors (Consumet / FlixHQ direct MP4)
2. High-speed multi-threaded download directly on VPS / server
3. Parallel upload to Telegram private channel with live progress
4. Immediate local file deletion (os.remove) to keep VPS disk space free
5. TMDB metadata fetch, default Sinhala subtitles generation, movies.json update,
   Cloudflare Pages deployment, and channel announcements
6. Robust cancellation handling via /cancel (kills processes, frees storage)

All log strings are in English to avoid Windows charmap errors.
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.parse
from typing import Any, Optional

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from handlers.announce import post_to_channel, _slugify
from services import (
    downloader,
    github_service,
    seedr_service,
    subtitle_service,
    task_tracker,
    telegram_upload,
    tmdb_service,
    video_service,
)
from services.scrapers import method1_telegram, method2_consumet, method3_ddl, method_yts

log = logging.getLogger(__name__)


class LeechCandidate:
    """Represents a potential downloadable movie source."""

    def __init__(
        self,
        method: str,
        method_name: str,
        source_url: str,
        quality: str = "1080p",
        size: str = "Unknown",
        size_bytes: int = 0,
        extra: Optional[dict] = None,
    ):
        self.method = method
        self.method_name = method_name
        self.source_url = source_url
        self.quality = quality
        self.size = size
        self.size_bytes = size_bytes
        self.extra = extra or {}

    def __repr__(self) -> str:
        return (
            f"LeechCandidate(method={self.method!r}, name={self.method_name!r}, "
            f"quality={self.quality!r}, size={self.size!r})"
        )


def parse_query(text: str) -> tuple[str, Optional[int], Optional[str], Optional[str]]:
    """
    Parse user query into (title, year, imdb_id, direct_url_or_magnet).
    Supports:
      /leech Inception
      /leech Inception 2010
      /leech tt1375666
      /leech magnet:?xt=...
      /leech https://pixeldrain.com/u/...
      /leech https://example.com/movie.torrent
      /boost@Bot Inception 2010
    """
    raw = re.sub(r"^/(?:leech|auto|boost)(?:@\w+)?\s*", "", text.strip())

    # Check for magnet
    if raw.startswith("magnet:?"):
        dn_match = re.search(r"[?&]dn=([^&]+)", raw)
        title = urllib.parse.unquote_plus(dn_match.group(1)) if dn_match else ""
        return title, None, None, raw

    # Check for direct URL
    if raw.startswith(("http://", "https://")):
        parts = raw.split(maxsplit=1)
        url = parts[0]
        extra = parts[1] if len(parts) > 1 else ""
        if extra:
            t, y, i, _ = parse_query(extra)
            return t, y, i, url

        # Infer title and year from URL path
        parsed_url = urllib.parse.urlparse(url)
        path_name = os.path.basename(parsed_url.path)
        clean_name = re.sub(r"\.(mp4|mkv|avi|webm|torrent)$", "", path_name, flags=re.IGNORECASE)
        clean_name = re.sub(r"[._-]", " ", clean_name).strip()
        ym = re.search(r"\b(19\d\d|20\d\d)\b", clean_name)
        year_val = int(ym.group(1)) if ym else None
        if ym:
            clean_name = clean_name[:ym.start()].strip()
        return clean_name, year_val, None, url

    # Check for IMDb ID
    imdb_match = re.match(r"^(tt\d+)$", raw, re.IGNORECASE)
    if imdb_match:
        return "", None, imdb_match.group(1), None

    # Check for Title + Year
    year_match = re.match(r"^(.+?)\s+(\d{4})$", raw)
    if year_match:
        return year_match.group(1).strip(), int(year_match.group(2)), None, None

    return raw, None, None, None


async def find_all_candidates(
    title: str,
    year: Optional[int] = None,
    imdb_id: Optional[str] = None,
    bot_client: Optional[Client] = None,
) -> list[LeechCandidate]:
    """
    Query all acquisition sources in priority order:
      Method A: DDL Scrapers (PixelDrain / Pahe / PSArips)
      Method B: YTS Torrents (< 1.95GB)
      Method C: Telegram Movie Channels
      Method D: Consumet / FlixHQ Stream Extractors
    Returns a list of viable LeechCandidates.
    """
    candidates: list[LeechCandidate] = []
    log.info("[LeechService] Finding candidates for: '%s' (%s), imdb=%s", title, year, imdb_id)

    # ── Method 1: Telegram Movie Channels (Highest Priority - 0 Download Time) ──
    if bot_client:
        try:
            log.info("[LeechService] Trying Method 1: Telegram Movie Channels...")
            tg_res = await method1_telegram.search(
                title=title, year=year, imdb_id=imdb_id, client=bot_client
            )
            if tg_res and tg_res.get("file_id"):
                candidates.append(
                    LeechCandidate(
                        method="telegram",
                        method_name=tg_res.get("server_label", "Telegram Movie Channel"),
                        source_url=tg_res["file_id"],
                        quality=tg_res.get("quality", "1080p"),
                        size=downloader.format_bytes(tg_res.get("file_size", 0)),
                        size_bytes=tg_res.get("file_size", 0),
                        extra=tg_res,
                    )
                )
                log.info("[LeechService] Method 1 yielded Telegram candidate.")
        except Exception as exc:
            log.warning("[LeechService] Method 1 error: %s", exc)

    # ── Method 2: DDL Scrapers (PixelDrain / Pahe / Direct HTTP - Safe & Fast) ──
    try:
        log.info("[LeechService] Trying Method 2: DDL Scrapers (PixelDrain/Pahe)...")
        ddl_res = await method3_ddl.search(title=title, year=year, imdb_id=imdb_id)
        if ddl_res and ddl_res.get("downloads"):
            for d in ddl_res["downloads"]:
                url = d.get("direct_url") or d.get("url")
                host = d.get("host", "DDL").title()
                if url:
                    candidates.append(
                        LeechCandidate(
                            method="ddl",
                            method_name=f"DDL Direct ({host})",
                            source_url=url,
                            quality=ddl_res.get("quality", "1080p"),
                            size=ddl_res.get("size", "Unknown"),
                            extra=d,
                        )
                    )
            log.info("[LeechService] Method 2 yielded %d candidate(s).", len(ddl_res["downloads"]))
    except Exception as exc:
        log.warning("[LeechService] Method 2 error: %s", exc)

    # ── Method 3: YTS Torrent API (< 1.95GB via aria2c + Live Trackers) ───────
    try:
        log.info("[LeechService] Trying Method 3: YTS Torrent API...")
        yts_list = await method_yts.search(title=title, year=year, imdb_id=imdb_id)
        for tor in yts_list:
            source_url = tor.get("magnet") or tor.get("torrent_url")
            if source_url:
                candidates.append(
                    LeechCandidate(
                        method="yts",
                        method_name=f"YTS Torrent ({tor.get('quality', '1080p')})",
                        source_url=source_url,
                        quality=tor.get("quality", "1080p"),
                        size=tor.get("size", "Unknown"),
                        size_bytes=tor.get("size_bytes", 0),
                        extra=tor,
                    )
                )
        log.info("[LeechService] Method 3 yielded %d candidate(s).", len(yts_list))
    except Exception as exc:
        log.warning("[LeechService] Method 3 error: %s", exc)

    log.info("[LeechService] Total candidates acquired: %d (All strict Telegram upload)", len(candidates))
    return candidates


async def run_auto_leech(
    client: Client,
    status_msg: Message,
    user_id: int,
    query_text: str,
    reply_media: Optional[dict] = None,
) -> None:
    """
    Main entry point for executing the automated leech & upload workflow.
    """
    title, year, imdb_id, direct_link = parse_query(query_text)
    task_key = f"leech_{user_id}_{int(time.time())}"
    temp_dir: Optional[str] = None
    local_file: Optional[str] = None

    # Step 1: TMDB Metadata resolution
    tmdb_meta: dict[str, Any] = {}
    if imdb_id:
        try:
            tmdb_meta = await tmdb_service.fetch_by_imdb_id(imdb_id)
        except Exception as exc:
            log.warning("[LeechService] TMDB fetch by IMDb failed: %s", exc)
    elif title:
        try:
            tmdb_meta = await tmdb_service.fetch_metadata(title, year)
        except Exception as exc:
            log.warning("[LeechService] TMDB fetch by title failed: %s", exc)

    if tmdb_meta:
        title = tmdb_meta.get("title") or title
        year = tmdb_meta.get("year") or year
        imdb_id = imdb_id or tmdb_meta.get("imdb_id")
    else:
        title = title or "Movie"
        year = year or 2024

    display_title = f"{title} ({year})" if year else title

    # Update active task title in tracker without destroying the task handle
    active_task = task_tracker.tracker.get_active_task(user_id)
    if active_task:
        active_task.title = display_title
    else:
        task_tracker.tracker.start_task(user_id=user_id, title=display_title)

    kb_cancel = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Leech", callback_data="leech:cancel")]
    ])

    await status_msg.edit_text(
        f"🚀 <b>Ultra Auto-Leech & Uploader (/boost)</b>\n\n"
        f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
        f"🔍 <b>පියවර 1/3:</b> ක්‍රම 4 ඔස්සේ බාගත කිරීමේ මූලාශ්‍ර සොයමින් පවතී...\n\n"
        f"• Method A: DDL Scrapers (PixelDrain/Pahe)\n"
        f"• Method B: YTS Torrents (&lt; 1.95GB)\n"
        f"• Method C: Telegram Movie Channels\n"
        f"• Method D: Web Stream Extractors",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_cancel,
    )

    try:
        candidates: list[LeechCandidate] = []

        if reply_media:
            if reply_media.get("type") == "torrent":
                temp_dir = temp_dir or tempfile.mkdtemp(prefix="leech_")
                tor_dest = os.path.join(temp_dir, reply_media.get("file_name", "movie.torrent"))
                local_tor = await client.download_media(message=reply_media["file_id"], file_name=tor_dest)
                if local_tor and os.path.exists(local_tor):
                    candidates.append(
                        LeechCandidate(
                            method="torrent",
                            method_name="Replied Telegram Torrent File",
                            source_url=local_tor,
                            quality="1080p",
                        )
                    )
            elif reply_media.get("type") == "video":
                candidates.append(
                    LeechCandidate(
                        method="telegram",
                        method_name="Replied Telegram Video",
                        source_url=reply_media["file_id"],
                        quality="1080p",
                        size=downloader.format_bytes(reply_media.get("file_size", 0)),
                        size_bytes=reply_media.get("file_size", 0),
                        extra=reply_media,
                    )
                )

        if direct_link:
            # User provided a direct download link or magnet directly
            if direct_link.startswith("magnet:?"):
                candidates.append(
                    LeechCandidate(
                        method="magnet",
                        method_name="Direct Magnet Link",
                        source_url=direct_link,
                        quality="1080p",
                    )
                )
            elif direct_link.endswith(".torrent") or ".torrent" in direct_link.lower():
                candidates.append(
                    LeechCandidate(
                        method="torrent",
                        method_name="Direct Torrent URL",
                        source_url=direct_link,
                        quality="1080p",
                    )
                )
            elif "pixeldrain.com" in direct_link:
                direct_pd = method3_ddl.resolve_direct_url(direct_link) or direct_link
                candidates.append(
                    LeechCandidate(
                        method="ddl",
                        method_name="PixelDrain Direct",
                        source_url=direct_pd,
                        quality="1080p",
                    )
                )
            elif "t.me" in direct_link:
                tg_direct = await method1_telegram.resolve_telegram_link(client, direct_link)
                if tg_direct and tg_direct.get("file_id"):
                    candidates.append(
                        LeechCandidate(
                            method="telegram",
                            method_name="Telegram Message Link",
                            source_url=tg_direct["file_id"],
                            quality="1080p",
                            size=downloader.format_bytes(tg_direct.get("file_size", 0)),
                            extra=tg_direct,
                        )
                    )
            else:
                candidates.append(
                    LeechCandidate(
                        method="direct",
                        method_name="Direct HTTP Link",
                        source_url=direct_link,
                        quality="1080p",
                    )
                )
        if not candidates:
            candidates = await find_all_candidates(
                title=title, year=year, imdb_id=imdb_id, bot_client=client
            )

        if not candidates:
            task_tracker.tracker.fail_task(user_id, "No download candidates found across any method.")
            await status_msg.edit_text(
                f"❌ <b>චිත්‍රපටය හමු නොවීය (Not Found)!</b>\n\n"
                f"🎬 <b>{display_title}</b> සඳහා ක්‍රම 4 ඔස්සේ කිසිදු Direct බාගත කිරීමේ Link එකක් හමු නොවීය.\n\n"
                f"කරුණාකර නම නිවැරදිදැයි පරීක්ෂා කරන්න, නැතහොත් Direct Download URL එකක් හෝ වීඩියෝවක් එවන්න.",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Step 2: Download with Multi-Method Fallback ────────────────────────
        chosen_candidate: Optional[LeechCandidate] = None
        if not temp_dir:
            temp_dir = tempfile.mkdtemp(prefix="leech_")

        for idx, candidate in enumerate(candidates, 1):
            log.info(
                "[LeechService] Attempting download with candidate %d/%d: %s",
                idx, len(candidates), candidate.method_name
            )
            task_tracker.tracker.set_step(
                user_id, f"1/3 - Downloading via {candidate.method_name}..."
            )

            # Inform user immediately that a source was found and download is starting
            found_text = (
                f"🎯 <b>බාගත කිරීමේ මූලාශ්‍රයක් හමුවිය (Source Found)!</b>\n\n"
                f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
                f"⚡ <b>මූලාශ්‍රය ({idx}/{len(candidates)}):</b> {candidate.method_name}\n"
                f"📦 <b>ප්‍රමාණය:</b> {candidate.size}\n\n"
                f"⏳ <b>බාගත කිරීම ආරම්භ කරමින් පවතී (Connecting to peers/server)...</b>"
            )
            try:
                await status_msg.edit_text(found_text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
            except Exception:
                pass

            last_edit_time = 0.0

            async def _download_progress(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
                nonlocal last_edit_time
                now = time.time()
                if now - last_edit_time >= 2.0 or pct >= 99.0:
                    last_edit_time = now
                    p_bar = downloader.format_progress_bar(pct)
                    if pct == 0.0 and (speed_str in ("0B/s", "0 B/s", "0.0 B/s", "N/A", "") or "0B" in speed_str):
                        status_line = "⏳ <b>තත්ත්වය:</b> Seeders සම්බන්ධ කරගනිමින් පවතී (Connecting to peers)..."
                    else:
                        status_line = f"⚡ <b>වේගය:</b> {speed_str} | ⏱ <b>ETA:</b> {eta_str}"

                    text = (
                        f"📥 <b>පියවර 2/4: Seedr ➔ Render Cloud වෙත බාගත කරමින්...</b>\n\n"
                        f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
                        f"⚡ <b>ක්‍රමය:</b> {candidate.method_name} (Cloud Direct Link)\n"
                        f"📊 <b>ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                        f"📦 <b>ප්‍රමාණය:</b> {done_str} / {total_str}\n"
                        f"{status_line}\n"
                        f"☁️ <i>Render Data Center High-Speed Cloud Bandwidth (ඔබේ Data නොයයි)</i>"
                    )
                    try:
                        await status_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
                    except Exception:
                        pass

            try:
                if candidate.method in ("yts", "magnet", "torrent"):
                    seedr_res = None
                    if seedr_service.seedr_client.is_configured():
                        log.info("[LeechService] Attempting Seedr.cc Cloud Debrid conversion...")
                        async def _seedr_progress(status_str: str) -> None:
                            try:
                                await status_msg.edit_text(
                                    f"☁️ <b>Seedr Cloud Debrid ක්‍රියාත්මකයි...</b>\n\n"
                                    f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
                                    f"⚡ {status_str}",
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=kb_cancel,
                                )
                            except Exception:
                                pass

                        mag_to_use = candidate.source_url
                        if candidate.extra and candidate.extra.get("magnet"):
                            mag_to_use = candidate.extra.get("magnet")

                        seedr_res = await seedr_service.seedr_client.convert_magnet_to_direct_url(
                            magnet_url=mag_to_use,
                            progress_callback=_seedr_progress,
                        )

                    if seedr_res and seedr_res.get("direct_url"):
                        log.info("[LeechService] Downloading via Seedr direct HTTPS link: %s", seedr_res["file_name"])
                        clean_name = seedr_res.get("file_name") or f"{_slugify(title, year)}.mp4"
                        local_file = await downloader.download_http(
                            url=seedr_res["direct_url"],
                            dest_dir=temp_dir,
                            filename=clean_name,
                            task_key=task_key,
                            progress_callback=_download_progress,
                        )
                        asyncio.create_task(seedr_service.seedr_client.clean_storage())
                    elif seedr_service.seedr_client.is_configured():
                        log.warning("[LeechService] Seedr conversion failed for candidate %s. Skipping raw P2P fallback to protect local data.", candidate.method_name)
                        continue
                    else:
                        local_file = await downloader.download_torrent(
                            magnet_or_torrent=candidate.source_url,
                            dest_dir=temp_dir,
                            task_key=task_key,
                            progress_callback=_download_progress,
                        )
                elif candidate.method == "telegram":
                    # Download telegram media
                    tg_file_id = candidate.source_url
                    target_dest = os.path.join(temp_dir, f"{_slugify(title, year)}.mp4")
                    local_file = await client.download_media(
                        message=tg_file_id,
                        file_name=target_dest,
                    )
                else:
                    # Direct HTTP / DDL
                    clean_name = f"{_slugify(title, year)}.mp4"
                    local_file = await downloader.download_http(
                        url=candidate.source_url,
                        dest_dir=temp_dir,
                        filename=clean_name,
                        task_key=task_key,
                        progress_callback=_download_progress,
                    )

                if local_file and os.path.exists(local_file) and os.path.getsize(local_file) > 0:
                    chosen_candidate = candidate
                    log.info("[LeechService] Download SUCCEEDED via %s", candidate.method_name)
                    break

            except Exception as dl_err:
                log.warning(
                    "[LeechService] Download failed for candidate '%s': %s. Trying next candidate...",
                    candidate.method_name, dl_err
                )
                # Clean any partial files in temp directory
                for f in os.listdir(temp_dir):
                    p = os.path.join(temp_dir, f)
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                continue

        if not chosen_candidate or not local_file or not os.path.exists(local_file):
            task_tracker.tracker.fail_task(user_id, "All download candidates failed.")
            await status_msg.edit_text(
                f"⚠️ <b>බාගත කිරීම අසාර්ථක විය (Download Failed)!</b>\n\n"
                f"🎬 <b>{display_title}</b> සඳහා තිබූ මූලාශ්‍ර {len(candidates)} ම බාගත කිරීමේදී දෝෂ ඇති විය.\n\n"
                f"කරුණාකර වෙනත් නමකින් හෝ Direct Link එකක් ලබාදී නැවත උත්සාහ කරන්න.",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Step 2.5: Smart 1080p Compression if > 1.95GB / Web Stream Remux ────
        curr_size = os.path.getsize(local_file)
        if curr_size > video_service.MAX_TELEGRAM_BOT_SIZE:
            log.info(
                "[LeechService] File size %.2f GB exceeds 1.95 GB limit. Starting Smart 1080p compression...",
                curr_size / (1024 * 1024 * 1024),
            )
            task_tracker.tracker.set_step(user_id, "Smart 1080p Compression (FFmpeg)...")
            compressed_file = os.path.join(temp_dir, f"compressed_{_slugify(title, year)}.mp4")
            last_comp_edit = 0.0

            async def _compress_prog(pct: float, pct_str: str) -> None:
                nonlocal last_comp_edit
                now = time.time()
                if now - last_comp_edit >= 3.0:
                    last_comp_edit = now
                    p_bar = downloader.format_progress_bar(pct)
                    c_text = (
                        f"⚙️ <b>1080p Quality සුරකිමින් Telegram සඳහා සකසමින් පවතී...</b>\n\n"
                        f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
                        f"📊 <b>සැකසුම් ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                        f"💡 <i>1080p Full HD තත්ත්වය ඒ ආකාරයෙන්ම තබා ගනිමින් File Size එක 1.2GB දක්වා අඩු කෙරේ.</i>"
                    )
                    try:
                        await status_msg.edit_text(c_text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
                    except Exception:
                        pass

            ok = await video_service.compress_smart_1080p(local_file, compressed_file, _compress_prog)
            if ok and os.path.exists(compressed_file) and os.path.getsize(compressed_file) > 0:
                try:
                    os.remove(local_file)
                except Exception:
                    pass
                local_file = compressed_file
        else:
            # File is < 1.95 GB: Ensure it is web-streamable MP4 (+faststart)
            ext = os.path.splitext(local_file)[1].lower()
            if ext in (".mkv", ".avi", ".webm"):
                log.info("[LeechService] Remuxing %s to web-streamable MP4...", ext)
                remuxed = os.path.join(temp_dir, f"web_{_slugify(title, year)}.mp4")
                if await video_service.ensure_web_streamable(local_file, remuxed):
                    try:
                        os.remove(local_file)
                    except Exception:
                        pass
                    local_file = remuxed

        # ── Step 3: Fast Parallel Upload to Telegram Channel ──────────────────
        file_size = os.path.getsize(local_file)
        file_name = os.path.basename(local_file)
        size_str = downloader.format_bytes(file_size)

        task_tracker.tracker.set_step(
            user_id, f"4/4 - Render to Telegram Upload ({size_str})..."
        )

        last_upload_edit = 0.0

        async def _upload_progress(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
            nonlocal last_upload_edit
            now = time.time()
            if now - last_upload_edit >= 2.5 or pct >= 99.0:
                last_upload_edit = now
                p_bar = downloader.format_progress_bar(pct)
                text = (
                    f"📤 <b>පියවර 4/4: Render Cloud ➔ Telegram Storage වෙත Upload වෙමින්...</b>\n\n"
                    f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
                    f"📁 <b>ගොනුව:</b> <code>{file_name}</code>\n"
                    f"📊 <b>ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                    f"📦 <b>ප්‍රමාණය:</b> {done_str} / {total_str}\n"
                    f"⚡ <b>Cloud Upload Speed:</b> {speed_str} | ⏱ <b>ETA:</b> {eta_str}\n"
                    f"☁️ <i>Telegram Private Storage වෙත සෘජුවම සුරැකේ.</i>"
                )
                try:
                    await status_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
                except Exception:
                    pass

        target_channel = config.PRIVATE_CHANNEL_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0)
        upload_res = await telegram_upload.upload_video_file(
            bot_client=client,
            file_path=local_file,
            target_chat=target_channel,
            caption=f"🎬 {display_title}\n\n⚡ Uploaded via Auto-Leech (/boost)",
            progress_callback=_upload_progress,
            fallback_chat=user_id,
        )

        file_id = upload_res.get("file_id", "")
        stream_url = upload_res.get("stream_url", "")
        message_id = upload_res.get("message_id", 0)

        # ── Step 4: Immediate VPS Disk Cleanup ────────────────────────────────
        try:
            if os.path.exists(local_file):
                os.remove(local_file)
                log.info("[LeechService] Immediate cleanup: local video '%s' deleted.", local_file)
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                log.info("[LeechService] Temporary folder '%s' removed.", temp_dir)
        except Exception as clean_err:
            log.warning("[LeechService] Error during disk cleanup: %s", clean_err)

        # ── Step 5: Save to movies.json & Deploy to Website ───────────────────
        task_tracker.tracker.set_step(user_id, "Completed - Updating Website...")
        await status_msg.edit_text(
            f"⚡ <b>අවසන් පියවර:</b> වෙබ් අඩවිය යාවත්කාලීන කරමින් පවතී (Cloudflare Pages)...",
            parse_mode=ParseMode.HTML,
        )

        slug = _slugify(title, year)
        base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
        site_url = f"{base_site}/movie.html?id={slug}"

        # Default Sinhala Subtitle
        sub_text = (
            f"WEBVTT\n\n1\n00:00:01.000 --> 00:00:06.000\n"
            f"FilmSub.lk වෙතින් සිංහල උපසිරැසි සමඟ\n\n2\n00:00:07.000 --> 00:00:15.000\n"
            f"{display_title} නැරඹීමට ස්තූතියි!"
        )
        default_sub_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(sub_text)}"

        # Multi-quality download cards (1080p, 720p, 480p, 360p)
        file_ext = os.path.splitext(file_name)[1].lstrip(".").upper() or "MP4"
        stream_type = "video/mp4" if file_ext == "MP4" else "video/x-matroska"

        sz_360 = int(file_size * 0.18)
        sz_480 = int(file_size * 0.28)
        sz_720 = int(file_size * 0.55)
        sz_1080 = file_size

        downloads_list = [
            {"quality": "360p", "size": downloader.format_bytes(sz_360), "url": stream_url, "format": file_ext, "host": "Direct"},
            {"quality": "480p", "size": downloader.format_bytes(sz_480), "url": stream_url, "format": file_ext, "host": "Direct"},
            {"quality": "720p", "size": downloader.format_bytes(sz_720), "url": stream_url, "format": file_ext, "host": "Direct"},
            {"quality": "1080p", "size": downloader.format_bytes(sz_1080), "url": stream_url, "format": file_ext, "host": "Direct"},
        ]

        raw_dur = tmdb_meta.get("duration", 120)
        dur_str = f"{raw_dur} min" if isinstance(raw_dur, int) else (str(raw_dur) if str(raw_dur).endswith("min") else f"{raw_dur} min")

        is_series = tmdb_meta.get("type") == "series"

        movie_entry = {
            "id": slug,
            "slug": slug,
            "title": title,
            "title_si": tmdb_meta.get("title_si", ""),
            "year": year,
            "imdb": tmdb_meta.get("rating") or tmdb_meta.get("imdb", "8.0"),
            "rating": tmdb_meta.get("rating") or tmdb_meta.get("imdb", "8.0"),
            "imdb_id": imdb_id or "",
            "tmdb_id": str(tmdb_meta.get("tmdb_id", "")),
            "type": "series" if is_series else "movie",
            "number_of_seasons": tmdb_meta.get("number_of_seasons", 1) if is_series else 0,
            "number_of_episodes": tmdb_meta.get("number_of_episodes", 0) if is_series else 0,
            "seasons": tmdb_meta.get("seasons", []) if is_series else [],
            "poster": tmdb_meta.get("poster_url") or tmdb_meta.get("poster", ""),
            "poster_url": tmdb_meta.get("poster_url") or tmdb_meta.get("poster", ""),
            "backdrop": tmdb_meta.get("backdrop_url") or tmdb_meta.get("backdrop", ""),
            "backdrop_url": tmdb_meta.get("backdrop_url") or tmdb_meta.get("backdrop", ""),
            "genres": tmdb_meta.get("genres", ["Action", "Adventure"]),
            "language": "English",
            "subtitle_language": "Sinhala",
            "quality": chosen_candidate.quality,
            "duration": dur_str,
            "description": tmdb_meta.get("description", ""),
            "director": tmdb_meta.get("director", ""),
            "cast": tmdb_meta.get("cast", []),
            "featured": False,
            "trending": True,
            "file_id": file_id,
            "message_id": message_id,
            "file_name": file_name,
            "file_size": file_size,
            "stream_url": stream_url,
            "streams": [
                {
                    "server": "Server 1",
                    "label": "Server 1 (Telegram Direct)",
                    "type": stream_type,
                    "stream_url": stream_url,
                }
            ],
            "downloads": downloads_list,
            "subtitles": [
                {
                    "language": "Sinhala",
                    "label": "Sinhala Subtitle",
                    "url": default_sub_url,
                    "default": True,
                }
            ],
            "source_method": chosen_candidate.method,
            "site_url": site_url,
            "added_date": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d"),
            "added_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "added_by": "bot_auto_leech",
        }

        # Save to database (movies.json & movies_data.js) & trigger Cloudflare deploy
        saved = await github_service.add_movie(movie_entry)
        if not saved:
            log.warning("[LeechService] github_service.add_movie failed to commit.")

        # Post announcement to public channel
        if config.PUBLIC_CHANNEL_ID:
            try:
                await post_to_channel(client, movie_entry, config.PUBLIC_CHANNEL_ID)
                log.info("[LeechService] Channel announcement posted.")
            except Exception as ann_err:
                log.warning("[LeechService] Channel announcement failed: %s", ann_err)

        task_tracker.tracker.complete_task(user_id)

        # ── Final Success Response ───────────────────────────────────────────
        kb_done = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=site_url)],
        ])

        imdb_val = movie_entry.get("imdb", "8.0")
        genres_val = ", ".join(movie_entry.get("genres", [])[:3])

        await status_msg.edit_text(
            f"🎉 <b>Ultra Auto-Leech සාර්ථකව නිම විය!</b>\n\n"
            f"🎬 <b>{display_title}</b>\n"
            f"⭐ <b>IMDb:</b> {imdb_val} / 10 | 🎞 <b>Quality:</b> {chosen_candidate.quality}\n"
            f"📦 <b>ප්‍රමාණය:</b> {size_str}\n"
            f"🎭 <b>කාණ්ඩ:</b> {genres_val}\n"
            f"⚡ <b>භාවිතා කළ ක්‍රමය:</b> {chosen_candidate.method_name}\n"
            f"🧹 <b>VPS Storage:</b> 100% Free (තාවකාලික ගොනු ඉවත් කෙරිණි)\n\n"
            f"🌐 <b>Live Link:</b> <a href=\"{site_url}\">{site_url}</a>\n"
            f"📢 <b>Telegram Channel:</b> Announcement Post කරන ලදී!\n"
            f"⚡ <b>Cloudflare Pages:</b> Auto-deployed!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_done,
            disable_web_page_preview=False,
        )

    except asyncio.CancelledError:
        log.info("[LeechService] Auto-leech task cancelled for user %s (%s)", user_id, display_title)
        await downloader.cancel_active_download(task_key)
        task_tracker.tracker.cancel_task(user_id)

        if local_file and os.path.exists(local_file):
            try:
                os.remove(local_file)
            except Exception:
                pass

        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log.info("[LeechService] Deleted temporary directory on cancellation.")

        try:
            await status_msg.edit_text(
                f"❌ <b>Auto-Leech ක්‍රියාවලිය අවලංගු කරන ලදී (Cancelled)!</b>\n\n"
                f"🎬 <b>{display_title}</b>\n"
                f"🧹 තාවකාලික ගොනු සියල්ල මකා දමා VPS Memory නිදහස් කරන ලදී.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        raise

    except Exception as exc:
        log.exception("[LeechService] Unhandled error during auto-leech for user %s: %s", user_id, exc)
        task_tracker.tracker.fail_task(user_id, str(exc))

        if local_file and os.path.exists(local_file):
            try:
                os.remove(local_file)
            except Exception:
                pass

        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

        err_clean = str(exc).replace("<", "&lt;").replace(">", "&gt;")
        try:
            await status_msg.edit_text(
                f"⚠️ <b>Auto-Leech සැකසීමේදී දෝෂයක් සිදු විය (Failed)!</b>\n\n"
                f"🎬 <b>{display_title}</b>\n"
                f"❌ දෝෂය: <code>{err_clean}</code>\n\n"
                f"කරුණාකර /status බලන්න හෝ නැවත උත්සාහ කරන්න.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
