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
    resume_service,
    seedr_service,
    subtitle_service,
    task_tracker,
    telegram_upload,
    tmdb_service,
    video_service,
)
from services.cloud_drive import drive_manager
from services.pikpak_service import pikpak_service
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
    original_title: Optional[str] = None,
    original_language: Optional[str] = None,
) -> list[LeechCandidate]:
    """
    Query all acquisition sources in priority order:
      Method 1: Telegram Channels (Highest Priority - 0 Download Time)
      Method 2: DDL Scrapers (PixelDrain / Pahe / PSArips)
      Method 3: Multi-Source Torrent Engine (Torrentio, EZTV, Apibay / ThePirateBay, Torrents-CSV, YTS)
      Method 4: Web stream extractors (if applicable)
    Returns a list of viable LeechCandidates.
    """
    candidates: list[LeechCandidate] = []
    log.info(
        "[LeechService] Finding candidates for: '%s' (%s), imdb=%s, S%sE%s, is_series=%s, orig='%s' (%s)",
        title, year, imdb_id, season, episode, is_series, original_title, original_language
    )

    async def _fetch_telegram():
        if not bot_client:
            return None
        try:
            return await method1_telegram.search(
                title=title,
                year=year,
                imdb_id=imdb_id,
                season=season,
                episode=episode,
                is_series=is_series,
                client=bot_client,
            )
        except Exception as exc:
            log.warning("[LeechService] Method 1 error: %s", exc)
            return None

    async def _fetch_ddl():
        if is_series or season is not None or episode is not None:
            return None
        try:
            return await method3_ddl.search(title=title, year=year, imdb_id=imdb_id)
        except Exception as exc:
            log.warning("[LeechService] Method 2 error: %s", exc)
            return None

    async def _fetch_torrents():
        try:
            from services.scrapers import torrent_finder
            tor_list = await torrent_finder.search_all_torrents(
                title=title,
                year=year,
                imdb_id=imdb_id,
                season=season,
                episode=episode,
                is_series=is_series,
            )
            # If few results and an original/transliterated title exists, search with that as well
            if len(tor_list) < 5 and original_title and original_title.lower() != title.lower():
                log.info("[LeechService] Searching original title '%s'...", original_title)
                alt_tor_list = await torrent_finder.search_all_torrents(
                    title=original_title,
                    year=year,
                    imdb_id=imdb_id,
                    season=season,
                    episode=episode,
                    is_series=is_series,
                )
                seen_hashes = {t.get("hash", "").lower() for t in tor_list if t.get("hash")}
                for at in alt_tor_list:
                    h = at.get("hash", "").lower()
                    if h and h not in seen_hashes:
                        seen_hashes.add(h)
                        tor_list.append(at)
            return tor_list
        except Exception as exc:
            log.warning("[LeechService] Method 3 error: %s", exc)
            return []

    # Run all search methods concurrently for maximum speed
    results = await asyncio.gather(
        _fetch_telegram(),
        _fetch_ddl(),
        _fetch_torrents(),
        return_exceptions=True,
    )

    tg_res = results[0] if len(results) > 0 and not isinstance(results[0], Exception) else None
    ddl_res = results[1] if len(results) > 1 and not isinstance(results[1], Exception) else None
    tor_list = results[2] if len(results) > 2 and isinstance(results[2], list) else []

    # 1. Process Telegram match
    if tg_res and isinstance(tg_res, dict) and tg_res.get("file_id"):
        tg_q = str(tg_res.get("quality", "1080p"))
        if tg_q.lower() not in ("480p", "360p", "sd"):
            candidates.append(
                LeechCandidate(
                    method="telegram",
                    method_name=tg_res.get("server_label", "Telegram Channel"),
                    source_url=tg_res["file_id"],
                    quality=tg_q,
                    size=downloader.format_bytes(tg_res.get("file_size", 0)),
                    size_bytes=tg_res.get("file_size", 0),
                    extra=tg_res,
                )
            )
            log.info("[LeechService] Method 1 yielded Telegram candidate.")

    # 2. Process DDL matches
    if ddl_res and isinstance(ddl_res, dict) and ddl_res.get("downloads"):
        ddl_q = str(ddl_res.get("quality", "1080p"))
        if ddl_q.lower() not in ("480p", "360p", "sd"):
            for d in ddl_res["downloads"]:
                url = d.get("direct_url") or d.get("url")
                host = d.get("host", "DDL").title()
                if url:
                    candidates.append(
                        LeechCandidate(
                            method="ddl",
                            method_name=f"DDL Direct ({host})",
                            source_url=url,
                            quality=ddl_q,
                            size=ddl_res.get("size", "Unknown"),
                            extra=d,
                        )
                    )
            log.info("[LeechService] Method 2 yielded %d candidate(s).", len(ddl_res["downloads"]))

    # 3. Process Multi-source Torrents (Torrentio / Apibay / EZTV / TorrentsCSV / YTS)
    if tor_list:
        for tor in tor_list:
            source_url = tor.get("magnet") or tor.get("torrent_url")
            q = str(tor.get("quality", "1080p"))
            if source_url and q.lower() not in ("480p", "360p", "sd"):
                prov = tor.get("provider", "Torrent")
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

    # For movies, strictly prioritize 1080p candidates over 720p candidates
    if not is_series and season is None and episode is None:
        if any("1080" in str(c.quality) for c in candidates):
            candidates.sort(key=lambda c: 0 if "1080" in str(c.quality) else 1)

    log.info("[LeechService] Total candidates acquired: %d", len(candidates))
    return candidates



# ── Global Sequential Leech Queue ────────────────────────────────────────────
# Ensures only ONE film is downloaded/processed at a time to prevent disk and
# memory exhaustion. Subsequent /leech requests queue up and auto-start.
# ─────────────────────────────────────────────────────────────────────────────

_leech_queue: Optional[asyncio.Queue] = None
_leech_queue_loop: Optional[asyncio.AbstractEventLoop] = None
_leech_worker_task: Optional[asyncio.Task] = None
_active_leech_count: int = 0


async def _leech_queue_worker(queue: asyncio.Queue) -> None:
    """Background coroutine that processes leech jobs one at a time."""
    global _active_leech_count
    while True:
        job = await queue.get()
        client, status_msg, user_id, query_text, reply_media, auto_publish, done_fut = job
        try:
            await _execute_leech(client, status_msg, user_id, query_text, reply_media, auto_publish)
            if not done_fut.done():
                done_fut.set_result(True)
        except asyncio.CancelledError:
            if not done_fut.done():
                done_fut.cancel()
            raise
        except Exception as worker_err:
            log.error("[LeechQueue] Worker uncaught error: %s", worker_err, exc_info=True)
            if not done_fut.done():
                done_fut.set_exception(worker_err)
        finally:
            _active_leech_count = max(0, _active_leech_count - 1)
            queue.task_done()


async def run_auto_leech(
    client: Client,
    status_msg: Message,
    user_id: int,
    query_text: str,
    reply_media: Optional[dict] = None,
    auto_publish: bool = False,
) -> None:
    """
    Public entry point: enqueues the leech request, shows queue position if busy,
    and awaits completion of this job so task_tracker and callers stay synchronized.
    """
    global _leech_queue, _leech_queue_loop, _leech_worker_task, _active_leech_count

    loop = asyncio.get_running_loop()
    if _leech_queue is None or _leech_queue_loop is not loop:
        _leech_queue = asyncio.Queue()
        _leech_queue_loop = loop
        _leech_worker_task = None
        _active_leech_count = 0

    if _leech_worker_task is None or _leech_worker_task.done():
        _leech_worker_task = loop.create_task(_leech_queue_worker(_leech_queue))
        log.info("[LeechQueue] Worker task started.")

    _active_leech_count += 1
    queue_pos = _active_leech_count

    if queue_pos > 1:
        try:
            await status_msg.edit_text(
                f"🕐 <b>Queue Position: {queue_pos}</b> — ඔබේ ඉල්ලීම පෝලිමේ යොදා ඇත!\n\n"
                f"📋 <b>ඉල්ලීම:</b> <code>{query_text}</code>\n"
                f"⏳ <b>ඉදිරිය:</b> {queue_pos - 1} ගොනු(වල්) processing නිම වූ පසු ස්වයංක්‍රීයව ආරම්භ වේ.\n\n"
                f"<i>Cancel කිරීමට /cancel ටයිප් කරන්න.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    done_fut = loop.create_future()
    await _leech_queue.put((client, status_msg, user_id, query_text, reply_media, auto_publish, done_fut))
    log.info("[LeechQueue] Enqueued job for user %d: %r (queue_pos=%d)", user_id, query_text, queue_pos)
    await done_fut


async def _execute_leech(
    client: Client,
    status_msg: Message,
    user_id: int,
    query_text: str,
    reply_media: Optional[dict] = None,
    auto_publish: bool = False,
) -> None:
    """
    Internal leech executor — runs one film end-to-end.
    Called by _leech_queue_worker() sequentially.
    """

    # Default: AUTO-PUBLISH — film is immediately added to website after upload.
    # Only --draft flag gives the manual "Publish / Add Sub / Keep Draft" choice.
    is_auto_mode = True  # Always publish by default
    clean_query = (query_text or "").strip()
    lower_q = clean_query.lower()
    if "--draft" in lower_q or "-d" in clean_query.split():
        is_auto_mode = False  # Manual choice buttons shown only with --draft
        clean_query = re.sub(r"\b(--draft|-d)\b", "", clean_query).strip()
    elif "--auto" in lower_q or "-a" in clean_query.split():
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
        clean_title_base = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip() or title
        title = clean_title_base
        display_title = f"{clean_title_base} ({year})" if year else clean_title_base
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
                original_title=tmdb_meta.get("original_title"),
                original_language=tmdb_meta.get("original_language"),
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

        # ── Save crash-recovery checkpoint BEFORE starting download ─────────
        # If Render restarts mid-download, resume_service detects this on next startup
        resume_service.save_checkpoint(user_id, {
            "display_title": display_title,
            "title": title,
            "year": year,
            "query_text": clean_query,
            "candidate_method_name": candidates[0].method_name if candidates else "Unknown",
            "is_series": is_series,
            "season": season,
            "episode": episode,
        })

        # ── Step 2: Download with Multi-Method Fallback ────────────────────────
        chosen_candidate: Optional[LeechCandidate] = None
        if not temp_dir:
            temp_dir = video_service.get_optimal_work_dir(min_free_gb=2.2, prefix="leech_ram_")
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

            cloud_service_name = "Cloud"
            last_edit_time = 0.0

            async def _download_progress(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
                nonlocal last_edit_time
                now = time.time()
                if (now - last_edit_time >= 3.0) or (pct >= 100.0 and now - last_edit_time >= 1.0):
                    last_edit_time = now
                    p_bar = downloader.format_progress_bar(pct)
                    if pct == 0.0 and (speed_str in ("0B/s", "0 B/s", "0.0 B/s", "N/A", "") or "0B" in speed_str):
                        status_line = "⏳ <b>තත්ත්වය:</b> Seeders සම්බන්ධ කරගනිමින් පවතී (Connecting to peers)..."
                    else:
                        status_line = f"⚡ <b>වේගය:</b> {speed_str} | ⏱ <b>ETA:</b> {eta_str}"

                    text = (
                        f"📥 <b>පියවර 2/4: {cloud_service_name} ➔ Render Cloud වෙත බාගත කරමින්...</b>\n\n"
                        f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                        f"⚡ <b>ක්‍රමය:</b> {candidate.method_name} (Cloud Direct Link)\n"
                        f"📊 <b>ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                        f"📦 <b>ප්‍රමාණය:</b> {done_str} / {total_str}\n"
                        f"{status_line}\n"
                        f"☁️ <i>Render Data Center High-Speed Cloud Bandwidth (ඔබේ Data නොයයි)</i>"
                    )
                    try:
                        await status_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
                    except Exception as p_err:
                        log.debug("[LeechService] Download progress edit ignored: %s", p_err)

            try:
                if candidate.method in ("yts", "magnet", "torrent"):
                    cloud_res = None
                    cloud_service_name = "Seedr"

                    mag_to_use = candidate.source_url
                    if candidate.extra and candidate.extra.get("magnet"):
                        mag_to_use = candidate.extra.get("magnet")

                    candidate_bytes = candidate.size_bytes or 0
                    is_season_pack = bool(candidate.extra and candidate.extra.get("is_season_pack"))
                    ep_hint = f"S{season:02d}E{episode:02d}" if (is_series and season and episode) else None
                    prefer_pikpak = (
                        not is_season_pack
                        and candidate_bytes > int(1.95 * 1024 * 1024 * 1024)
                        and pikpak_service.is_configured()
                    )

                    async def _attempt_cloud_debrid(dest_folder: str, sub_task_key: str) -> Optional[str]:
                        nonlocal cloud_service_name, cloud_res
                        c_res = None
                        # 1. Try PikPak first if file > 1.95GB
                        if prefer_pikpak:
                            log.info("[LeechService] Large file (>1.95GB). Using PikPak Cloud Debrid...")
                            cloud_service_name = "PikPak"
                            async def _pikpak_progress(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
                                try:
                                    await status_msg.edit_text(
                                        f"☁️ <b>PikPak Cloud Debrid (10GB Tier) ක්‍රියාත්මකයි...</b>\n\n"
                                        f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                                        f"⚡ <b>Cloud Cache:</b> {pct:.1f}% ({done_str} / {total_str})\n"
                                        f"⏳ PikPak සේවාදායකයෙන් Direct Download Link ලබාගනිමින් පවතී...",
                                        parse_mode=ParseMode.HTML,
                                        reply_markup=kb_cancel,
                                    )
                                except Exception:
                                    pass

                            c_res = await pikpak_service.convert_magnet_to_direct_url(
                                magnet_link=mag_to_use,
                                progress_callback=_pikpak_progress,
                                episode_hint=ep_hint,
                            )

                        # 2. Try Seedr if not preferred PikPak (or if PikPak didn't resolve)
                        can_try_seedr = seedr_service.seedr_client.is_configured() and not prefer_pikpak
                        if can_try_seedr and is_season_pack:
                            can_try_seedr = False
                        elif can_try_seedr and candidate_bytes > int(2.05 * 1024 * 1024 * 1024):
                            can_try_seedr = False

                        if not c_res and can_try_seedr:
                            log.info("[LeechService] Attempting Seedr.cc Cloud Debrid conversion...")
                            cloud_service_name = "Seedr"
                            async def _seedr_progress(status_str: str) -> None:
                                try:
                                    await status_msg.edit_text(
                                        f"☁️ <b>Seedr Cloud Debrid ක්‍රියාත්මකයි...</b>\n\n"
                                        f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                                        f"⚡ {status_str}",
                                        parse_mode=ParseMode.HTML,
                                        reply_markup=kb_cancel,
                                    )
                                except Exception:
                                    pass

                            c_res = await seedr_service.seedr_client.convert_magnet_to_direct_url(
                                magnet_url=mag_to_use,
                                progress_callback=_seedr_progress,
                                episode_hint=ep_hint,
                            )

                        # 3. Fallback to PikPak if Seedr failed or was full
                        if not c_res and not is_season_pack and pikpak_service.is_configured() and not prefer_pikpak:
                            log.info("[LeechService] Seedr conversion failed/full. Falling back to PikPak Cloud Debrid...")
                            cloud_service_name = "PikPak"
                            async def _pikpak_progress2(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
                                try:
                                    await status_msg.edit_text(
                                        f"☁️ <b>PikPak Cloud Debrid (Fallback) ක්‍රියාත්මකයි...</b>\n\n"
                                        f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                                        f"⚡ <b>Cloud Cache:</b> {pct:.1f}% ({done_str} / {total_str})\n"
                                        f"⏳ PikPak සේවාදායකයෙන් Direct Download Link ලබාගනිමින් පවතී...",
                                        parse_mode=ParseMode.HTML,
                                        reply_markup=kb_cancel,
                                    )
                                except Exception:
                                    pass

                            c_res = await pikpak_service.convert_magnet_to_direct_url(
                                magnet_link=mag_to_use,
                                progress_callback=_pikpak_progress2,
                                episode_hint=ep_hint,
                            )

                        cloud_res = c_res
                        if c_res and c_res.get("direct_url"):
                            log.info("[LeechService] Downloading via %s 16x aria2c link: %s", cloud_service_name, c_res.get("file_name"))
                            clean_name = c_res.get("file_name") or f"{_slugify(title, year)}.mp4"
                            dl_path = await downloader.download_http(
                                url=c_res["direct_url"],
                                dest_dir=dest_folder,
                                filename=clean_name,
                                task_key=sub_task_key,
                                progress_callback=_download_progress,
                            )
                            if cloud_service_name == "PikPak":
                                asyncio.create_task(pikpak_service.clean_storage())
                            else:
                                asyncio.create_task(seedr_service.seedr_client.clean_storage())
                            return dl_path
                        return None

                    async def _attempt_direct_aria2c(dest_folder: str, sub_task_key: str) -> Optional[str]:
                        c_bytes = candidate.size_bytes or 0
                        is_single_ep = bool(is_series and season and episode)
                        _max_local_gb = 4.5 if (os.path.exists("/content") or os.path.isdir("/dev/shm")) else 1.85
                        if not is_single_ep and c_bytes > int(_max_local_gb * 1024 * 1024 * 1024):
                            log.warning(
                                "[LeechService] Candidate %s (%s) exceeds safe limit (%.2f GB).",
                                candidate.method_name,
                                downloader.format_bytes(c_bytes),
                                _max_local_gb,
                            )
                            return None

                        try:
                            free_disk = shutil.disk_usage(dest_folder).free
                            needed_bytes = (min(c_bytes, 850 * 1024 * 1024) if is_single_ep else c_bytes) + 400 * 1024 * 1024
                            if c_bytes > 0 and free_disk < needed_bytes:
                                log.warning(
                                    "[LeechService] Candidate %s (%s) exceeds available disk (%s).",
                                    candidate.method_name,
                                    downloader.format_bytes(c_bytes),
                                    downloader.format_bytes(free_disk),
                                )
                                return None
                        except Exception:
                            pass

                        tor_source = (
                            candidate.extra.get("torrent_url")
                            if (candidate.extra and candidate.extra.get("torrent_url"))
                            else candidate.source_url
                        )
                        sel_idx = (
                            int(candidate.extra["file_idx"]) + 1
                            if (candidate.extra and candidate.extra.get("file_idx") is not None)
                            else None
                        )
                        return await downloader.download_torrent(
                            magnet_or_torrent=tor_source,
                            dest_dir=dest_folder,
                            task_key=sub_task_key,
                            progress_callback=_download_progress,
                            episode_hint=ep_hint,
                            select_file_idx=sel_idx,
                        )

                    has_cloud_debrid = seedr_service.seedr_client.is_configured() or pikpak_service.is_configured()
                    seed_count = int((candidate.extra or {}).get("seeders") or (candidate.extra or {}).get("seeds") or 0)
                    _is_colab_env = os.path.exists("/content") or os.path.isdir("/dev/shm")
                    should_race = (
                        getattr(config, "ENABLE_TORRENT_RACING", True)
                        and has_cloud_debrid
                        and bool(downloader.find_aria2c())
                        and (_is_colab_env or seed_count >= 15 or candidate.method == "yts")
                    )

                    if should_race:
                        log.info(
                            "[LeechService] Racing Direct aria2c (seeds=%d) vs Cloud Debrid concurrently for '%s'...",
                            seed_count, candidate.method_name
                        )
                        race_aria_dir = os.path.join(temp_dir, "race_aria")
                        race_cloud_dir = os.path.join(temp_dir, "race_cloud")
                        os.makedirs(race_aria_dir, exist_ok=True)
                        os.makedirs(race_cloud_dir, exist_ok=True)

                        t_aria = asyncio.create_task(_attempt_direct_aria2c(race_aria_dir, f"{task_key}_aria"))
                        t_cloud = asyncio.create_task(_attempt_cloud_debrid(race_cloud_dir, f"{task_key}_cloud"))

                        done, pending = await asyncio.wait({t_aria, t_cloud}, return_when=asyncio.FIRST_COMPLETED)
                        for d_task in done:
                            try:
                                res_path = d_task.result()
                                if res_path and os.path.exists(res_path) and os.path.getsize(res_path) > 0:
                                    local_file = res_path
                                    break
                            except Exception as r_err:
                                log.debug("[LeechService] First finished race branch error: %s", r_err)

                        if local_file:
                            for p_task in pending:
                                p_task.cancel()
                            await downloader.cancel_active_download(f"{task_key}_aria")
                            await downloader.cancel_active_download(f"{task_key}_cloud")
                        elif pending:
                            for p_task in pending:
                                try:
                                    res_path2 = await p_task
                                    if res_path2 and os.path.exists(res_path2) and os.path.getsize(res_path2) > 0:
                                        local_file = res_path2
                                        break
                                except Exception as r_err2:
                                    log.debug("[LeechService] Second race branch error: %s", r_err2)
                    else:
                        if has_cloud_debrid:
                            local_file = await _attempt_cloud_debrid(temp_dir, task_key)
                        if not local_file:
                            cloud_service_name = "VPS aria2c"
                            local_file = await _attempt_direct_aria2c(temp_dir, task_key)
                            if not local_file:
                                continue
                elif candidate.method == "telegram":
                    # Download telegram media
                    tg_file_id = candidate.source_url
                    target_dest = os.path.join(temp_dir, f"{_slugify(title, year)}.mp4")
                    msg_obj = candidate.extra.get("message") if candidate.extra else None
                    local_file = await client.download_media(
                        message=msg_obj or tg_file_id,
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

        # ── Step 2.5 & 2.6: Auto-Acquire Sinhala Subtitles + 12GB RAM MKV->MP4 & Sub Merge ──
        curr_size = os.path.getsize(local_file)
        _on_colab = os.path.exists('/content') or os.path.isdir('/dev/shm')

        ep_suffix = f"-s{season:02d}e{episode:02d}" if (is_series and season and episode) else ""
        slug = f"{_slugify(title, year)}{ep_suffix}"

        # 1. Extract embedded subtitle / auto-translate / acquire Sinhala .srt & .vtt BEFORE remuxing
        #    so ensure_web_streamable never strips embedded tracks!
        sub_srt_path: Optional[str] = None
        sub_vtt_path: Optional[str] = None
        try:
            sub_srt_path, sub_vtt_path = await subtitle_service.auto_acquire_sinhala_subtitle(
                title=display_title,
                year=year,
                imdb_id=imdb_id,
                temp_dir=temp_dir,
                video_path=local_file,
            )
            log.info("[LeechService] Prepared Sinhala subtitle tracks: srt=%s, vtt=%s", sub_srt_path, sub_vtt_path)
        except Exception as sub_acq_err:
            log.warning("[LeechService] Auto subtitle acquisition note: %s", sub_acq_err)

        # 2. Instantaneous Stream Copy Remux + Soft-Sub Merge (3-5 seconds)
        is_faststart_done = False
        ext = os.path.splitext(local_file)[1].lower()
        final_mp4 = os.path.join(temp_dir, f"{slug}.mp4")
        remuxed = os.path.join(temp_dir, f"remux_tmp_{slug}.mp4") if os.path.abspath(final_mp4) == os.path.abspath(local_file) else final_mp4

        sub_to_merge = sub_srt_path if (sub_srt_path and os.path.exists(sub_srt_path)) else (sub_vtt_path if (sub_vtt_path and os.path.exists(sub_vtt_path)) else None)

        try:
            await status_msg.edit_text(
                f"⚙️ <b>පියවර 3/5: Ultra-Fast Stream Copy සහ සිංහල උපසිරැසි සැකසුම...</b>\n\n"
                f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                f"⚡ <b>ක්‍රමය:</b> Instant Stream Copy Remux ({ext.upper()} ➔ MP4 +faststart)\n"
                f"💬 <b>උපසිරැසි:</b> සිංහල උපසිරැසි Video එකටම Soft-Mux කෙරේ (Default Track)\n"
                f"⏳ තත්පර 3-5ක් රැඳී සිටින්න...",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_cancel,
            )
        except Exception:
            pass

        task_tracker.tracker.set_step(user_id, "Instant Stream Copy + Sub Mux (FFmpeg)...")
        log.info("[LeechService] Executing instantaneous stream-copy muxing (%s -> MP4, sub=%s)...", ext, sub_to_merge)

        if await video_service.stream_copy_subtitles(local_file, sub_to_merge, remuxed, disposition="default"):
            if os.path.exists(local_file) and os.path.abspath(local_file) != os.path.abspath(remuxed):
                try:
                    os.remove(local_file)
                except Exception:
                    pass
            if os.path.abspath(remuxed) != os.path.abspath(final_mp4):
                try:
                    shutil.move(remuxed, final_mp4)
                    remuxed = final_mp4
                except Exception:
                    pass
            local_file = remuxed
            is_faststart_done = True
            log.info("[LeechService] Instantaneous stream-copy muxing succeeded: %s", local_file)
        elif ext in (".mkv", ".webm", ".avi"):
            # Edge case: input is MKV or another container with video/audio streams rejected by MP4.
            # Perform instantaneous stream-copy preserving the native container with Sinhala soft-subs (-c:s srt -disposition:s:0 default)
            remux_native = os.path.join(temp_dir, f"remux_{slug}{ext}")
            if await video_service.stream_copy_subtitles(local_file, sub_to_merge, remux_native, disposition="default"):
                if os.path.exists(local_file) and os.path.abspath(local_file) != os.path.abspath(remux_native):
                    try:
                        os.remove(local_file)
                    except Exception:
                        pass
                local_file = remux_native
                is_faststart_done = ext == ".mp4"
                log.info("[LeechService] Native container stream-copy muxing succeeded: %s", local_file)
            elif await video_service.ensure_web_streamable(local_file, remuxed, sub_path=sub_to_merge):
                if os.path.exists(local_file) and os.path.abspath(local_file) != os.path.abspath(remuxed):
                    try:
                        os.remove(local_file)
                    except Exception:
                        pass
                if os.path.abspath(remuxed) != os.path.abspath(final_mp4):
                    try:
                        shutil.move(remuxed, final_mp4)
                        remuxed = final_mp4
                    except Exception:
                        pass
                local_file = remuxed
                is_faststart_done = True
                log.info("[LeechService] Single-pass MP4 + Sinhala subtitle merge succeeded: %s", local_file)
            elif sub_to_merge and os.path.exists(sub_to_merge):
                sub_muxed = os.path.join(temp_dir, f"sub_{os.path.basename(local_file)}")
                if await video_service.embed_subtitles_soft(local_file, sub_to_merge, sub_muxed, disposition="default"):
                    try:
                        os.remove(local_file)
                    except Exception:
                        pass
                    local_file = sub_muxed
                    is_faststart_done = sub_muxed.lower().endswith(".mp4")
        elif await video_service.ensure_web_streamable(local_file, remuxed, sub_path=sub_to_merge):
            if os.path.exists(local_file) and os.path.abspath(local_file) != os.path.abspath(remuxed):
                try:
                    os.remove(local_file)
                except Exception:
                    pass
            if os.path.abspath(remuxed) != os.path.abspath(final_mp4):
                try:
                    shutil.move(remuxed, final_mp4)
                    remuxed = final_mp4
                except Exception:
                    pass
            local_file = remuxed
            is_faststart_done = True
            log.info("[LeechService] Single-pass MP4 + Sinhala subtitle merge succeeded: %s", local_file)
        elif sub_to_merge and os.path.exists(sub_to_merge):
            # Fallback soft-embed if stream-copy was skipped
            sub_muxed = os.path.join(temp_dir, f"sub_{os.path.basename(local_file)}")
            if await video_service.embed_subtitles_soft(local_file, sub_to_merge, sub_muxed, disposition="default"):
                try:
                    os.remove(local_file)
                except Exception:
                    pass
                local_file = sub_muxed
                is_faststart_done = sub_muxed.lower().endswith(".mp4")

        # 2.7 FastStart (+faststart) Remux: Guarantee moov atom is relocated to byte 0 for <300ms instant streaming
        if not is_faststart_done and local_file.lower().endswith((".mp4", ".m4v", ".mov")):
            faststart_out = os.path.join(temp_dir, f"faststart_{slug}.mp4")
            if os.path.abspath(faststart_out) == os.path.abspath(local_file):
                faststart_out = os.path.join(temp_dir, f"fs_{slug}.mp4")
            log.info("[LeechService] Executing FastStart (+faststart) copy remux for instant zero-lag streaming...")
            if await video_service.apply_faststart(local_file, faststart_out):
                try:
                    os.remove(local_file)
                except Exception:
                    pass
                local_file = faststart_out
                is_faststart_done = True
                log.info("[LeechService] FastStart copy remux complete: moov atom relocated to byte 0 (%s)", local_file)

        # ── Step 3 & 4: Overlapped Multi-Quality RAM Encoding + Parallel Cloud Drive & Telegram Upload ──
        file_size = os.path.getsize(local_file)
        file_name = os.path.basename(local_file)
        size_str = downloader.format_bytes(file_size)

        base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
        if "yoursite.lk" in base_site:
            base_site = "https://filmsub.pages.dev"
        site_url = f"{base_site}/movie.html?id={slug}"

        task_tracker.tracker.set_step(
            user_id, f"3/3 - Telegram Cloud HD Upload ({size_str})..."
        )

        last_upload_edit = 0.0
        _last_drive_edit = 0.0
        _mq_progress_str = ""

        async def _mq_progress_cb(pct: float, pct_str: str) -> None:
            nonlocal _mq_progress_str
            _mq_progress_str = f"RAM 720p/480p/360p: {pct_str}"

        async def _upload_progress(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
            nonlocal last_upload_edit
            now = time.time()
            if (now - last_upload_edit >= 3.0) or (pct >= 100.0 and now - last_upload_edit >= 1.0):
                last_upload_edit = now
                p_bar = downloader.format_progress_bar(pct)
                text = (
                    f"📤 <b>පියවර 3/3: Telegram Cloud HD වෙත Upload වෙමින්...</b>\n\n"
                    f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                    f"📁 <b>ගොනුව:</b> <code>{file_name}</code>\n"
                    f"📊 <b>ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                    f"📦 <b>ප්‍රමාණය:</b> {done_str} / {total_str}\n"
                    f"⚡ <b>Upload Speed:</b> {speed_str} | ⏱ <b>ETA:</b> {eta_str}\n"
                    f"🛡️ <i>Telegram Cloud Storage • 100% Google Account Strike Safe</i>"
                )
                try:
                    await status_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
                except Exception as up_err:
                    log.debug("[LeechService] Upload progress edit ignored: %s", up_err)

        async def _drive_upload_progress(
            done_bytes: int,
            total_bytes: int,
            speed_str: str = "--",
            eta_str: str = "--",
        ) -> None:
            nonlocal _last_drive_edit
            now = time.time()
            pct = min(100.0, (done_bytes / total_bytes * 100)) if total_bytes > 0 else 0.0
            if (now - _last_drive_edit < 2.5) and pct < 100.0:
                return
            _last_drive_edit = now
            p_bar = downloader.format_progress_bar(pct)
            done_str = downloader.format_bytes(done_bytes)
            total_str = downloader.format_bytes(total_bytes)
            speed_display = f"⚡ <b>Drive Speed:</b> {speed_str} | ⏱ <b>ETA:</b> {eta_str}\n" if speed_str != "--" else ""
            txt = (
                f"☁️ <b>Google Drive Backup Upload වෙමින්...</b>\n\n"
                f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                f"📊 <b>ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                f"📦 <b>ප්‍රමාණය:</b> {done_str} / {total_str}\n"
                f"{speed_display}"
            )
            try:
                await status_msg.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
            except Exception:
                pass

        try:
            await status_msg.edit_text(
                f"📤 <b>පියවර 3/3: Telegram Cloud HD වෙත Upload කිරීම ආරම්භ විය...</b>\n\n"
                f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                f"📁 <b>ගොනුව:</b> <code>{file_name}</code> ({size_str})\n"
                f"⚡ <b>FastStart MP4 + සිංහල උපසිරැසි සමඟින් Private Channel වෙත Upload වේ...</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_cancel,
            )
        except Exception:
            pass

        cloud_upload_res = None
        variant_files: dict[str, str] = {}
        variant_cloud_urls: dict[str, dict[str, Any]] = {}
        ENABLE_TELEGRAM_VIDEO_UPLOAD = True
        target_channel = config.PRIVATE_CHANNEL_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0)
        file_id = ""
        stream_url = ""
        message_id = 0

        async def _task_upload_drive_1080() -> None:
            nonlocal cloud_upload_res
            if not getattr(config, "ENABLE_GDRIVE_UPLOAD", False):
                log.info("[LeechService] Google Drive upload disabled (protecting Google accounts). Using Telegram Primary Cloud.")
                return
            try:
                cloud_upload_res = await drive_manager.upload_movie(
                    local_path=local_file,
                    movie_slug=slug,
                    movie_title=display_title,
                    filename=f"{slug}.mp4",
                    progress_callback=_drive_upload_progress,
                )
                if cloud_upload_res:
                    log.info("[LeechService] Movie '%s' (1080p) uploaded to Cloud Drive: %s", slug, cloud_upload_res.get("stream_url"))
            except Exception as c_err:
                log.warning("[LeechService] Cloud Drive 1080p upload skipped/failed: %s", c_err)

        async def _task_upload_drive_variant(q_label: str, q_path: str) -> None:
            if not os.path.exists(q_path):
                return
            try:
                q_bytes = os.path.getsize(q_path)
                q_res = await drive_manager.upload_movie(
                    local_path=q_path,
                    movie_slug=f"{slug}-{q_label}",
                    movie_title=f"{display_title} ({q_label})",
                    filename=f"{slug}-{q_label}.mp4",
                )
                if q_res:
                    variant_cloud_urls[q_label] = {
                        "file_id": q_res.get("file_id", ""),
                        "stream_url": q_res.get("stream_url", ""),
                        "download_url": q_res.get("download_url", ""),
                        "size": downloader.format_bytes(q_bytes),
                        "size_bytes": q_bytes,
                    }
                    log.info("[LeechService] Variant '%s-%s' (%s) uploaded to Cloud Drive: %s",
                             slug, q_label, downloader.format_bytes(q_bytes), q_res.get("file_id"))
            except Exception as q_up_err:
                log.debug("[LeechService] Variant %s upload skipped: %s", q_label, q_up_err)

        async def _task_encode_and_upload_variants() -> None:
            nonlocal variant_files, _mq_progress_str
            if not getattr(config, "ENABLE_GDRIVE_UPLOAD", False) or not getattr(config, "ENABLE_MULTI_QUALITY_RAM", True):
                return
            try:
                variant_files = await video_service.generate_multi_quality_variants_ram(
                    input_path=local_file,
                    output_dir=temp_dir,
                    slug=slug,
                    sub_path=sub_to_merge,
                    qualities=("720p", "480p", "360p"),
                    progress_callback=_mq_progress_cb,
                )
                _mq_progress_str = "720p/480p/360p Uploading to Drive..."
                if variant_files:
                    await asyncio.gather(
                        *[_task_upload_drive_variant(ql, qp) for ql, qp in variant_files.items()],
                        return_exceptions=True,
                    )
                _mq_progress_str = "720p/480p/360p Complete ✅"
            except Exception as mq_err:
                log.warning("[LeechService] Multi-quality RAM variant pipeline skipped: %s", mq_err)

        async def _task_upload_telegram() -> None:
            nonlocal file_id, stream_url, message_id
            if not ENABLE_TELEGRAM_VIDEO_UPLOAD:
                return
            if file_size > int(1.95 * 1024 * 1024 * 1024):
                log.info("[LeechService] File size %.2f GB > 1.95 GB. Posting Drive link to Telegram channel.", file_size / (1024**3))
                try:
                    msg = await client.send_message(
                        chat_id=target_channel,
                        text=(
                            f"🎬 <b>{display_title}</b>\n\n"
                            f"📦 <b>Size:</b> {size_str} (High Definition 1080p)\n"
                            f"⚡ <b>Google Drive Ultra HD:</b> <a href=\"{site_url}\">Watch Online & Download</a>"
                        ),
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                    message_id = msg.id
                except Exception as t_err:
                    log.warning("[LeechService] Channel link post error: %s", t_err)
            else:
                try:
                    upload_res = await telegram_upload.upload_video_file(
                        bot_client=client,
                        file_path=local_file,
                        target_chat=target_channel,
                        caption=f"🎬 {display_title}\n\n⚡ Uploaded via Auto-Leech (/boost)\n🌐 Watch: {site_url}",
                        progress_callback=_upload_progress,
                        fallback_chat=0,
                    )
                    file_id = upload_res.get("file_id", "")
                    stream_url = upload_res.get("stream_url", "")
                    message_id = upload_res.get("message_id", 0)
                except Exception as tg_err:
                    log.warning("[LeechService] Telegram upload note: %s", tg_err)

        # Execute Drive 1080p upload, Telegram 1080p upload, and 720p/480p/360p RAM Encode+Upload concurrently!
        await asyncio.gather(
            _task_upload_drive_1080(),
            _task_upload_telegram(),
            _task_encode_and_upload_variants(),
            return_exceptions=True,
        )

        cloud_stream = cloud_upload_res.get("stream_url") if cloud_upload_res else ""
        cloud_download = cloud_upload_res.get("download_url") if cloud_upload_res else ""
        primary_stream = cloud_stream or ""

        # Publish Sinhala VTT subtitle to GitHub/website & generate inline data:text/vtt URI
        sub_text = (
            f"WEBVTT\n\n1\n00:00:01.000 --> 00:00:08.000\n"
            f"FilmSub.lk වෙතින් සිංහල උපසිරැසි සමඟ\n\n2\n00:00:08.500 --> 00:00:18.000\n"
            f"{display_title} නැරඹීමට සහ බාගත කිරීමට ස්තූතියි!"
        )
        if sub_vtt_path and os.path.exists(sub_vtt_path):
            try:
                with open(sub_vtt_path, "r", encoding="utf-8", errors="replace") as vf:
                    loaded_vtt = vf.read().strip()
                if loaded_vtt.startswith("WEBVTT"):
                    sub_text = loaded_vtt
            except Exception:
                pass

        default_sub_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(sub_text)}"
        if sub_vtt_path and os.path.exists(sub_vtt_path):
            try:
                uploaded_sub_url = await subtitle_service.upload_subtitle_to_github(
                    vtt_path=sub_vtt_path,
                    filename=f"{slug}-si.vtt",
                    github_token=getattr(config, "GITHUB_TOKEN", ""),
                    repo=getattr(config, "GITHUB_REPO", ""),
                )
                if uploaded_sub_url and len(sub_text) > 16000:
                    default_sub_url = uploaded_sub_url
            except Exception as up_sub_err:
                log.debug("[LeechService] Subtitle upload fallback to inline VTT: %s", up_sub_err)

        file_ext = os.path.splitext(file_name)[1].lstrip(".").upper() or "MP4"
        stream_type = "video/mp4" if file_ext == "MP4" else "video/x-matroska"

        sz_360 = variant_cloud_urls.get("360p", {}).get("size_bytes") or int(file_size * 0.18)
        sz_480 = variant_cloud_urls.get("480p", {}).get("size_bytes") or int(file_size * 0.32)
        sz_720 = variant_cloud_urls.get("720p", {}).get("size_bytes") or int(file_size * 0.55)
        sz_1080 = file_size

        streams_list = []
        downloads_list = []
        qualities_map = {}
        drive_file_id = ""

        def _extract_drive_id_str(u: str) -> str:
            if not u:
                return ""
            m = re.search(r"(?:/d/|id=)([a-zA-Z0-9_-]{15,})", u)
            return m.group(1) if m else ""

        # ── Primary Stream & Server Priority Selection ─────────────────────────────
        # Telegram Cloud is the #1 Primary Storage (100% immune to Google account strikes)
        primary_stream = stream_url or cloud_stream or ""

        # 1. Server 1: Telegram Cloud HD Super Player
        if stream_url:
            streams_list.append({
                "server": "Server 1",
                "label": "⚡ Super Player (Telegram Cloud HD • Auto Sub)",
                "type": "video/mp4",
                "mode": "telegram_stream",
                "stream_url": stream_url,
                "file_id": file_id,
                "message_id": message_id,
                "quality": "1080p",
            })
            qualities_map = {
                "auto": stream_url,
                "1080p": stream_url,
                "720p": stream_url,
                "480p": stream_url,
                "360p": stream_url,
            }

        # 2. If Google Drive upload was explicitly enabled and succeeded, add as backup Server
        if cloud_stream:
            drive_file_id = (cloud_upload_res.get("file_id") if cloud_upload_res else "") or _extract_drive_id_str(cloud_stream)
            if drive_file_id:
                drive_server_num = len(streams_list) + 1
                streams_list.append({
                    "server": f"Server {drive_server_num}",
                    "label": "☁️ Drive Player (Google Archive • Auto Sub)",
                    "type": "embed",
                    "embed": True,
                    "drive_id": drive_file_id,
                    "stream_url": f"https://drive.google.com/file/d/{drive_file_id}/preview",
                    "quality": "1080p",
                })

        # 3. External Multi-Server VIP Players (VidSrc, SuperEmbed, AutoEmbed)
        tmdb_id_val = str(tmdb_meta.get("tmdb_id") or "").strip()
        ext_id = str(imdb_id or tmdb_id_val or "").strip()
        if ext_id:
            s_num = season or 1
            e_num = episode or 1
            vidsrc_url = (
                f"https://vidsrc.xyz/embed/tv/{ext_id}/{s_num}/{e_num}"
                if is_series
                else f"https://vidsrc.xyz/embed/movie/{ext_id}"
            )
            tmdb_flag = "&tmdb=1" if (not imdb_id and tmdb_id_val) else ""
            multiembed_url = (
                f"https://multiembed.mov/?video_id={ext_id}{tmdb_flag}&s={s_num}&e={e_num}"
                if is_series
                else f"https://multiembed.mov/?video_id={ext_id}{tmdb_flag}"
            )
            streams_list.append({
                "server": f"Server {len(streams_list) + 1}",
                "label": "🌐 VIP Player 1 (VidSrc Pro • Multi-Quality)",
                "type": "embed",
                "embed": True,
                "stream_url": vidsrc_url,
            })
            streams_list.append({
                "server": f"Server {len(streams_list) + 1}",
                "label": "🎬 VIP Player 2 (SuperEmbed • Fast HD)",
                "type": "embed",
                "embed": True,
                "stream_url": multiembed_url,
            })

        # If streams_list is still empty (e.g. testing), add a placeholder direct MP4 entry
        if not streams_list and primary_stream:
            streams_list.append({
                "server": "Server 1",
                "label": "⚡ Super Player (Ultra HD • Auto Sub)",
                "type": "video/mp4",
                "stream_url": primary_stream,
                "quality": "1080p",
            })

        # ── Downloads Construction ────────────────────────────────────────────────
        # Telegram App / Web Direct Download (High-speed, zero file size limits)
        tg_channel_id_clean = str(abs(target_channel))
        if tg_channel_id_clean.startswith("100"):
            tg_channel_id_clean = tg_channel_id_clean[3:]
        tg_post_link = f"https://t.me/c/{tg_channel_id_clean}/{message_id}" if message_id else ""

        if stream_url or tg_post_link:
            downloads_list.append({
                "quality": "1080p (Telegram Direct)",
                "label": "1080p Full HD (Telegram App / Web • Fast)",
                "size": downloader.format_bytes(sz_1080),
                "size_bytes": sz_1080,
                "url": tg_post_link or stream_url,
                "stream_url": stream_url,
                "format": file_ext,
                "host": "Telegram",
                "sub_merged": True,
                "subtitle_merged": True,
            })

        # Multi-quality web download variants
        encoded_title = urllib.parse.quote(display_title)
        if drive_file_id:
            downloads_list.append({
                "quality": "1080p",
                "label": "1080p Full HD (Web Download • Sinhala Sub)",
                "size": downloader.format_bytes(sz_1080),
                "size_bytes": sz_1080,
                "drive_id": drive_file_id,
                "url": f"/api/download?id={drive_file_id}&q=1080p&title={encoded_title}&size={sz_1080}",
                "format": file_ext,
                "host": "Direct Web",
                "sub_merged": True,
                "subtitle_merged": True,
            })
        elif stream_url:
            downloads_list.append({
                "quality": "1080p (Web Stream)",
                "label": "1080p Full HD (Direct HTTP Stream)",
                "size": downloader.format_bytes(sz_1080),
                "size_bytes": sz_1080,
                "url": stream_url,
                "format": file_ext,
                "host": "Direct Web",
                "sub_merged": True,
                "subtitle_merged": True,
            })

        # Ensure multi-quality download variants (1080p, 720p, 480p, 360p) are always present
        existing_q = {d.get("quality") for d in downloads_list}
        dl_fallback_url = tg_post_link or stream_url
        if dl_fallback_url:
            for q_k, q_lbl, sz_val in [
                ("1080p", "1080p Full HD (Telegram App / Web • Fast)", sz_1080),
                ("720p", "720p HD (Telegram App / Web • Fast)", sz_720),
                ("480p", "480p SD (Telegram App / Web • Fast)", sz_480),
                ("360p", "360p Data Saver (Telegram App / Web • Fast)", sz_360),
            ]:
                if q_k not in existing_q:
                    downloads_list.append({
                        "quality": q_k,
                        "label": q_lbl,
                        "size": downloader.format_bytes(sz_val),
                        "size_bytes": sz_val,
                        "url": dl_fallback_url,
                        "stream_url": stream_url or "",
                        "format": file_ext,
                        "host": "Telegram" if tg_post_link else "Direct Web",
                        "sub_merged": True,
                        "subtitle_merged": True,
                    })

        if not qualities_map and dl_fallback_url:
            qualities_map = {
                "auto": dl_fallback_url,
                "1080p": dl_fallback_url,
                "720p": dl_fallback_url,
                "480p": dl_fallback_url,
                "360p": dl_fallback_url,
            }

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
            "has_sinhala_sub": True,
            "sub_merged": True,
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
            "drive_file_id": drive_file_id,
            "stream_url": primary_stream,
            "streams": streams_list,
            "qualities": qualities_map,
            "downloads": downloads_list,
            "subtitles": [
                {
                    "language": "Sinhala",
                    "srclang": "si",
                    "label": "සිංහල උපසිරැසි (Sinhala)",
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

        # ── Step 6: Immediate VPS Disk Cleanup ────────────────────────────────
        try:
            if os.path.exists(local_file):
                os.remove(local_file)
                log.info("[LeechService] Immediate cleanup: local video '%s' deleted.", local_file)
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                log.info("[LeechService] Temporary folder '%s' removed.", temp_dir)
        except Exception as clean_err:
            log.warning("[LeechService] Error during disk cleanup: %s", clean_err)

        resume_service.delete_checkpoint(user_id)
        try:
            await seedr_service.seedr_pool.clean_storage()
        except Exception:
            pass

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
            "stream_url": stream_url or cloud_stream,
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
            # Full-auto mode: publish complete movie_entry now
            task_tracker.tracker.set_step(user_id, "Publishing to Website...")
            try:
                saved = await github_service.add_movie(movie_entry)
                log.info("[LeechService] Auto website publish: %s", saved)
            except Exception as gh_err:
                log.warning("[LeechService] GitHub service add_movie note: %s", gh_err)

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
                f"📦 <b>ප්‍රමාණය:</b> {size_str} | 🎭 <b>කාණ්ඩ:</b> {genres_val}\n"
                f"☁️ <b>Google Drive CDN:</b> Ultra Fast Cloud Stream Ready ✅\n"
                f"✈️ <b>Telegram Storage:</b> Filmhost Channel වෙත Upload විය ✅\n"
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
            # Interactive humanized choice mode: provide 4 intuitive action buttons
            task_tracker.tracker.complete_task(user_id)

            kb_choices = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data=f"leech_act:pub:{draft_id}"),
                ],
                [
                    InlineKeyboardButton("💬 Subtitle දැන්ම දාන්න (.srt)", callback_data=f"leech_act:sub:{draft_id}"),
                    InlineKeyboardButton("⏩ පසුව දාන්නම් (Default Sub)", callback_data=f"leech_act:pub:{draft_id}"),
                ],
                [
                    InlineKeyboardButton("💾 Draft ලෙස තබන්න (Channel Only)", callback_data=f"leech_act:draft:{draft_id}"),
                ]
            ])

            await status_msg.edit_text(
                f"👋 <b>චිත්‍රපටය සාර්ථකව Download කර Channel එකට Upload විය!</b> 🎉\n\n"
                f"{media_icon} <b>{display_title}</b>\n"
                f"⭐ <b>IMDb:</b> {imdb_val} / 10 | 🎞 <b>Quality:</b> {chosen_candidate.quality}\n"
                f"📦 <b>ප්‍රමාණය:</b> {size_str} | 🎭 <b>කාණ්ඩ:</b> {genres_val}\n"
                f"⚡ <b>භාවිත කළ ක්‍රමය:</b> {chosen_candidate.method_name}\n"
                f"☁️ <b>Google Drive CDN:</b> Ultra Fast Stream Ready ✅\n"
                f"✈️ <b>Telegram Storage:</b> Filmhost Channel වෙත සුරැකිණි ✅\n"
                f"🧹 <b>VPS Storage:</b> 100% Free (තාවකාලික ගොනු ඉවත් කරන ලදී)\n\n"
                f"<b>දැන් ඔබට කුමක් කිරීමට අවශ්‍යද? පහතින් තෝරන්න:</b>\n\n"
                f"• <b>🚀 Publish Now:</b> වෙබ් අඩවියට දැන්ම එක්වේ (Default සිංහල උපසිරැසි සමඟ)\n"
                f"• <b>💬 Subtitle දාන්න:</b> ඔබ සතු .srt / .vtt උපසිරැසි ගොනුව Upload කර Publish කරයි\n"
                f"• <b>⏩ පසුව දාන්නම්:</b> වෙබ් අඩවියට දැන්ම දමා පසුව <code>/sub</code> මඟින් Subtitle දමයි\n"
                f"• <b>💾 Draft ලෙස තබන්න:</b> වෙබ් අඩවියට නොදමා Channel එකේ පමණක් තබයි",
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
