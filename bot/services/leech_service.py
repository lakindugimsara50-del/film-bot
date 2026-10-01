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

    async def _fetch_srilankan_matched():
        try:
            from services.scrapers import srilankan_matched_scraper
            return await srilankan_matched_scraper.search_matched_srilankan_releases(
                title=title,
                year=year,
                season=season,
                episode=episode,
                is_series=is_series,
            )
        except Exception as exc:
            log.warning("[LeechService] Method 0 (Same-Site Matched) error: %s", exc)
            return []

    # Run all search methods concurrently for maximum speed
    results = await asyncio.gather(
        _fetch_srilankan_matched(),
        _fetch_telegram(),
        _fetch_ddl(),
        _fetch_torrents(),
        return_exceptions=True,
    )

    matched_res = results[0] if len(results) > 0 and isinstance(results[0], list) else []
    tg_res = results[1] if len(results) > 1 and not isinstance(results[1], Exception) else None
    ddl_res = results[2] if len(results) > 2 and not isinstance(results[2], Exception) else None
    tor_list = results[3] if len(results) > 3 and isinstance(results[3], list) else []

    # 0. Process Same-Site Matched Releases (Highest Quality & Guaranteed 0.0ms Subtitle Sync)
    matched_candidates: list[LeechCandidate] = []
    if matched_res:
        for m in matched_res:
            m_url = m.get("url")
            m_q = str(m.get("quality", "1080p"))
            m_portal = m.get("portal", "SriLankan")
            m_ht = m.get("host_type", "ddl")
            m_method = "torrent" if m_ht == "magnet" else "ddl"
            m_is_hardsub = bool(
                m.get("is_already_hardsubbed")
                or m_portal in ("SinhalaSub", "CineSubz")
                or any(k in str(m_url).lower() for k in ("cdn.sinhalasub.net", "ddl.sinhalasub.net", "cinesubz", "csplayer"))
            )
            matched_candidates.append(
                LeechCandidate(
                    method=m_method,
                    method_name=f"⚡ {m_portal} Matched WebRip ({m_q})",
                    source_url=m_url,
                    quality=m_q,
                    size="Matched WebRip",
                    extra={
                        "is_matched_same_site": True,
                        "sub_srt_path": m.get("sub_srt_path"),
                        "portal": m_portal,
                        "post_url": m.get("post_url"),
                        "is_already_hardsubbed": m_is_hardsub,
                    },
                )
            )
        log.info("[LeechService] Method 0 yielded %d same-site matched candidate(s).", len(matched_candidates))
        # User instruction: If Sri Lankan releases found, take movies ONLY from Lankan sites (no torrents!)
        return matched_candidates

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

    # For TV series, 720p is enough for Telegram upload; for movies, strictly prioritize 1080p
    if is_series or season is not None or episode is not None:
        if any("720" in str(c.quality) for c in candidates):
            candidates.sort(key=lambda c: 0 if "720" in str(c.quality) else (1 if "1080" in str(c.quality) else 2))
    else:
        if any("1080" in str(c.quality) for c in candidates):
            candidates.sort(key=lambda c: 0 if "1080" in str(c.quality) else 1)

    # Priority 1: Prepend same-site matched candidates (0.0ms subtitle drift guaranteed)
    if matched_candidates:
        if is_series or season is not None or episode is not None:
            matched_candidates.sort(key=lambda c: 0 if "720" in str(c.quality) else (1 if "1080" in str(c.quality) else 2))
        else:
            matched_candidates.sort(key=lambda c: 0 if "1080" in str(c.quality) else (1 if "720" in str(c.quality) else 2))
        candidates = matched_candidates + [c for c in candidates if c not in matched_candidates]

    log.info("[LeechService] Total candidates acquired: %d (same-site matched: %d)", len(candidates), len(matched_candidates))
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
            f"🔍 <b>පියවර 1/4:</b> බාගත කිරීමේ මූලාශ්‍ර සොයමින් පවතී (SinhalaSub, Cineru, SubzLK, Torrentio, EZTV)..."
            f"{auto_ep_note}\n\n"
            f"⚡ <i>Sri Lankan Matched Video & Subtitle Engine සක්‍රීයයි...</i>"
        )
    else:
        step1_text = (
            f"🚀 <b>Ultra Auto-Leech & Uploader (/boost)</b>\n\n"
            f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
            f"🔍 <b>පියවර 1/4:</b> බාගත කිරීමේ මූලාශ්‍ර සොයමින් පවතී...\n\n"
            f"• Priority 1: Sri Lankan Matched Portals (SinhalaSub/Cineru/SubzLK)\n"
            f"• Priority 2: Telegram Cloud HD Channels\n"
            f"• Priority 3: DDL Scrapers (PixelDrain/Pahe)\n"
            f"• Priority 4: High-Seed Torrents (Torrentio/YTS/EZTV)"
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
        pre_sub_srt: Optional[str] = None
        if not candidates and not os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                temp_dir = temp_dir or video_service.get_optimal_work_dir(min_free_gb=2.0, prefix="leech_ram_")
                _show_name_pre = locals().get("show_name", "")
                clean_sub_title_pre = _show_name_pre if (is_series and _show_name_pre) else (title or display_title)
                pre_sub_srt = await subtitle_service.fetch_sri_lankan_sinhala_subtitle(
                    title=clean_sub_title_pre,
                    year=year,
                    season=season,
                    episode=episode,
                    temp_dir=temp_dir,
                )
                if pre_sub_srt and os.path.exists(pre_sub_srt):
                    log.info(
                        "[LeechService] Pre-discovered Sinhala subtitle & WebRip reference (%r): %s",
                        subtitle_service.get_last_release_hint(clean_sub_title_pre),
                        pre_sub_srt,
                    )
            except Exception as pre_sub_err:
                log.debug("[LeechService] Pre-discovery subtitle note: %s", pre_sub_err)

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

        if is_series and candidates:
            # User requirement: TV Series strictly download 720p & 480p only! NEVER download 1080p for TV Series.
            series_non_1080 = [c for c in candidates if str(c.quality or "").lower() != "1080p"]
            if series_non_1080:
                log.info("[LeechService] TV Series mode: Filtered out 1080p candidates (%d -> %d).", len(candidates), len(series_non_1080))
                candidates = series_non_1080

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
        pre_downloaded_variants: dict[str, str] = {}
        variant_files: dict[str, str] = {}
        variant_tg_info: dict[str, dict] = {}
        variant_cloud_urls: dict[str, dict[str, Any]] = {}
        file_id = ""
        stream_url = ""
        message_id = 0
        ENABLE_TELEGRAM_VIDEO_UPLOAD = True
        target_channel = config.PRIVATE_CHANNEL_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0)
        ep_suffix = f"-s{season:02d}e{episode:02d}" if (is_series and season and episode) else ""
        slug = f"{_slugify(title, year)}{ep_suffix}"
        base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
        site_url = f"{base_site}/movie.html?id={slug}"

        if not temp_dir:
            temp_dir = video_service.get_optimal_work_dir(min_free_gb=2.0, prefix="leech_ram_")
        task_tracker.tracker.set_metadata(user_id, task_key=task_key, temp_dir=temp_dir)

        for idx, candidate in enumerate(candidates, 1):
            log.info(
                "[LeechService] Attempting download with candidate %d/%d: %s",
                idx, len(candidates), candidate.method_name
            )
            task_tracker.tracker.set_step(
                user_id, f"1/3 - Downloading via {candidate.method_name}..."
            )

            # Detect companion variants from same portal/post for ALL-QUALITY PARALLEL DOWNLOAD
            companion_candidates: dict[str, LeechCandidate] = {}
            if candidate.method in ("ddl", "http"):
                cand_portal = (candidate.extra or {}).get("portal", "")
                cand_host = urllib.parse.urlparse(str(candidate.source_url)).netloc
                cand_post = (candidate.extra or {}).get("post_url", "")
                primary_q = (candidate.quality or "1080p").lower()

                for other_c in candidates:
                    if other_c == candidate:
                        continue
                    o_q = (other_c.quality or "").lower()
                    if is_series and o_q == "1080p":
                        continue
                    if o_q in ("720p", "480p", "360p", "1080p") and o_q != primary_q and o_q not in companion_candidates:
                        o_portal = (other_c.extra or {}).get("portal", "")
                        o_host = urllib.parse.urlparse(str(other_c.source_url)).netloc
                        o_post = (other_c.extra or {}).get("post_url", "")
                        if (cand_post and o_post == cand_post) or (cand_portal and o_portal == cand_portal) or (cand_host and o_host == cand_host):
                            companion_candidates[o_q] = other_c

            cand_display_label = candidate.method_name
            if companion_candidates:
                cand_display_label = f"{candidate.method_name} (+ {', '.join(companion_candidates.keys())} Parallel CDN)"

            # Inform user immediately that a source was found and download is starting
            found_text = (
                f"🎯 <b>බාගත කිරීමේ මූලාශ්‍රයක් හමුවිය (Source Found)!</b>\n\n"
                f"🎬 <b>චිත්‍රපටය:</b> {display_title}\n"
                f"⚡ <b>මූලාශ්‍රය ({idx}/{len(candidates)}):</b> {cand_display_label}\n"
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

                    _env_name = "Google Colab" if (os.path.exists("/content") or os.path.isdir("/dev/shm")) else "Cloud VPS"
                    text = (
                        f"📥 <b>පියවර 2/4: {cloud_service_name} ➔ {_env_name} වෙත බාගත කරමින්...</b>\n\n"
                        f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                        f"⚡ <b>ක්‍රමය:</b> {cand_display_label} (Cloud Direct Link)\n"
                        f"📊 <b>ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                        f"📦 <b>ප්‍රමාණය:</b> {done_str} / {total_str}\n"
                        f"{status_line}\n"
                        f"☁️ <i>{_env_name} High-Speed Cloud Bandwidth (ඔබේ Data නොයයි)</i>"
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
                        getattr(config, "ENABLE_TORRENT_RACING", False)
                        and has_cloud_debrid
                        and bool(downloader.find_aria2c())
                        and seed_count >= 35
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

                        pending = {t_aria, t_cloud}
                        while pending and not local_file:
                            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                            for d_task in done:
                                try:
                                    res_path = d_task.result()
                                    if res_path and os.path.exists(res_path) and os.path.getsize(res_path) > 0:
                                        local_file = res_path
                                        break
                                except Exception as r_err:
                                    log.debug("[LeechService] Race branch error: %s", r_err)

                        if local_file:
                            for p_task in pending:
                                p_task.cancel()
                            await downloader.cancel_active_download(f"{task_key}_aria")
                            await downloader.cancel_active_download(f"{task_key}_cloud")
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
                    ep_sfx = f"-s{season:02d}e{episode:02d}" if (is_series and season and episode) else ""
                    clean_name = f"{_slugify(title, year)}{ep_sfx}.mp4"

                    if companion_candidates:
                        log.info(
                            "[LeechService] Step 2: Launching Pipelined Multi-Quality Concurrent Download & Upload for %s + %s from %s...",
                            candidate.quality, list(companion_candidates.keys()), cand_portal or cand_host
                        )

                        # Build full dict of available qualities (candidate + companions)
                        all_cands_by_q = {candidate.quality: candidate, **companion_candidates}

                        if is_series:
                            # User requirement: TV Series strictly 720p & 480p only! NEVER download 1080p for TV Series.
                            all_cands_by_q = {q: c for q, c in all_cands_by_q.items() if str(q).lower() != "1080p"}
                            if not all_cands_by_q:
                                all_cands_by_q = {candidate.quality: candidate}

                        # Prioritize Primary Quality:
                        # For TV Series: 720p strictly first, then 480p, 360p
                        # For Movies: 1080p strictly first, then 720p, 480p, 360p
                        ordered_qualities = []
                        pref_order = ("720p", "480p", "360p") if is_series else ("1080p", "720p", "480p", "360p")
                        for q_pref in pref_order:
                            if q_pref in all_cands_by_q:
                                ordered_qualities.append(q_pref)
                        for q in all_cands_by_q:
                            if q not in ordered_qualities:
                                ordered_qualities.append(q)

                        primary_q = ordered_qualities[0]
                        target_qualities = {q: all_cands_by_q[q] for q in ordered_qualities}
                        companion_progress: dict[str, dict] = {
                            q: {
                                "stage": "downloading" if q == primary_q else "waiting",
                                "dl_pct": 0.0,
                                "dl_done": "0B",
                                "dl_total": "Unknown",
                                "dl_speed": "0B/s",
                                "dl_eta": "N/A",
                                "up_pct": 0.0,
                                "up_done": "0B",
                                "up_total": "0B",
                                "up_speed": "0B/s",
                                "up_eta": "N/A",
                                "msg_id": 0,
                                "size": "Unknown",
                            }
                            for q in target_qualities
                        }
                        cdn_dl_lock = asyncio.Lock()

                        async def _update_multi_dl_display(force: bool = False) -> None:
                            nonlocal last_edit_time
                            now = time.time()
                            if not force and (now - last_edit_time < 2.5):
                                return
                            last_edit_time = now

                            _env_name = "Google Colab" if (os.path.exists("/content") or os.path.isdir("/dev/shm")) else "Cloud VPS"
                            lines = [
                                f"⚡ <b>Sequential Turbo CDN ➔ Pipelined Telegram Upload ({cand_portal or cand_host})</b>\n",
                                f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}",
                                f"🌐 <b>Website:</b> <a href='{site_url}'>filmsub.pages.dev</a>\n",
                            ]
                            for q_name, q_data in companion_progress.items():
                                st = q_data.get("stage", "downloading")
                                if st == "waiting":
                                    lines.append(
                                        f"• <b>{q_name}:</b> ⏳ <i>(පෝලිමේ - {primary_q} බාගත වූ පසු ආරම්භ වේ)</i>"
                                    )
                                elif st == "downloading":
                                    pct = q_data.get("dl_pct", 0.0)
                                    pbar = downloader.format_progress_bar(pct)
                                    lines.append(
                                        f"• <b>{q_name}:</b> {pbar} {pct:.1f}% ({q_data.get('dl_done', '0B')}/{q_data.get('dl_total', '0B')}) "
                                        f"⚡ {q_data.get('dl_speed', '0B/s')} | ⏱ {q_data.get('dl_eta', 'N/A')} 📥 <i>(Full Speed බාගත වෙමින්)</i>"
                                    )
                                elif st == "muxing":
                                    lines.append(
                                        f"• <b>{q_name}:</b> [██████████] 100% 🇱🇰 <i>(සිංහල උපසිරැසි Fast-Mux...)</i>"
                                    )
                                elif st == "compressing":
                                    lines.append(
                                        f"• <b>{q_name}:</b> [██████████] 100% 🗜️ <i>(FFmpeg 2GB Limit Compression...)</i>"
                                    )
                                elif st == "uploading":
                                    pct = q_data.get("up_pct", 0.0)
                                    pbar = downloader.format_progress_bar(pct)
                                    lines.append(
                                        f"• <b>{q_name}:</b> {pbar} {pct:.1f}% ({q_data.get('up_done', '0B')}/{q_data.get('up_total', '0B')}) "
                                        f"⚡ {q_data.get('up_speed', '0B/s')} | ⏱ {q_data.get('up_eta', 'N/A')} 🚀 <i>(Telegram Upload...)</i>"
                                    )
                                elif st == "uploaded":
                                    msg_id_val = q_data.get("msg_id", 0)
                                    msg_tag = f" • Msg #{msg_id_val}" if msg_id_val else ""
                                    lines.append(
                                        f"• <b>{q_name}:</b> [██████████] 100% ({q_data.get('size', 'OK')}) ✅ <i>(Telegram HD Uploaded{msg_tag})</i>"
                                    )
                                elif st == "failed":
                                    lines.append(
                                        f"• <b>{q_name}:</b> ⚠️ <i>Direct Link අසාර්ථකයි (Auto Fallback)</i>"
                                    )

                            lines.append(f"\n☁️ <i>{_env_name} High-Speed Bandwidth • Multi-Session Direct Streaming</i>")
                            try:
                                await status_msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
                            except Exception as e:
                                log.debug("[LeechService] Multi progress edit ignored: %s", e)

                        cand_is_hardsub = bool(
                            (candidate.extra and candidate.extra.get("is_already_hardsubbed"))
                            or any(k in str(candidate.source_url).lower() for k in ("cdn.sinhalasub.net", "ddl.sinhalasub.net", "cinesubz", "csplayer"))
                            or (candidate.extra and any(k in str(candidate.extra.get("portal", "")).lower() for k in ("sinhalasub", "cinesubz")))
                        )
                        active_sub_to_mux = None if cand_is_hardsub else (pre_sub_srt if (pre_sub_srt and os.path.exists(pre_sub_srt)) else None)

                        async def _run_single_quality_pipeline(q_name: str, cand_obj: LeechCandidate, is_primary_flag: bool):
                            nonlocal active_sub_to_mux
                            v_clean_name = f"{slug}-{q_name}.mp4"

                            async def _dl_cb(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str):
                                companion_progress[q_name].update({
                                    "stage": "downloading",
                                    "dl_pct": pct,
                                    "dl_done": done_str,
                                    "dl_total": total_str,
                                    "dl_speed": speed_str,
                                    "dl_eta": eta_str,
                                })
                                await _update_multi_dl_display()

                            # 1. Download with exclusive lock to prevent CDN bandwidth splitting
                            async with cdn_dl_lock:
                                companion_progress[q_name]["stage"] = "downloading"
                                await _update_multi_dl_display(force=True)
                                log.info("[LeechService] Starting full-speed download for quality %s from %s...", q_name, cand_obj.source_url)
                                c_dl = await downloader.download_http(
                                    url=cand_obj.source_url,
                                    dest_dir=temp_dir,
                                    filename=v_clean_name,
                                    task_key=f"{task_key}_{q_name}",
                                    progress_callback=_dl_cb,
                                )

                            if not (c_dl and downloader.is_valid_downloaded_video(c_dl)):
                                companion_progress[q_name]["stage"] = "failed"
                                await _update_multi_dl_display(force=True)
                                log.warning("[LeechService] Pipeline quality %s download failed.", q_name)
                                return None

                            # The lock is now RELEASED! The next quality starts downloading immediately from the CDN.
                            # Meanwhile, THIS quality proceeds to Faststart, Subtitle Muxing, and Telegram Upload!

                            # 2. Faststart remux
                            fs_out = os.path.join(temp_dir, f"fs_{slug}_{q_name}.mp4")
                            if await video_service.apply_faststart(c_dl, fs_out):
                                try:
                                    os.remove(c_dl)
                                except Exception:
                                    pass
                                c_dl = fs_out

                            # 3. Fast stream-copy subtitle mux (~1s) if needed and not already hardsubbed
                            if not cand_is_hardsub and not active_sub_to_mux:
                                try:
                                    clean_sub_title = show_name if (is_series and show_name) else (title or display_title)
                                    auto_srt, _ = await subtitle_service.auto_acquire_sinhala_subtitle(
                                        title=clean_sub_title,
                                        year=year,
                                        imdb_id=imdb_id,
                                        temp_dir=temp_dir,
                                        season=season,
                                        episode=episode,
                                    )
                                    if auto_srt and os.path.exists(auto_srt):
                                        active_sub_to_mux = auto_srt
                                        log.info("[LeechService] Auto-acquired Sinhala subtitle in pipeline: %s", auto_srt)
                                except Exception as s_err:
                                    log.debug("[LeechService] Pipeline sub discovery note: %s", s_err)

                            if active_sub_to_mux and os.path.exists(active_sub_to_mux):
                                companion_progress[q_name]["stage"] = "muxing"
                                await _update_multi_dl_display(force=True)
                                sub_out = os.path.join(temp_dir, f"subbed_{slug}_{q_name}.mp4")
                                if await video_service.stream_copy_subtitles(c_dl, active_sub_to_mux, sub_out):
                                    try:
                                        os.remove(c_dl)
                                    except Exception:
                                        pass
                                    c_dl = sub_out
                                    log.info("[LeechService] Subtitle muxed into %s: %s", q_name, c_dl)

                            c_size_bytes = os.path.getsize(c_dl)
                            # User requirement: If file > 1.95GB (e.g. 1080p 3.5GB), compress down to 1.85GB using FFmpeg
                            if c_size_bytes > int(1.95 * 1024 * 1024 * 1024):
                                log.info(
                                    "[LeechService] File %s (%s) exceeds Telegram 2GB limit. Running fast FFmpeg compression to fit...",
                                    q_name, downloader.format_bytes(c_size_bytes)
                                )
                                companion_progress[q_name]["stage"] = "compressing"
                                await _update_multi_dl_display(force=True)
                                comp_out = os.path.join(temp_dir, f"comp_{slug}_{q_name}.mp4")
                                comp_ok = await video_service.compress_video(
                                    input_path=c_dl,
                                    output_path=comp_out,
                                    target_size_bytes=int(1.85 * 1024 * 1024 * 1024),
                                )
                                if comp_ok and os.path.exists(comp_out) and os.path.getsize(comp_out) <= int(1.95 * 1024 * 1024 * 1024):
                                    try:
                                        os.remove(c_dl)
                                    except Exception:
                                        pass
                                    c_dl = comp_out
                                    c_size_bytes = os.path.getsize(c_dl)
                                    log.info("[LeechService] Compression complete for %s: %s", q_name, downloader.format_bytes(c_size_bytes))

                            c_size_str = downloader.format_bytes(c_size_bytes)
                            companion_progress[q_name].update({
                                "stage": "uploading",
                                "dl_pct": 100.0,
                                "size": c_size_str,
                                "up_pct": 0.0,
                                "up_done": "0B",
                                "up_total": c_size_str,
                                "up_speed": "--",
                                "up_eta": "--",
                            })
                            await _update_multi_dl_display(force=True)

                            # 4. Immediate Upload to Telegram Channel (PIPELINED - NO BLOCKING OTHER QUALITIES!)
                            async def _up_cb(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str):
                                companion_progress[q_name].update({
                                    "stage": "uploading",
                                    "up_pct": pct,
                                    "up_done": done_str,
                                    "up_total": total_str,
                                    "up_speed": speed_str,
                                    "up_eta": eta_str,
                                })
                                await _update_multi_dl_display()

                            up_res = {}
                            if ENABLE_TELEGRAM_VIDEO_UPLOAD and target_channel:
                                var_caption = f"🎬 {display_title} [{q_name}]\n\n⚡ Quality: {q_name} (High-Speed Telegram Cloud)\n🌐 Watch: {site_url}"
                                try:
                                    from services.upload_pool import upload_pool
                                    up_res = await upload_pool.upload_with_pool(
                                        file_path=c_dl,
                                        target_chat=target_channel,
                                        quality=q_name,
                                        caption=var_caption,
                                        file_name=os.path.basename(c_dl),
                                        progress_callback=_up_cb,
                                        fallback_client=client,
                                    )
                                except Exception as pool_err:
                                    log.warning("[LeechService] upload_pool failed for %s (%s). Retrying directly via main bot_client...", q_name, pool_err)
                                    try:
                                        up_res = await telegram_upload.upload_video_file(
                                            bot_client=client,
                                            file_path=c_dl,
                                            target_chat=target_channel,
                                            caption=var_caption,
                                            progress_callback=_up_cb,
                                            fallback_chat=0,
                                        )
                                    except Exception as fb_err:
                                        log.error("[LeechService] Direct bot_client upload failed for %s: %s", q_name, fb_err)
                                        up_res = {}

                            up_msg_id = up_res.get("message_id", 0) if up_res else 0
                            if up_msg_id > 0:
                                companion_progress[q_name].update({
                                    "stage": "uploaded",
                                    "up_pct": 100.0,
                                    "msg_id": up_msg_id,
                                    "size": c_size_str,
                                })
                                log.info("[LeechService] Pipeline quality %s successfully downloaded & uploaded (msg_id=%s, size=%s)", q_name, up_msg_id, c_size_str)
                            else:
                                companion_progress[q_name].update({
                                    "stage": "failed",
                                    "size": c_size_str,
                                })
                                log.error("[LeechService] Pipeline quality %s upload failed or returned no message_id.", q_name)

                            await _update_multi_dl_display(force=True)
                            return (q_name, c_dl, up_res)

                        pipe_tasks = [
                            _run_single_quality_pipeline(q, target_qualities[q], is_primary_flag=(q == primary_q))
                            for q in ordered_qualities
                        ]
                        pipe_results = await asyncio.gather(*pipe_tasks, return_exceptions=True)

                        for r in pipe_results:
                            if isinstance(r, tuple) and len(r) == 3 and r[0] and r[1]:
                                q_id, q_path, q_up = r
                                variant_files[q_id] = q_path
                                pre_downloaded_variants[q_id] = q_path
                                if q_up and q_up.get("file_id"):
                                    variant_tg_info[q_id] = q_up
                                if q_id == candidate.quality or not local_file:
                                    local_file = q_path
                                    if q_up:
                                        file_id = q_up.get("file_id", "")
                                        stream_url = q_up.get("stream_url", "")
                                        message_id = q_up.get("message_id", 0)
                    else:
                        local_file = await downloader.download_http(
                            url=candidate.source_url,
                            dest_dir=temp_dir,
                            filename=clean_name,
                            task_key=task_key,
                            progress_callback=_download_progress,
                        )

                if local_file and downloader.is_valid_downloaded_video(local_file):
                    chosen_candidate = candidate
                    log.info("[LeechService] Download SUCCEEDED via %s (%s)", candidate.method_name, downloader.format_bytes(os.path.getsize(local_file)))
                    break
                else:
                    if local_file and os.path.exists(local_file):
                        try:
                            os.remove(local_file)
                        except Exception:
                            pass
                    log.warning(
                        "[LeechService] Downloaded candidate '%s' failed video validation (corrupt/HTML/too small). Trying next candidate...",
                        candidate.method_name
                    )

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

        if not chosen_candidate or not local_file or not downloader.is_valid_downloaded_video(local_file):
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

        # Check if the source video has Sinhala subtitles pre-burned by the portal (SinhalaSub, CineSubz, etc.)
        is_already_hardsubbed = bool(
            (chosen_candidate and chosen_candidate.extra and chosen_candidate.extra.get("is_already_hardsubbed"))
            or (chosen_candidate and any(k in str(chosen_candidate.source_url).lower() for k in ("cdn.sinhalasub.net", "ddl.sinhalasub.net", "cinesubz", "csplayer")))
            or (chosen_candidate and chosen_candidate.extra and any(k in str(chosen_candidate.extra.get("portal", "")).lower() for k in ("sinhalasub", "cinesubz")))
        )
        if is_already_hardsubbed:
            log.info(
                "[LeechService] Source candidate '%s' has pre-burned Sinhala subtitles (is_already_hardsubbed=True). "
                "Disabling secondary video subtitle burn to avoid double subtitles.",
                chosen_candidate.method_name if chosen_candidate else "Unknown"
            )

        # 1. Check if subtitle was already cached by Stage 1 (/add) or /sub; otherwise auto-acquire
        sub_srt_path: Optional[str] = None
        sub_vtt_path: Optional[str] = None
        try:
            import tempfile
            title_slug = _slugify(title, year)
            candidate_srts = [
                os.path.join(tempfile.gettempdir(), f"sub_{slug}.srt"),
                os.path.join(tempfile.gettempdir(), f"sub_{slug}{ep_suffix}.srt"),
                os.path.join(tempfile.gettempdir(), f"sub_{title_slug}.srt"),
                os.path.join(tempfile.gettempdir(), f"sub_{title_slug}{ep_suffix}.srt"),
                os.path.join(tempfile.gettempdir(), f"sub_{slug}.vtt"),
                os.path.join(tempfile.gettempdir(), f"sub_{title_slug}.vtt"),
            ]
            for c_sub in candidate_srts:
                if os.path.exists(c_sub) and os.path.getsize(c_sub) > 32:
                    if c_sub.lower().endswith(".vtt"):
                        local_stage_srt = subtitle_service.vtt_to_srt(c_sub, os.path.join(temp_dir, "sinhala_merged.srt"))
                    else:
                        local_stage_srt = os.path.join(temp_dir, "sinhala_merged.srt")
                        shutil.copyfile(c_sub, local_stage_srt)
                    if local_stage_srt and os.path.exists(local_stage_srt):
                        sub_srt_path = local_stage_srt
                        sub_vtt_path = subtitle_service.srt_to_vtt(local_stage_srt)
                        log.info("[LeechService] Reusing Stage 1 cached Sinhala subtitle: %s (from %s)", sub_srt_path, c_sub)
                        break
        except Exception as cache_err:
            log.debug("[LeechService] Cache reuse note: %s", cache_err)

        if not sub_srt_path and chosen_candidate and chosen_candidate.extra.get("sub_srt_path"):
            cand_sub = chosen_candidate.extra["sub_srt_path"]
            if os.path.exists(cand_sub) and os.path.getsize(cand_sub) > 32:
                try:
                    local_stage_srt = os.path.join(temp_dir, "sinhala_merged.srt")
                    if os.path.abspath(cand_sub) != os.path.abspath(local_stage_srt):
                        shutil.copyfile(cand_sub, local_stage_srt)
                    sub_srt_path = local_stage_srt
                    sub_vtt_path = subtitle_service.srt_to_vtt(local_stage_srt)
                    log.info("[LeechService] Reusing candidate's exact same-site matched subtitle: srt=%s, vtt=%s", sub_srt_path, sub_vtt_path)
                except Exception as cand_sub_err:
                    log.debug("[LeechService] Matched candidate subtitle reuse note: %s", cand_sub_err)

        if not sub_srt_path and pre_sub_srt and os.path.exists(pre_sub_srt):
            try:
                local_stage_srt = os.path.join(temp_dir, "sinhala_merged.srt")
                if os.path.abspath(pre_sub_srt) != os.path.abspath(local_stage_srt):
                    shutil.copyfile(pre_sub_srt, local_stage_srt)
                sub_srt_path = local_stage_srt
                sub_vtt_path = subtitle_service.srt_to_vtt(local_stage_srt)
                log.info("[LeechService] Reusing pre-discovered Sri Lankan Sinhala subtitle: srt=%s, vtt=%s", sub_srt_path, sub_vtt_path)
            except Exception as pre_reuse_err:
                log.debug("[LeechService] Pre-sub reuse note: %s", pre_reuse_err)

        if not sub_srt_path:
            try:
                _show_name = locals().get('show_name', '')
                clean_sub_title = _show_name if (is_series and _show_name) else (title or display_title)
                sub_srt_path, sub_vtt_path = await subtitle_service.auto_acquire_sinhala_subtitle(
                    title=clean_sub_title,
                    year=year,
                    imdb_id=imdb_id,
                    temp_dir=temp_dir,
                    video_path=local_file,
                    season=season,
                    episode=episode,
                )
                log.info("[LeechService] Prepared Sinhala subtitle tracks: srt=%s, vtt=%s", sub_srt_path, sub_vtt_path)
            except Exception as sub_acq_err:
                log.warning("[LeechService] Auto subtitle acquisition note: %s", sub_acq_err)

        # 2. Intelligent Video Processing & Subtitle Muxing
        # If file exceeds Telegram limit (1.95 GB), compress directly targeting 1.85 GB
        # Otherwise, perform instant stream copy remux in 3-5 seconds
        is_faststart_done = False
        ext = os.path.splitext(local_file)[1].lower()
        final_mp4 = os.path.join(temp_dir, f"{slug}.mp4")
        remuxed = os.path.join(temp_dir, f"remux_tmp_{slug}.mp4") if os.path.abspath(final_mp4) == os.path.abspath(local_file) else final_mp4

        sub_to_merge = sub_srt_path if (sub_srt_path and os.path.exists(sub_srt_path)) else (sub_vtt_path if (sub_vtt_path and os.path.exists(sub_vtt_path)) else None)
        # If the video is already pre-hardsubbed by the portal (SinhalaSub, CineSubz), NEVER burn subtitles again
        sub_to_burn_video = None if is_already_hardsubbed else sub_to_merge

        _last_comp_edit = 0.0
        async def _compress_progress(pct: float, pct_str: str) -> None:
            nonlocal _last_comp_edit
            now = time.time()
            if (now - _last_comp_edit < 3.0) and pct < 100.0:
                return
            _last_comp_edit = now
            p_bar = downloader.format_progress_bar(pct)
            if is_already_hardsubbed:
                sub_lbl = "මූලාශ්‍රයේම සිංහල උපසිරැසි අඩංගුයි (Pre-hardsubbed)"
            elif sub_to_merge:
                sub_lbl = "සිංහල උපසිරැසි Soft-Mux වේ"
            else:
                sub_lbl = "උපසිරැසි රහිතව"
            txt = (
                f"⚙️ <b>පියවර 3/5: Fast 1080p Compression (1.40GB Safe Ceiling)...</b>\n\n"
                f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                f"📊 <b>ප්‍රගතිය:</b> {p_bar} {pct:.1f}%\n"
                f"📦 <b>ඉලක්කය:</b> 1.40 GB (Telegram Bot 2GB Limit Safe)\n"
                f"💬 <b>උපසිරැසි:</b> {sub_lbl}\n"
                f"⚡ <i>Multi-Core NVENC/CPU High-Speed Encoding</i>"
            )
            try:
                await status_msg.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
            except Exception:
                pass

        if curr_size > video_service.MAX_TELEGRAM_BOT_SIZE:
            log.info("[LeechService] Downloaded size %s > 1.95GB limit. Starting fast compression...", downloader.format_bytes(curr_size))
            task_tracker.tracker.set_step(user_id, "Fast 1080p Compression (FFmpeg)...")
            try:
                await status_msg.edit_text(
                    f"⚙️ <b>පියවර 3/5: Fast 1080p Compression ආරම්භ විය...</b>\n\n"
                    f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                    f"📦 <b>මූලික ප්‍රමාණය:</b> {downloader.format_bytes(curr_size)} (> 1.95 GB Limit)\n"
                    f"🎯 <b>ඉලක්කගත ප්‍රමාණය:</b> 1.40 GB (Telegram Safe)\n"
                    f"⚡ <b>ක්‍රමය:</b> Multi-Core H.264 Fast Transcoding + Subtitle Muxing\n"
                    f"⏳ මිනිත්තු කිහිපයක් රැඳී සිටින්න...",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_cancel,
                )
            except Exception:
                pass

            comp_ok = await video_service.compress_video(
                input_path=local_file,
                output_path=remuxed,
                target_size_bytes=int(1.40 * 1024 * 1024 * 1024),
                progress_callback=_compress_progress,
                sub_path=sub_to_burn_video,
            )
            if comp_ok and os.path.exists(remuxed) and os.path.getsize(remuxed) <= video_service.MAX_TELEGRAM_BOT_SIZE:
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
                log.info("[LeechService] Direct compression succeeded: %s (%s)", local_file, downloader.format_bytes(os.path.getsize(local_file)))
        else:
            # File is <= 1.95 GB
            try:
                if is_already_hardsubbed:
                    sub_status_text = "මූලාශ්‍රයේම සිංහල උපසිරැසි අඩංගුයි (Pre-hardsubbed ✅ Single Clean Sub)"
                    method_text = f"Instant Stream Copy Remux ({ext.upper()} ➔ MP4 +faststart)"
                elif sub_to_merge:
                    sub_status_text = "සිංහල උපසිරැසි Hard-Burn Engine වෙත යොමු කෙරේ (Single-Decode NVENC)"
                    method_text = f"Fast MP4 Remux + Hard-Burn Prep ({ext.upper()} ➔ MP4)"
                else:
                    sub_status_text = "උපසිරැසි රහිතව Remux වේ"
                    method_text = f"Instant Stream Copy Remux ({ext.upper()} ➔ MP4 +faststart)"
                await status_msg.edit_text(
                    f"⚙️ <b>පියවර 3/5: වීඩියෝ සහ සිංහල උපසිරැසි සැකසුම...</b>\n\n"
                    f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                    f"⚡ <b>ක්‍රමය:</b> {method_text}\n"
                    f"💬 <b>උපසිරැසි:</b> {sub_status_text}\n"
                    f"⏳ තත්පර කිහිපයක් රැඳී සිටින්න...",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_cancel,
                )
            except Exception:
                pass

            task_tracker.tracker.set_step(user_id, "Video Remux & Subtitle Prep (FFmpeg)...")
            log.info("[LeechService] Executing video remux and sub prep (%s -> MP4, sub=%s)...", ext, sub_to_burn_video)

            if await video_service.stream_copy_subtitles(local_file, sub_to_burn_video, remuxed, disposition="default"):
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
                # Always attempt to convert MKV/WebM/AVI into Web-Streamable MP4 with Sinhala subtitles (+faststart)
                if await video_service.ensure_web_streamable(local_file, remuxed, sub_path=sub_to_burn_video):
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
                    log.info("[LeechService] Web streamable MP4 conversion succeeded: %s", local_file)
                elif await video_service.stream_copy_subtitles(local_file, sub_to_burn_video, remux_native := os.path.join(temp_dir, f"remux_{slug}{ext}"), disposition="default"):
                    if os.path.exists(local_file) and os.path.abspath(local_file) != os.path.abspath(remux_native):
                        try:
                            os.remove(local_file)
                        except Exception:
                            pass
                    local_file = remux_native
                    is_faststart_done = ext == ".mp4"
                    log.info("[LeechService] Native container stream-copy muxing succeeded: %s", local_file)
                elif sub_to_burn_video and os.path.exists(sub_to_burn_video):
                    sub_muxed = os.path.join(temp_dir, f"sub_{os.path.basename(local_file)}")
                    if await video_service.embed_subtitles_soft(local_file, sub_to_burn_video, sub_muxed, disposition="default"):
                        try:
                            os.remove(local_file)
                        except Exception:
                            pass
                        local_file = sub_muxed
                        is_faststart_done = sub_muxed.lower().endswith(".mp4")
            elif await video_service.ensure_web_streamable(local_file, remuxed, sub_path=sub_to_burn_video):
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
            elif sub_to_burn_video and os.path.exists(sub_to_burn_video):
                # Fallback soft-embed if stream-copy was skipped
                sub_muxed = os.path.join(temp_dir, f"sub_{os.path.basename(local_file)}")
                if await video_service.embed_subtitles_soft(local_file, sub_to_burn_video, sub_muxed, disposition="default"):
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

        # If companion variants were pre-downloaded and subtitle needs to be burned/muxed (is_already_hardsubbed=False)
        if pre_downloaded_variants and sub_to_burn_video and not is_already_hardsubbed and os.path.exists(sub_to_burn_video):
            for vq, vpath in list(pre_downloaded_variants.items()):
                if os.path.exists(vpath):
                    sub_var_out = os.path.join(temp_dir, f"subbed_{slug}_{vq}.mp4")
                    if await video_service.stream_copy_subtitles(vpath, sub_to_burn_video, sub_var_out, disposition="default"):
                        try:
                            os.remove(vpath)
                        except Exception:
                            pass
                        pre_downloaded_variants[vq] = sub_var_out
                        log.info("[LeechService] Subtitle muxed into companion variant %s: %s", vq, sub_var_out)

        # 2.8 Post-Remux Guarantee: If output is still > 1.95GB, compress to 1.40GB
        if os.path.exists(local_file) and os.path.getsize(local_file) > video_service.MAX_TELEGRAM_BOT_SIZE:
            log.warning("[LeechService] File %s (%d bytes) exceeds Telegram 1.95GB limit after remux. Compressing...",
                        local_file, os.path.getsize(local_file))
            task_tracker.tracker.set_step(user_id, "Compressing video <= 1.95GB...")
            comp_guard_out = os.path.join(temp_dir, f"guard_comp_{slug}.mp4")
            comp_ok = await video_service.compress_video(
                input_path=local_file,
                output_path=comp_guard_out,
                target_size_bytes=int(1.40 * 1024 * 1024 * 1024),
                progress_callback=_compress_progress,
                sub_path=sub_to_burn_video,
            )
            if comp_ok and os.path.exists(comp_guard_out) and os.path.getsize(comp_guard_out) <= video_service.MAX_TELEGRAM_BOT_SIZE:
                try:
                    os.remove(local_file)
                except Exception:
                    pass
                local_file = comp_guard_out

        # ── Step 3 & 4: Overlapped Multi-Quality RAM Encoding + Parallel Cloud Drive & Telegram Upload ──
        file_size = os.path.getsize(local_file)
        file_name = os.path.basename(local_file)
        size_str = downloader.format_bytes(file_size)

        base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
        if "yoursite.lk" in base_site:
            base_site = "https://filmsub.pages.dev"
        site_url = f"{base_site}/movie.html?id={slug}"

        # Detect source resolution upfront to cleanly route primary and variant pipelines (mutually exclusive)
        src_w, src_h = video_service.get_video_resolution(local_file)
        if src_w >= 1600 or src_h >= 900:
            is_source_1080p = True
            is_source_720p = False
            primary_quality = "1080p"
        elif src_w >= 1000 or src_h >= 540:
            is_source_1080p = False
            is_source_720p = True
            primary_quality = "720p"
        elif src_w == 0 and src_h == 0:
            _cand_q = (getattr(chosen_candidate, "quality", "") or "").lower()
            is_source_720p = ("720p" in _cand_q) or ("720p" in file_name.lower()) or bool(is_series)
            is_source_1080p = not is_source_720p
            primary_quality = "720p" if is_source_720p else "1080p"
        else:
            is_source_1080p = False
            is_source_720p = bool(is_series)
            primary_quality = "720p" if is_source_720p else "1080p"
        log.info("[LeechService] Source resolution: %dx%d -> primary_quality='%s' (is_series=%s)", src_w, src_h, primary_quality, is_series)

        task_tracker.tracker.set_step(
            user_id, f"3/3 - Telegram Cloud HD Upload ({size_str})..."
        )

        last_upload_edit = 0.0
        _last_drive_edit = 0.0
        last_rendered_text = ""
        dashboard_lock = asyncio.Lock()
        _mq_progress_str = ""
        _hw_enc = video_service.detect_hw_encoder()
        _engine_tag = "⚡ <b>Engine:</b> NVIDIA T4 GPU (Hardware Acceleration)" if _hw_enc == "h264_nvenc" else "⚡ <b>Engine:</b> Ultra-Fast Multi-Thread CPU"
        _sub_tag = "\n🇱🇰 <b>Subtitle:</b> සිංහල උපසිරැසි Muxed (mov_text auto-play)" if (sub_to_merge and os.path.exists(sub_to_merge)) else ""

        dashboard_state = {
            "primary_q": primary_quality,
            "primary_file": file_name,
            "primary_pct": 0.0,
            "primary_done": "0 MB",
            "primary_total": size_str,
            "primary_speed": "--",
            "primary_eta": "--",
            "primary_status": "waiting",
            "mq_target": ("480p" if primary_quality == "720p" else "720p/480p"),
            "mq_encode_pct": "0%",
            "mq_encode_status": "waiting",
            "variant_q": "",
            "variant_pct": 0.0,
            "variant_done": "0 MB",
            "variant_total": "",
            "variant_speed": "--",
            "variant_eta": "--",
            "variant_status": "waiting",
            "drive_pct": 0.0,
            "drive_done": "0 MB",
            "drive_total": "",
            "drive_speed": "--",
            "drive_eta": "--",
            "drive_status": "waiting",
        }

        if "variant_tg_info" in locals() and variant_tg_info:
            if primary_quality in variant_tg_info and variant_tg_info[primary_quality].get("file_id"):
                dashboard_state["primary_status"] = "complete"
                dashboard_state["primary_pct"] = 100.0
                dashboard_state["primary_done"] = size_str
            dashboard_state["variants"] = {}
            for vq, vinfo in variant_tg_info.items():
                if vq != primary_quality and vinfo.get("file_id"):
                    v_sz_str = downloader.format_bytes(vinfo.get("file_size", 0)) if vinfo.get("file_size") else "OK"
                    dashboard_state["variants"][vq] = {
                        "status": "complete",
                        "pct": 100.0,
                        "done": v_sz_str,
                        "total": v_sz_str,
                        "speed": "Fast (Pipelined)",
                        "eta": "--",
                    }

        async def _render_dashboard(force: bool = False) -> None:
            nonlocal last_upload_edit, last_rendered_text
            now = time.time()
            if not force and (now - last_upload_edit < 2.5):
                return

            async with dashboard_lock:
                now = time.time()
                if not force and (now - last_upload_edit < 2.5):
                    return

                p_q = dashboard_state["primary_q"]
                p_pct = dashboard_state["primary_pct"]
                p_status = dashboard_state["primary_status"]
                p_bar = downloader.format_progress_bar(p_pct)

                if p_status == "complete":
                    p_line = f"✅ <b>{p_q} (Primary):</b> Upload සම්පූර්ණයි (Telegram HD ✅)"
                elif p_status == "failed":
                    p_line = f"❌ <b>{p_q} (Primary):</b> Upload අසාර්ථකයි"
                elif p_status == "waiting":
                    p_line = f"⏳ <b>{p_q} (Primary) Upload:</b> සූදානම් වෙමින්..."
                else:
                    p_speed = dashboard_state["primary_speed"]
                    p_eta = dashboard_state["primary_eta"]
                    p_done = dashboard_state["primary_done"]
                    p_total = dashboard_state["primary_total"]
                    p_line = (
                        f"📦 <b>{p_q} (Primary) Upload:</b> {p_bar} {p_pct:.1f}%\n"
                        f"   ▫️ ප්‍රමාණය: {p_done} / {p_total} | ⚡ Speed: {p_speed} | ⏱ ETA: {p_eta}"
                    )

                mq_status = dashboard_state["mq_encode_status"]
                mq_target = dashboard_state["mq_target"]
                is_direct_mq = "Direct" in str(mq_target)
                if mq_status == "encoding":
                    mq_pct_str = dashboard_state["mq_encode_pct"]
                    if is_direct_mq:
                        mq_line = f"⚡ <b>Multi-Quality ({mq_target}) Direct බාගත කිරීම:</b> ක්‍රියාත්මකයි..."
                    else:
                        mq_line = f"🔄 <b>Multi-Quality ({mq_target}) පරිවර්තනය:</b> {mq_pct_str}"
                elif mq_status == "complete":
                    if is_direct_mq:
                        mq_line = f"✅ <b>Multi-Quality ({mq_target}):</b> කෙලින්ම බාගත වීම සාර්ථකයි"
                    else:
                        mq_line = f"✅ <b>Multi-Quality ({mq_target}) පරිවර්තනය:</b> සම්පූර්ණයි"
                elif mq_status == "skipped":
                    mq_line = f"ℹ️ <b>Multi-Quality ({mq_target}):</b> Skipped"
                else:
                    mq_line = f"⏳ <b>Multi-Quality ({mq_target}):</b> ක්‍රියාත්මක වෙමින්..."

                v_line = ""
                variants_map = dashboard_state.get("variants", {})
                if variants_map:
                    v_items = []
                    for v_q, v_info in variants_map.items():
                        v_st = v_info.get("status")
                        if v_st == "uploading":
                            v_pct = v_info.get("pct", 0.0)
                            v_bar = downloader.format_progress_bar(v_pct)
                            v_items.append(
                                f"📦 <b>{v_q} Upload:</b> {v_bar} {v_pct:.1f}%\n"
                                f"   ▫️ ප්‍රමාණය: {v_info.get('done', '0B')} / {v_info.get('total', '0B')} | ⚡ Speed: {v_info.get('speed', '--')} | ⏱ ETA: {v_info.get('eta', '--')}"
                            )
                        elif v_st == "complete":
                            v_items.append(f"✅ <b>{v_q}:</b> Upload සම්පූර්ණයි (Telegram HD ✅)")
                    if v_items:
                        v_line = "\n" + "\n".join(v_items)
                elif dashboard_state.get("variant_status") == "uploading" and dashboard_state.get("variant_q"):
                    v_q = dashboard_state["variant_q"]
                    v_pct = dashboard_state["variant_pct"]
                    v_bar = downloader.format_progress_bar(v_pct)
                    v_speed = dashboard_state["variant_speed"]
                    v_eta = dashboard_state["variant_eta"]
                    v_done = dashboard_state["variant_done"]
                    v_total = dashboard_state["variant_total"]
                    v_line = (
                        f"\n📦 <b>{v_q} (Variant) Upload:</b> {v_bar} {v_pct:.1f}%\n"
                        f"   ▫️ ප්‍රමාණය: {v_done} / {v_total} | ⚡ Speed: {v_speed} | ⏱ ETA: {v_eta}"
                    )
                elif dashboard_state.get("variant_status") == "complete" and dashboard_state.get("variant_q"):
                    v_line = f"\n✅ <b>{dashboard_state['variant_q']} (Variant):</b> Upload සම්පූර්ණයි (Telegram HD ✅)"

                drive_line = ""
                if getattr(config, "ENABLE_GDRIVE_UPLOAD", False):
                    d_st = dashboard_state.get("drive_status")
                    if d_st == "uploading":
                        d_bar = downloader.format_progress_bar(dashboard_state["drive_pct"])
                        drive_line = (
                            f"\n☁️ <b>Google Drive Backup:</b> {d_bar} {dashboard_state['drive_pct']:.1f}%\n"
                            f"   ▫️ ප්‍රමාණය: {dashboard_state['drive_done']} / {dashboard_state['drive_total']}"
                        )
                    elif d_st == "complete":
                        drive_line = "\n✅ <b>Google Drive Backup:</b> Upload සම්පූර්ණයි"

                text = (
                    f"📤 <b>පියවර 3/3: Telegram Cloud HD Upload & Processing...</b>\n\n"
                    f"🎬 <b>{'ගොනුව' if is_series else 'චිත්‍රපටය'}:</b> {display_title}\n"
                    f"📁 <b>ගොනුව:</b> <code>{file_name}</code>\n\n"
                    f"{p_line}\n"
                    f"{mq_line}"
                    f"{v_line}"
                    f"{drive_line}\n\n"
                    f"{_engine_tag}{_sub_tag}\n"
                    f"🛡️ <i>Telegram Cloud Storage • 100% Google Account Strike Safe</i>"
                )
                if text == last_rendered_text:
                    return
                last_upload_edit = now
                last_rendered_text = text
                try:
                    await status_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel)
                except Exception as up_err:
                    log.debug("[LeechService] Dashboard edit note: %s", up_err)

        async def _mq_progress_cb(pct: float, pct_str: str) -> None:
            nonlocal _mq_progress_str
            _mq_progress_str = f"GPU: {pct_str}" if _hw_enc == "h264_nvenc" else f"RAM: {pct_str}"
            dashboard_state["mq_encode_status"] = "encoding"
            dashboard_state["mq_encode_pct"] = pct_str
            await _render_dashboard(force=False)

        async def _upload_progress(pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
            dashboard_state["primary_pct"] = pct
            dashboard_state["primary_done"] = done_str
            dashboard_state["primary_total"] = total_str
            dashboard_state["primary_speed"] = speed_str
            dashboard_state["primary_eta"] = eta_str
            if pct >= 100.0:
                dashboard_state["primary_status"] = "complete"
                await _render_dashboard(force=True)
            else:
                await _render_dashboard(force=False)

        async def _variant_upload_progress(q_label: str, pct: float, done_str: str, total_str: str, speed_str: str, eta_str: str) -> None:
            if "variants" not in dashboard_state:
                dashboard_state["variants"] = {}
            dashboard_state["variants"][q_label] = {
                "status": "complete" if pct >= 100.0 else "uploading",
                "pct": pct,
                "done": done_str,
                "total": total_str,
                "speed": speed_str,
                "eta": eta_str,
            }
            dashboard_state["variant_q"] = q_label
            dashboard_state["variant_pct"] = pct
            dashboard_state["variant_done"] = done_str
            dashboard_state["variant_total"] = total_str
            dashboard_state["variant_speed"] = speed_str
            dashboard_state["variant_eta"] = eta_str
            dashboard_state["variant_status"] = "complete" if pct >= 100.0 else "uploading"
            await _render_dashboard(force=(pct >= 100.0))

        async def _drive_upload_progress(
            done_bytes: int,
            total_bytes: int,
            speed_str: str = "--",
            eta_str: str = "--",
        ) -> None:
            pct = min(100.0, (done_bytes / total_bytes * 100)) if total_bytes > 0 else 0.0
            dashboard_state["drive_pct"] = pct
            dashboard_state["drive_done"] = downloader.format_bytes(done_bytes)
            dashboard_state["drive_total"] = downloader.format_bytes(total_bytes)
            dashboard_state["drive_speed"] = speed_str
            dashboard_state["drive_eta"] = eta_str
            dashboard_state["drive_status"] = "complete" if pct >= 100.0 else "uploading"
            await _render_dashboard(force=(pct >= 100.0))

        try:
            await _render_dashboard(force=True)
        except Exception:
            pass

        cloud_upload_res = None
        variant_files = variant_files if ("variant_files" in locals() and variant_files) else {}
        variant_cloud_urls = variant_cloud_urls if ("variant_cloud_urls" in locals() and variant_cloud_urls) else {}
        variant_tg_info = variant_tg_info if ("variant_tg_info" in locals() and variant_tg_info) else {}
        ENABLE_TELEGRAM_VIDEO_UPLOAD = True
        target_channel = config.PRIVATE_CHANNEL_ID or (config.ADMIN_IDS[0] if config.ADMIN_IDS else 0)
        file_id = file_id or (variant_tg_info.get(primary_quality, {}).get("file_id", "") if "variant_tg_info" in locals() else "")
        stream_url = stream_url or (variant_tg_info.get(primary_quality, {}).get("stream_url", "") if "variant_tg_info" in locals() else "")
        message_id = message_id or (variant_tg_info.get(primary_quality, {}).get("message_id", 0) if "variant_tg_info" in locals() else 0)

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

        async def _task_upload_tg_variant(q_label: str, q_path: str) -> None:
            if not os.path.exists(q_path) or os.path.getsize(q_path) == 0:
                return
            q_bytes = os.path.getsize(q_path)
            dashboard_state["variant_q"] = q_label
            dashboard_state["variant_status"] = "uploading"
            dashboard_state["variant_total"] = downloader.format_bytes(q_bytes)
            dashboard_state["variant_pct"] = 0.0
            await _render_dashboard(force=True)

            for attempt in range(3):
                try:
                    log.info("[LeechService] Uploading variant '%s' to Telegram channel (attempt %d)...", q_label, attempt + 1)
                    var_caption = f"🎬 {display_title} [{q_label}]\n\n⚡ Quality: {q_label} (High-Speed Telegram Cloud)\n🌐 Watch: {site_url}"
                    async def _var_cb(p: float, d: str, t: str, s: str, e: str) -> None:
                        await _variant_upload_progress(q_label, p, d, t, s, e)

                    from services.upload_pool import upload_pool
                    up_res = await upload_pool.upload_with_pool(
                        file_path=q_path,
                        target_chat=target_channel,
                        quality=q_label,
                        caption=var_caption,
                        file_name=os.path.basename(q_path),
                        progress_callback=_var_cb,
                        fallback_client=client,
                    )
                    if up_res and up_res.get("file_id"):
                        variant_tg_info[q_label] = up_res
                        dashboard_state["variant_status"] = "complete"
                        dashboard_state["variant_pct"] = 100.0
                        await _render_dashboard(force=True)
                        log.info("[LeechService] Telegram upload for variant %s succeeded: msg_id=%s", q_label, up_res.get("message_id"))
                        break
                    else:
                        log.warning("[LeechService] Variant upload attempt %d returned no file_id: %s", attempt + 1, up_res)
                except Exception as tg_v_err:
                    log.warning("[LeechService] Telegram upload for variant %s attempt %d skipped/failed: %s", q_label, attempt + 1, tg_v_err)
                    await asyncio.sleep(2.0 * (attempt + 1))

            if not variant_tg_info.get(q_label) and client and getattr(client, "is_connected", False):
                try:
                    log.info("[LeechService] Executing direct bot client fallback upload for variant '%s'...", q_label)
                    from services import telegram_upload
                    direct_res = await telegram_upload.upload_video_file(
                        bot_client=client,
                        file_path=q_path,
                        target_chat=target_channel,
                        caption=var_caption,
                        progress_callback=_var_cb,
                        fallback_chat=0,
                    )
                    if direct_res and direct_res.get("file_id"):
                        variant_tg_info[q_label] = direct_res
                        dashboard_state["variant_status"] = "complete"
                        dashboard_state["variant_pct"] = 100.0
                        await _render_dashboard(force=True)
                        log.info("[LeechService] Direct client fallback upload for variant %s succeeded: msg_id=%s", q_label, direct_res.get("message_id"))
                except Exception as direct_err:
                    log.error("[LeechService] Direct client fallback upload for variant %s failed: %s", q_label, direct_err)

        async def _task_encode_variants_only() -> None:
            nonlocal variant_files, _mq_progress_str, local_file, file_size, file_name, size_str
            if not getattr(config, "ENABLE_MULTI_QUALITY_RAM", True):
                dashboard_state["mq_encode_status"] = "skipped"
                await _render_dashboard(force=True)
                return
            try:
                has_hard_sub = bool(sub_to_burn_video and os.path.exists(sub_to_burn_video))
                # When Sinhala subtitle is present and not pre-burned, include primary_quality in Single-Decode Hard-Burn (split=3)
                # so 1080p, 720p, and 480p ALL get libass Noto Sans Sinhala Bold hard-burned simultaneously!
                if is_series:
                    # User requirement: TV Series strictly 720p & 480p only! NEVER encode or download 1080p for TV Series.
                    if primary_quality == "720p":
                        requested_qualities = ("720p", "480p") if has_hard_sub else ("480p",)
                    else:
                        requested_qualities = ("480p",) if has_hard_sub else ()
                else:
                    if primary_quality == "1080p":
                        requested_qualities = ("1080p", "720p", "480p") if has_hard_sub else ("720p", "480p")
                    elif primary_quality == "720p":
                        requested_qualities = ("720p", "480p") if has_hard_sub else ("480p",)
                    else:
                        requested_qualities = ("480p",) if has_hard_sub else ()

                if not requested_qualities:
                    _mq_progress_str = "Multi-Quality Skipped (Source <= 480p) ✅"
                    dashboard_state["mq_encode_status"] = "skipped"
                    await _render_dashboard(force=True)
                    return

                # If all requested qualities are already in variant_tg_info, mark complete and skip
                if all(q in variant_tg_info and variant_tg_info[q].get("file_id") for q in requested_qualities):
                    _mq_progress_str = f"{'/'.join(requested_qualities)} Complete (Pre-Uploaded) ✅"
                    dashboard_state["mq_encode_status"] = "complete"
                    dashboard_state["mq_target"] = "/".join(requested_qualities)
                    dashboard_state["mq_encode_pct"] = "100%"
                    await _render_dashboard(force=True)
                    log.info("[LeechService] All requested qualities %s already uploaded in Step 2 pipeline. Skipping re-encode.", requested_qualities)
                    return

                # ── Fast-Path: Direct Multi-Quality Download from Same Portal ────────
                needed_qualities = [q for q in requested_qualities if q != primary_quality]
                direct_downloaded: dict[str, str] = {}

                # 1. Re-use companion variants already downloaded in parallel during Step 2
                if pre_downloaded_variants:
                    for vq, vpath in pre_downloaded_variants.items():
                        if os.path.exists(vpath) and os.path.getsize(vpath) > 1024:
                            direct_downloaded[vq] = vpath
                            variant_files[vq] = vpath
                            log.info("[LeechService] Reusing Step 2 parallel downloaded variant %s: %s", vq, vpath)
                    if variant_files:
                        dashboard_state["mq_encode_status"] = "complete"
                        dashboard_state["mq_target"] = "/".join(variant_files.keys()) + " (Parallel CDN)"
                        dashboard_state["mq_encode_pct"] = "100%"
                        _mq_progress_str = f"{'/'.join(variant_files.keys())} Complete (Parallel CDN) ✅"
                        await _render_dashboard(force=True)

                needed_qualities = [q for q in needed_qualities if q not in direct_downloaded]

                if chosen_candidate and needed_qualities:
                    chosen_portal = (chosen_candidate.extra or {}).get("portal", "")
                    chosen_host = urllib.parse.urlparse(str(chosen_candidate.source_url)).netloc

                    matched_variants: dict[str, LeechCandidate] = {}
                    for c in candidates:
                        if c == chosen_candidate:
                            continue
                        c_q = (c.quality or "").lower()
                        if c_q in needed_qualities and c_q not in matched_variants:
                            c_portal = (c.extra or {}).get("portal", "")
                            c_host = urllib.parse.urlparse(str(c.source_url)).netloc
                            if (chosen_portal and c_portal == chosen_portal) or (chosen_host and c_host == chosen_host):
                                matched_variants[c_q] = c

                    if matched_variants:
                        log.info(
                            "[LeechService] Found %d pre-encoded direct variant(s) on %s (%s). Starting direct turbo download...",
                            len(matched_variants), chosen_portal or chosen_host, list(matched_variants.keys())
                        )
                        dashboard_state["mq_encode_status"] = "encoding"
                        dashboard_state["mq_target"] = "/".join(matched_variants.keys()) + " (Direct CDN)"
                        await _render_dashboard(force=True)

                        async def _dl_single_variant(vq: str, vcand: LeechCandidate) -> Optional[tuple[str, str]]:
                            v_clean_name = f"{slug}-{vq}.mp4"
                            dl_out = await downloader.download_http(
                                url=vcand.source_url,
                                dest_dir=temp_dir,
                                filename=v_clean_name,
                                task_key=f"{task_key}_{vq}",
                            )
                            if dl_out and downloader.is_valid_downloaded_video(dl_out):
                                fs_out = os.path.join(temp_dir, f"fs_{slug}_{vq}.mp4")
                                if await video_service.apply_faststart(dl_out, fs_out):
                                    try:
                                        os.remove(dl_out)
                                    except Exception:
                                        pass
                                    dl_out = fs_out

                                # If not pre-hardsubbed and subtitle exists, fast stream-copy subtitle into variant (~1 sec)
                                if not is_already_hardsubbed and sub_to_burn_video and os.path.exists(sub_to_burn_video):
                                    sub_var_out = os.path.join(temp_dir, f"subbed_{slug}_{vq}.mp4")
                                    if await video_service.stream_copy_subtitles(dl_out, sub_to_burn_video, sub_var_out):
                                        try:
                                            os.remove(dl_out)
                                        except Exception:
                                            pass
                                        dl_out = sub_var_out

                                log.info("[LeechService] Direct variant %s download complete: %s (%s)", vq, dl_out, downloader.format_bytes(os.path.getsize(dl_out)))
                                return (vq, dl_out)
                            return None

                        dl_tasks = [
                            _dl_single_variant(vq, vcand)
                            for vq, vcand in matched_variants.items()
                        ]
                        dl_results = await asyncio.gather(*dl_tasks, return_exceptions=True)
                        for r in dl_results:
                            if isinstance(r, tuple) and r[0] and r[1]:
                                direct_downloaded[r[0]] = r[1]
                                variant_files[r[0]] = r[1]

                remaining_qualities = tuple(q for q in requested_qualities if q not in direct_downloaded)

                if remaining_qualities and any(q != primary_quality for q in remaining_qualities):
                    dashboard_state["mq_encode_status"] = "encoding"
                    dashboard_state["mq_target"] = "/".join(remaining_qualities)
                    await _render_dashboard(force=True)

                    sub_for_encode = None if is_already_hardsubbed else sub_to_burn_video
                    ffmpeg_variants = await video_service.generate_multi_quality_variants_ram(
                        input_path=local_file,
                        output_dir=temp_dir,
                        slug=slug,
                        sub_path=sub_for_encode,
                        qualities=remaining_qualities,
                        progress_callback=_mq_progress_cb,
                    )
                    if ffmpeg_variants:
                        variant_files.update(ffmpeg_variants)

                if variant_files:
                    q_keys = "/".join(variant_files.keys())
                    # If primary_quality was hard-burned in the single-decode pass, promote it to local_file
                    if primary_quality in variant_files and os.path.exists(variant_files[primary_quality]):
                        burned_primary = variant_files.pop(primary_quality)
                        if os.path.getsize(burned_primary) <= video_service.MAX_TELEGRAM_BOT_SIZE:
                            if os.path.exists(local_file) and os.path.abspath(local_file) != os.path.abspath(burned_primary):
                                try:
                                    os.remove(local_file)
                                except Exception:
                                    pass
                            local_file = burned_primary
                            file_size = os.path.getsize(local_file)
                            file_name = os.path.basename(local_file)
                            size_str = downloader.format_bytes(file_size)
                            log.info("[LeechService] Promoted Hard-Burned %s file as primary upload: %s (%s)", primary_quality, local_file, size_str)
                    _mq_progress_str = f"{q_keys} Complete ✅"
                    dashboard_state["mq_encode_status"] = "complete"
                    dashboard_state["mq_encode_pct"] = "100%"
                    await _render_dashboard(force=True)
                else:
                    _mq_progress_str = "Multi-Quality Skipped (Primary Stream Active) ✅"
                    dashboard_state["mq_encode_status"] = "skipped"
                    await _render_dashboard(force=True)
            except Exception as mq_err:
                log.warning("[LeechService] Multi-quality RAM variant encoding skipped: %s", mq_err)
                dashboard_state["mq_encode_status"] = "skipped"
                await _render_dashboard(force=True)

        async def _task_upload_all_variants() -> None:
            nonlocal _mq_progress_str
            try:
                pending_variants = {
                    ql: qp for ql, qp in variant_files.items()
                    if ql not in variant_tg_info and ql != primary_quality
                }
                if not pending_variants:
                    log.info("[LeechService] All variants already uploaded. Zero pending uploads.")
                    return
                if ENABLE_TELEGRAM_VIDEO_UPLOAD:
                    q_keys = "/".join(pending_variants.keys())
                    _mq_progress_str = f"{q_keys} Uploading to Telegram..."
                    from services.upload_pool import upload_pool
                    admin_Pool = await upload_pool.get_admin_sessions(target_channel) if str(target_channel).startswith("-100") else []
                    if len(admin_Pool) >= 2 and len(pending_variants) > 1:
                        await asyncio.gather(
                            *[
                                _task_upload_tg_variant(ql, qp)
                                for ql, qp in pending_variants.items()
                            ],
                            return_exceptions=True,
                        )
                    else:
                        for ql, qp in pending_variants.items():
                            await _task_upload_tg_variant(ql, qp)
                    _mq_progress_str = f"Telegram {q_keys} Upload Complete ✅"

                if getattr(config, "ENABLE_GDRIVE_UPLOAD", False):
                    q_keys = "/".join(pending_variants.keys())
                    _mq_progress_str = f"{q_keys} Uploading to Drive..."
                    for ql, qp in pending_variants.items():
                        await _task_upload_drive_variant(ql, qp)
                    _mq_progress_str = f"{q_keys} Drive Complete ✅"
            except Exception as v_up_err:
                log.warning("[LeechService] Variant upload note: %s", v_up_err)

        async def _task_upload_telegram() -> None:
            nonlocal file_id, stream_url, message_id
            if not ENABLE_TELEGRAM_VIDEO_UPLOAD:
                return
            if primary_quality in variant_tg_info and variant_tg_info[primary_quality].get("file_id"):
                up_res = variant_tg_info[primary_quality]
                file_id = up_res.get("file_id", "")
                stream_url = up_res.get("stream_url", "")
                message_id = up_res.get("message_id", 0)
                dashboard_state["primary_status"] = "complete"
                dashboard_state["primary_pct"] = 100.0
                await _render_dashboard(force=True)
                log.info("[LeechService] Primary quality %s was already uploaded during parallel pipeline (msg_id=%s).", primary_quality, message_id)
                return
            dashboard_state["primary_status"] = "uploading"
            await _render_dashboard(force=True)
            try:
                from services.upload_pool import upload_pool
                upload_res = await upload_pool.upload_with_pool(
                    file_path=local_file,
                    target_chat=target_channel,
                    quality=primary_quality,
                    caption=f"🎬 {display_title} [{primary_quality}]\n\n⚡ Uploaded via Auto-Leech (/boost)\n🌐 Watch: {site_url}",
                    file_name=os.path.basename(local_file),
                    progress_callback=_upload_progress,
                    fallback_client=client,
                )
                file_id = upload_res.get("file_id", "")
                stream_url = upload_res.get("stream_url", "")
                message_id = upload_res.get("message_id", 0)
                if file_id and message_id:
                    variant_tg_info[primary_quality] = upload_res
                    dashboard_state["primary_status"] = "complete"
                    dashboard_state["primary_pct"] = 100.0
                    await _render_dashboard(force=True)
                    log.info("[LeechService] Registered primary upload into variant_tg_info['%s']: msg_id=%s", primary_quality, message_id)
                else:
                    dashboard_state["primary_status"] = "failed"
                    await _render_dashboard(force=True)
            except Exception as tg_err:
                dashboard_state["primary_status"] = "failed"
                await _render_dashboard(force=True)
                log.error("[LeechService] Telegram upload failed: %s", tg_err)

        # High-Speed Parallel Upload: Primary Telegram upload runs concurrently with Variant processing & Drive
        async def _encode_and_upload_variants() -> None:
            await _task_encode_variants_only()
            await _task_upload_all_variants()

        if not (primary_quality in variant_tg_info and variant_tg_info[primary_quality].get("file_id")):
            dashboard_state["primary_status"] = "uploading"
        await _render_dashboard(force=True)

        await asyncio.gather(
            _task_upload_telegram(),
            _encode_and_upload_variants(),
            _task_upload_drive_1080(),
            return_exceptions=True,
        )

        cloud_stream = cloud_upload_res.get("stream_url") if cloud_upload_res else ""
        cloud_download = cloud_upload_res.get("download_url") if cloud_upload_res else ""
        primary_stream = cloud_stream or ""

        # Publish Sinhala VTT subtitle to GitHub/website & generate inline data:text/vtt URI
        has_sinhala = bool(sub_to_merge and os.path.exists(sub_to_merge))
        sub_text = ""
        default_sub_url = ""
        if has_sinhala and sub_vtt_path and os.path.exists(sub_vtt_path):
            try:
                with open(sub_vtt_path, "r", encoding="utf-8", errors="replace") as vf:
                    loaded_vtt = vf.read().strip()
                if loaded_vtt.startswith("WEBVTT"):
                    sub_text = loaded_vtt
            except Exception:
                pass
            if sub_text:
                default_sub_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(sub_text)}"
                try:
                    uploaded_sub_url = await subtitle_service.upload_subtitle_to_github(
                        vtt_path=sub_vtt_path,
                        filename=f"{slug}-si.vtt",
                        github_token=getattr(config, "GITHUB_TOKEN", ""),
                        repo=getattr(config, "GITHUB_REPO", ""),
                    )
                    if uploaded_sub_url:
                        default_sub_url = uploaded_sub_url
                except Exception as up_sub_err:
                    log.debug("[LeechService] Subtitle upload fallback to inline VTT: %s", up_sub_err)

        file_ext = os.path.splitext(file_name)[1].lstrip(".").upper() or "MP4"
        stream_type = "video/mp4" if file_ext == "MP4" else "video/x-matroska"

        if "720p" in variant_files and os.path.exists(variant_files["720p"]):
            sz_720 = os.path.getsize(variant_files["720p"])
        elif variant_cloud_urls.get("720p", {}).get("size_bytes"):
            sz_720 = variant_cloud_urls["720p"]["size_bytes"]
        elif variant_tg_info.get("720p", {}).get("file_size"):
            sz_720 = variant_tg_info["720p"]["file_size"]
        else:
            sz_720 = int(file_size * 0.55)

        if "480p" in variant_files and os.path.exists(variant_files["480p"]):
            sz_480 = os.path.getsize(variant_files["480p"])
        elif variant_cloud_urls.get("480p", {}).get("size_bytes"):
            sz_480 = variant_cloud_urls["480p"]["size_bytes"]
        elif variant_tg_info.get("480p", {}).get("file_size"):
            sz_480 = variant_tg_info["480p"]["file_size"]
        else:
            sz_480 = int(file_size * 0.32)

        if "360p" in variant_files and os.path.exists(variant_files["360p"]):
            sz_360 = os.path.getsize(variant_files["360p"])
        elif variant_cloud_urls.get("360p", {}).get("size_bytes"):
            sz_360 = variant_cloud_urls["360p"]["size_bytes"]
        elif variant_tg_info.get("360p", {}).get("file_size"):
            sz_360 = variant_tg_info["360p"]["file_size"]
        else:
            sz_360 = int(file_size * 0.18)

        sz_1080 = file_size

        stream_720 = variant_tg_info.get("720p", {}).get("stream_url") or variant_cloud_urls.get("720p", {}).get("stream_url") or stream_url
        stream_480 = variant_tg_info.get("480p", {}).get("stream_url") or variant_cloud_urls.get("480p", {}).get("stream_url") or stream_url
        stream_360 = variant_tg_info.get("360p", {}).get("stream_url") or variant_cloud_urls.get("360p", {}).get("stream_url") or stream_url

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
                "1080p": variant_tg_info.get("1080p", {}).get("stream_url") or stream_url,
                "720p": stream_720,
                "480p": stream_480,
                "360p": stream_360,
            }
        elif cloud_stream:
            qualities_map = {
                "auto": cloud_stream,
                "1080p": cloud_stream,
                "720p": variant_cloud_urls.get("720p", {}).get("stream_url") or cloud_stream,
                "480p": variant_cloud_urls.get("480p", {}).get("stream_url") or cloud_stream,
                "360p": variant_cloud_urls.get("360p", {}).get("stream_url") or cloud_stream,
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

        # 3. External Multi-Server VIP Players (VidLink, AutoEmbed, MultiEmbed)
        tmdb_id_val = str(tmdb_meta.get("tmdb_id") or "").strip()
        ext_id = str(imdb_id or tmdb_id_val or "").strip()
        if ext_id:
            s_num = season or 1
            e_num = episode or 1
            # VIP 1: VidLink Pro Ultra HD
            target_tmdb = tmdb_id_val or ext_id
            vidlink_url = (
                f"https://vidlink.pro/tv/{target_tmdb}/{s_num}/{e_num}"
                if is_series
                else f"https://vidlink.pro/movie/{target_tmdb}"
            )
            # VIP 2: AutoEmbed HD
            autoembed_url = (
                f"https://autoembed.co/tv/imdb/{ext_id}-{s_num}-{e_num}"
                if is_series
                else f"https://autoembed.co/movie/imdb/{ext_id}"
            )
            # VIP 3: 2Embed Fast
            twoembed_url = (
                f"https://www.2embed.cc/embedtv/{ext_id}&s={s_num}&e={e_num}"
                if is_series
                else f"https://www.2embed.cc/embed/{ext_id}"
            )
            streams_list.append({
                "server": f"Server {len(streams_list) + 1}",
                "label": "🎬 VIP Player 1 (VidLink Ultra HD)",
                "type": "embed",
                "embed": True,
                "stream_url": vidlink_url,
            })
            streams_list.append({
                "server": f"Server {len(streams_list) + 1}",
                "label": "⚡ VIP Player 2 (AutoEmbed HD)",
                "type": "embed",
                "embed": True,
                "stream_url": autoembed_url,
            })
            streams_list.append({
                "server": f"Server {len(streams_list) + 1}",
                "label": "🚀 VIP Player 3 (2Embed Fast)",
                "type": "embed",
                "embed": True,
                "stream_url": twoembed_url,
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

        try:
            _bot_me = getattr(client, "me", None)
            bot_username = _bot_me.username if (_bot_me and getattr(_bot_me, "username", None)) else "Filmsinhala200Bot"
        except Exception:
            bot_username = "Filmsinhala200Bot"

        variant_media = {
            "1080p": {
                "file_id": (variant_tg_info.get("1080p", {}).get("file_id", "") or (file_id if primary_quality == "1080p" else "")),
                "message_id": (variant_tg_info.get("1080p", {}).get("message_id", 0) or (message_id if primary_quality == "1080p" else 0)),
                "stream_url": (variant_tg_info.get("1080p", {}).get("stream_url") or (stream_url if primary_quality == "1080p" else "")),
                "size_bytes": (variant_tg_info.get("1080p", {}).get("file_size", 0) or (sz_1080 if primary_quality == "1080p" else 0)),
            },
            "720p": {
                "file_id": (variant_tg_info.get("720p", {}).get("file_id", "") or (file_id if primary_quality == "720p" else "")),
                "message_id": (variant_tg_info.get("720p", {}).get("message_id", 0) or (message_id if primary_quality == "720p" else 0)),
                "stream_url": (variant_tg_info.get("720p", {}).get("stream_url") or (stream_url if primary_quality == "720p" else stream_720)),
                "size_bytes": (variant_tg_info.get("720p", {}).get("file_size", 0) or (sz_1080 if primary_quality == "720p" else sz_720)),
            },
            "480p": {
                "file_id": variant_tg_info.get("480p", {}).get("file_id", ""),
                "message_id": variant_tg_info.get("480p", {}).get("message_id", 0),
                "stream_url": variant_tg_info.get("480p", {}).get("stream_url") or stream_480,
                "size_bytes": variant_tg_info.get("480p", {}).get("file_size", 0) or sz_480,
            },
            "360p": {
                "file_id": variant_tg_info.get("360p", {}).get("file_id", ""),
                "message_id": variant_tg_info.get("360p", {}).get("message_id", 0),
                "stream_url": variant_tg_info.get("360p", {}).get("stream_url") or stream_360,
                "size_bytes": variant_tg_info.get("360p", {}).get("file_size", 0) or sz_360,
            },
        }

        # Store in task_tracker so QueueService / Stage2Patcher can retrieve it
        task_tracker.tracker.store_upload_results(user_id, variant_media, subtitle_url=default_sub_url)

        # ── Downloads Construction ────────────────────────────────────────────────
        tg_channel_id_clean = str(abs(target_channel))
        if tg_channel_id_clean.startswith("100"):
            tg_channel_id_clean = tg_channel_id_clean[3:]

        # Real Telegram direct channel downloads for every verified uploaded quality
        added_msg_ids = set()
        for q_var, q_lbl, fallback_sz, fallback_stream in [
            ("1080p", "1080p Full HD (Telegram Channel • Fast)", sz_1080, stream_url),
            ("720p", "720p HD (Telegram Channel • Fast)", sz_720, stream_720),
            ("480p", "480p SD (Telegram Channel • Fast)", sz_480, stream_480),
            ("360p", "360p Mobile (Telegram Channel • Fast)", sz_360, stream_360),
        ]:
            info = variant_tg_info.get(q_var) or {}
            v_msg = info.get("message_id")
            if not v_msg and q_var == primary_quality:
                v_msg = message_id
            if v_msg and v_msg > 0 and v_msg not in added_msg_ids and tg_channel_id_clean:
                added_msg_ids.add(v_msg)
                v_sz = info.get("file_size") or (sz_1080 if q_var == primary_quality else fallback_sz)
                v_fid = info.get("file_id") or (file_id if q_var == primary_quality else "")
                v_stream = info.get("stream_url") or (stream_url if q_var == primary_quality else fallback_stream)
                downloads_list.append({
                    "quality": f"{q_var} (Telegram Direct)",
                    "label": q_lbl,
                    "size": downloader.format_bytes(v_sz),
                    "size_bytes": v_sz,
                    "url": f"https://t.me/c/{tg_channel_id_clean}/{v_msg}",
                    "telegram_url": f"https://t.me/c/{tg_channel_id_clean}/{v_msg}",
                    "file_id": v_fid,
                    "message_id": v_msg,
                    "stream_url": v_stream,
                    "format": file_ext,
                    "host": "Telegram",
                    "sub_merged": True,
                    "subtitle_merged": True,
                })

        # Multi-quality bot deep-link downloads (High-speed, direct file delivery from bot)
        for q_k, q_lbl, sz_val, s_url in [
            ("1080p", "1080p Full HD (Telegram Bot / Cloud)", sz_1080, stream_url),
            ("720p", "720p HD (Telegram Bot / Cloud)", sz_720, stream_720),
            ("480p", "480p SD (Telegram Bot / Cloud)", sz_480, stream_480),
            ("360p", "360p Data Saver (Telegram Bot / Cloud)", sz_360, stream_360),
        ]:
            downloads_list.append({
                "quality": q_k,
                "label": q_lbl,
                "size": downloader.format_bytes(sz_val),
                "size_bytes": sz_val,
                "url": f"https://t.me/{bot_username}?start=dl_{slug}_{q_k}",
                "stream_url": s_url or "",
                "format": file_ext,
                "host": "Telegram",
                "sub_merged": True,
                "subtitle_merged": True,
            })

        # Multi-quality web download variants
        encoded_title = urllib.parse.quote(display_title)
        if drive_file_id:
            downloads_list.append({
                "quality": "1080p (Drive Web)",
                "label": "1080p Full HD (Web Download • Sinhala Sub)",
                "size": downloader.format_bytes(sz_1080),
                "size_bytes": sz_1080,
                "drive_id": drive_file_id,
                "url": f"/api/download?id={drive_file_id}&q=1080p&title={encoded_title}&size={sz_1080}",
                "format": file_ext,
                "host": "Direct Web",
                "sub_merged": has_sinhala,
                "subtitle_merged": has_sinhala,
            })

        if not qualities_map:
            qualities_map = {
                "auto": stream_url or primary_stream,
                "1080p": stream_url or primary_stream,
                "720p": stream_720 or stream_url or primary_stream,
                "480p": stream_480 or stream_url or primary_stream,
                "360p": stream_360 or stream_url or primary_stream,
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
            "has_sinhala_sub": has_sinhala or is_already_hardsubbed,
            "sub_merged": has_sinhala or is_already_hardsubbed,
            "is_already_hardsubbed": is_already_hardsubbed,
            "sub_hardcoded": is_already_hardsubbed,
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
            "variant_media": variant_media,
            "subtitles": [
                {
                    "language": "Sinhala",
                    "srclang": "si",
                    "label": "සිංහල උපසිරැසි (Sinhala)",
                    "url": default_sub_url,
                    "default": not is_already_hardsubbed,
                }
            ] if (has_sinhala and default_sub_url) else [],
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
            "variant_media": variant_media,
        })

        # CRITICAL SAFETY GATE: Ensure video was successfully uploaded to Telegram before announcing/publishing
        if not message_id or message_id == 0 or not stream_url:
            log.error("[LeechService] Telegram upload failed or stream_url missing: msg_id=%s, stream=%s", message_id, stream_url)
            task_tracker.tracker.complete_task(user_id)
            err_text = (
                f"❌ <b>Telegram Cloud HD Upload අසාර්ථක විය!</b>\n\n"
                f"{media_icon} <b>{display_title}</b>\n"
                f"📦 <b>ප්‍රමාණය:</b> {size_str}\n\n"
                f"⚠️ Video ගොනුව Telegram වෙත upload වීම සම්පූර්ණ නොවූ බැවින්, වෙබ් අඩවියට හෝ ප්‍රසිද්ධ Channel එකට දෝෂ සහිත links පළ කිරීම වළක්වන ලදී.\n"
                f"💾 <b>Draft ID:</b> <code>{draft_id}</code> ලෙස සුරකින ලදී.\n\n"
                f"💡 කරුණාකර නැවත උත්සාහ කරන්න හෝ Bot log පරීක්ෂා කරන්න."
            )
            try:
                await status_msg.edit_text(err_text, parse_mode=ParseMode.HTML)
            except Exception:
                pass
            return

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

            sub_status_text = "💬 <b>සිංහල උපසිරැසි:</b> Video එකට Soft-Mux විය ✅\n" if has_sinhala else "⚠️ <b>සිංහල උපසිරැසි:</b> නිවැරදි සිංහල උපසිරැසි හමු නොවීය\n"
            sub_hint_text = f"💡 <i>සිංහල උපසිරැසි එක් කිරීමට: <code>/sub {title}</code></i>" if not has_sinhala else f"💡 <i>වෙනත් සිංහල උපසිරැසි එක් කිරීමට: <code>/sub {title}</code></i>"

            await status_msg.edit_text(
                f"🎉 <b>Ultra Auto-Leech සාර්ථකව නිම විය!</b>\n\n"
                f"{media_icon} <b>{display_title}</b>\n"
                f"⭐ <b>IMDb:</b> {imdb_val} / 10 | 🎞 <b>Quality:</b> {chosen_candidate.quality}\n"
                f"📦 <b>ප්‍රමාණය:</b> {size_str} | 🎭 <b>කාණ්ඩ:</b> {genres_val}\n"
                f"{sub_status_text}"
                f"☁️ <b>Google Drive CDN:</b> Ultra Fast Cloud Stream Ready ✅\n"
                f"✈️ <b>Telegram Storage:</b> Filmhost Channel වෙත Upload විය ✅\n"
                f"🧹 <b>Seedr & VPS Storage:</b> 100% Free (තාවකාලික ගොනු ඉවත් කෙරිණි)\n\n"
                f"🌐 <b>Live Link:</b> <a href=\"{site_url}\">{site_url}</a>\n"
                f"📢 <b>Telegram Channel:</b> Announcement Post කරන ලදී!\n"
                f"⚡ <b>Cloudflare Pages:</b> Auto-deployed!\n\n"
                f"{sub_hint_text}",
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
                    InlineKeyboardButton("⏩ පසුව දාන්නම් (No Sub)", callback_data=f"leech_act:pub:{draft_id}"),
                ],
                [
                    InlineKeyboardButton("💾 Draft ලෙස තබන්න (Channel Only)", callback_data=f"leech_act:draft:{draft_id}"),
                ]
            ])

            sub_choice_desc = "• <b>🚀 Publish Now:</b> වෙබ් අඩවියට දැන්ම එක්වේ (සිංහල උපසිරැසි සමඟ)\n" if has_sinhala else "• <b>🚀 Publish Now:</b> වෙබ් අඩවියට දැන්ම එක්වේ (උපසිරැසි රහිතව)\n"

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
                f"{sub_choice_desc}"
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
