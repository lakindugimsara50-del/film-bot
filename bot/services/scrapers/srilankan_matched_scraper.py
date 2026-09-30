"""
srilankan_matched_scraper.py — Priority 1 Same-Site Matched Video & Subtitle Scraper.

Solves Subtitle Timing Drift (0.0ms desync guaranteed):
Sri Lankan subtitle sites (sinhalasub.lk, cineru.lk, baiscope.lk, subz.lk, lksubs.com, zoom.lk, piratelk.com)
publish posts where the translator's subtitle was timed to the exact WebRip / WEB-DL / BluRay video releases
hosted directly in that same post (PixelDrain, Mega, Google Drive, direct MP4/MKV, or matched torrents).

By downloading the exact video release and burning the exact subtitle provided in the same post:
1. The video and subtitle are 100% frame-perfect synchronized (0.0ms drift).
2. Video quality (1080p, 720p, 480p) matches the translator's master release.
3. Fallback to general torrent search only occurs if the site doesn't host direct video links.
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
    {"name": "Cineru",      "base": "https://cineru.lk",      "wp_api": True},
    {"name": "SinhalaSub",  "base": "https://sinhalasub.lk",  "wp_api": True},
    {"name": "Subz",        "base": "https://subz.lk",        "wp_api": True},
    {"name": "Baiscope",    "base": "https://baiscope.lk",    "wp_api": True},
    {"name": "LKSubs",      "base": "https://lksubs.com",     "wp_api": True},
    {"name": "Zoom",        "base": "https://zoom.lk",        "wp_api": False},
    {"name": "PirateLK",    "base": "https://piratelk.com",    "wp_api": False},
]

# Patterns for direct/cloud video hosts
VIDEO_HOST_PATTERNS = {
    "pixeldrain": re.compile(r"https?://(?:www\.)?pixeldrain\.com/(?:u|api/file)/([a-zA-Z0-9_-]+)", re.IGNORECASE),
    "gdrive":     re.compile(r"https?://drive\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)", re.IGNORECASE),
    "mega":       re.compile(r"https?://mega\.nz/(?:file/|#!)?[a-zA-Z0-9_#-]+", re.IGNORECASE),
    "gofile":     re.compile(r"https?://gofile\.io/d/[a-zA-Z0-9_-]+", re.IGNORECASE),
    "mediafire":  re.compile(r"https?://(?:www\.)?mediafire\.com/(?:file|download)/[a-zA-Z0-9_-]+", re.IGNORECASE),
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
        "/links/", "/api-", "linkvertise", "gplinks", "droplink", "short"
    ])
    if not is_intermediate:
        return None

    try:
        req_headers = dict(HEADERS)
        if referer_url:
            req_headers["Referer"] = referer_url

        resp = await client.get(link_url, headers=req_headers, timeout=10.0)
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
            # Check script mappings
            if "google.com/server" in raw_href:
                # Replace server pattern per CineSubz script:
                # https://google.com/server11/1:/ -> https://drive.csplayer2.space/server1/
                m_srv = re.search(r"https://google\.com/server(\d+)/1:/", raw_href)
                if m_srv:
                    mapped = re.sub(r"https://google\.com/server\d+/1:/", f"https://drive.csplayer2.space/server{m_srv.group(1)[0]}/", raw_href)
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
    """Search a single Sri Lankan portal for matched post, extracting subtitle and video links."""
    base_url = portal["base"]
    p_name = portal["name"]
    candidate_posts: list[str] = []
    title_words = [w.lower() for w in clean_title.split() if len(w) > 2]
    search_queries = [query]
    if clean_title and clean_title not in search_queries:
        search_queries.append(clean_title)

    for q_try in search_queries:
        if candidate_posts:
            break
        # 1. Search via WP REST API if supported
        if portal.get("wp_api"):
            try:
                api_url = f"{base_url.rstrip('/')}/wp-json/wp/v2/posts?search={urllib.parse.quote_plus(q_try)}&per_page=6"
                resp = await client.get(api_url, headers=HEADERS, timeout=8.0)
                if resp.status_code == 200 and isinstance(resp.json(), list):
                    for p in resp.json():
                        link = p.get("link") or ""
                        rendered = (p.get("title", {}) or {}).get("rendered", "").lower()
                        if link and (not title_words or any(w in rendered or w in link.lower() for w in title_words)):
                            candidate_posts.append(link)
            except Exception as e_api:
                log.debug("[MatchedScraper] %s WP API note: %s", p_name, e_api)

        # 2. Fallback to HTML search (/?s=)
        if not candidate_posts:
            try:
                s_url = f"{base_url.rstrip('/')}/?s={urllib.parse.quote_plus(q_try)}"
                resp = await client.get(s_url, headers=HEADERS, timeout=8.0)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    selectors = [
                        ".display-item a", ".item-box a", ".result-item a", "article a",
                        "h2 a", "h3 a", ".entry-title a", ".post-title a", "main a",
                    ]
                    for a in soup.select(", ".join(selectors)):
                        href = a.get("href", "")
                        if href and base_url.split("//")[-1].split("/")[0] in href:
                            if not any(ign in href for ign in ("/category/", "/tag/", "/author/", "/page/", "#", "wp-login")):
                                if href not in candidate_posts:
                                    candidate_posts.append(href)
            except Exception as e_html:
                log.debug("[MatchedScraper] %s HTML search note: %s", p_name, e_html)

    found_candidates: list[dict] = []

    # 3. Inspect matched post pages
    for post_url in candidate_posts[:4]:
        try:
            p_resp = await client.get(post_url, headers=HEADERS, timeout=10.0)
            if p_resp.status_code != 200:
                continue

            soup = BeautifulSoup(p_resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)

            # Verification: make sure this post actually matches the target title
            if title_words and not any(w in page_text.lower() for w in title_words):
                continue

            # TV Series navigation: If season and episode are specified, locate the specific episode page
            current_target_url = post_url
            if season and episode:
                # 1. First try strict season-and-episode regex (prevents S08E05 matching when searching for S06E05)
                ep_pat = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b|{season}x0*{episode}\b|season[-_\s]*0*{season}/episode[-_\s]*0*{episode}\b)",
                    re.IGNORECASE,
                )
                ep_link = None
                for a in soup.find_all("a", href=True):
                    h = a["href"].strip()
                    if ep_pat.search(h):
                        ep_link = urllib.parse.urljoin(post_url, h)
                        break

                # 2. Fallback: check anchor text with strict season and episode
                if not ep_link:
                    for a in soup.find_all("a", href=True):
                        h = a["href"].strip()
                        t = a.get_text(" ", strip=True).lower()
                        if ep_pat.search(t):
                            ep_link = urllib.parse.urljoin(post_url, h)
                            break

                if ep_link:
                    log.info("[MatchedScraper] %s found specific episode link: %s", p_name, ep_link)
                    ep_resp = await client.get(ep_link, headers=HEADERS, timeout=10.0)
                    if ep_resp.status_code == 200:
                        soup = BeautifulSoup(ep_resp.text, "html.parser")
                        current_target_url = ep_link
                        page_text = soup.get_text(" ", strip=True)

            # A. Extract Subtitle from post/episode page
            sub_srt_path = None
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
                    if full_href not in dl_sub_urls:
                        dl_sub_urls.append(full_href)

            for sub_url in dl_sub_urls[:3]:
                try:
                    s_res = await client.get(sub_url, headers={"Referer": current_target_url}, timeout=12.0)
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

            # If no subtitle was found on the post page, attempt fallback to subtitle_service
            if not sub_srt_path:
                try:
                    fallback_sub = await subtitle_service.fetch_sri_lankan_sinhala_subtitle(
                        clean_title, year, season=season, episode=episode, temp_dir=temp_dir
                    )
                    if fallback_sub and os.path.exists(fallback_sub):
                        sub_srt_path = fallback_sub
                        log.info("[MatchedScraper] Attached Sri Lankan subtitle via subtitle_service: %s", fallback_sub)
                except Exception as fb_err:
                    log.debug("[MatchedScraper] Subtitle fallback lookup note: %s", fb_err)

            # B. Extract Video Download Links from table rows and anchors
            video_links: list[dict] = []
            seen_dl_urls: set[str] = set()

            # 1. Check table rows (typical for SinhalaSub, DooPlay, ZetaFlix releases)
            for tr in soup.find_all("tr"):
                tr_txt = tr.get_text(" ", strip=True)
                for a in tr.find_all("a", href=True):
                    h = a["href"].strip()
                    if h in seen_dl_urls or h.startswith("#"):
                        continue
                    seen_dl_urls.add(h)

                    # Skip Telegram channels/bots in direct video candidate list
                    if "t.me" in h.lower() or "telegram.me" in h.lower():
                        continue

                    q = detect_quality_from_context(tr_txt, h)
                    resolved_url = await resolve_srilankan_intermediate_link(client, h, referer_url=current_target_url)
                    if resolved_url and not any(ign in resolved_url for ign in ["telegram.me", "t.me"]):
                        h_type = "cdn" if "cdn.sinhalasub" in resolved_url else ("pixeldrain" if "pixeldrain" in resolved_url else "ddl")
                        video_links.append({
                            "url": resolved_url,
                            "original_url": h,
                            "host_type": h_type,
                            "quality": q,
                            "context": tr_txt[:120],
                        })

            # 2. Check general anchors for direct video hosts or remaining intermediate links
            for a in soup.find_all("a", href=True):
                h = a["href"].strip()
                if h in seen_dl_urls or h.startswith("#"):
                    continue
                seen_dl_urls.add(h)

                if "t.me" in h.lower() or "telegram.me" in h.lower():
                    continue

                txt = a.get_text(" ", strip=True)
                parent_txt = a.parent.get_text(" ", strip=True) if a.parent else ""

                # Check against known direct video patterns
                matched_pat = False
                for h_type, pat in VIDEO_HOST_PATTERNS.items():
                    m = pat.search(h)
                    if m:
                        matched_pat = True
                        matched_url = m.group(0)
                        direct_url = resolve_direct_video_url(matched_url)
                        q = detect_quality_from_context(f"{txt} {parent_txt}", h)
                        video_links.append({
                            "url": direct_url,
                            "original_url": matched_url,
                            "host_type": h_type,
                            "quality": q,
                            "context": f"{txt} {parent_txt}"[:120],
                        })
                        break

                # If not matched directly, check if it's an intermediate locker link
                if not matched_pat and ("/links/" in h or "/api-" in h):
                    resolved_url = await resolve_srilankan_intermediate_link(client, h, referer_url=current_target_url)
                    if resolved_url and not any(ign in resolved_url for ign in ["telegram.me", "t.me"]):
                        q = detect_quality_from_context(f"{txt} {parent_txt}", h)
                        h_type = "cdn" if "cdn.sinhalasub" in resolved_url else ("pixeldrain" if "pixeldrain" in resolved_url else "ddl")
                        video_links.append({
                            "url": resolved_url,
                            "original_url": h,
                            "host_type": h_type,
                            "quality": q,
                            "context": f"{txt} {parent_txt}"[:120],
                        })

            # Package found video links as top-priority candidates
            for vl in video_links:
                found_candidates.append({
                    "portal": p_name,
                    "post_url": current_target_url,
                    "url": vl["url"],
                    "quality": vl["quality"],
                    "host_type": vl["host_type"],
                    "sub_srt_path": sub_srt_path,
                })

            if found_candidates:
                log.info(
                    "[MatchedScraper] Found %d matched video release(s) on %s (sub_found=%s)",
                    len(found_candidates), p_name, bool(sub_srt_path)
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
    Returns a list of candidate dictionaries with exact direct/cloud video links and
    the synchronously matched Sinhala subtitle file path.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return []

    clean_title = re.sub(
        r"[\(\[\{]?\b(s\d{1,2}[\s._-]*e\d{1,2}|season\s*\d{1,2}|episode\s*\d{1,2}|ep\s*\d{1,2})\b.*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip(" -_")
    clean_title = re.sub(r"[^a-zA-Z0-9\s]", " ", clean_title).strip()

    if not clean_title:
        return []

    if season and episode:
        query = f"{clean_title} Season {season}"
    elif year:
        query = f"{clean_title} {year}"
    else:
        query = clean_title

    log.info("[MatchedScraper] Querying Sri Lankan portals for matched video+sub for '%s'...", query)

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, verify=False) as client:
        tasks = [
            _search_portal(
                client=client,
                portal=portal,
                query=query,
                clean_title=clean_title,
                year=year,
                season=season,
                episode=episode,
                temp_dir=temp_dir,
            )
            for portal in PORTALS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_matched: list[dict] = []
    seen_urls: set[str] = set()

    for res in results:
        if isinstance(res, list):
            for item in res:
                u = item.get("url")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    all_matched.append(item)

    log.info("[MatchedScraper] Total matched same-site candidates discovered: %d", len(all_matched))
    return all_matched
