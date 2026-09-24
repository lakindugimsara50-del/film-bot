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

# Standardized browser headers for torrent indexing APIs and scrapers
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# High-speed public trackers (both UDP and HTTP/HTTPS for firewall resilience)
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "http://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://movies.zsw.ca:6969/announce",
    "https://tracker.tamersunion.org:443/announce",
    "https://tracker.gbitt.info:443/announce",
    "udp://9.rarbg.to:2710/announce",
]

# File size boundaries
MIN_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB minimum to avoid posters/soundtracks/samples
MAX_FILE_SIZE_BYTES = int(2.05 * 1024 * 1024 * 1024)  # 2.05 GB limit: Seedr-compatible & safe for Render disk
SEEDR_SAFE_SIZE_BYTES = int(1.95 * 1024 * 1024 * 1024)

# Regex patterns identifying non-video junk files, subtitle packs, soundtracks, and 3D SBS
JUNK_EXTENSIONS = [
    r"\.jpg\b", r"\.jpeg\b", r"\.png\b", r"\.gif\b",
    r"\.mp3\b", r"\.flac\b", r"\.wav\b", r"\.aac\b",
    r"\.pdf\b", r"\.epub\b", r"\.mobi\b",
    r"\.txt\b", r"\.nfo\b", r"\.exe\b", r"\.zip\b", r"\.rar\b",
    r"\bsoundtrack\b", r"\bost\b", r"\balbum\b", r"\bdiscography\b",
    r"\bmusic\s+from\b", r"\bmotion\s+picture\s+score\b", r"\boriginal\s+score\b",
    r"\bsous[\s._-]*titres\b", r"\bsubpack\b", r"\bsubtitles[\s._-]*only\b",
    r"\b3d[\s._-]*(?:full[\s._-]*)?sbs\b", r"\bhalf[\s._-]*sbs\b", r"\bhsbs\b",
    r"\bwallpaper\b", r"\bposter\b", r"\baudiobook\b",
]

# Patterns identifying multi-movie collection packs to avoid when searching for a single film
MOVIE_COLLECTION_PATTERNS = [
    r"\b(?:top|imdb)\s*\d{2,3}\s*movies\b",
    r"\b(?:trilogy|quadrilogy|pentalogy|hexalogy|anthology|duology)\b",
    r"\b(?:filmography|filmografi)\b",
    r"\bmovie\s*collection\b",
    r"\bcomplete\s*collection\b",
    r"\b\d+\s*peliculas\b",
]


def is_junk_release(name: str) -> bool:
    """Check if the release is a non-video asset (poster, soundtrack, text, executable, 3D SBS)."""
    name_lower = name.lower()
    return any(re.search(pat, name_lower) for pat in JUNK_EXTENSIONS)


def is_movie_collection_pack(name: str, target_title: str = "") -> bool:
    """Detect multi-movie mega-packs (e.g. 'IMDb Top 263 Movies', 'Filmography', 'Trilogy') unless requested."""
    n = name.lower()
    t = (target_title or "").lower()
    for pat in MOVIE_COLLECTION_PATTERNS:
        if re.search(pat, n) and not re.search(pat, t):
            return True
    return False


def get_release_penalty(name: str) -> int:
    """Return penalty points for commentary tracks, samples, foreign dubs, or low-quality CAM/TS recordings."""
    n = name.lower()
    penalty = 0
    if "rifftrax" in n or "commentary" in n or "audio commentary" in n:
        penalty += 1000
    if "sample" in n or "trailer" in n or "preview" in n or "extras" in n or "censored" in n:
        penalty += 2000
    if any(k in n for k in ["telesync", "hdts", "hd-ts", "camrip", "hdcam", "hd-cam", "cam-rip", "workprint", "dvdscr", "screener"]):
        penalty += 1000
    elif re.search(r"\b(cam|ts|tc)\b", n):
        penalty += 1000
    if any(k in n for k in [
        "swesub", "nordic", "norsub", "dansub", "latino", "french", "truefrench", "vostfr", "saison",
        "german", "deutsch", "ita", "sub ita", "italiano", "stagioni", "trono di spade",
        "castellano", "temporada", "juego de tronos", "espanol", "español",
        "dublado", "lektor", "arabic-sub",
    ]):
        penalty += 250
    if re.search(r"[а-яА-Я]", name):
        penalty += 300
    return penalty


def build_magnet_uri(info_hash: str, title: str) -> str:
    """Construct a high-performance magnet link from info hash and title."""
    clean_hash = info_hash.strip().upper()
    encoded_name = urllib.parse.quote(title)
    tr_params = "&".join(f"tr={urllib.parse.quote(t)}" for t in PUBLIC_TRACKERS)
    return f"magnet:?xt=urn:btih:{clean_hash}&dn={encoded_name}&{tr_params}"


def extract_quality_from_name(name: str) -> str:
    """
    Extract standard resolution/quality tag from a release name.
    Strictly identifies 1080p, 720p, 2160p, and classifies SD / untagged releases as 480p
    so that SD torrents are never falsely promoted to 720p HD.
    """
    n = name.lower()
    # Explicit SD / low-resolution markers
    if re.search(r"\b(480p|480i|360p|240p|406p|540p|576p|sd|dvdrip|xvid|divx|tvrip|vcd|svcd|camrip|hdcam|telesync|hdts|dvdscr)\b", n):
        return "480p"
    if n.endswith(".avi") or ".avi " in n:
        return "480p"
    # Check 1080p and 720p BEFORE 4K so tags like '720p DS4K' are accurately classified as 720p
    if re.search(r"\b(1080p|1080i|1920x1080|fhd|fullhd|full-hd|m1080|bd1080)\b", n) or "1080p" in n:
        return "1080p"
    if re.search(r"\b(720p|720i|1280x720|hd720)\b", n) or "720p" in n:
        return "720p"
    if re.search(r"\b(2160p|4k|uhd|3840x2160)\b", n):
        return "2160p"
    # Untagged HDTV / WEBRip / BDRip without 720p/1080p or completely untagged releases are SD (480p)
    return "480p"


def is_exact_single_episode(name: str, season: Optional[int], episode: Optional[int]) -> bool:
    """
    Return True if the release name represents a single-episode release (e.g. S01E01, 1x01)
    rather than a full season pack or multi-season pack.
    """
    if season is None or episode is None:
        return False
    n = name.lower()
    # Reject if it explicitly mentions multi-season or full-season pack keywords
    if re.search(r"\b(complete|integrale|temporada|saison|stagioni|seasons?\s*\d+\s*[-~to]+\s*\d+|s0?\d+\s*[-~]\s*s?0?\d+)\b", n):
        return False
    # Reject if it is an episode range (e.g. S01E01-E10, S01E01-10)
    if re.search(rf"\bs0?{season}\s*e(?:p)?0?{episode}\s*(?:[-~]|[-~]?e(?:p)?)\s*\d+\b", n):
        return False
    if re.search(rf"\b0?{season}x0?{episode}\s*(?:[-~]|[-~]?\d{{1,2}}x|[-~]?x)\s*\d+\b", n):
        return False

    exact_patterns = [
        rf"\bs0?{season}e0?{episode}\b",
        rf"\bs0?{season}\s*ep?0?{episode}\b",
        rf"\b0?{season}x0?{episode}\b",
        rf"season\s*0?{season}.*?episode\s*0?{episode}\b",
        rf"s0?{season}[\s._-]+e0?{episode}\b",
    ]
    return any(re.search(p, n) for p in exact_patterns)


def is_season_pack_release(name: str, season: Optional[int], episode: Optional[int]) -> bool:
    """Return True if a series release is a season/multi-episode pack rather than a single episode."""
    if season is None and episode is None:
        return False
    return not is_exact_single_episode(name, season, episode)


def matches_season_episode(name: str, season: Optional[int], episode: Optional[int]) -> bool:
    """Check if the release name matches the requested season and episode."""
    if season is None and episode is None:
        return True

    name_lower = name.lower()

    if season is not None and episode is not None:
        # 1. Exact episode match: S01E02, 1x02, Season 1 Episode 2, S01 Ep02, S01.E02
        exact_patterns = [
            rf"\bs0?{season}e0?{episode}\b",
            rf"\bs0?{season}\s*ep?0?{episode}\b",
            rf"\b0?{season}x0?{episode}\b",
            rf"season\s*0?{season}.*?episode\s*0?{episode}\b",
            rf"s0?{season}[\s._-]+e0?{episode}\b",
        ]
        if any(re.search(p, name_lower) for p in exact_patterns):
            return True

        # 2. Episode range: S01E01-E10, S01E01-04, 1x01-1x05, S01E01E02
        p1 = rf"\bs0?{season}\s*e(?:p)?(\d+)\s*(?:[-~]|[-~]?e(?:p)?)\s*0?(\d+)\b"
        m1 = re.search(p1, name_lower)
        if m1:
            ep1, ep2 = int(m1.group(1)), int(m1.group(2))
            if ep1 > ep2:
                ep1, ep2 = ep2, ep1
            if ep1 <= episode <= ep2:
                return True

        p2 = rf"\b0?{season}x0?(\d+)\s*(?:[-~]|[-~]?\d{{1,2}}x|[-~]?x)\s*0?(\d+)\b"
        m2 = re.search(p2, name_lower)
        if m2:
            ep1, ep2 = int(m2.group(1)), int(m2.group(2))
            if ep1 > ep2:
                ep1, ep2 = ep2, ep1
            if ep1 <= episode <= ep2:
                return True

        # 3. Season pack for this season (e.g. S01 Complete, Season 1 720p)
        # Must match season, and NOT mention a conflicting episode or conflicting season
        season_match = re.search(rf"\b(?:s0?{season}|season\s*0?{season})\b", name_lower)
        if season_match:
            # Check for conflicting episode
            other_ep = re.search(r"\b(?:s\d{1,2})?e(?:p)?(\d{1,3})\b|\b\d{1,2}x(\d{1,3})\b", name_lower)
            if other_ep:
                found_ep = int(other_ep.group(1) or other_ep.group(2))
                if found_ep != episode:
                    return False
            # Check for conflicting other seasons
            other_seasons = re.findall(r"\bs0?(\d{1,2})\b|\bseason\s*0?(\d{1,2})\b", name_lower)
            all_s = [int(s[0] or s[1]) for s in other_seasons if (s[0] or s[1])]
            if all_s and all(s != season for s in all_s):
                return False
            return True

        return False

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


def calculate_relevance_score(
    tor_name: str,
    target_title: str,
    is_series: bool = False,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> int:
    """Compute title relevance score penalizing commentary, foreign dubs, and CAM releases."""
    score = 100
    clean_n = re.sub(r"[._-]", " ", tor_name).lower()
    clean_target = re.sub(r"[._-]", " ", target_title).lower()

    if clean_n.startswith(clean_target):
        score += 50
    elif clean_target in clean_n:
        score += 30

    if is_series and season is not None and episode is not None:
        # Boost exact single-episode release significantly over season packs
        if is_exact_single_episode(tor_name, season, episode):
            score += 35
        else:
            exact_pat = rf"\bs0?{season}[\s._-]*e(?:p)?0?{episode}\b|\b0?{season}x0?{episode}\b"
            if re.search(exact_pat, tor_name.lower()):
                score += 15

    penalty = get_release_penalty(tor_name)
    score -= penalty
    return score


async def search_torrentio(
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    is_series: bool = False,
    target_title: str = "",
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> list[dict]:
    """
    Search Torrentio global aggregator (aggregates 1337x, EZTV, YTS, ThePirateBay,
    KickassTorrents, TorrentGalaxy, MagnetDL, Torrent9, Rutor, etc.).
    Supports both movies and TV series episodes, strictly filtering out 480p/SD/CAM.
    """
    clean_id = (imdb_id or "").strip()
    if not clean_id:
        return []
    if not clean_id.startswith("tt"):
        clean_id = f"tt{clean_id}"

    if is_series or (season is not None and episode is not None):
        s_num = season or 1
        e_num = episode or 1
        urls = [
            f"https://torrentio.strem.fun/qualityfilter=480p,scr,cam,unknown|sizefilter=2.2GB/stream/series/{clean_id}:{s_num}:{e_num}.json",
            f"https://torrentio.strem.fun/stream/series/{clean_id}:{s_num}:{e_num}.json",
        ]
    else:
        urls = [
            f"https://torrentio.strem.fun/qualityfilter=480p,scr,cam,unknown|sizefilter=2.2GB/stream/movie/{clean_id}.json",
            f"https://torrentio.strem.fun/stream/movie/{clean_id}.json",
        ]

    log.info("[TorrentFinder] Torrentio querying: %s (target='%s')", urls[0], target_title)
    candidates: list[dict] = []
    seen_hashes: set[str] = set()
    headers = DEFAULT_HEADERS

    raw_streams: list[dict] = []
    async with httpx.AsyncClient(headers=headers, timeout=12, follow_redirects=True) as client:
        for url in urls:
            for attempt in range(2):
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        st = resp.json().get("streams", [])
                        if st:
                            raw_streams.extend(st)
                        break
                    else:
                        log.warning("[TorrentFinder] Torrentio HTTP %d on %s (attempt %d)", resp.status_code, url, attempt + 1)
                        if attempt == 0:
                            await asyncio.sleep(1.5)
                except Exception as exc:
                    log.warning("[TorrentFinder] Torrentio attempt %d error: %s", attempt + 1, exc)
                    if attempt == 0:
                        await asyncio.sleep(1.5)

    if not raw_streams:
        return []

    try:
        from services.downloader import format_bytes

        for s in raw_streams:
            info_hash = s.get("infoHash", "").strip().lower()
            if not info_hash or len(info_hash) < 8 or info_hash in seen_hashes:
                continue

            tf = s.get("title", "")
            nf = s.get("name", "")
            lines = [line.strip() for line in tf.split("\n") if line.strip()]
            content_lines = [
                l for l in lines
                if not any(icon in l for icon in ("👤", "💾", "⚙️", "🇬🇧", "🇫🇷", "🇪🇸", "🇮🇹", "🇩🇪", "🇷🇺", "🇵🇹", "🇮🇳", "🇸🇦", "🇲🇽", "🇭🇺"))
            ]
            pack_title = content_lines[0] if content_lines else (lines[0] if lines else target_title)
            episode_file = content_lines[1] if len(content_lines) > 1 else pack_title

            if is_junk_release(pack_title) or is_junk_release(episode_file):
                continue

            # Validate title relevance and reject wrong shows or multi-movie collection packs
            if target_title:
                if is_series or season is not None or episode is not None:
                    if not title_matches(pack_title, target_title):
                        continue
                    if not (
                        is_valid_series_title(pack_title, target_title, season, episode)
                        or is_valid_series_title(episode_file, target_title, season, episode)
                    ):
                        continue
                else:
                    if not title_matches(pack_title, target_title):
                        continue
                    if is_movie_collection_pack(pack_title, target_title):
                        continue

            # Parse seeders: 👤 (\d+)
            seeds_m = re.search(r"👤\s*(\d+)", tf)
            seeds = int(seeds_m.group(1)) if seeds_m else 0

            # Parse file size: 💾 ([\d.]+)\s*(GB|MB|KB)
            size_m = re.search(r"💾\s*([\d.]+)\s*(GB|MB|KB)", tf, re.IGNORECASE)
            size_bytes = 0
            if size_m:
                val = float(size_m.group(1))
                unit = size_m.group(2).upper()
                if unit == "GB":
                    size_bytes = int(val * 1024 * 1024 * 1024)
                elif unit == "MB":
                    size_bytes = int(val * 1024 * 1024)
                elif unit == "KB":
                    size_bytes = int(val * 1024)

            if size_bytes < MIN_FILE_SIZE_BYTES or size_bytes > max_size_bytes:
                continue

            quality = extract_quality_from_name(f"{nf} {pack_title} {episode_file}")
            # Strictly enforce >= 720p HD quality (reject 480p / SD)
            if quality == "480p":
                continue

            # Parse upstream indexer from ⚙️
            prov_m = re.search(r"⚙️\s*([^\n\r]+)", tf)
            sub_prov = prov_m.group(1).strip() if prov_m else ""
            provider = f"Torrentio ({sub_prov})" if sub_prov and "torrentio" not in sub_prov.lower() else "Torrentio"

            is_season_pack = bool(
                (is_series or season is not None or episode is not None)
                and (len(content_lines) > 1 or is_season_pack_release(pack_title, season, episode))
            )

            if is_season_pack and episode_file != pack_title:
                ep_base = episode_file.replace("\\", "/").split("/")[-1]
                display_name = f"{pack_title} [{ep_base}]"
            else:
                display_name = pack_title

            seen_hashes.add(info_hash)
            magnet = build_magnet_uri(info_hash, pack_title)
            rel_score = calculate_relevance_score(
                display_name,
                target_title or pack_title,
                is_series=is_series,
                season=season,
                episode=episode,
            )

            candidates.append({
                "method": "torrent",
                "provider": provider,
                "title": display_name,
                "quality": quality,
                "size": format_bytes(size_bytes),
                "size_bytes": size_bytes,
                "hash": info_hash,
                "magnet": magnet,
                "torrent_url": "",
                "seeds": seeds,
                "peers": 0,
                "relevance_score": rel_score,
                "file_idx": s.get("fileIdx"),
                "is_season_pack": is_season_pack,
            })

        if candidates:
            log.info("[TorrentFinder] Torrentio yielded %d valid HD candidate(s).", len(candidates))
    except Exception as exc:
        log.warning("[TorrentFinder] Torrentio error: %s", exc)

    return candidates


async def search_apibay(
    query: str,
    target_show_title: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    alternate_queries: Optional[list[str]] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> list[dict]:
    """
    Search The Pirate Bay via the official Apibay JSON API.
    Supports movies (both old and new) and TV series episodes, strictly filtering out 480p/SD.
    """
    candidates: list[dict] = []
    seen_hashes: set[str] = set()

    all_queries = [query]
    if alternate_queries:
        for aq in alternate_queries:
            if aq and aq not in all_queries:
                all_queries.append(aq)

    apibay_urls = [
        "https://apibay.org/q.php",
        "https://piratebay.party/api/q.php",
    ]

    is_series = season is not None or episode is not None or target_show_title is not None
    show_name = target_show_title or query

    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=12, follow_redirects=True) as client:
        for q in all_queries:
            log.info("[TorrentFinder] Apibay searching: '%s'", q)
            for base_url in apibay_urls:
                try:
                    resp = await client.get(base_url, params={"q": q})
                    if resp.status_code != 200:
                        continue

                    items = resp.json()
                    if not isinstance(items, list):
                        continue

                    for it in items:
                        name = it.get("name", "")
                        info_hash = it.get("info_hash", "").strip().lower()
                        if not info_hash or info_hash.startswith("00000000") or it.get("id") == "0":
                            continue
                        if name == "No results returned":
                            continue
                        if info_hash in seen_hashes:
                            continue

                        # Filter junk extensions and multi-movie collection packs
                        if is_junk_release(name):
                            continue
                        if not is_series and is_movie_collection_pack(name, show_name):
                            continue

                        # Series prefix / title validation
                        if is_series:
                            if not is_valid_series_title(name, show_name, season, episode):
                                continue
                        else:
                            if not title_matches(name, show_name):
                                continue

                        quality = extract_quality_from_name(name)
                        # Strictly reject 480p / SD torrents
                        if quality == "480p":
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

                        is_season_pack = is_season_pack_release(name, season, episode) if is_series else False

                        seen_hashes.add(info_hash)
                        magnet = build_magnet_uri(info_hash, name)
                        from services.downloader import format_bytes

                        rel_score = calculate_relevance_score(
                            name,
                            show_name,
                            is_series=is_series,
                            season=season,
                            episode=episode,
                        )
                        if is_season_pack:
                            rel_score -= 60

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
                            "is_season_pack": is_season_pack,
                        })

                    if candidates:
                        log.info("[TorrentFinder] Apibay query '%s' yielded %d valid HD candidate(s).", q, len(candidates))
                        break
                except Exception as exc:
                    log.warning("[TorrentFinder] Apibay error on %s for '%s': %s", base_url, q, exc)
                    continue
            if candidates:
                break

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
    Filters specifically for requested season & episode and >= 720p HD quality.
    """
    log.info("[TorrentFinder] EZTV searching: title='%s', imdb_id=%s, S%sE%s", title, imdb_id, season, episode)
    candidates: list[dict] = []

    eztv_bases = [
        "https://eztvx.to/api/get-torrents",
        "https://eztv.re/api/get-torrents",
    ]

    clean_imdb = (imdb_id or "").lstrip("t")

    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=6.0, follow_redirects=True) as client:
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

                        quality = extract_quality_from_name(tor_title)
                        if quality == "480p":
                            continue

                        try:
                            size_bytes = int(tor.get("size_bytes", 0))
                        except (ValueError, TypeError):
                            size_bytes = 0

                        if size_bytes < MIN_FILE_SIZE_BYTES or size_bytes > max_size_bytes:
                            continue

                        info_hash = tor.get("hash", "").strip().lower()
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

                        from services.downloader import format_bytes

                        rel_score = calculate_relevance_score(
                            tor_title,
                            title,
                            is_series=True,
                            season=season,
                            episode=episode,
                        )
                        is_season_pack = is_season_pack_release(tor_title, season, episode)

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
                            "is_season_pack": is_season_pack,
                        })

                if candidates:
                    log.info("[TorrentFinder] EZTV yielded %d valid HD candidate(s).", len(candidates))
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
    alternate_queries: Optional[list[str]] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> list[dict]:
    """
    Search Torrents-CSV public open torrent index.
    Covers movies and TV series with infohash and seeds, strictly filtering out 480p/SD.
    """
    candidates: list[dict] = []
    seen_hashes: set[str] = set()

    all_queries = [query]
    if alternate_queries:
        for aq in alternate_queries:
            if aq and aq not in all_queries:
                all_queries.append(aq)

    is_series = season is not None or episode is not None or target_show_title is not None
    show_name = target_show_title or query

    url = "https://torrents-csv.com/service/search"
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10, follow_redirects=True) as client:
        for q in all_queries:
            log.info("[TorrentFinder] Torrents-CSV searching: '%s'", q)
            try:
                resp = await client.get(url, params={"q": q})
                if resp.status_code == 200:
                    data = resp.json()
                    torrents = data.get("torrents", [])
                    for it in torrents:
                        name = it.get("name", "")
                        info_hash = it.get("infohash", "").strip().lower()
                        if not info_hash or len(info_hash) != 40:
                            continue
                        if info_hash in seen_hashes:
                            continue

                        # Filter junk extensions and movie collection packs
                        if is_junk_release(name):
                            continue
                        if not is_series and is_movie_collection_pack(name, show_name):
                            continue

                        # Series prefix / title validation
                        if is_series:
                            if not is_valid_series_title(name, show_name, season, episode):
                                continue
                        else:
                            if not title_matches(name, show_name):
                                continue

                        quality = extract_quality_from_name(name)
                        if quality == "480p":
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

                        is_season_pack = is_season_pack_release(name, season, episode) if is_series else False

                        seen_hashes.add(info_hash)
                        magnet = build_magnet_uri(info_hash, name)
                        from services.downloader import format_bytes

                        rel_score = calculate_relevance_score(
                            name,
                            show_name,
                            is_series=is_series,
                            season=season,
                            episode=episode,
                        )
                        if is_season_pack:
                            rel_score -= 60

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
                            "is_season_pack": is_season_pack,
                        })

                    if candidates:
                        log.info("[TorrentFinder] Torrents-CSV query '%s' yielded %d valid HD candidate(s).", q, len(candidates))
                        break
            except Exception as exc:
                log.warning("[TorrentFinder] Torrents-CSV error on '%s': %s", q, exc)

    return candidates


async def resolve_imdb_cinemeta(title: str, is_series: bool = False) -> Optional[str]:
    """Auto-resolve IMDb ID using Cinemeta public metadata API (no API key required)."""
    m_type = "series" if is_series else "movie"
    url = f"https://v3-cinemeta.strem.io/catalog/{m_type}/top/search={urllib.parse.quote(title)}.json"
    try:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=4.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                metas = resp.json().get("metas", [])
                if metas and metas[0].get("id"):
                    return metas[0]["id"]
    except Exception as exc:
        log.debug("[TorrentFinder] Cinemeta IMDb resolution error for '%s': %s", title, exc)
    return None


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
    - If TV series: Torrentio (1337x/EZTV/Galaxy/RARBG/TPB) + EZTV + Torrents-CSV + Apibay
    - If Movie: YTS + Torrentio + Torrents-CSV + Apibay

    Strictly enforces:
    - TV Series: >= 720p quality (1080p & 720p HD only; 480p/SD is strictly rejected)
    - Movies: 1080p quality strictly prioritized (480p/SD is strictly rejected)
    - Single-episode / single-file Seedr-compatible releases prioritized ahead of season packs
    """
    clean_title = re.sub(r"[._-]", " ", title).strip()
    series_flag = bool(is_series or season is not None or episode is not None)

    # If imdb_id is missing, auto-resolve it via Cinemeta so Torrentio can be leveraged
    if not imdb_id:
        imdb_id = await resolve_imdb_cinemeta(clean_title, is_series=series_flag)
        if imdb_id:
            log.info("[TorrentFinder] Resolved IMDb ID for '%s': %s", clean_title, imdb_id)

    tasks = []

    if series_flag:
        # Construct specific search query for series episode
        ep_tag = ""
        alt_ep_tag = ""
        spaced_ep_tag = ""
        if season is not None and episode is not None:
            ep_tag = f"S{season:02d}E{episode:02d}"
            alt_ep_tag = f"{season}x{episode:02d}"
            spaced_ep_tag = f"S{season:02d} E{episode:02d}"
        elif season is not None:
            ep_tag = f"S{season:02d}"
        elif episode is not None:
            ep_tag = f"E{episode:02d}"

        series_query = f"{clean_title} {ep_tag}".strip()
        alt_series_queries = []
        if ep_tag:
            for q_cand in (
                f"{clean_title} {ep_tag} 1080p",
                f"{clean_title} {ep_tag} 720p",
                f"{clean_title} {spaced_ep_tag}".strip() if spaced_ep_tag else "",
                f"{clean_title} {alt_ep_tag}".strip() if alt_ep_tag else "",
            ):
                if q_cand and q_cand not in alt_series_queries and q_cand != series_query:
                    alt_series_queries.append(q_cand)
        if season is not None and episode is None:
            s_q = f"{clean_title} S{season:02d}"
            if s_q not in alt_series_queries and s_q != series_query:
                alt_series_queries.append(s_q)

        # 1. EZTV API (specialized TV indexer with .torrent URLs)
        tasks.append(search_eztv(clean_title, imdb_id=imdb_id, season=season, episode=episode, max_size_bytes=max_size_bytes))
        # 2. Torrentio (if imdb_id) - Highest quality multi-tracker aggregator
        if imdb_id:
            tasks.append(search_torrentio(
                imdb_id=imdb_id,
                season=season,
                episode=episode,
                is_series=True,
                target_title=clean_title,
                max_size_bytes=max_size_bytes,
            ))
        # 3. Torrents-CSV
        tasks.append(search_torrents_csv(series_query, target_show_title=clean_title, season=season, episode=episode, alternate_queries=alt_series_queries, max_size_bytes=max_size_bytes))
        # 4. Apibay
        tasks.append(search_apibay(series_query, target_show_title=clean_title, season=season, episode=episode, alternate_queries=alt_series_queries, max_size_bytes=max_size_bytes))
    else:
        # Movie search
        movie_query = f"{clean_title} {year}" if year else clean_title
        alt_movie_queries = [f"{movie_query} 1080p", clean_title] if year else [f"{clean_title} 1080p"]

        # 1. YTS (First so official YTS .torrent URLs and metadata are preserved on hash deduplication)
        tasks.append(method_yts.search(clean_title, year=year, imdb_id=imdb_id, max_size_bytes=max_size_bytes))
        # 2. Torrentio (if imdb_id)
        if imdb_id:
            tasks.append(search_torrentio(
                imdb_id=imdb_id,
                is_series=False,
                target_title=clean_title,
                max_size_bytes=max_size_bytes,
            ))
        # 3. Torrents-CSV
        tasks.append(search_torrents_csv(movie_query, alternate_queries=alt_movie_queries, max_size_bytes=max_size_bytes))
        # 4. Apibay
        tasks.append(search_apibay(movie_query, alternate_queries=alt_movie_queries, max_size_bytes=max_size_bytes))

    results_lists = await asyncio.gather(*tasks, return_exceptions=True)

    all_torrents: list[dict] = []
    hash_to_tor: dict[str, dict] = {}

    for res in results_lists:
        if isinstance(res, list):
            for tor in res:
                q_str = tor.get("quality", "").lower()
                # Strictly exclude 480p / SD across all sources
                if "480" in q_str:
                    continue

                tor.setdefault("provider", "YTS" if tor.get("method") == "yts" else "Torrent")
                if "relevance_score" not in tor:
                    tor["relevance_score"] = calculate_relevance_score(
                        tor.get("title", ""),
                        clean_title,
                        is_series=series_flag,
                        season=season,
                        episode=episode,
                    )
                if "is_season_pack" not in tor:
                    tor["is_season_pack"] = is_season_pack_release(tor.get("title", ""), season, episode) if series_flag else False

                h = tor.get("hash", "").strip().lower()
                if h and h in hash_to_tor:
                    existing = hash_to_tor[h]
                    # Merge missing torrent_url or file_idx or higher seed count from duplicate indexer
                    if tor.get("torrent_url") and not existing.get("torrent_url"):
                        existing["torrent_url"] = tor["torrent_url"]
                    if tor.get("file_idx") is not None and existing.get("file_idx") is None:
                        existing["file_idx"] = tor["file_idx"]
                    if tor.get("seeds", 0) > existing.get("seeds", 0):
                        existing["seeds"] = tor["seeds"]
                    if existing.get("provider") == "ThePirateBay" and tor.get("provider") != "ThePirateBay":
                        existing["provider"] = tor["provider"]
                    continue

                if h:
                    hash_to_tor[h] = tor
                all_torrents.append(tor)

    log.info("[TorrentFinder] Total HD torrent candidates collected: %d", len(all_torrents))

    def _rank_torrent(tor: dict) -> tuple[int, int, int, int, int, int, int]:
        sz = tor.get("size_bytes", 0)
        # Tier 2: 50MB <= sz <= 1.95GB (100% Seedr cloud compatible)
        # Tier 1: 1.95GB < sz <= max_size_bytes (<= 2.05GB / PikPak / FFmpeg Smart 1080p)
        # Tier 0: < 50MB or > max_size_bytes
        if MIN_FILE_SIZE_BYTES <= sz <= SEEDR_SAFE_SIZE_BYTES:
            seedr_tier = 2
        elif SEEDR_SAFE_SIZE_BYTES < sz <= max_size_bytes:
            seedr_tier = 1
        else:
            seedr_tier = 0

        rel_score = tor.get("relevance_score", 0)
        # 1. Clean release check: genuine English/matching release without commentary/Rifftrax/CAM/foreign dub penalties
        is_clean = 1 if rel_score >= 80 else 0

        # 2. Active seeds check: alive torrents (>0 seeds) always rank above dead 0-seed torrents
        seeds = tor.get("seeds", 0)
        has_seeds = 1 if seeds > 0 else 0

        # 3. Single-episode / single-movie release check:
        # True single-episode releases (not season packs) can be downloaded directly by Seedr (2GB cap) AND aria2c
        is_single_release = 0 if tor.get("is_season_pack", False) else 1

        # 4. Quality score: 1080p strictly prioritized above 720p, and both above 4K
        q = tor.get("quality", "").lower()
        if "1080" in q:
            q_score = 4
        elif "720" in q:
            q_score = 3
        elif "2160" in q or "4k" in q:
            q_score = 2
        else:
            q_score = 1

        # 5. Provider reliability tier: prefer YTS / EZTV / Torrentio / TorrentsCSV over raw ThePirateBay
        prov = tor.get("provider", "").lower()
        if "yts" in prov or "eztv" in prov or "torrentio" in prov:
            prov_tier = 2
        elif "torrentscsv" in prov:
            prov_tier = 1
        else:
            prov_tier = 0

        return (is_clean, has_seeds, is_single_release, seedr_tier, q_score, seeds, prov_tier)

    all_torrents.sort(key=_rank_torrent, reverse=True)
    return all_torrents
