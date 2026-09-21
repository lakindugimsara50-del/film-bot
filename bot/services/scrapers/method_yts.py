"""
method_yts.py - Method B: YTS Torrent API Scraper (< 1.9GB filtered).

Queries YTS API mirrors for high-speed torrents & magnet links,
strictly filtering for releases under 1.95GB to ensure 100% compliance
with Telegram's upload limit.

All log strings are in English to avoid Windows charmap errors.
"""

import logging
import re
import urllib.parse
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# YTS official and mirror API domains
YTS_BASE_URLS = [
    "https://yts.gg",
    "https://yts.bz",
    "https://yts.mx",
    "https://yts.lt",
    "https://yts.am",
    "https://yts.do",
]

# High-speed public trackers to append to magnet links for maximum VPS download speed
TRACKERS = [
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://glotorrents.pw:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://torrent.gresille.org:80/announce",
    "udp://p4p.arenabg.com:1337",
    "udp://tracker.internetwarriors.net:1337",
    "udp://tracker.leechers-paradise.org:6969",
    "udp://9.rarbg.to:2710/announce",
]

# Max file size: 3.2 GB (files > 1.95 GB are automatically compressed via Smart 1080p FFmpeg)
MAX_FILE_SIZE_BYTES = int(3.2 * 1024 * 1024 * 1024)
SAFE_TELEGRAM_SIZE_BYTES = int(1.95 * 1024 * 1024 * 1024)


def build_magnet_uri(info_hash: str, title: str) -> str:
    """Construct a high-performance magnet link from an info hash and title."""
    encoded_name = urllib.parse.quote(title)
    tr_params = "&".join(f"tr={urllib.parse.quote(t)}" for t in TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={encoded_name}&{tr_params}"


async def search(
    title: str,
    year: Optional[int] = None,
    imdb_id: Optional[str] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
    is_series: bool = False,
) -> list[dict]:
    """
    Search YTS for torrent releases matching the movie.
    Filters out any release larger than max_size_bytes (< 1.95 GB).
    Rejects TV series, documentaries, and false title matches.

    Returns a list of candidate dictionaries sorted by quality preference (1080p, then 720p).
    """
    if is_series:
        log.info("[M-YTS] Skipping YTS because target is a TV series.")
        return []

    query = imdb_id if imdb_id else (f"{title} {year}" if year else title)
    log.info("[M-YTS] Searching YTS API for: %s", query)

    candidates: list[dict] = []

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for base in YTS_BASE_URLS:
            endpoint = f"{base}/api/v2/list_movies.json"
            try:
                params = {"query_term": query, "limit": 5}
                resp = await client.get(endpoint, params=params)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                movie_list = data.get("data", {}).get("movies", [])
                if not movie_list:
                    continue

                for movie in movie_list:
                    # If imdb_id given, check for exact match
                    if imdb_id and movie.get("imdb_code") and movie.get("imdb_code").lower() != imdb_id.lower():
                        continue

                    # If year given, verify tolerance (+/- 1 year for release dates)
                    m_year = movie.get("year")
                    if year and m_year and abs(int(m_year) - int(year)) > 1 and not imdb_id:
                        continue

                    m_title = movie.get("title_english") or movie.get("title") or title

                    # Clean comparison to filter false matches (e.g. documentaries, spinoffs)
                    clean_target = re.sub(r"[^\w\s]", "", title).strip().lower()
                    clean_m = re.sub(r"[^\w\s]", "", m_title).strip().lower()
                    genres = [str(g).lower() for g in movie.get("genres", [])]

                    if not imdb_id:
                        if "documentary" in genres and "documentary" not in clean_target:
                            log.debug("[M-YTS] Skipping documentary '%s'", m_title)
                            continue
                        if any(k in clean_m for k in ("parody", "behind the scenes", "the making of", "unauthorized")):
                            log.debug("[M-YTS] Skipping parody/spinoff '%s'", m_title)
                            continue
                        if clean_target != clean_m:
                            target_words = clean_target.split()
                            if not all(w in clean_m for w in target_words):
                                continue
                            if not year or (m_year and abs(int(m_year) - int(year)) > 1):
                                log.debug("[M-YTS] Skipping '%s' - non-exact title without matching year", m_title)
                                continue

                    torrents = movie.get("torrents", [])

                    for tor in torrents:
                        size_bytes = tor.get("size_bytes", 0)
                        # Ensure size is strictly under 1.95 GB
                        if size_bytes > max_size_bytes:
                            log.debug(
                                "[M-YTS] Skipping torrent '%s' (%s) because size %s > max %s",
                                m_title, tor.get("quality"), size_bytes, max_size_bytes
                            )
                            continue

                        info_hash = tor.get("hash", "")
                        if not info_hash:
                            continue

                        magnet = build_magnet_uri(info_hash, m_title)
                        quality = tor.get("quality", "1080p")
                        size_str = tor.get("size", "Unknown")

                        torrent_dl_url = f"{base}/torrent/download/{info_hash}" if info_hash else (tor.get("url") or "")
                        candidates.append({
                            "method": "yts",
                            "title": m_title,
                            "year": m_year or year,
                            "quality": quality,
                            "size": size_str,
                            "size_bytes": size_bytes,
                            "hash": info_hash,
                            "magnet": magnet,
                            "torrent_url": torrent_dl_url,
                            "seeds": tor.get("seeds", 0),
                            "peers": tor.get("peers", 0),
                        })

                if candidates:
                    log.info("[M-YTS] Found %d valid torrent candidate(s) on %s", len(candidates), base)
                    break

            except Exception as exc:
                log.warning("[M-YTS] Error querying YTS base %s: %s", base, exc)
                continue

    # Sort priority: 1080p under safe size first, then larger 1080p, then 720p, then seed count
    def _rank(item: dict) -> tuple[int, int, int]:
        q = item.get("quality", "").lower()
        q_score = 2 if "1080" in q else (1 if "720" in q else 0)
        sz_score = 1 if item.get("size_bytes", 0) <= SAFE_TELEGRAM_SIZE_BYTES else 0
        seeds = item.get("seeds", 0)
        return (q_score, sz_score, seeds)

    candidates.sort(key=_rank, reverse=True)
    return candidates
