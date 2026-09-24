"""
movie_finder.py - Ultra Cascading Movie Finder Orchestrator.

Tries 4 acquisition methods in priority order, gracefully falling back
when a method returns no result or raises an exception.

Method priority:
  1. Telegram Media Hub Search   (instant, 0 cost, 0 bandwidth)
  2. Consumet / FlixHQ Stream    (direct .mp4 / .m3u8 links)
  3. DDL Sites (Pahe / PSArips)  (x265 lightweight download links)
  4. Embed Aggregators (Vidsrc)  (iframe embed fallback, last resort)

All log strings are in English to avoid Windows charmap errors.
"""

import asyncio
import logging
from typing import Any, Optional

import config
from services.scrapers import method1_telegram, method2_consumet, method3_ddl, method4_embed

log = logging.getLogger(__name__)


class FindResult:
    """Encapsulates a successful find result from any method."""

    def __init__(
        self,
        method: str,
        stream_url: Optional[str],
        servers: Optional[list[dict]],
        downloads: Optional[list[dict]],
        quality: str,
        embed: bool = False,
        raw: Optional[dict] = None,
    ):
        self.method = method
        self.stream_url = stream_url
        self.servers = servers or []
        self.downloads = downloads or []
        self.quality = quality
        self.embed = embed
        self.raw = raw or {}

    def __repr__(self) -> str:
        return (
            f"FindResult(method={self.method!r}, quality={self.quality!r}, "
            f"servers={len(self.servers)}, stream_url={str(self.stream_url)[:60]!r})"
        )

    def to_streams_list(self) -> list[dict]:
        """Convert result to the streams[] format expected by movies.json."""
        if self.servers:
            # Build a de-duplicated server list (embed or direct)
            out = []
            for s in self.servers:
                out.append({
                    "server": s.get("server", "Server 1"),
                    "label": s.get("label", "Server 1"),
                    "type": s.get("type", "video/mp4"),
                    "stream_url": s.get("stream_url") or s.get("embed_url", ""),
                    "embed": s.get("type") == "embed",
                })
            return out
        if self.stream_url:
            return [{
                "server": "Server 1",
                "label": "Server 1",
                "type": "video/mp4",
                "stream_url": self.stream_url,
            }]
        return []

    def to_downloads_list(self) -> list[dict]:
        """Convert DDL results to the downloads[] format expected by movies.json."""
        if self.downloads:
            out = []
            for d in self.downloads:
                host = d.get("host", "Direct")
                url = d.get("url", "")
                quality = d.get("quality", self.quality)
                out.append({
                    "quality": quality,
                    "size": d.get("size", "Unknown"),
                    "url": url,
                    "format": "MP4",
                    "host": host.title(),
                })
            return out
        return []


async def find_movie(
    title: str,
    year: Optional[int] = None,
    imdb_id: Optional[str] = None,
    tmdb_id: Optional[str] = None,
) -> Optional[FindResult]:
    """
    Master orchestrator — tries each method in order, returns first success.

    Args:
        title:    Movie title (English preferred for best results).
        year:     Release year for disambiguation.
        imdb_id:  IMDb ID (e.g. 'tt1375666') — used by embed methods.
        tmdb_id:  TMDB ID — used by embed methods.

    Returns:
        FindResult on success, None if all 4 methods fail.
    """
    log.info(
        "[MovieFinder] Starting search — title=%r year=%s imdb=%s tmdb=%s",
        title, year, imdb_id, tmdb_id,
    )

    # ── Method 1: Telegram Channels ──────────────────────────────────────────
    log.info("[MovieFinder] Trying Method 1: Telegram Channels...")
    try:
        m1 = await method1_telegram.search(
            title=title, year=year, imdb_id=imdb_id, bot_token=config.BOT_TOKEN
        )
        if m1:
            log.info("[MovieFinder] SUCCESS via Method 1 (Telegram).")
            return FindResult(
                method="telegram",
                stream_url=m1.get("stream_url"),
                servers=[{
                    "server": "Server 1",
                    "label": "Server 1 (Telegram)",
                    "type": "video/mp4",
                    "stream_url": m1.get("stream_url", ""),
                }],
                downloads=[],
                quality=m1.get("quality", "1080p"),
                raw=m1,
            )
    except Exception as exc:
        log.warning("[MovieFinder] Method 1 raised exception: %s", exc)

    # ── Method 2: Consumet / FlixHQ ──────────────────────────────────────────
    log.info("[MovieFinder] Trying Method 2: Consumet/FlixHQ...")
    try:
        m2 = await method2_consumet.search(title=title, year=year, imdb_id=imdb_id)
        if m2 and m2.get("stream_url"):
            log.info("[MovieFinder] SUCCESS via Method 2 (Consumet).")
            return FindResult(
                method="consumet",
                stream_url=m2.get("stream_url"),
                servers=m2.get("servers", []),
                downloads=[],
                quality=m2.get("quality", "Auto"),
                raw=m2,
            )
    except Exception as exc:
        log.warning("[MovieFinder] Method 2 raised exception: %s", exc)

    # ── Method 3: DDL Sites (Pahe / PSArips) ────────────────────────────────
    log.info("[MovieFinder] Trying Method 3: DDL Sites...")
    try:
        m3 = await method3_ddl.search(title=title, year=year, imdb_id=imdb_id)
        if m3 and m3.get("downloads"):
            log.info("[MovieFinder] SUCCESS via Method 3 (DDL). %d download links.", len(m3["downloads"]))
            dl_list = []
            for d in m3["downloads"]:
                dl_list.append({
                    "quality": m3.get("quality", "1080p"),
                    "size": m3.get("size", "Unknown"),
                    "url": d.get("url", ""),
                    "format": "MP4",
                    "host": d.get("host", "Direct").title(),
                })
            return FindResult(
                method="ddl",
                stream_url=m3.get("stream_url"),  # May be None for DDL
                servers=[],
                downloads=dl_list,
                quality=m3.get("quality", "1080p"),
                raw=m3,
            )
    except Exception as exc:
        log.warning("[MovieFinder] Method 3 raised exception: %s", exc)

    # ── Method 4: Embed Aggregators (Last Resort) ────────────────────────────
    log.info("[MovieFinder] Trying Method 4: Embed Aggregators...")
    try:
        m4 = await method4_embed.search(
            title=title, year=year, imdb_id=imdb_id, tmdb_id=tmdb_id
        )
        if m4 and m4.get("servers"):
            log.info("[MovieFinder] SUCCESS via Method 4 (Embed). %d servers.", len(m4["servers"]))
            return FindResult(
                method="embed",
                stream_url=m4.get("stream_url"),
                servers=m4.get("servers", []),
                downloads=[],
                quality=m4.get("quality", "Auto"),
                embed=True,
                raw=m4,
            )
    except Exception as exc:
        log.warning("[MovieFinder] Method 4 raised exception: %s", exc)

    log.warning("[MovieFinder] ALL 4 methods failed for: %r (%s)", title, year)
    return None
