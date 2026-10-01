"""
piratelk.py — Dedicated Scraper for PirateLK.com.

Characteristics:
- Clean video + separate SRT/ZIP subtitle (is_already_hardsubbed=False).
- Subtitle MUST be downloaded/extracted and burned/muxed into the video.
- Direct HTTP/DDL downloads: PixelDrain, Google Drive, direct links.
- STRICT: NO TORRENTS (Magnets / .torrent files are strictly ignored).
"""

import logging
import os
import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from services import subtitle_service

log = logging.getLogger(__name__)

BASE_URL = "https://piratelk.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
}


from services.scrapers.srilankan_matched_scraper import (
    matches_title_and_year,
    score_candidate_post,
    resolve_direct_video_url,
    resolve_srilankan_intermediate_link,
    detect_quality_from_context,
    VIDEO_HOST_PATTERNS,
)


async def search(
    client: httpx.AsyncClient,
    clean_title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    temp_dir: str = "/tmp",
) -> list[dict]:
    """Search PirateLK for direct clean video downloads + standalone subtitle."""
    clean_t = clean_title.strip()
    slug_title = re.sub(r"[^a-z0-9]+", "-", clean_t.lower()).strip("-")
    direct_candidates = []

    if season and episode:
        direct_candidates.append(f"{BASE_URL}/{slug_title}-season-{season:02d}-with-sinhala-subtitles/")
        direct_candidates.append(f"{BASE_URL}/{slug_title}-with-sinhala-subtitles/")
        search_queries = [f"{clean_t} Season {season}", clean_t]
    elif year:
        direct_candidates.append(f"{BASE_URL}/{slug_title}-{year}-with-sinhala-subtitles/")
        direct_candidates.append(f"{BASE_URL}/{slug_title}-with-sinhala-subtitles/")
        search_queries = [f"{clean_t} {year}", clean_t, f"{clean_t} ({year})"]
    else:
        direct_candidates.append(f"{BASE_URL}/{slug_title}-with-sinhala-subtitles/")
        search_queries = [clean_t]

    candidate_posts: list[tuple[str, int]] = []
    seen_posts = set()

    for dc in direct_candidates:
        if dc not in seen_posts:
            seen_posts.add(dc)
            candidate_posts.append((dc, 25))

    for q in search_queries:
        if any(sc >= 40 for _, sc in candidate_posts):
            break
        try:
            s_url = f"{BASE_URL}/?s={urllib.parse.quote_plus(q)}"
            resp = await client.get(s_url, headers=HEADERS, timeout=6.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select("article a, h2 a, h3 a, .entry-title a, .post-title a"):
                    raw_href = a.get("href", "").strip()
                    if not raw_href or raw_href.startswith("#"):
                        continue
                    full_href = urllib.parse.urljoin(BASE_URL, raw_href)
                    if "piratelk" in full_href and not any(x in full_href for x in ("/category/", "/tag/", "/author/", "/page/", "#")):
                        if full_href not in seen_posts:
                            seen_posts.add(full_href)
                            txt = a.get_text(" ", strip=True) or a.get("title", "")
                            score = score_candidate_post(clean_t, full_href, txt, year=year, season=season, episode=episode)
                            if score > 0:
                                candidate_posts.append((full_href, score))
        except Exception as e_html:
            log.debug("[PirateLK] HTML search note: %s", e_html)

    candidate_posts.sort(key=lambda x: x[1], reverse=True)
    results: list[dict] = []

    for post_url, _ in candidate_posts[:3]:
        try:
            p_resp = await client.get(post_url, headers=HEADERS, timeout=6.0)
            if p_resp.status_code != 200:
                continue
            soup = BeautifulSoup(p_resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)

            verify_text = f"{post_url} {soup.title.get_text() if soup.title else ''} {soup.h1.get_text() if soup.h1 else ''} {page_text[:3000]}"
            if not matches_title_and_year(clean_t, verify_text, year=year, season=season, episode=episode):
                continue

            current_url = post_url
            if season and episode:
                ep_pat = re.compile(rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b|{season}x0*{episode}\b)", re.IGNORECASE)
                for a in soup.find_all("a", href=True):
                    h = a["href"].strip()
                    if ep_pat.search(h) or ep_pat.search(a.get_text(" ", strip=True)):
                        ep_link = urllib.parse.urljoin(post_url, h)
                        ep_resp = await client.get(ep_link, headers=HEADERS, timeout=8.0)
                        if ep_resp.status_code == 200:
                            soup = BeautifulSoup(ep_resp.text, "html.parser")
                            current_url = ep_link
                        break

            # 1. Standalone Subtitle Extraction
            sub_srt_path = None
            dl_sub_urls = []
            for a in soup.find_all("a", href=True):
                h = a["href"].strip()
                txt = a.get_text(" ", strip=True).lower()
                if (
                    any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".srt"))
                    or ("උපසිරැසි" in txt and "බාගත" in txt)
                    or "download-subtitle" in h.lower()
                ):
                    full_h = urllib.parse.urljoin(current_url, h)
                    if full_h not in dl_sub_urls and not full_h.startswith("magnet:"):
                        dl_sub_urls.append(full_h)

            for s_url in dl_sub_urls[:3]:
                try:
                    s_resp = await client.get(s_url, headers={"Referer": current_url}, timeout=10.0)
                    if s_resp.status_code == 200 and len(s_resp.content) > 128:
                        srt_p, _ = subtitle_service._extract_srt_from_bytes(
                            s_resp.content,
                            temp_dir=temp_dir,
                            season=season,
                            episode=episode,
                            prefix="piratelk_sub",
                        )
                        if srt_p and os.path.exists(srt_p):
                            sub_srt_path = srt_p
                            log.info("[PirateLK] Extracted standalone subtitle: %s", sub_srt_path)
                            break
                except Exception:
                    pass

            if not sub_srt_path:
                try:
                    fb_sub = await subtitle_service.fetch_sri_lankan_sinhala_subtitle(
                        clean_t, year, season=season, episode=episode, temp_dir=temp_dir
                    )
                    if fb_sub and os.path.exists(fb_sub):
                        sub_srt_path = fb_sub
                except Exception:
                    pass

            # 2. Direct Video Download Links (STRICT: NO TORRENTS)
            video_links = []
            for a in soup.find_all("a", href=True):
                h = a["href"].strip()
                if any(ign in h.lower() for ign in ["t.me", "telegram.me", "#"]):
                    continue
                # Exclude torrents and magnets completely
                if h.startswith("magnet:") or ".torrent" in h.lower():
                    continue

                ctx = a.get_text(" ", strip=True)
                q = detect_quality_from_context(ctx, h)

                matched = False
                for h_type, pat in VIDEO_HOST_PATTERNS.items():
                    if h_type == "magnet":
                        continue
                    m = pat.search(h)
                    if m:
                        matched = True
                        video_links.append({
                            "url": resolve_direct_video_url(m.group(0)),
                            "quality": q,
                            "host_type": h_type,
                        })
                        break

                if not matched and ("/links/" in h or "/api-" in h or "/download/" in h):
                    try:
                        resolved = await resolve_srilankan_intermediate_link(client, h, referer_url=current_url)
                        if resolved and not resolved.startswith("magnet:") and ".torrent" not in resolved.lower():
                            h_type = "cdn" if "cdn." in resolved else ("pixeldrain" if "pixeldrain" in resolved else "ddl")
                            video_links.append({
                                "url": resolved,
                                "quality": q,
                                "host_type": h_type,
                            })
                    except Exception:
                        pass

            for vl in video_links:
                results.append({
                    "portal": "PirateLK",
                    "post_url": current_url,
                    "url": vl["url"],
                    "quality": vl["quality"],
                    "host_type": vl["host_type"],
                    "sub_srt_path": sub_srt_path,  # Standalone sub to be burned/muxed
                    "is_already_hardsubbed": False,  # Clean video -> MUST burn/mux sub!
                })

            if results:
                break
        except Exception as e_post:
            log.debug("[PirateLK] Post inspect note: %s", e_post)

    return results
