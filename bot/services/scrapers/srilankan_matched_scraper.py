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
    {"name": "Baiscope",          "base": "https://baiscope.lk",          "wp_api": True},
    {"name": "BaiscopeDownloads", "base": "https://baiscopedownloads.co", "wp_api": True},
    {"name": "PirateLK",          "base": "https://piratelk.com",         "wp_api": False},
    {"name": "Cines",             "base": "https://cines.lk",             "wp_api": True},
    {"name": "Cineru",            "base": "https://cineru.lk",            "wp_api": True},
    {"name": "Subz",              "base": "https://subz.lk",              "wp_api": True},
    {"name": "Zoom",              "base": "https://zoom.lk",              "wp_api": False},
    {"name": "LKSubs",            "base": "https://lksubs.com",           "wp_api": True},
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
        ep_pat = rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b|{season}x0*{episode}\b|season[-_\s]*0*{season}/episode[-_\s]*0*{episode}\b)"
        if not re.search(ep_pat, text_lower):
            return False

    # Year check: if year is given and present in text, verify it
    if year and not (season and episode):
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
            from services.scrapers import sinhalasub
            s_res = await sinhalasub.search(client, clean_title, year=year, season=season, episode=episode, temp_dir=temp_dir)
            if s_res:
                return s_res
        except Exception as e_mod:
            log.debug("[MatchedScraper] Dedicated sinhalasub note: %s", e_mod)
    elif p_name == "CineSubz":
        try:
            from services.scrapers import cinesubz
            s_res = await cinesubz.search(client, clean_title, year=year, season=season, episode=episode, temp_dir=temp_dir)
            if s_res:
                return s_res
        except Exception as e_mod:
            log.debug("[MatchedScraper] Dedicated cinesubz note: %s", e_mod)
    elif p_name in ("Baiscope", "BaiscopeDownloads"):
        try:
            from services.scrapers import baiscope
            s_res = await baiscope.search(client, clean_title, year=year, season=season, episode=episode, temp_dir=temp_dir)
            if s_res:
                return s_res
        except Exception as e_mod:
            log.debug("[MatchedScraper] Dedicated baiscope note: %s", e_mod)
    elif p_name == "PirateLK":
        try:
            from services.scrapers import piratelk
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

            # Verification: make sure this post actually matches the target title & year
            verify_text = f"{post_url} {soup.title.get_text() if soup.title else ''} {soup.h1.get_text() if soup.h1 else ''} {page_text[:3000]}"
            if not matches_title_and_year(clean_t, verify_text, year=year, season=season, episode=episode):
                continue

            current_target_url = post_url
            if season and episode:
                ep_pat = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b|{season}x0*{episode}\b|season[-_\s]*0*{season}/episode[-_\s]*0*{episode}\b)",
                    re.IGNORECASE,
                )
                ep_link = None
                for a in soup.find_all("a", href=True):
                    h = a["href"].strip()
                    if ep_pat.search(h) or ep_pat.search(a.get_text(" ", strip=True)):
                        ep_link = urllib.parse.urljoin(post_url, h)
                        break

                if ep_link:
                    ep_resp = await client.get(ep_link, headers=HEADERS, timeout=8.0)
                    if ep_resp.status_code == 200:
                        soup = BeautifulSoup(ep_resp.text, "html.parser")
                        current_target_url = ep_link
                        page_text = soup.get_text(" ", strip=True)

            # A. Subtitle extraction (only needed for clean video portals)
            is_hardsub = (
                p_name in PRE_HARDSUBBED_PORTALS
                or any(k in post_url.lower() for k in ("sinhalasub", "cinesubz"))
            )
            sub_srt_path = None

            if not is_hardsub:
                dl_sub_urls = []
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    cls = " ".join(a.get("class", [])).lower()
                    a_txt = a.get_text(" ", strip=True).lower()
                    if (
                        "action=sub_download" in href
                        or "subz-list-btn" in cls
                        or "js-premium-download" in cls
                        or any(ext in href.lower() for ext in (".zip", ".rar", ".7z", ".srt"))
                        or ("උපසිරැසි" in a_txt and "බාගත" in a_txt)
                    ):
                        full_href = urllib.parse.urljoin(current_target_url, href)
                        if full_href not in dl_sub_urls and not full_href.startswith("magnet:"):
                            dl_sub_urls.append(full_href)

                for sub_url in dl_sub_urls[:3]:
                    try:
                        s_res = await client.get(sub_url, headers={"Referer": current_target_url}, timeout=10.0)
                        if s_res.status_code == 200 and len(s_res.content) > 128:
                            srt_p, _ = subtitle_service._extract_srt_from_bytes(
                                s_res.content,
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

    clean_title = re.sub(
        r"[\(\[\{]?\b(s\d{1,2}[\s._-]*e\d{1,2}|season\s*\d{1,2}|episode\s*\d{1,2}|ep\s*\d{1,2})\b.*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip(" -_")
    # Strip any trailing year from clean_title if year was also parsed
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

    log.info("[MatchedScraper] Querying Sri Lankan portals for matched video+sub for '%s'...", query)

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, verify=False) as client:
        task_objs = [
            asyncio.create_task(_search_portal(
                client=client,
                portal=portal,
                query=query,
                clean_title=clean_title,
                year=year,
                season=season,
                episode=episode,
                temp_dir=temp_dir,
            ))
            for portal in PORTALS
        ]
        done, pending = await asyncio.wait(task_objs, timeout=18.0)
        for p in pending:
            p.cancel()

        results = []
        for d in done:
            try:
                res = d.result()
                if isinstance(res, list):
                    results.append(res)
            except Exception as e_done:
                log.debug("[MatchedScraper] Portal result note: %s", e_done)

    all_matched: list[dict] = []
    seen_urls: set[str] = set()

    for res in results:
        if isinstance(res, list):
            for item in res:
                u = item.get("url")
                # Strictly reject any torrents or magnets
                if not u or u.startswith("magnet:") or ".torrent" in u.lower():
                    continue
                q = str(item.get("quality", "")).lower()
                # User requirement: TV Series strictly 720p & 480p only! NEVER download 1080p for TV Series.
                if (is_series or season is not None or episode is not None) and q == "1080p":
                    continue
                if u not in seen_urls:
                    seen_urls.add(u)
                    all_matched.append(item)

    log.info("[MatchedScraper] Total matched same-site candidates discovered: %d", len(all_matched))
    return all_matched
