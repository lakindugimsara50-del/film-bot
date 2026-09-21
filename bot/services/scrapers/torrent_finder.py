"""
torrent_finder.py — Multi-Source Torrent Search Engine for TV Series & Movies.

Sources:
1. The Pirate Bay / Apibay API (Movies + TV series, classic films to new releases)
2. EZTV API (Specialized high-speed TV series torrents, filtered by SxxExx and IMDb)
3. Torrents-CSV API (Open global torrent search engine)
4. YTS Torrent API (Specialized for high quality movies < 2GB)

Prioritizes releases under 2.05 GB for 100% Seedr cloud conversion compatibility,
while also supporting larger releases (up to 3.2 GB) via FFmpeg Smart 1080p compression.
Filters out commentary tracks (Rifftrax), junk files (posters, soundtracks), and false title matches.

All log strings are in English to avoid Windows charmap errors.
"""

import asyncio
import logging
import re
import urllib.parse
from typing import Optional

import httpx

from services.scrapers import method_yts

log = logging.getLogger(__name__)

# High-speed public trackers to attach to all magnet links
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://movies.zsw.ca:6969/announce",
    "udp://9.rarbg.to:2710/announce",
]

# File size boundaries
MIN_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB minimum to avoid posters/soundtracks/samples
MAX_FILE_SIZE_BYTES = int(3.2 * 1024 * 1024 * 1024)
SEEDR_SAFE_SIZE_BYTES = int(2.05 * 1024 * 1024 * 1024)

# Regex patterns identifying non-video junk files
JUNK_EXTENSIONS = [
    r"\.jpg\b", r"\.jpeg\b", r"\.png\b", r"\.gif\b",
    r"\.mp3\b", r"\.flac\b", r"\.wav\b", r"\.aac\b",
    r"\.pdf\b", r"\.epub\b", r"\.mobi\b",
    r"\.txt\b", r"\.nfo\b", r"\.exe\b", r"\.zip\b", r"\.rar\b",
    r"\bsoundtrack\b", r"\bost\b", r"\balbum\b", r"\bdiscography\b",
    r"\bwallpaper\b", r"\bposter\b",
]


def is_junk_release(name: str) -> bool:
    """Check if the release is a non-video asset (poster, soundtrack, text, executable)."""
    name_lower = name.lower()
    return any(re.search(pat, name_lower) for pat in JUNK_EXTENSIONS)


def get_release_penalty(name: str) -> int:
    """Return penalty points for commentary tracks, samples, or low-quality CAM/TS recordings."""
    n = name.lower()
    penalty = 0
    if "rifftrax" in n or "commentary" in n or "audio commentary" in n:
        penalty += 1000
    if "sample" in n or "trailer" in n or "preview" in n or "extras" in n:
        penalty += 2000
    if any(k in n for k in ["telesync", "hdts", "hd-ts", "camrip", "hdcam", "hd-cam", "cam-rip", "workprint"]):
        penalty += 500
    elif re.search(r"\b(cam|ts)\b", n):
        penalty += 500
    if any(k in n for k in ["swesub", "nordic", "latino", "french", "german", "ita", "sub ita", "hindi dubbed", "tamil dubbed"]):
        penalty += 40
    return penalty


def build_magnet_uri(info_hash: str, title: str) -> str:
    """Construct a high-performance magnet link from info hash and title."""
    clean_hash = info_hash.strip().upper()
    encoded_name = urllib.parse.quote(title)
    tr_params = "&".join(f"tr={urllib.parse.quote(t)}" for t in PUBLIC_TRACKERS)
    return f"magnet:?xt=urn:btih:{clean_hash}&dn={encoded_name}&{tr_params}"


def extract_quality_from_name(name: str) -> str:
    """Extract standard resolution/quality tag from a release name."""
    n = name.lower()
    if "2160p" in n or "4k" in n or "uhd" in n:
        return "2160p"
    if "1080p" in n or "1080i" in n:
        return "1080p"
    if "720p" in n or "720" in n:
        return "720p"
    if "480p" in n or "dvdrip" in n or "xvid" in n or "hdtv" in n or "webrip" in n:
        return "480p"
    return "720p"


def matches_season_episode(name: str, season: Optional[int], episode: Optional[int]) -> bool:
    """Check if the release name matches the requested season and episode."""
    if season is None and episode is None:
        return True

    name_lower = name.lower()

    if season is not None and episode is not None:
        patterns = [
            rf"\bs0?{season}e0?{episode}\b",
            rf"\bs0?{season}\s*ep?0?{episode}\b",
            rf"\b0?{season}x0?{episode}\b",
            rf"season\s*0?{season}.*?episode\s*0?{episode}\b",
        ]
        return any(re.search(p, name_lower) for p in patterns)

    if season is not None:
        patterns = [
            rf"\bs0?{season}(?:e\d{{1,3}}|ep\d{{1,3}}|\b)",
            rf"season\s*0?{season}\b",
            rf"\b0?{season}x\d{{1,3}}\b",
        ]
        return any(re.search(p, name_lower) for p in patterns)

    if episode is not None:
        patterns = [
            rf"(?:\b|s\d{{1,2}})e(?:p)?0?{episode}\b",
            rf"episode\s*0?{episode}\b",
            rf"\b\d{{1,2}}x0?{episode}\b",
            rf"\be(?:p)?0?{episode}\b",
        ]
        return any(re.search(p, name_lower) for p in patterns)

    return True


def is_valid_series_title(release_name: str, show_title: str, season: Optional[int], episode: Optional[int]) -> bool:
    """
    Ensure the release title actually belongs to show_title, not another show
    that happened to mention show_title in the description or tag.
    Standard TV release format is: <Show Name> SxxExx <Quality/Tags>.
    The show title must be present in the portion of the name BEFORE SxxExx.
    """
    if not matches_season_episode(release_name, season, episode):
        return False

    name_lower = release_name.lower().replace(".", " ").replace("_", " ").replace("-", " ")
    parts = re.split(
        r"\b(?:s\d{1,2}(?:\s*e(?:p)?\d{1,3}(?:[\-e]\d{1,3})?)?|season\s*\d+(?:\s*(?:ep|episode)\s*\d+)?|\d{1,2}x\d{1,3})\b",
        name_lower,
        flags=re.IGNORECASE,
    )
    prefix = parts[0].strip() if parts else name_lower

    show_words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9]+\b", show_title) if len(w) > 2]
    if not show_words:
        return True

    # All significant words of show_title must be in prefix
    return all(w in prefix for w in show_words)


def title_matches(name: str, query: str) -> bool:
    """Verify that significant words from query exist in release name."""
    words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9]+\b", query) if len(w) > 2]
    if not words:
        return True
    name_lower = name.lower().replace(".", " ").replace("_", " ").replace("-", " ")
    return all(w in name_lower for w in words)


def calculate_relevance_score(tor_name: str, target_title: str, is_series: bool = False) -> int:
    """Compute title relevance score penalizing commentary and CAM releases."""
    score = 100
    clean_n = re.sub(r"[._-]", " ", tor_name).lower()
    clean_target = re.sub(r"[._-]", " ", target_title).lower()

    if clean_n.startswith(clean_target):
        score += 50
    elif clean_target in clean_n:
        score += 30

    penalty = get_release_penalty(tor_name)
    score -= penalty
    return score


async def search_apibay(
    query: str,
    target_show_title: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> list[dict]:
    """
    Search The Pirate Bay via the official Apibay JSON API.
    Supports movies (both old and new) and TV series episodes.
    """
    log.info("[TorrentFinder] Apibay searching: '%s'", query)
    candidates: list[dict] = []

    apibay_urls = [
        "https://apibay.org/q.php",
    ]

    is_series = season is not None or episode is not None or target_show_title is not None
    show_name = target_show_title or query

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for base_url in apibay_urls:
            try:
                resp = await client.get(base_url, params={"q": query})
                if resp.status_code != 200:
                    continue

                items = resp.json()
                if not isinstance(items, list):
                    continue

                for it in items:
                    name = it.get("name", "")
                    info_hash = it.get("info_hash", "").strip()
                    if not info_hash or info_hash.startswith("00000000") or it.get("id") == "0":
                        continue
                    if name == "No results returned":
                        continue

                    # Filter junk extensions
                    if is_junk_release(name):
                        continue

                    # Series prefix / title validation
                    if is_series:
                        if not is_valid_series_title(name, show_name, season, episode):
                            continue
                    else:
                        if not title_matches(name, query):
                            continue

                    try:
                        size_bytes = int(it.get("size", 0))
                    except (ValueError, TypeError):
                        size_bytes = 0

                    if size_bytes < MIN_FILE_SIZE_BYTES or size_bytes > max_size_bytes:
                        continue

                    try:
                        seeders = int(it.get("seeders", 0))
                    except (ValueError, TypeError):
                        seeders = 0

                    try:
                        leechers = int(it.get("leechers", 0))
                    except (ValueError, TypeError):
                        leechers = 0

                    quality = extract_quality_from_name(name)
                    magnet = build_magnet_uri(info_hash, name)
                    from services.downloader import format_bytes

                    rel_score = calculate_relevance_score(name, show_name, is_series=is_series)

                    candidates.append({
                        "method": "torrent",
                        "provider": "ThePirateBay",
                        "title": name,
                        "quality": quality,
                        "size": format_bytes(size_bytes),
                        "size_bytes": size_bytes,
                        "hash": info_hash,
                        "magnet": magnet,
                        "torrent_url": "",
                        "seeds": seeders,
                        "peers": leechers,
                        "relevance_score": rel_score,
                    })

                if candidates:
                    log.info("[TorrentFinder] Apibay yielded %d valid candidate(s).", len(candidates))
                    break
            except Exception as exc:
                log.warning("[TorrentFinder] Apibay error on %s: %s", base_url, exc)
                continue

    return candidates


async def search_eztv(
    title: str,
    imdb_id: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> list[dict]:
    """
    Search EZTV API for television series releases.
    Filters specifically for requested season & episode, checking page 1 and page 2.
    """
    log.info("[TorrentFinder] EZTV searching: title='%s', imdb_id=%s, S%sE%s", title, imdb_id, season, episode)
    candidates: list[dict] = []

    eztv_bases = [
        "https://eztvx.to/api/get-torrents",
        "https://eztv.re/api/get-torrents",
    ]

    clean_imdb = (imdb_id or "").lstrip("t")

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for base_url in eztv_bases:
            try:
                pages_to_check = [1, 2] if clean_imdb else [1]
                for p in pages_to_check:
                    params: dict = {"limit": 100, "page": p}
                    if clean_imdb:
                        params["imdb_id"] = clean_imdb
                    else:
                        params["search"] = title

                    resp = await client.get(base_url, params=params)
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    torrents = data.get("torrents", [])
                    if not torrents and p == 1 and clean_imdb and title:
                        params = {"search": title, "limit": 100, "page": 1}
                        resp = await client.get(base_url, params=params)
                        if resp.status_code == 200:
                            data = resp.json()
                            torrents = data.get("torrents", [])

                    for tor in torrents:
                        tor_title = tor.get("title") or tor.get("filename") or ""
                        tor_season = tor.get("season")
                        tor_episode = tor.get("episode")

                        if is_junk_release(tor_title):
                            continue

                        if not title_matches(tor_title, title):
                            continue

                        if season is not None:
                            if str(tor_season) != str(season) and not matches_season_episode(tor_title, season, episode):
                                continue
                        if episode is not None:
                            if str(tor_episode) != str(episode) and not matches_season_episode(tor_title, season, episode):
                                continue

                        try:
                            size_bytes = int(tor.get("size_bytes", 0))
                        except (ValueError, TypeError):
                            size_bytes = 0

                        if size_bytes < MIN_FILE_SIZE_BYTES or size_bytes > max_size_bytes:
                            continue

                        info_hash = tor.get("hash", "").strip()
                        magnet = tor.get("magnet_url") or (build_magnet_uri(info_hash, tor_title) if info_hash else "")
                        if not magnet:
                            continue

                        try:
                            seeds = int(tor.get("seeds", 0))
                        except (ValueError, TypeError):
                            seeds = 0

                        try:
                            peers = int(tor.get("peers", 0))
                        except (ValueError, TypeError):
                            peers = 0

                        quality = extract_quality_from_name(tor_title)
                        from services.downloader import format_bytes

                        rel_score = calculate_relevance_score(tor_title, title, is_series=True)

                        candidates.append({
                            "method": "torrent",
                            "provider": "EZTV",
                            "title": tor_title,
                            "quality": quality,
                            "size": format_bytes(size_bytes),
                            "size_bytes": size_bytes,
                            "hash": info_hash,
                            "magnet": magnet,
                            "torrent_url": tor.get("torrent_url", ""),
                            "seeds": seeds,
                            "peers": peers,
                            "relevance_score": rel_score,
                        })

                if candidates:
                    log.info("[TorrentFinder] EZTV yielded %d valid candidate(s).", len(candidates))
                    break

            except Exception as exc:
                log.warning("[TorrentFinder] EZTV error on %s: %s", base_url, exc)
                continue

    return candidates


async def search_torrents_csv(
    query: str,
    target_show_title: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> list[dict]:
    """
    Search Torrents-CSV public open torrent index.
    Covers movies and TV series with infohash and seeds.
    """
    log.info("[TorrentFinder] Torrents-CSV searching: '%s'", query)
    candidates: list[dict] = []

    is_series = season is not None or episode is not None or target_show_title is not None
    show_name = target_show_title or query

    url = "https://torrents-csv.com/service/search"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, params={"q": query})
            if resp.status_code == 200:
                data = resp.json()
                torrents = data.get("torrents", [])
                for it in torrents:
                    name = it.get("name", "")
                    info_hash = it.get("infohash", "").strip()
                    if not info_hash or len(info_hash) != 40:
                        continue

                    # Filter junk extensions (like .jpg posters)
                    if is_junk_release(name):
                        continue

                    # Series prefix / title validation
                    if is_series:
                        if not is_valid_series_title(name, show_name, season, episode):
                            continue
                    else:
                        if not title_matches(name, query):
                            continue

                    try:
                        size_bytes = int(it.get("size_bytes", 0))
                    except (ValueError, TypeError):
                        size_bytes = 0

                    if size_bytes < MIN_FILE_SIZE_BYTES or size_bytes > max_size_bytes:
                        continue

                    try:
                        seeders = int(it.get("seeders", 0))
                    except (ValueError, TypeError):
                        seeders = 0

                    try:
                        leechers = int(it.get("leechers", 0))
                    except (ValueError, TypeError):
                        leechers = 0

                    quality = extract_quality_from_name(name)
                    magnet = build_magnet_uri(info_hash, name)
                    from services.downloader import format_bytes

                    rel_score = calculate_relevance_score(name, show_name, is_series=is_series)

                    candidates.append({
                        "method": "torrent",
                        "provider": "TorrentsCSV",
                        "title": name,
                        "quality": quality,
                        "size": format_bytes(size_bytes),
                        "size_bytes": size_bytes,
                        "hash": info_hash,
                        "magnet": magnet,
                        "torrent_url": "",
                        "seeds": seeders,
                        "peers": leechers,
                        "relevance_score": rel_score,
                    })

                if candidates:
                    log.info("[TorrentFinder] Torrents-CSV yielded %d valid candidate(s).", len(candidates))
    except Exception as exc:
        log.warning("[TorrentFinder] Torrents-CSV error: %s", exc)

    return candidates


async def search_all_torrents(
    title: str,
    year: Optional[int] = None,
    imdb_id: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    is_series: bool = False,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> list[dict]:
    """
    Aggregates results from multiple torrent sources:
    - If TV series: EZTV + Apibay + Torrents-CSV
    - If Movie: YTS + Apibay + Torrents-CSV

    Deduplicates by infohash and sorts by:
    1. Tier 2: Size <= 2.05 GB (100% Seedr cloud compatible)
    2. Relevance score: exact show/movie name, penalizing commentary/samples/CAM
    3. Active seeds: alive torrents (>0 seeds) rank far above dead torrents
    4. Quality: 1080p > 720p > 480p
    5. Raw seed count
    """
    clean_title = re.sub(r"[._-]", " ", title).strip()

    tasks = []

    if is_series or season is not None or episode is not None:
        # Construct specific search query for series episode
        ep_tag = ""
        if season is not None and episode is not None:
            ep_tag = f"S{season:02d}E{episode:02d}"
        elif season is not None:
            ep_tag = f"S{season:02d}"
        elif episode is not None:
            ep_tag = f"E{episode:02d}"

        series_query = f"{clean_title} {ep_tag}".strip()

        # 1. EZTV API
        tasks.append(search_eztv(clean_title, imdb_id=imdb_id, season=season, episode=episode, max_size_bytes=max_size_bytes))
        # 2. Apibay
        tasks.append(search_apibay(series_query, target_show_title=clean_title, season=season, episode=episode, max_size_bytes=max_size_bytes))
        # 3. Torrents-CSV
        tasks.append(search_torrents_csv(series_query, target_show_title=clean_title, season=season, episode=episode, max_size_bytes=max_size_bytes))
    else:
        # Movie search
        movie_query = f"{clean_title} {year}" if year else clean_title

        # 1. YTS
        tasks.append(method_yts.search(clean_title, year=year, imdb_id=imdb_id, max_size_bytes=max_size_bytes))
        # 2. Apibay
        tasks.append(search_apibay(movie_query, max_size_bytes=max_size_bytes))
        # 3. Torrents-CSV
        tasks.append(search_torrents_csv(movie_query, max_size_bytes=max_size_bytes))

    results_lists = await asyncio.gather(*tasks, return_exceptions=True)

    all_torrents: list[dict] = []
    seen_hashes: set[str] = set()

    for res in results_lists:
        if isinstance(res, list):
            for tor in res:
                tor.setdefault("provider", "YTS" if tor.get("method") == "yts" else "Torrent")
                if "relevance_score" not in tor:
                    tor["relevance_score"] = calculate_relevance_score(tor.get("title", ""), clean_title, is_series=is_series)
                h = tor.get("hash", "").strip().lower()
                if h and h in seen_hashes:
                    continue
                if h:
                    seen_hashes.add(h)
                all_torrents.append(tor)

    log.info("[TorrentFinder] Total raw torrent candidates collected: %d", len(all_torrents))

    def _rank_torrent(tor: dict) -> tuple[int, int, int, int, int]:
        sz = tor.get("size_bytes", 0)
        # Tier 2: 50MB <= sz <= 2.05GB (100% Seedr cloud compatible)
        # Tier 1: 2.05GB < sz <= max_size_bytes
        # Tier 0: < 50MB or > max_size_bytes
        if MIN_FILE_SIZE_BYTES <= sz <= SEEDR_SAFE_SIZE_BYTES:
            seedr_tier = 2
        elif SEEDR_SAFE_SIZE_BYTES < sz <= max_size_bytes:
            seedr_tier = 1
        else:
            seedr_tier = 0

        rel_score = tor.get("relevance_score", 0)

        seeds = tor.get("seeds", 0)
        has_seeds = 1 if seeds > 0 else 0

        q = tor.get("quality", "").lower()
        if "1080" in q:
            q_score = 3
        elif "720" in q:
            q_score = 2
        elif "480" in q:
            q_score = 1
        else:
            q_score = 1

        return (seedr_tier, rel_score, has_seeds, q_score, seeds)

    all_torrents.sort(key=_rank_torrent, reverse=True)
    return all_torrents
