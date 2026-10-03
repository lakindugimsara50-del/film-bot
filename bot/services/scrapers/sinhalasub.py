"""
sinhalasub.py — Dedicated Scraper for SinhalaSub.lk.

Characteristics:
- Pre-hardsubbed: Sinhala subtitles are already burned into video (is_already_hardsubbed=True).
- Subtitle does NOT need to be burned again.
- Direct HTTP/DDL downloads: PixelDrain, cdn.sinhalasub.net, ddl.sinhalasub.net, ZetaFlix unlocker.
- STRICT: NO TORRENTS.
"""

import json
import logging
import re
import time
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://sinhalasub.lk"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
    "Referer": "https://sinhalasub.lk/",
}

_CACHED_NONCE: Optional[str] = None
_CACHED_NONCE_TIME: float = 0.0

from services.scrapers.srilankan_matched_scraper import (
    matches_title_and_year,
    score_candidate_post,
    resolve_direct_video_url,
    resolve_srilankan_intermediate_link,
    detect_quality_from_context,
    VIDEO_HOST_PATTERNS,
)


async def _get_zetaflix_nonce(client: httpx.AsyncClient) -> Optional[str]:
    """Extract or return cached Zetaflix REST API nonce from SinhalaSub homepage."""
    global _CACHED_NONCE, _CACHED_NONCE_TIME
    now = time.time()
    if _CACHED_NONCE and (now - _CACHED_NONCE_TIME < 1800):
        return _CACHED_NONCE

    try:
        r = await client.get(f"{BASE_URL}/", headers=HEADERS, timeout=8.0, follow_redirects=True)
        if r.status_code == 200:
            m = re.search(r"var\s+ztGo\s*=\s*(\{.*?\});", r.text)
            if m:
                zt_data = json.loads(m.group(1))
                nonce = zt_data.get("nonce")
                if nonce:
                    _CACHED_NONCE = nonce
                    _CACHED_NONCE_TIME = now
                    log.debug("[SinhalaSub] Acquired Zetaflix nonce: %s", nonce)
                    return nonce
    except Exception as e_nonce:
        log.debug("[SinhalaSub] Failed to acquire Zetaflix nonce: %s", e_nonce)

    return _CACHED_NONCE


async def search(
    client: httpx.AsyncClient,
    clean_title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    temp_dir: str = "/tmp",
) -> list[dict]:
    """Search SinhalaSub for direct pre-hardsubbed downloads."""
    clean_t = clean_title.strip()
    search_queries = []
    if season and episode:
        search_queries.append(f"{clean_t} S{season:02d}E{episode:02d}")
        search_queries.append(clean_t)
    elif year:
        search_queries.append(f"{clean_t} {year}")
        search_queries.append(clean_t)
        search_queries.append(f"{clean_t} ({year})")
    else:
        search_queries.append(clean_t)

    candidate_posts: list[tuple[str, int]] = []
    seen_posts = set()

    # 1. Zetaflix LiveSearch API (Primary & Most Accurate)
    nonce = await _get_zetaflix_nonce(client)
    for q in search_queries:
        if any(sc >= 40 for _, sc in candidate_posts):
            break
        if nonce:
            try:
                api_url = f"{BASE_URL}/wp-json/zetaflix/search/"
                params = {"keyword": q, "nonce": nonce}
                resp = await client.get(api_url, params=params, headers=HEADERS, timeout=7.0, follow_redirects=True)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        if isinstance(data, dict):
                            for _, item in data.items():
                                if isinstance(item, dict) and item.get("url"):
                                    link = item["url"].strip()
                                    rendered_title = item.get("title", "")
                                    if link not in seen_posts and "sinhalasub" in link:
                                        seen_posts.add(link)
                                        score = score_candidate_post(clean_t, link, rendered_title, year=year, season=season, episode=episode)
                                        if score > 0:
                                            candidate_posts.append((link, score))
                    except Exception:
                        pass
            except Exception as e_zt:
                log.debug("[SinhalaSub] Zetaflix search note: %s", e_zt)

        # 2. HTML Search with 302 redirect following
        if not candidate_posts:
            try:
                s_url = f"{BASE_URL}/?s={urllib.parse.quote_plus(q)}"
                resp = await client.get(s_url, headers=HEADERS, timeout=6.0, follow_redirects=True)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    selectors = ".display-item a, .item-box a, .result-item a, article a, h2 a, h3 a, .entry-title a"
                    for a in soup.select(selectors):
                        raw_href = a.get("href", "").strip()
                        if not raw_href or raw_href.startswith("#"):
                            continue
                        full_href = urllib.parse.urljoin(BASE_URL, raw_href)
                        if "sinhalasub" in full_href and not any(x in full_href for x in ("/category/", "/tag/", "/author/", "/page/", "#")):
                            if full_href not in seen_posts:
                                seen_posts.add(full_href)
                                txt = a.get_text(" ", strip=True) or a.get("title", "")
                                score = score_candidate_post(clean_t, full_href, txt, year=year, season=season, episode=episode)
                                if score > 0:
                                    candidate_posts.append((full_href, score))
            except Exception as e_html:
                log.debug("[SinhalaSub] HTML search note: %s", e_html)

        # 3. Direct /search/ path check
        if not candidate_posts:
            try:
                dir_search_url = f"{BASE_URL}/search/{urllib.parse.quote_plus(q)}/"
                resp = await client.get(dir_search_url, headers=HEADERS, timeout=6.0, follow_redirects=True)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for a in soup.select(".display-item a, .item-box a, .result a, h2 a, h3 a, .entry-title a"):
                        raw_href = a.get("href", "").strip()
                        full_href = urllib.parse.urljoin(BASE_URL, raw_href)
                        if "sinhalasub" in full_href and not any(x in full_href for x in ("/category/", "/tag/", "/author/", "/page/", "#")):
                            if full_href not in seen_posts:
                                seen_posts.add(full_href)
                                txt = a.get_text(" ", strip=True) or a.get("title", "")
                                score = score_candidate_post(clean_t, full_href, txt, year=year, season=season, episode=episode)
                                if score > 0:
                                    candidate_posts.append((full_href, score))
            except Exception as e_dir:
                log.debug("[SinhalaSub] Direct search path note: %s", e_dir)

    candidate_posts.sort(key=lambda x: x[1], reverse=True)
    results: list[dict] = []

    for post_url, _ in candidate_posts[:4]:
        try:
            p_resp = await client.get(post_url, headers=HEADERS, timeout=6.0, follow_redirects=True)
            if p_resp.status_code != 200:
                continue
            soup = BeautifulSoup(p_resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)

            verify_text = f"{post_url} {soup.title.get_text() if soup.title else ''} {soup.h1.get_text() if soup.h1 else ''} {page_text[:3000]}"
            if not matches_title_and_year(clean_t, verify_text, year=year, season=season, episode=episode):
                if not (season or episode and matches_title_and_year(clean_t, verify_text, year=year, season=None, episode=None)):
                    continue

            current_url = post_url

            # TV Series Episode Navigation
            is_already_ep = False
            if episode is not None:
                ep_chk = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                    rf"|{season}x0*{episode}\b"
                    rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep|e)[-_\s]*0*{episode}\b"
                    rf"|(?:\b|[-_\[/])(?:ep|episode|e)\.?\s*0*{episode}(?:\b|[-_\]/]))",
                    re.IGNORECASE,
                )
                if ep_chk.search(current_url) or (soup.title and ep_chk.search(soup.title.get_text())):
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
                for a in soup.find_all("a", href=True):
                    h = a["href"].strip()
                    txt = a.get_text(" ", strip=True)
                    is_internal = ("sinhalasub" in h.lower() or h.startswith("/"))
                    is_file_or_host = any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".mp4", ".mkv", ".avi", ".webm")) or any(vh in h.lower() for vh in ("pixeldrain", "userscloud", "mega.nz", "1fichier", "drive.google"))
                    is_show_match = any(tok in h.lower() for tok in slug_tokens) or any(tok in txt.lower() for tok in slug_tokens) or not slug_tokens
                    if is_internal and not is_file_or_host and is_show_match and (ep_pat.search(h) or ep_pat.search(txt) or "/episodes/" in h.lower()):
                        if ep_pat.search(h) or ep_pat.search(txt):
                            ep_link = urllib.parse.urljoin(post_url, h)
                            try:
                                ep_resp = await client.get(ep_link, headers=HEADERS, timeout=8.0, follow_redirects=True)
                                if ep_resp.status_code == 200:
                                    soup = BeautifulSoup(ep_resp.text, "html.parser")
                                    current_url = ep_link
                                    break
                            except Exception:
                                pass

            # Extract direct links & resolve locker/intermediate links
            video_links = []
            seen_dl_urls = set()
            for tr in soup.find_all(["tr", "p", "div", "a"]):
                ctx = tr.get_text(" ", strip=True)
                anchors = tr.find_all("a", href=True) if tr.name != "a" else [tr]
                for a in anchors:
                    h = a.get("href", "").strip()
                    if not h or h in seen_dl_urls or any(ign in h.lower() for ign in ["t.me", "telegram.me", "#"]):
                        continue
                    seen_dl_urls.add(h)

                    # STRICT: Skip magnets or torrent links
                    if h.startswith("magnet:") or ".torrent" in h.lower():
                        continue

                    q = detect_quality_from_context(ctx, h)
                    matched = False
                    for h_type, pat in VIDEO_HOST_PATTERNS.items():
                        if h_type == "magnet":
                            continue
                        m = pat.search(h)
                        if m:
                            matched = True
                            dir_url = resolve_direct_video_url(m.group(0))
                            if any(ign in dir_url.lower() for ign in ["t.me", "telegram.me"]):
                                break
                            dir_lower = dir_url.lower()
                            if "720p" in dir_lower:
                                q = "720p"
                            elif any(k in dir_lower for k in ("480p", "360p", "sd")):
                                q = "480p"
                            elif "1080p" in dir_lower:
                                q = "1080p"
                            video_links.append({
                                "url": dir_url,
                                "quality": q,
                                "host_type": h_type,
                            })
                            break

                    if not matched and ("/links/" in h or "/api-" in h):
                        try:
                            resolved = await resolve_srilankan_intermediate_link(client, h, referer_url=current_url)
                            if resolved and not resolved.startswith("magnet:") and ".torrent" not in resolved.lower():
                                if any(ign in resolved.lower() for ign in ["t.me", "telegram.me"]):
                                    continue
                                res_lower = resolved.lower()
                                if "720p" in res_lower:
                                    q = "720p"
                                elif any(k in res_lower for k in ("480p", "360p", "sd")):
                                    q = "480p"
                                elif "1080p" in res_lower:
                                    q = "1080p"
                                h_type = "cdn" if "cdn.sinhalasub" in resolved else ("pixeldrain" if "pixeldrain" in resolved else "ddl")
                                video_links.append({
                                    "url": resolved,
                                    "quality": q,
                                    "host_type": h_type,
                                })
                        except Exception:
                            pass

            for vl in video_links:
                results.append({
                    "portal": "SinhalaSub",
                    "post_url": current_url,
                    "url": vl["url"],
                    "quality": vl["quality"],
                    "host_type": vl["host_type"],
                    "sub_srt_path": None,  # Pre-hardsubbed, no secondary sub file needed
                    "is_already_hardsubbed": True,
                })

            if results:
                break
        except Exception as e_post:
            log.debug("[SinhalaSub] Post inspect note: %s", e_post)

    return results
