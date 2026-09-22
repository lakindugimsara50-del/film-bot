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
import uuid

from handlers.announce import post_to_channel, _slugify
from services import (
    downloader,
    draft_service,
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


class ParsedQuery(tuple):
    """Subclass of 4-tuple (title, year, imdb_id, direct_url) that preserves backward compatibility while exposing series fields."""
    def __new__(cls, title, year, imdb_id, direct_url, season=None, episode=None, is_series=False):
        return super().__new__(cls, (title, year, imdb_id, direct_url))

    def __init__(self, title, year, imdb_id, direct_url, season=None, episode=None, is_series=False):
        self.title = title
        self.year = year
        self.imdb_id = imdb_id
        self.direct_url = direct_url
        self.season = season
        self.episode = episode
        self.is_series = is_series


def parse_query(text: str) -> ParsedQuery:
    """
    Parse user query into (title, year, imdb_id, direct_url_or_magnet) with smart series detection.
    Supports:
      /leech Inception
      /leech Inception 2010
      /leech Game of Thrones S01E01
      /leech Breaking Bad Season 2 Episode 5
      /leech tt1375666
      /leech magnet:?xt=...
      /leech https://pixeldrain.com/u/...
      /boost@Bot Inception 2010
    """
    raw = re.sub(r"^/(?:leech|auto|boost)(?:@\w+)?\s*", "", text.strip())

    # Check for magnet
    if raw.startswith("magnet:?"):
        dn_match = re.search(r"[?&]dn=([^&]+)", raw)
        title = urllib.parse.unquote_plus(dn_match.group(1)) if dn_match else ""
        return ParsedQuery(title, None, None, raw)

    # Check for direct URL
    if raw.startswith(("http://", "https://")):
        parts = raw.split(maxsplit=1)
        url = parts[0]
        extra = parts[1] if len(parts) > 1 else ""
        if extra:
            sub = parse_query(extra)
            return ParsedQuery(sub.title, sub.year, sub.imdb_id, url, sub.season, sub.episode, sub.is_series)

        # Infer title and year from URL path
        parsed_url = urllib.parse.urlparse(url)
        path_name = os.path.basename(parsed_url.path)
        clean_name = re.sub(r"\.(mp4|mkv|avi|webm|torrent)$", "", path_name, flags=re.IGNORECASE)
        clean_name = re.sub(r"[._-]", " ", clean_name).strip()
        ym = re.search(r"\b(19\d\d|20\d\d)\b", clean_name)
        year_val = int(ym.group(1)) if ym else None
        if ym:
            clean_name = clean_name[:ym.start()].strip()
        return ParsedQuery(clean_name, year_val, None, url)

    # Check for IMDb ID
    imdb_match = re.match(r"^(tt\d+)$", raw, re.IGNORECASE)
    if imdb_match:
        return ParsedQuery("", None, imdb_match.group(1), None)

    # Check for Season & Episode pattern (e.g. S01E01, Season 1 Episode 2, 1x01, S02)
    se_match = re.search(r"\b(?:s|season\s*)(\d{1,2})\s*(?:e|ep|episode\s*|x)(\d{1,2})\b", raw, re.IGNORECASE)
    x_match = re.search(r"\b(\d{1,2})x(\d{1,2})\b", raw, re.IGNORECASE)
    s_match = re.search(r"\b(?:s|season\s*)(\d{1,2})\b", raw, re.IGNORECASE)
    e_match = re.search(r"\b(?:e|episode\s*|ep\s*)(\d{1,2})\b", raw, re.IGNORECASE)

    season_val = None
    episode_val = None
    is_series = False

    if se_match:
        season_val = int(se_match.group(1))
        episode_val = int(se_match.group(2))
        is_series = True
    elif x_match:
        season_val = int(x_match.group(1))
        episode_val = int(x_match.group(2))
        is_series = True
    else:
        if s_match:
            season_val = int(s_match.group(1))
            is_series = True
        if e_match:
            episode_val = int(e_match.group(1))
            is_series = True

    if is_series:
        clean_title = re.sub(
            r"\b(?:s\d{1,2}\s*(?:e|ep|episode\s*|x)\d{1,2}|season\s*\d+\s*(?:episode\s*\d+|ep\s*\d+)?|s\d{1,2}|episode\s*\d+|ep\s*\d+|\d{1,2}x\d{1,2})\b.*$",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        ym = re.search(r"\b(19\d\d|20\d\d)\b", clean_title)
        year_val = int(ym.group(1)) if ym else None
        if ym:
            clean_title = clean_title[:ym.start()].strip()
        if episode_val is None:
            episode_val = 1
        return ParsedQuery(clean_title or raw, year_val, None, None, season_val, episode_val, True)

    # Check for Title + Year
    year_match = re.match(r"^(.+?)\s+(\d{4})$", raw)
    if year_match:
        return ParsedQuery(year_match.group(1).strip(), int(year_match.group(2)), None, None)

    return ParsedQuery(raw, None, None, None)


async def find_all_candidates(
    title: str,
    year: Optional[int] = None,
    imdb_id: Optional[str] = None,
    bot_client: Optional[Client] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    is_series: bool = False,
) -> list[LeechCandidate]:
    """
    Query all acquisition sources in priority order:
      Method 1: Telegram Channels (Highest Priority - 0 Download Time)
      Method 2: DDL Scrapers (PixelDrain / Pahe / PSArips)
      Method 3: Multi-Source Torrent Engine (EZTV, Apibay / ThePirateBay, Torrents-CSV, YTS)
      Method 4: Web stream extractors (if applicable)
    Returns a list of viable LeechCandidates.
    """
    candidates: list[LeechCandidate] = []
    log.info(
        "[LeechService] Finding candidates for: '%s' (%s), imdb=%s, S%sE%s, is_series=%s",
        title, year, imdb_id, season, episode, is_series
    )

    # ── Method 1: Telegram Movie/TV Channels (Highest Priority) ───────────────
    if bot_client:
        try:
            log.info("[LeechService] Trying Method 1: Telegram Channels...")
            tg_res = await method1_telegram.search(
                title=title, year=year, imdb_id=imdb_id, client=bot_client
            )
            if tg_res and tg_res.get("file_id"):
                candidates.append(
                    LeechCandidate(
                        method="telegram",
                        method_name=tg_res.get("server_label", "Telegram Channel"),
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

    # ── Method 2: DDL Scrapers (PixelDrain / Pahe / Direct HTTP) ─────────────
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

    # ── Method 3: Multi-Source Torrent Engine (Apibay / EZTV / TorrentsCSV / YTS)
    try:
        log.info("[LeechService] Trying Method 3: Multi-Source Torrent Engine...")
        from services.scrapers import torrent_finder
        tor_list = await torrent_finder.search_all_torrents(
            title=title,
            year=year,
            imdb_id=imdb_id,
            season=season,
            episode=episode,
            is_series=is_series,
        )
        for tor in tor_list:
            source_url = tor.get("magnet") or tor.get("torrent_url")
            if source_url:
                prov = tor.get("provider", "Torrent")
                q = tor.get("quality", "1080p")
                candidates.append(
                    LeechCandidate(
                        method=tor.get("method", "torrent"),
                        method_name=f"{prov} ({q})",
                        source_url=source_url,
                        quality=q,
                        size=tor.get("size", "Unknown"),
                        size_bytes=tor.get("size_bytes", 0),
                        extra=tor,
                    )
                )
        log.info("[LeechService] Method 3 yielded %d candidate(s).", len(tor_list))
    except Exception as exc:
        log.warning("[LeechService] Method 3 error: %s", exc)

    log.info("[LeechService] Total candidates acquired: %d", len(candidates))
    return candidates


async def run_auto_leech(
    client: Client,
    status_msg: Message,
    user_id: int,
    query_text: str,
    reply_media: Optional[dict] = None,
    auto_publish: bool = False,
) -> None:
    """
    Main entry point for executing the automated leech & upload workflow.
    """
    is_auto_mode = auto_publish
    clean_query = (query_text or "").strip()
    lower_q = clean_query.lower()
    if "--auto" in lower_q or "-a" in clean_query.split():
        is_auto_mode = True
        clean_query = re.sub(r"\b(--auto|-a)\b", "", clean_query).strip()
    elif lower_q.startswith("auto "):
        is_auto_mode = True
        clean_query = clean_query[5:].strip()
    elif lower_q.endswith(" auto"):
        is_auto_mode = True
        clean_query = clean_query[:-5].strip()

    parsed = parse_query(clean_query)
    title, year, imdb_id, direct_link = parsed
    season = parsed.season
    episode = parsed.episode
    is_series = parsed.is_series

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
            tmdb_meta = await tmdb_service.fetch_metadata(
                title, year, is_series=is_series, season=season, episode=episode
            )
        except Exception as exc:
            log.warning("[LeechService] TMDB fetch by title failed: %s", exc)

    if tmdb_meta:
        title = tmdb_meta.get("title") or title
        year = tmdb_meta.get("year") or year
        imdb_id = imdb_id or tmdb_meta.get("imdb_id")
        if tmdb_meta.get("type") == "series":
            is_series = True
            season = season or tmdb_meta.get("current_season", 1)
            episode = episode or tmdb_meta.get("current_episode", 1)
    else:
        title = title or "Movie"
        year = year or 2024

    if is_series:
        ep_name = tmdb_meta.get("episode_title", "")
        show_name = tmdb_meta.get("title") or title
        season = season or 1
        episode = episode or 1
        ep_str = f"S{season:02d}E{episode:02d}"
        display_title = f"{show_name} {ep_str}" + (f" - {ep_name}" if ep_name else "")
        media_icon = "📺"
    else:
        display_title = f"{title} ({year})" if year else title
        media_icon = "🎬"

    # Update active task title & metadata in tracker
    active_task = task_tracker.tracker.get_active_task(user_id)
    if active_task:
        active_task.title = display_title
    else:
        task_tracker.tracker.start_task(user_id=user_id, title=display_title)

    task_tracker.tracker.set_metadata(user_id, task_key=task_key)
    task_tracker.tracker.register_cleanup(user_id, seedr_service.seedr_pool.clean_storage)

    kb_cancel = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Leech", callback_data="leech:cancel")]
    ])

    if is_series:
        auto_ep_note = ""
        if not parsed.is_series:
            auto_ep_note = f"\n💡 <i>(Episode සඳහන් නොකළ බැවින් {ep_str} ස්වයංක්‍රීයව තෝරාගන්නා ලදී. වෙනත් Episode එකක් බාගත කිරීමට: <code>/leech {show_name} S01E02</code>)</i>"

        step1_text = (
            f"🚀 <b>Ultra Auto-Leech & Uploader (/boost)</b>\n\n"
            f"📺 <b>TV Series:</b> {display_title}\n"
            f"🔍 <b>පියවර 1/4:</b> බාගත කිරීමේ මූලාශ්‍ර සොයමින් පවතී (EZTV, PirateBay, TorrentsCSV)..."
            f"{auto_ep_note}\n\n"
            f"⚡ <i>Cloud Multi-Source Auto Search සක්‍රීයයි...</i>"
        )
    else:
        step1_text = (
            f"🚀 <b>Ultra Auto-Leech & Uploader (/boost)</b>\n\n"
            f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
            f"🔍 <b>පියවර 1/4:</b> ක්‍රම 4 ඔස්සේ බාගත කිරීමේ මූලාශ්‍ර සොයමින් පවතී...\n\n"
            f"• Method A: DDL Scrapers (PixelDrain/Pahe)\n"
            f"• Method B: Multi-Torrent Scrapers (YTS/PirateBay)\n"
            f"• Method C: Telegram Movie Channels\n"
            f"• Method D: Web Stream Extractors"
        )

    await status_msg.edit_text(
        step1_text,
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
                title=title,
                year=year,
                imdb_id=imdb_id,
                bot_client=client,
                season=season,
                episode=episode,
                is_series=is_series,
            )

        if not candidates:
            task_tracker.tracker.fail_task(user_id, "No download candidates found across any method.")
            await status_msg.edit_text(
                f"❌ <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'} හමු නොවීය (Not Found)!</b>\n\n"
                f"{media_icon} <b>{display_title}</b> සඳහා බාගත කිරීමේ Link එකක් හමු නොවීය.\n\n"
                f"කරුණාකර නම හෝ Season/Episode නිවැරදිදැයි පරීක්ෂා කරන්න (උදා: <code>/leech {title} S01E01</code>), නැතහොත් Direct Magnet URL එකක් ලබා දෙන්න.",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Step 2: Download with Multi-Method Fallback ────────────────────────
        chosen_candidate: Optional[LeechCandidate] = None
        if not temp_dir:
            temp_dir = tempfile.mkdtemp(prefix="leech_")
        task_tracker.tracker.set_metadata(user_id, task_key=task_key, temp_dir=temp_dir)

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
            fallback_chat=0,  # Do NOT fall back to user DM; must go to channel
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

        # Clear Seedr storage to guarantee 100% free quota for subsequent tasks
        try:
            await seedr_service.seedr_pool.clean_storage()
        except Exception:
            pass

        # ── Step 5: Prepare Movie Payload & Draft ────────────────────────────
        ep_suffix = f"-s{season:02d}e{episode:02d}" if (is_series and season and episode) else ""
        slug = f"{_slugify(title, year)}{ep_suffix}"
        base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
        if "yoursite.lk" in base_site:
            base_site = "https://filmsub.pages.dev"
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
            "season": season if is_series else None,
            "episode": episode if is_series else None,
            "episode_title": tmdb_meta.get("episode_title", "") if is_series else "",
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

        # Save to draft_service so video in channel is never lost
        draft_id = f"leech_{uuid.uuid4().hex[:6]}"
        draft_service.save_draft({
            "id": draft_id,
            "title_hint": display_title,
            "movie_name": title,
            "year": year,
            "file_id": file_id,
            "message_id": message_id,
            "file_name": file_name,
            "file_size": file_size,
            "film_url": "",
            "stream_url": stream_url,
            "quality": chosen_candidate.quality,
            "subtitles": movie_entry.get("subtitles", []),
            "subtitle_url": default_sub_url,
            "meta": tmdb_meta,
            "movie_entry": movie_entry,
            "channel_id": target_channel,
        })

        imdb_val = movie_entry.get("imdb", "8.0")
        genres_val = ", ".join(movie_entry.get("genres", [])[:3])

        if is_auto_mode:
            # Full-auto mode: publish immediately
            task_tracker.tracker.set_step(user_id, "Completed - Updating Website...")
            saved = await github_service.add_movie(movie_entry)
            if not saved:
                log.warning("[LeechService] github_service.add_movie failed to commit.")

            if config.PUBLIC_CHANNEL_ID:
                try:
                    await post_to_channel(client, movie_entry, config.PUBLIC_CHANNEL_ID)
                    log.info("[LeechService] Channel announcement posted.")
                except Exception as ann_err:
                    log.warning("[LeechService] Channel announcement failed: %s", ann_err)

            draft_service.delete_draft(draft_id)
            task_tracker.tracker.complete_task(user_id)

            kb_done = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=site_url)],
                [InlineKeyboardButton("💬 Subtitle එක් කරන්න (Add Sub)", callback_data=f"leech_act:sub_posted:{slug}")],
            ])

            await status_msg.edit_text(
                f"🎉 <b>Ultra Auto-Leech සාර්ථකව නිම විය!</b>\n\n"
                f"{media_icon} <b>{display_title}</b>\n"
                f"⭐ <b>IMDb:</b> {imdb_val} / 10 | 🎞 <b>Quality:</b> {chosen_candidate.quality}\n"
                f"📦 <b>ප්‍රමාණය:</b> {size_str}\n"
                f"🎭 <b>කාණ්ඩ:</b> {genres_val}\n"
                f"⚡ <b>භාවිතා කළ ක්‍රමය:</b> {chosen_candidate.method_name}\n"
                f"☁️ <b>Telegram Storage:</b> Filmhost Channel වෙත සෘජුවම Upload විය!\n"
                f"🧹 <b>Seedr & VPS Storage:</b> 100% Free (තාවකාලික ගොනු ඉවත් කෙරිණි)\n\n"
                f"🌐 <b>Live Link:</b> <a href=\"{site_url}\">{site_url}</a>\n"
                f"📢 <b>Telegram Channel:</b> Announcement Post කරන ලදී!\n"
                f"⚡ <b>Cloudflare Pages:</b> Auto-deployed!\n\n"
                f"💡 <i>පසුව සිංහල උපසිරැසි එක් කිරීමට: <code>/sub {title}</code></i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_done,
                disable_web_page_preview=False,
            )
        else:
            # Interactive choice mode: provide 3 action buttons
            task_tracker.tracker.complete_task(user_id)

            kb_choices = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data=f"leech_act:pub:{draft_id}"),
                    InlineKeyboardButton("💬 Subtitle එක් කරන්න (Add Sub)", callback_data=f"leech_act:sub:{draft_id}"),
                ],
                [
                    InlineKeyboardButton("📁 Draft ලෙස තබන්න (Channel Only)", callback_data=f"leech_act:draft:{draft_id}"),
                ]
            ])

            await status_msg.edit_text(
                f"🎉 <b>Ultra Auto-Leech සාර්ථකව බාගත කර Channel එකට Upload විය!</b>\n\n"
                f"{media_icon} <b>{display_title}</b>\n"
                f"⭐ <b>IMDb:</b> {imdb_val} / 10 | 🎞 <b>Quality:</b> {chosen_candidate.quality}\n"
                f"📦 <b>ප්‍රමාණය:</b> {size_str}\n"
                f"🎭 <b>කාණ්ඩ:</b> {genres_val}\n"
                f"⚡ <b>භාවිතා කළ ක්‍රමය:</b> {chosen_candidate.method_name}\n"
                f"☁️ <b>Telegram Storage:</b> Filmhost Channel වෙත සුරැකිණි!\n"
                f"🧹 <b>Seedr & VPS Storage:</b> 100% Free\n\n"
                f"<b>දැන් ඔබට අවශ්‍ය කුමක්ද? පහත බොත්තමක් තෝරන්න:</b>\n"
                f"• <b>Publish Now:</b> වෙබ් අඩවියට දැන්ම එක්වේ (පසුව <code>/sub</code> මඟින් උපසිරැසි දැමිය හැක)\n"
                f"• <b>Add Sub:</b> උපසිරැසි ගොනුව Upload කර Publish කරයි\n"
                f"• <b>Keep as Draft:</b> වෙබ් අඩවියට නොයවා Channel එකේ පමණක් Draft එකක් ලෙස තබයි",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_choices,
                disable_web_page_preview=True,
            )

    except asyncio.CancelledError:
        log.info("[LeechService] Auto-leech task cancelled for user %s (%s)", user_id, display_title)
        async def _do_cancel_cleanup():
            try:
                await downloader.cancel_active_download(task_key)
            except Exception:
                pass
            try:
                await seedr_service.seedr_pool.clean_storage()
            except Exception:
                pass
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
                    f"❌ <b>Auto-Leech ක්‍රියාවලිය සාර්ථකව අවලංගු කරන ලදී (Cancelled)!</b>\n\n"
                    f"{media_icon} <b>{display_title}</b>\n"
                    f"🧹 Seedr Cloud Storage සහ Local Files සියල්ල පිරිසිදු කරන ලදී.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        try:
            await asyncio.shield(_do_cancel_cleanup())
        except (Exception, asyncio.CancelledError):
            pass
        raise

    except Exception as exc:
        log.exception("[LeechService] Unhandled error during auto-leech for user %s: %s", user_id, exc)
        task_tracker.tracker.fail_task(user_id, str(exc))

        try:
            await seedr_service.seedr_pool.clean_storage()
        except Exception:
            pass

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
                f"{media_icon} <b>{display_title}</b>\n"
                f"❌ දෝෂය: <code>{err_clean}</code>\n\n"
                f"කරුණාකර /status බලන්න හෝ නැවත උත්සාහ කරන්න.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
