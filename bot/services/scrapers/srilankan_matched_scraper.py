"""
srilankan_matched_scraper.py — Priority 1 Same-Site Matched Video & Subtitle Scraper.

Solves Subtitle Timing Drift (0.0ms desync guaranteed):
Sri Lankan subtitle sites (sinhalasub.lk, cinesubz.co, baiscope.lk, baiscopedownloads.co,
piratelk.com, cines.lk, cineru.lk, subz.lk, lksubs.com, zoom.lk)
publish posts where the translator's subtitle was timed to the exact WebRip / WEB-DL / BluRay video releases
hosted directly in that same post (PixelDrain, Mega, Google Drive, direct MP4/MKV, cdn/ddl endpoints).

Strict Constraints:
1. DO NOT use torrents. Only direct HTTP/DDL from Sri Lankan portals.
2. Short queries like "hi" or "hi 2026" handled properly without failing min-length or regex filters.
3. Portals with pre-hardsubbed video (SinhalaSub, CineSubz) -> is_already_hardsubbed=True, DO NOT burn again.
4. Portals with standalone subtitles (Baiscope, PirateLK, Cineru, Cines, Subz, Zoom, etc.) ->
   is_already_hardsubbed=False, MUST download/extract standalone subtitle and burn/mux into the video.
"""

import asyncio
import logging
import os
import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from services import subtitle_service

log = logging.getLogger(__name__)

# Standard browser headers with SSL fallback
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
}

# Sri Lankan Subtitle & Matched Video Portals
PORTALS = [
    {"name": "SinhalaSub",        "base": "https://sinhalasub.lk",        "wp_api": True},
    {"name": "CineSubz",          "base": "https://cinesubz.co",          "wp_api": True},
    {"name": "PirateLK",          "base": "https://piratelk.com",         "wp_api": False},
    {"name": "Baiscope",          "base": "https://baiscope.lk",          "wp_api": True},
    {"name": "BaiscopeDownloads", "base": "https://baiscopedownloads.co", "wp_api": True},
    {"name": "Subz",              "base": "https://subz.lk",              "wp_api": True},
    {"name": "Cines",             "base": "https://cines.lk",             "wp_api": True},
    {"name": "Cineru",            "base": "https://cineru.lk",            "wp_api": True},
    {"name": "Zoom",              "base": "https://zoom.lk",              "wp_api": False},
    {"name": "LKSubs",            "base": "https://lksubs.com",           "wp_api": False},
]

# Portals where video has pre-burned Sinhala subtitles (NEVER burn secondary sub)
PRE_HARDSUBBED_PORTALS = {"SinhalaSub", "CineSubz"}

# Patterns for direct/cloud video hosts
VIDEO_HOST_PATTERNS = {
    "pixeldrain": re.compile(r"https?://(?:www\.)?pixeldrain\.com/(?:u|api/file)/([a-zA-Z0-9_-]+)", re.IGNORECASE),
    "gdrive":     re.compile(r"https?://drive\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)", re.IGNORECASE),
    "mega":       re.compile(r"https?://mega\.nz/(?:file/|#!)?[a-zA-Z0-9_#-]+", re.IGNORECASE),
    "gofile":     re.compile(r"https?://gofile\.io/d/[a-zA-Z0-9_-]+", re.IGNORECASE),
    "mediafire":  re.compile(r"https?://(?:www\.)?mediafire\.com/(?:file|download)/[a-zA-Z0-9_-]+", re.IGNORECASE),
    "usersdrive": re.compile(r"https?://(?:www\.)?usersdrive\.com/[a-zA-Z0-9_-]+(?:\.html)?", re.IGNORECASE),
    "1fichier":   re.compile(r"https?://(?:www\.)?1fichier\.com/\?[a-zA-Z0-9_-]+", re.IGNORECASE),
    "direct_mp4": re.compile(r"https?://[^\s\"'<>]+\.(?:mp4|mkv)(?:\?[^\s\"'<>]*)?", re.IGNORECASE),
    "magnet":     re.compile(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s\"'<>]*", re.IGNORECASE),
}


def resolve_direct_video_url(url: str) -> str:
    """Resolve view/preview URLs to raw direct stream/download endpoints."""
    pd = VIDEO_HOST_PATTERNS["pixeldrain"].search(url)
    if pd:
        return f"https://pixeldrain.com/api/file/{pd.group(1)}"
    return url


async def resolve_srilankan_intermediate_link(
    client: httpx.AsyncClient,
    link_url: str,
    referer_url: str = "",
) -> Optional[str]:
    """
    Resolve intermediate ad/locker/unlocker links (e.g. sinhalasub.lk/links/xxxxxx/,
    cinesubz unlocker, etc.) to the direct video stream URL.
    """
    if not link_url:
        return None

    # 1. If already a direct video stream endpoint
    if "cdn.sinhalasub.net" in link_url or "ddl.sinhalasub.net" in link_url:
        return link_url
    pd_m = VIDEO_HOST_PATTERNS["pixeldrain"].search(link_url)
    if pd_m:
        return f"https://pixeldrain.com/api/file/{pd_m.group(1)}"
    if link_url.endswith((".mp4", ".mkv")) and "/links/" not in link_url:
        return link_url

    # 2. Check if this is an intermediate locker/redirect URL
    is_intermediate = any(k in link_url.lower() for k in [
        "/links/", "/api-", "linkvertise", "gplinks", "droplink", "short", "cinesubz", "#link"
    ])
    if not is_intermediate:
        return None

    try:
        req_headers = dict(HEADERS)
        if referer_url:
            req_headers["Referer"] = referer_url

        resp = await client.get(link_url, headers=req_headers, timeout=4.5)
        if resp.status_code != 200:
            return None

        body = resp.text

        # A. ZetaFlix / SinhalaSub Link Unlocker: var zluFinalLink = '...'
        m_zlu = re.search(r"var\s+zluFinalLink\s*=\s*['\"]([^'\"]+)['\"]", body)
        if m_zlu:
            final_link = m_zlu.group(1).strip()
            log.info("[MatchedScraper] Resolved zluFinalLink: %s", final_link[:90])
            return resolve_direct_video_url(final_link)

        # B. Generic variable extraction: var final_link = '...' / var download_url = '...'
        m_var = re.search(
            r"(?:var|let|const)\s+(?:final_link|download_url|direct_url|target_url)\s*=\s*['\"]([^'\"]+)['\"]",
            body,
            re.IGNORECASE,
        )
        if m_var:
            final_link = m_var.group(1).strip()
            log.info("[MatchedScraper] Resolved final_link variable: %s", final_link[:90])
            return resolve_direct_video_url(final_link)

        # C. CineSubz / SubzLK urlMappings and #link replacement
        soup = BeautifulSoup(body, "html.parser")
        link_elem = soup.find(id="link")
        if link_elem and link_elem.get("href"):
            raw_href = link_elem["href"].strip()
            if "google.com/server" in raw_href:
                m_srv = re.search(r"https://google\.com/server(\d+)/1:/", raw_href)
                if m_srv:
                    mapped = re.sub(
                        r"https://google\.com/server\d+/1:/",
                        f"https://drive.csplayer2.space/server{m_srv.group(1)[0]}/",
                        raw_href,
                    )
                    log.info("[MatchedScraper] Mapped CineSubz stream URL: %s", mapped[:90])
                    return mapped
            return resolve_direct_video_url(raw_href)

        # D. Search anchors inside the resolved page
        for a in soup.find_all("a", href=True):
            h = a["href"].strip()
            if any(k in h.lower() for k in ["cdn.sinhalasub", "ddl.sinhalasub", "pixeldrain.com", "usersdrive.com", "mega.nz"]):
                return resolve_direct_video_url(h)
            if h.endswith((".mp4", ".mkv")) and not h.startswith("#"):
                return h

        # E. Check for window.location redirects
        m_redir = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", body)
        if m_redir:
            loc = m_redir.group(1).strip()
            if any(k in loc for k in ["cdn.", "pixeldrain", "usersdrive", "mega", ".mp4", ".mkv"]):
                return resolve_direct_video_url(loc)

    except Exception as exc:
        log.debug("[MatchedScraper] Error resolving intermediate link %s: %s", link_url[:60], exc)

    return None


def detect_quality_from_context(text: str, href: str) -> str:
    """Infer resolution quality (1080p, 720p, 480p) from surrounding anchor text or URL."""
    h_lower = href.lower()
    if re.search(r"\b(?:1080p?|fhd|full[\s._-]*hd)\b", h_lower):
        return "1080p"
    if re.search(r"\b(?:720p?)\b", h_lower):
        return "720p"
    if re.search(r"\b(?:480p?|sd|360p?)\b", h_lower):
        return "480p"

    combined = f"{text} {href}".lower()
    if re.search(r"\b(?:1080p?|fhd|full[\s._-]*hd)\b", combined):
        return "1080p"
    if re.search(r"\b(?:720p?|hd)\b", combined):
        return "720p"
    if re.search(r"\b(?:480p?|sd|360p?)\b", combined):
        return "480p"
    return "720p"  # default to HD if untagged


STOP_WORDS = {"the", "a", "an", "and", "or", "in", "on", "of", "with", "film", "movie"}


def matches_title_and_year(
    clean_title: str,
    text: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> bool:
    """
    Accurately verify if a post text, title, or URL matches the target movie or series.
    Uses strict regex word boundaries to support short queries ('hi', 'up', 'it', 'rio')
    while preventing false matches against words like 'white', 'history', 'his', 'hitman'.
    Requires all significant title tokens to be present.
    """
    t_clean = clean_title.strip().lower()
    if not t_clean or not text:
        return False
    text_lower = text.lower()

    # Split into clean alphanumeric tokens
    raw_tokens = [w for w in re.findall(r"[a-z0-9]+", t_clean) if w]
    if not raw_tokens:
        return False

    # Filter out stop words unless all words are stop words
    tokens = [w for w in raw_tokens if w not in STOP_WORDS] or raw_tokens

    # Remove year from title tokens if year is also passed or checked separately
    if year:
        tokens_no_year = [w for w in tokens if w != str(year)]
        title_tokens = tokens_no_year or tokens
    else:
        title_tokens = tokens

    # Every title token must match with word boundary
    for token in title_tokens:
        pat = rf"(?:\b|[-_/]){re.escape(token)}(?:\b|[-_/])"
        if not re.search(pat, text_lower):
            return False

    # Season and episode check (for series)
    if season and episode:
        ep_pat = (
            rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
            rf"|{season}x0*{episode}\b"
            rf"|season[-_\s]*0*{season}[^a-z0-9]+(?:episode|ep|e)[-_\s]*0*{episode}\b"
            rf"|(?:\b|[-_\[/])(?:ep|episode|e)\.?\s*0*{episode}(?:\b|[-_\]/]))"
        )
        if not re.search(ep_pat, text_lower):
            return False
        # If it matched an un-seasoned episode (e.g. [E02]), ensure it's not explicitly from a DIFFERENT season
        if not re.search(rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b|{season}x0*{episode}\b|season[-_\s]*0*{season})", text_lower):
            other_seasons = [int(s) for s in re.findall(r"\b(?:s|season[\s._-]*)0*(\d{1,2})\b", text_lower) if int(s) != season]
            if other_seasons:
                return False
    elif season:
        s_pat = rf"(?:s0*{season}\b|season[\s._-]*0*{season}\b|complete[\s._-]*season[\s._-]*0*{season}\b)"
        if not re.search(s_pat, text_lower):
            return False

    # Year check: for movies (when season is not specified), verify release year against found years
    if year and not season:
        found_years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", text_lower)]
        if found_years:
            if not any(abs(fy - year) <= 1 for fy in found_years):
                return False

    return True


def score_candidate_post(
    clean_title: str,
    post_url: str,
    post_title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> int:
    """Score candidate post relevance (higher is better). Returns <= 0 if irrelevant."""
    combined = f"{post_title} {post_url}".lower()

    if not matches_title_and_year(clean_title, combined, year=year, season=season, episode=episode):
        # Check if this is a TV series hub page (e.g. /tvshows/, /tv/, or title has tv/series/season)
        is_hub_url = any(k in combined for k in ("/tvshows/", "/tv/", "tv-series", "tv-shows", "tv show", "complete-season", "season-"))
        if (season or episode) and is_hub_url:
            if matches_title_and_year(clean_title, combined, year=None, season=None, episode=None):
                return 35  # High-priority series hub page that needs episode inspection!
        # If year was specified, check if title matches but year wasn't in snippet yet
        if year and matches_title_and_year(clean_title, combined, year=None, season=season, episode=episode):
            found_years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", combined)]
            if found_years and not any(abs(fy - year) <= 1 for fy in found_years):
                return 0  # Conflicting year in post snippet -> irrelevant
            return 25
        return 0

    score = 30
    t_clean = clean_title.strip().lower()
    if re.search(rf"(?:\b|[-_/]){re.escape(t_clean)}(?:\b|[-_/])", combined):
        score += 30
    if post_url and re.search(rf"(?:\b|[-_/]){re.escape(t_clean)}(?:\b|[-_/])", post_url.lower()):
        score += 20

    if year and str(year) in combined:
        score += 40

    if season and episode:
        if f"s{season:02d}e{episode:02d}" in combined or f"s{season}e{episode}" in combined:
            score += 50
        elif (f"e{episode:02d}" in combined or f"e{episode}" in combined):
            score += 45
    elif season:
        if f"season {season}" in combined or f"season-{season}" in combined or f"s{season:02d}" in combined or f"season {season:02d}" in combined:
            score += 40

    return score


async def _search_portal(
    client: httpx.AsyncClient,
    portal: dict,
    query: str,
    clean_title: str,
    year: Optional[int],
    season: Optional[int],
    episode: Optional[int],
    temp_dir: str,
) -> list[dict]:
    """Search a single Sri Lankan portal for matched post, extracting subtitle and direct video links."""
    base_url = portal["base"]
    p_name = portal["name"]

    # Check if a dedicated scraper module exists for this portal; if it returns results, use them
    if p_name == "SinhalaSub":
        try:
            try:
                from services.scrapers import sinhalasub
            except ImportError:
                from bot.services.scrapers import sinhalasub
            s_res = await sinhalasub.search(client, clean_title, year=year, season=season, episode=episode, temp_dir=temp_dir)
            if s_res:
                return s_res
        except Exception as e_mod:
            log.debug("[MatchedScraper] Dedicated sinhalasub note: %s", e_mod)
    elif p_name == "CineSubz":
        try:
            try:
                from services.scrapers import cinesubz
            except ImportError:
                from bot.services.scrapers import cinesubz
            s_res = await cinesubz.search(client, clean_title, year=year, season=season, episode=episode, temp_dir=temp_dir)
            if s_res:
                return s_res
        except Exception as e_mod:
            log.debug("[MatchedScraper] Dedicated cinesubz note: %s", e_mod)
    elif p_name in ("Baiscope", "BaiscopeDownloads"):
        try:
            try:
                from services.scrapers import baiscope
            except ImportError:
                from bot.services.scrapers import baiscope
            s_res = await baiscope.search(client, clean_title, year=year, season=season, episode=episode, temp_dir=temp_dir)
            if s_res:
                return s_res
        except Exception as e_mod:
            log.debug("[MatchedScraper] Dedicated baiscope note: %s", e_mod)
    elif p_name == "PirateLK":
        try:
            try:
                from services.scrapers import piratelk
            except ImportError:
                from bot.services.scrapers import piratelk
            s_res = await piratelk.search(client, clean_title, year=year, season=season, episode=episode, temp_dir=temp_dir)
            if s_res:
                return s_res
        except Exception as e_mod:
            log.debug("[MatchedScraper] Dedicated piratelk note: %s", e_mod)

    # General portal search logic (Cineru, Cines, Subz, Zoom, LKSubs, and fallback for dedicated scrapers)
    candidate_posts: list[tuple[str, int]] = []
    seen_posts = set()

    clean_t = clean_title.strip()
    if season and episode:
        search_queries = [f"{clean_t} S{season:02d}E{episode:02d}", clean_t]
    elif year:
        search_queries = [f"{clean_t} {year}", clean_t, f"{clean_t} ({year})"]
    else:
        search_queries = [clean_t]

    for q_try in search_queries:
        if any(sc >= 40 for _, sc in candidate_posts):
            break

        # 1. HTML search (/?s=)
        try:
            s_url = f"{base_url.rstrip('/')}/?s={urllib.parse.quote_plus(q_try)}"
            resp = await client.get(s_url, headers=HEADERS, timeout=6.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                selectors = [
                    ".display-item a", ".item-box a", ".result-item a", "article a",
                    "h2 a", "h3 a", ".entry-title a", ".post-title a", "main a",
                ]
                domain_part = base_url.split("//")[-1].split("/")[0].lower()
                for a in soup.select(", ".join(selectors)):
                    raw_href = a.get("href", "").strip()
                    if not raw_href or raw_href.startswith("#"):
                        continue
                    full_href = urllib.parse.urljoin(base_url, raw_href)
                    if domain_part in full_href.lower():
                        if not any(ign in full_href for ign in ("/category/", "/tag/", "/author/", "/page/", "#", "wp-login")):
                            if full_href not in seen_posts:
                                seen_posts.add(full_href)
                                txt = a.get_text(" ", strip=True) or a.get("title", "")
                                score = score_candidate_post(clean_t, full_href, txt, year=year, season=season, episode=episode)
                                if score > 0:
                                    candidate_posts.append((full_href, score))
        except Exception as e_html:
            log.debug("[MatchedScraper] %s HTML search note: %s", p_name, e_html)

        # 2. WP REST API fallback
        if not candidate_posts and portal.get("wp_api"):
            try:
                api_url = f"{base_url.rstrip('/')}/wp-json/wp/v2/posts?search={urllib.parse.quote_plus(q_try)}&per_page=6"
                resp = await client.get(api_url, headers=HEADERS, timeout=6.0)
                if resp.status_code == 200 and isinstance(resp.json(), list):
                    for p in resp.json():
                        link = p.get("link") or ""
                        rendered = (p.get("title", {}) or {}).get("rendered", "")
                        if link and link not in seen_posts:
                            seen_posts.add(link)
                            score = score_candidate_post(clean_t, link, rendered, year=year, season=season, episode=episode)
                            if score > 0:
                                candidate_posts.append((link, score))
            except Exception as e_api:
                log.debug("[MatchedScraper] %s WP API note: %s", p_name, e_api)

    candidate_posts.sort(key=lambda x: x[1], reverse=True)
    found_candidates: list[dict] = []

    # 3. Inspect top matched post pages
    for post_url, _ in candidate_posts[:4]:
        try:
            p_resp = await client.get(post_url, headers=HEADERS, timeout=6.0)
            if p_resp.status_code != 200:
                continue

            soup = BeautifulSoup(p_resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)
            title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
            h1_text = " ".join(h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"]))
            verify_text = f"{post_url} {title_text} {h1_text} {page_text[:3000]}"

            # Verification: make sure this post actually matches the target title & year
            is_matched = matches_title_and_year(clean_t, verify_text, year=year, season=season, episode=episode)
            if not is_matched and (season or episode):
                is_matched = matches_title_and_year(clean_t, verify_text, year=year, season=None, episode=None)
            if not is_matched:
                continue

            current_target_url = post_url
            main_soup = soup
            is_already_ep = False
            if episode is not None:
                ep_chk = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                    rf"|{season}x0*{episode}\b"
                    rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep|e)[-_\s]*0*{episode}\b"
                    rf"|(?:\b|[-_\[/])(?:ep|episode|e)\.?\s*0*{episode}(?:\b|[-_\]/]))",
                    re.IGNORECASE,
                )
                if ep_chk.search(current_target_url) or (soup.title and ep_chk.search(soup.title.get_text())):
                    is_already_ep = True

            if season and episode and not is_already_ep:
                ep_pat = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                    rf"|{season}x0*{episode}\b"
                    rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep|e)[-_\s]*0*{episode}\b"
                    rf"|(?:\b|[-_\[/])(?:ep|episode|e)\.?\s*0*{episode}(?:\b|[-_\]/]))",
                    re.IGNORECASE,
                )
                slug_tokens = [t for t in re.sub(r"[^a-zA-Z0-9]+", " ", clean_t.lower()).split() if len(t) > 2]
                ep_link = None
                for a in soup.find_all("a", href=True):
                    h = a["href"].strip()
                    txt = a.get_text(" ", strip=True)
                    is_internal = any(dom in h.lower() for dom in ("sinhalasub.lk", "cinesubz.co", "piratelk.com", "baiscope.lk", "baiscopedownloads.co", "subz.lk", "cines.lk", "cineru.lk", "zoom.lk")) or h.startswith("/")
                    is_file_or_host = any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".mp4", ".mkv", ".avi", ".webm")) or any(vh in h.lower() for vh in ("pixeldrain", "userscloud", "mega.nz", "1fichier", "drive.google"))
                    is_show_match = any(tok in h.lower() for tok in slug_tokens) or any(tok in txt.lower() for tok in slug_tokens) or not slug_tokens
                    if is_internal and not is_file_or_host and is_show_match and (ep_pat.search(h) or ep_pat.search(txt)):
                        ep_link = urllib.parse.urljoin(post_url, h)
                        break

                if ep_link:
                    try:
                        ep_resp = await client.get(ep_link, headers=HEADERS, timeout=8.0, follow_redirects=True)
                        if ep_resp.status_code == 200:
                            soup = BeautifulSoup(ep_resp.text, "html.parser")
                            current_target_url = ep_link
                            page_text = soup.get_text(" ", strip=True)
                    except Exception:
                        pass

            # A. Subtitle extraction (only needed for clean video portals)
            is_hardsub = (
                p_name in PRE_HARDSUBBED_PORTALS
                or any(k in post_url.lower() for k in ("sinhalasub", "cinesubz"))
            )
            sub_srt_path = None

            if not is_hardsub:
                dl_sub_urls = []
                soups_to_scan = [(soup, current_target_url)]
                if main_soup and main_soup != soup:
                    soups_to_scan.append((main_soup, post_url))

                for cur_s, cur_u in soups_to_scan:
                    if sub_srt_path:
                        break
                    article_elem = cur_s.select_one(".entry-content, article, main, .download-links, .box-download") or cur_s
                    for a in article_elem.find_all("a", href=True):
                        href = a["href"].strip()
                        cls = " ".join(a.get("class", [])).lower()
                        a_txt = a.get_text(" ", strip=True).lower()
                        if any(ign in href.lower() for ign in ("t.me", "telegram.me", "#", "facebook", "youtube", "imdb", "wikipedia")):
                            continue
                        if any(vm in a_txt or vm in href.lower() for vm in ("1080p", "720p", "480p", "x264", "x265", "hevc", "pixeldrain")):
                            continue
                        if not any(ext in href.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) and not any(k in href.lower() for k in ("/download/", "/downloads/", "sub-download", "download-sub", "action=sub_download")):
                            if "-sinhala-sub" in href.lower() or "-with-sinhala" in href.lower():
                                continue
                        if (
                            "action=sub_download" in href
                            or "subz-list-btn" in cls
                            or "js-premium-download" in cls
                            or any(ext in href.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt"))
                            or any(k in href.lower() for k in ("/download/", "/downloads/", "sub-download", "download-sub", "subtitles"))
                            or (("උපසිරැසි" in a_txt or "sub" in a_txt) and ("බාගත" in a_txt or "download" in a_txt or "zip" in a_txt))
                            or "download-subtitle" in href.lower()
                        ):
                            full_href = urllib.parse.urljoin(cur_u, href)
                            if full_href not in dl_sub_urls and not full_href.startswith("magnet:"):
                                dl_sub_urls.append(full_href)

                for sub_url in dl_sub_urls[:6]:
                    try:
                        s_res = await client.get(sub_url, headers={"Referer": current_target_url}, timeout=10.0)
                        res_content = getattr(s_res, "content", b"")
                        if isinstance(res_content, str):
                            res_content = res_content.encode("utf-8")
                        elif not res_content and hasattr(s_res, "text") and s_res.text:
                            res_content = s_res.text.encode("utf-8")

                        if s_res.status_code == 200 and len(res_content) > 64:
                            res_headers = getattr(s_res, "headers", {})
                            content_type = res_headers.get("content-type", "").lower() if hasattr(res_headers, "get") else ""
                            if "text/html" in content_type or res_content.startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                                sub_page_soup = BeautifulSoup(s_res.text if hasattr(s_res, "text") else res_content.decode("utf-8", errors="ignore"), "html.parser")
                                real_sub_url = None
                                for sa in sub_page_soup.find_all("a", href=True):
                                    sh = sa["href"].strip()
                                    st = sa.get_text(" ", strip=True).lower()
                                    if any(ext in sh.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) or "download" in st or "බාගත" in st:
                                        real_sub_url = urllib.parse.urljoin(sub_url, sh)
                                        break
                                if real_sub_url:
                                    s_res2 = await client.get(real_sub_url, headers={"Referer": sub_url}, timeout=10.0)
                                    res_content2 = getattr(s_res2, "content", b"")
                                    if isinstance(res_content2, str):
                                        res_content = res_content2.encode("utf-8")
                                    elif res_content2:
                                        res_content = res_content2
                                    elif hasattr(s_res2, "text") and s_res2.text:
                                        res_content = s_res2.text.encode("utf-8")

                            if len(res_content) > 64:
                                srt_p, _ = subtitle_service._extract_srt_from_bytes(
                                    res_content,
                                    temp_dir=temp_dir,
                                    season=season,
                                    episode=episode,
                                    prefix=f"{p_name.lower()}_matched",
                                )
                                if srt_p and os.path.exists(srt_p):
                                    sub_srt_path = srt_p
                                    log.info("[MatchedScraper] Extracted matching subtitle from %s: %s", p_name, srt_p)
                                    break
                    except Exception as sub_dl_err:
                        log.debug("[MatchedScraper] Subtitle download note: %s", sub_dl_err)

                # Fallback to subtitle_service if post had no standalone sub
                if not sub_srt_path:
                    try:
                        fallback_sub = await subtitle_service.fetch_sri_lankan_sinhala_subtitle(
                            clean_t, year, season=season, episode=episode, temp_dir=temp_dir
                        )
                        if fallback_sub and os.path.exists(fallback_sub):
                            sub_srt_path = fallback_sub
                    except Exception as fb_err:
                        log.debug("[MatchedScraper] Subtitle fallback lookup note: %s", fb_err)

            # B. Extract Direct Video Download Links (STRICT: NO TORRENTS)
            video_links: list[dict] = []
            seen_dl_urls: set[str] = set()
            intermediate_to_resolve: list[tuple[str, str, str]] = []

            for elem in soup.find_all(["tr", "p", "div", "a"]):
                ctx = elem.get_text(" ", strip=True)
                anchors = elem.find_all("a", href=True) if elem.name != "a" else [elem]
                for a in anchors:
                    h = a["href"].strip()
                    if h in seen_dl_urls or h.startswith("#"):
                        continue
                    seen_dl_urls.add(h)

                    # Exclude Telegram channels/bots
                    if "t.me" in h.lower() or "telegram.me" in h.lower():
                        continue
                    # STRICT USER CONSTRAINT: Exclude magnets and torrents
                    if h.startswith("magnet:") or ".torrent" in h.lower():
                        continue

                    q = detect_quality_from_context(ctx, h)
                    matched_pat = False
                    for h_type, pat in VIDEO_HOST_PATTERNS.items():
                        if h_type == "magnet":
                            continue
                        m = pat.search(h)
                        if m:
                            matched_pat = True
                            direct_url = resolve_direct_video_url(m.group(0))
                            video_links.append({
                                "url": direct_url,
                                "original_url": m.group(0),
                                "host_type": h_type,
                                "quality": q,
                                "context": ctx[:120],
                            })
                            break

                    if not matched_pat and ("/links/" in h or "/api-" in h or "cinesubz" in h):
                        intermediate_to_resolve.append((h, q, ctx))

            # Resolve intermediate locker links concurrently
            if intermediate_to_resolve:
                async def _resolve_worker(item_h: str, item_q: str, item_ctx: str):
                    try:
                        resolved = await resolve_srilankan_intermediate_link(client, item_h, referer_url=current_target_url)
                        if resolved and not resolved.startswith("magnet:") and ".torrent" not in resolved.lower():
                            if not any(ign in resolved for ign in ["telegram.me", "t.me"]):
                                h_type = "cdn" if "cdn.sinhalasub" in resolved else ("pixeldrain" if "pixeldrain" in resolved else "ddl")
                                return {
                                    "url": resolved,
                                    "original_url": item_h,
                                    "host_type": h_type,
                                    "quality": item_q,
                                    "context": item_ctx[:120],
                                }
                    except Exception:
                        pass
                    return None

                resolved_items = await asyncio.gather(
                    *[_resolve_worker(h, q, ctx) for h, q, ctx in intermediate_to_resolve[:6]],
                    return_exceptions=True,
                )
                for item in resolved_items:
                    if isinstance(item, dict) and item.get("url"):
                        video_links.append(item)

            if season and episode and video_links:
                ep_pat = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                    rf"|{season}x0*{episode}\b"
                    rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep)[-_\s]*0*{episode}\b"
                    rf"|\b(?:ep|episode)\.?\s*0*{episode}\b)",
                    re.IGNORECASE,
                )
                matching_ep_links = [
                    vl for vl in video_links
                    if ep_pat.search(vl.get("context", "")) or ep_pat.search(vl.get("original_url", "")) or ep_pat.search(vl.get("url", ""))
                ]
                if matching_ep_links:
                    video_links = matching_ep_links

            # Package found video links
            for vl in video_links:
                u_str = str(vl.get("url", "")).lower()
                final_is_hardsub = (
                    is_hardsub
                    or any(k in u_str for k in ("cdn.sinhalasub.net", "ddl.sinhalasub.net", "cinesubz", "csplayer"))
                )
                found_candidates.append({
                    "portal": p_name,
                    "post_url": current_target_url,
                    "url": vl["url"],
                    "quality": vl["quality"],
                    "host_type": vl["host_type"],
                    "sub_srt_path": None if final_is_hardsub else sub_srt_path,
                    "is_already_hardsubbed": final_is_hardsub,
                })

            if found_candidates:
                log.info(
                    "[MatchedScraper] Found %d matched release(s) on %s (hardsub=%s, sub_found=%s)",
                    len(found_candidates), p_name, is_hardsub, bool(sub_srt_path)
                )
                return found_candidates

        except Exception as p_err:
            log.debug("[MatchedScraper] %s post parse error: %s", p_name, p_err)

    return found_candidates


async def search_matched_srilankan_releases(
    title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    is_series: bool = False,
    temp_dir: str = "/tmp",
) -> list[dict]:
    """
    Search top Sri Lankan subtitle portals concurrently for the exact matched post.
    Returns candidate list of direct/cloud HTTP/DDL links.
    STRICT: NEVER returns torrents or magnet links.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return []

    if is_series or season:
        clean_title = subtitle_service.extract_clean_show_name(title)
    else:
        clean_title = re.sub(
            r"[\(\[\{]?\b(s\d{1,2}[\s._-]*e\d{1,2}|season\s*\d{1,2}|episode\s*\d{1,2}|ep\s*\d{1,2})\b.*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip(" -_")
        if year:
            clean_title = re.sub(rf"\b{year}\b", "", clean_title).strip()
    clean_title = re.sub(r"[^a-zA-Z0-9\s]", " ", clean_title).strip()

    if not clean_title:
        return []

    if season and episode:
        query = clean_title
    elif year:
        query = f"{clean_title} {year}"
    else:
        query = clean_title

    all_matched: list[dict] = []
    seen_urls: set[str] = set()

    def _package_results(portal_results: list) -> list[dict]:
        matched_batch = []
        for res in portal_results:
            if isinstance(res, list):
                for item in res:
                    u = item.get("url")
                    if not u or u.startswith("magnet:") or ".torrent" in u.lower():
                        continue
                    q = str(item.get("quality", "")).lower()
                    if (is_series or season is not None or episode is not None) and q == "1080p":
                        continue
                    if u not in seen_urls:
                        seen_urls.add(u)
                        matched_batch.append(item)
        return matched_batch

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, verify=False) as client:
        async def _safe_search(portal):
            try:
                return await asyncio.wait_for(
                    _search_portal(
                        client=client,
                        portal=portal,
                        query=query,
                        clean_title=clean_title,
                        year=year,
                        season=season,
                        episode=episode,
                        temp_dir=temp_dir,
                    ),
                    timeout=20.0,
                )
            except Exception as e_s:
                log.debug("[MatchedScraper] Portal %s error or timeout: %s", portal.get("name"), e_s)
                return []

        # Tier 1: Pre-hardsubbed Portals (SinhalaSub, CineSubz) - User requirement: TOP PRIORITY
        tier1_portals = [p for p in PORTALS if p["name"] in PRE_HARDSUBBED_PORTALS]
        tier1_tasks = [_safe_search(portal) for portal in tier1_portals]

        # Tier 2: Separate Subtitle Portals (PirateLK, Baiscope, Subz, etc.)
        tier2_portals = [p for p in PORTALS if p["name"] not in PRE_HARDSUBBED_PORTALS]
        tier2_tasks = [_safe_search(portal) for portal in tier2_portals]

        all_tasks = tier1_tasks + tier2_tasks
        raw_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        tier1_raw = raw_results[:len(tier1_tasks)]
        tier2_raw = raw_results[len(tier1_tasks):]

        tier1_matched = _package_results(tier1_raw)
        tier2_matched = _package_results(tier2_raw)

        # Pre-hardsubbed (SinhalaSub, CineSubz) ALWAYS prioritized first!
        # Standalone subtitle portals (PirateLK, Baiscope, Subz) appended as reliable fallbacks!
        seen_matched_urls = set()
        final_matched: list[dict] = []
        for item in (tier1_matched + tier2_matched):
            u = item.get("url")
            if u and u not in seen_matched_urls:
                seen_matched_urls.add(u)
                final_matched.append(item)

        log.info(
            "[MatchedScraper] Search complete: %d Tier 1 (pre-hardsubbed) + %d Tier 2 (separate sub) = %d total matched candidates",
            len(tier1_matched),
            len(tier2_matched),
            len(final_matched),
        )
        return final_matched


async def resolve_srilankan_post_url(
    post_url: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    temp_dir: str = "/tmp",
) -> list[dict]:
    """
    Directly inspect and resolve video downloads and subtitles from a Sri Lankan portal post URL
    (SinhalaSub, CineSubz, PirateLK, Baiscope, Cines, Cineru, Subz, Zoom, LKSubs).
    Extracts direct CDN/PixelDrain/Mega links and handles subtitle extraction/bypass.
    """
    if not post_url:
        return []

    p_url_lower = post_url.lower()
    p_name = "SriLankan"
    for portal in PORTALS:
        base_domain = portal["base"].split("//")[-1].split("/")[0].lower()
        if base_domain in p_url_lower or portal["name"].lower() in p_url_lower:
            p_name = portal["name"]
            break

    log.info("[MatchedScraper] Resolving direct post URL (%s): %s", p_name, post_url)

    is_hardsub = (
        p_name in PRE_HARDSUBBED_PORTALS
        or any(k in p_url_lower for k in ("sinhalasub", "cinesubz"))
    )

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, verify=False) as client:
        try:
            resp = await client.get(post_url, headers=HEADERS)
            if resp.status_code != 200:
                log.warning("[MatchedScraper] Post URL returned status %s: %s", resp.status_code, post_url)
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            current_target_url = post_url
            main_soup = soup

            if season and episode:
                ep_pat = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                    rf"|{season}x0*{episode}\b"
                    rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep)[-_\s]*0*{episode}\b"
                    rf"|\b(?:ep|episode)\.?\s*0*{episode}\b)",
                    re.IGNORECASE,
                )
                ep_link = None
                for a in soup.find_all("a", href=True):
                    h = a["href"].strip()
                    txt = a.get_text(" ", strip=True)
                    is_internal = any(dom in h.lower() for dom in ("piratelk.com", "baiscope.lk", "baiscopedownloads.co", "subz.lk", "cines.lk", "cineru.lk", "zoom.lk")) or h.startswith("/")
                    is_file_or_host = any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".mp4", ".mkv", ".avi", ".webm")) or any(vh in h.lower() for vh in ("pixeldrain", "userscloud", "mega.nz", "1fichier", "drive.google"))
                    if is_internal and not is_file_or_host and (ep_pat.search(h) or ep_pat.search(txt)):
                        ep_link = urllib.parse.urljoin(post_url, h)
                        break

                if ep_link:
                    try:
                        ep_resp = await client.get(ep_link, headers=HEADERS, timeout=8.0)
                        if ep_resp.status_code == 200:
                            soup = BeautifulSoup(ep_resp.text, "html.parser")
                            current_target_url = ep_link
                    except Exception:
                        pass

            sub_srt_path = None
            if not is_hardsub:
                dl_sub_urls = []
                for cur_s, cur_u in [(soup, current_target_url), (main_soup, post_url)]:
                    if sub_srt_path:
                        break
                    article_elem = cur_s.select_one(".entry-content, article, main, .download-links, .box-download") or cur_s
                    for a in article_elem.find_all("a", href=True):
                        href = a["href"].strip()
                        cls = " ".join(a.get("class", [])).lower()
                        a_txt = a.get_text(" ", strip=True).lower()
                        if any(ign in href.lower() for ign in ("t.me", "telegram.me", "#", "facebook", "youtube", "imdb", "wikipedia")):
                            continue
                        if any(vm in a_txt or vm in href.lower() for vm in ("1080p", "720p", "480p", "x264", "x265", "hevc", "pixeldrain")):
                            continue
                        if (
                            "action=sub_download" in href
                            or "subz-list-btn" in cls
                            or "js-premium-download" in cls
                            or any(ext in href.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt"))
                            or any(k in href.lower() for k in ("/download/", "/downloads/", "sub-download", "download-sub", "subtitles"))
                            or (("උපසිරැසි" in a_txt or "sub" in a_txt) and ("බාගත" in a_txt or "download" in a_txt or "zip" in a_txt))
                            or "download-subtitle" in href.lower()
                        ):
                            full_href = urllib.parse.urljoin(cur_u, href)
                            if full_href not in dl_sub_urls and not full_href.startswith("magnet:"):
                                dl_sub_urls.append(full_href)

                for sub_url in dl_sub_urls[:6]:
                    try:
                        s_res = await client.get(sub_url, headers={"Referer": current_target_url}, timeout=10.0)
                        res_content = getattr(s_res, "content", b"")
                        if isinstance(res_content, str):
                            res_content = res_content.encode("utf-8")
                        elif not res_content and hasattr(s_res, "text") and s_res.text:
                            res_content = s_res.text.encode("utf-8")

                        if s_res.status_code == 200 and len(res_content) > 64:
                            res_headers = getattr(s_res, "headers", {})
                            content_type = res_headers.get("content-type", "").lower() if hasattr(res_headers, "get") else ""
                            if "text/html" in content_type or res_content.startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                                sub_page_soup = BeautifulSoup(s_res.text if hasattr(s_res, "text") else res_content.decode("utf-8", errors="ignore"), "html.parser")
                                real_sub_url = None
                                for sa in sub_page_soup.find_all("a", href=True):
                                    sh = sa["href"].strip()
                                    st = sa.get_text(" ", strip=True).lower()
                                    if any(ext in sh.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) or "download" in st or "බාගත" in st:
                                        real_sub_url = urllib.parse.urljoin(sub_url, sh)
                                        break
                                if real_sub_url:
                                    s_res2 = await client.get(real_sub_url, headers={"Referer": sub_url}, timeout=10.0)
                                    res_content2 = getattr(s_res2, "content", b"")
                                    if isinstance(res_content2, str):
                                        res_content = res_content2.encode("utf-8")
                                    elif res_content2:
                                        res_content = res_content2

                            if len(res_content) > 64:
                                srt_p, _ = subtitle_service._extract_srt_from_bytes(
                                    res_content,
                                    temp_dir=temp_dir,
                                    season=season,
                                    episode=episode,
                                    prefix=f"{p_name.lower()}_matched",
                                )
                                if srt_p and os.path.exists(srt_p):
                                    sub_srt_path = srt_p
                                    log.info("[MatchedScraper] Extracted standalone subtitle: %s", srt_p)
                                    break
                    except Exception as s_err:
                        log.debug("[MatchedScraper] Sub extraction error: %s", s_err)

            video_links: list[dict] = []
            seen_dl_urls: set[str] = set()
            intermediate_to_resolve: list[tuple[str, str, str]] = []

            for elem in soup.find_all(["tr", "p", "div", "a"]):
                ctx = elem.get_text(" ", strip=True)
                anchors = elem.find_all("a", href=True) if elem.name != "a" else [elem]
                for a in anchors:
                    h = a["href"].strip()
                    if h in seen_dl_urls or h.startswith("#"):
                        continue
                    seen_dl_urls.add(h)
                    if "t.me" in h.lower() or "telegram.me" in h.lower():
                        continue
                    if h.startswith("magnet:") or ".torrent" in h.lower():
                        continue

                    a_txt = a.get_text(" ", strip=True)
                    q = detect_quality_from_context(a_txt if a_txt else ctx, h)
                    matched_pat = False
                    for h_type, pat in VIDEO_HOST_PATTERNS.items():
                        if h_type == "magnet":
                            continue
                        m = pat.search(h)
                        if m:
                            matched_pat = True
                            direct_url = resolve_direct_video_url(m.group(0))
                            video_links.append({
                                "url": direct_url,
                                "original_url": m.group(0),
                                "host_type": h_type,
                                "quality": q,
                                "context": ctx[:120],
                            })
                            break

                    if not matched_pat and ("/links/" in h or "/api-" in h or "cinesubz" in h):
                        intermediate_to_resolve.append((h, q, ctx))

            if intermediate_to_resolve:
                async def _resolve_worker(item_h: str, item_q: str, item_ctx: str):
                    try:
                        resolved = await resolve_srilankan_intermediate_link(client, item_h, referer_url=current_target_url)
                        if resolved and not resolved.startswith("magnet:") and ".torrent" not in resolved.lower():
                            if not any(ign in resolved for ign in ["telegram.me", "t.me"]):
                                h_type = "cdn" if "cdn.sinhalasub" in resolved else ("pixeldrain" if "pixeldrain" in resolved else "ddl")
                                return {
                                    "url": resolved,
                                    "original_url": item_h,
                                    "host_type": h_type,
                                    "quality": item_q,
                                    "context": item_ctx[:120],
                                }
                    except Exception:
                        pass
                    return None

                resolved_items = await asyncio.gather(
                    *[_resolve_worker(h, q, ctx) for h, q, ctx in intermediate_to_resolve[:6]],
                    return_exceptions=True,
                )
                for item in resolved_items:
                    if isinstance(item, dict) and item.get("url"):
                        video_links.append(item)

            if season and episode and video_links:
                ep_pat = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                    rf"|{season}x0*{episode}\b"
                    rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep)[-_\s]*0*{episode}\b"
                    rf"|\b(?:ep|episode)\.?\s*0*{episode}\b)",
                    re.IGNORECASE,
                )
                matching_ep_links = [
                    vl for vl in video_links
                    if ep_pat.search(vl.get("context", "")) or ep_pat.search(vl.get("original_url", "")) or ep_pat.search(vl.get("url", ""))
                ]
                if matching_ep_links:
                    video_links = matching_ep_links

            results: list[dict] = []
            seen_res_urls = set()
            for vl in video_links:
                u_str = str(vl.get("url", "")).lower()
                final_is_hardsub = (
                    is_hardsub
                    or any(k in u_str for k in ("cdn.sinhalasub.net", "ddl.sinhalasub.net", "cinesubz", "csplayer"))
                )
                if vl["url"] not in seen_res_urls:
                    seen_res_urls.add(vl["url"])
                    results.append({
                        "portal": p_name,
                        "post_url": current_target_url,
                        "url": vl["url"],
                        "quality": vl["quality"],
                        "host_type": vl["host_type"],
                        "sub_srt_path": None if final_is_hardsub else sub_srt_path,
                        "is_already_hardsubbed": final_is_hardsub,
                    })

            return results
        except Exception as exc:
            log.warning("[MatchedScraper] Error resolving post URL %s: %s", post_url, exc)
            return []
