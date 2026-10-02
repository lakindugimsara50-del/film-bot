"""
baiscope.py — Dedicated Scraper for Baiscope.lk & BaiscopeDownloads.co.

Characteristics:
- Clean video + separate SRT/ZIP subtitle (is_already_hardsubbed=False).
- Subtitle MUST be downloaded/extracted and burned/muxed into the video.
- Direct HTTP/DDL downloads: PixelDrain, Mega, Google Drive, Mediafire, direct MP4/MKV.
- STRICT: NO TORRENTS.
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

DOMAINS = [
    "https://baiscopedownloads.co",
    "https://baiscope.lk",
]

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
    """Search Baiscope / BaiscopeDownloads for direct clean video downloads + standalone subtitle."""
    if season or episode:
        clean_t = subtitle_service.extract_clean_show_name(clean_title) or clean_title.strip()
    else:
        clean_t = re.sub(r"[\(\[]\s*\d{4}\s*[\)\]]", "", clean_title).strip()
    search_queries = []
    if season and episode:
        search_queries.append(f"{clean_t} S{season:02d}E{episode:02d}")
        search_queries.append(f"{clean_t} Season {season}")
        search_queries.append(clean_t)
    elif year:
        search_queries.append(f"{clean_t} {year}")
        search_queries.append(clean_t)
        search_queries.append(f"{clean_t} ({year})")
    else:
        search_queries.append(clean_t)

    results: list[dict] = []

    for base_url in DOMAINS:
        if results:
            break
        candidate_posts: list[tuple[str, int]] = []
        seen_posts = set()
        domain_part = base_url.split("//")[-1].split("/")[0].lower()

        slug_title = re.sub(r"[^a-z0-9]+", "-", clean_t.lower()).strip("-")
        direct_candidates = []
        if season:
            direct_candidates.extend([
                f"{base_url}/{slug_title}-season-{season}-sinhala-subtitles/",
                f"{base_url}/{slug_title}-season-{season:02d}-sinhala-subtitles/",
                f"{base_url}/{slug_title}-season-{season}/",
            ])
        direct_candidates.append(f"{base_url}/{slug_title}-sinhala-subtitles/")
        for dc in direct_candidates:
            if dc not in seen_posts:
                seen_posts.add(dc)
                candidate_posts.append((dc, 25))

        for q in search_queries:
            if any(sc >= 40 for _, sc in candidate_posts):
                break
            try:
                s_url = f"{base_url}/?s={urllib.parse.quote_plus(q)}"
                resp = await client.get(s_url, headers=HEADERS, timeout=6.0)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for a in soup.select("article a, h2 a, h3 a, .entry-title a, .post-title a, .result-item a"):
                        raw_href = a.get("href", "").strip()
                        if not raw_href or raw_href.startswith("#"):
                            continue
                        full_href = urllib.parse.urljoin(base_url, raw_href)
                        if domain_part in full_href.lower() and not any(x in full_href for x in ("/category/", "/tag/", "/author/", "/page/", "#")):
                            if full_href not in seen_posts:
                                seen_posts.add(full_href)
                                txt = a.get_text(" ", strip=True) or a.get("title", "")
                                score = score_candidate_post(clean_t, full_href, txt, year=year, season=season, episode=episode)
                                if score > 0:
                                    candidate_posts.append((full_href, score))
            except Exception as e_html:
                log.debug("[Baiscope] HTML search note on %s: %s", base_url, e_html)

            # WP REST API fallback
            if not candidate_posts:
                try:
                    api_url = f"{base_url}/wp-json/wp/v2/posts?search={urllib.parse.quote_plus(q)}&per_page=6"
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
                    log.debug("[Baiscope] WP API note on %s: %s", base_url, e_api)

        candidate_posts.sort(key=lambda x: x[1], reverse=True)

        for post_url, _ in candidate_posts[:4]:
            try:
                p_resp = await client.get(post_url, headers=HEADERS, timeout=6.0)
                if p_resp.status_code != 200:
                    continue
                soup = BeautifulSoup(p_resp.text, "html.parser")
                page_text = soup.get_text(" ", strip=True)

                verify_text = f"{post_url} {soup.title.get_text() if soup.title else ''} {soup.h1.get_text() if soup.h1 else ''} {page_text[:3000]}"
                is_matched = matches_title_and_year(clean_t, verify_text, year=year, season=season, episode=episode)
                if not is_matched and season:
                    is_matched = matches_title_and_year(clean_t, verify_text, year=year, season=season, episode=None)
                if not is_matched:
                    continue

                current_url = post_url
                season_soup = soup

                # Follow season-specific link if on a general TV series hub page
                if season:
                    s_token_dash = f"season-{season}"
                    s_token_padded = f"season-{season:02d}"
                    s_token_space = f"season {season}"
                    if (
                        s_token_dash not in current_url.lower()
                        and s_token_padded not in current_url.lower()
                        and s_token_space not in current_url.lower()
                    ):
                        s_pat = re.compile(rf"(?:season[\s._-]*0*{season}\b|s0*{season}\b)", re.IGNORECASE)
                        for a in soup.find_all("a", href=True):
                            h = a["href"].strip()
                            txt = a.get_text(" ", strip=True)
                            if (s_pat.search(h) or s_pat.search(txt)) and (any(dom in h for dom in ("baiscope.lk", "baiscopedownloads.co")) or h.startswith("/")):
                                if not any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".mp4", ".mkv")):
                                    s_link = urllib.parse.urljoin(current_url, h)
                                    try:
                                        s_resp = await client.get(s_link, headers=HEADERS, timeout=8.0)
                                        if s_resp.status_code == 200:
                                            soup = BeautifulSoup(s_resp.text, "html.parser")
                                            current_url = s_link
                                            season_soup = soup
                                            break
                                    except Exception:
                                        pass

                if season and episode:
                    ep_pat = re.compile(
                        rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                        rf"|{season}x0*{episode}\b"
                        rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep)[-_\s]*0*{episode}\b"
                        rf"|\b(?:ep|episode)\.?\s*0*{episode}\b)",
                        re.IGNORECASE,
                    )
                    for a in soup.find_all("a", href=True):
                        h = a["href"].strip()
                        txt = a.get_text(" ", strip=True)
                        is_internal = any(dom in h.lower() for dom in ("baiscope.lk", "baiscopedownloads.co")) or h.startswith("/")
                        is_file_or_host = any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".mp4", ".mkv", ".avi", ".webm")) or any(vh in h.lower() for vh in ("pixeldrain", "userscloud", "mega.nz", "1fichier", "drive.google"))
                        if is_internal and not is_file_or_host and (ep_pat.search(h) or ep_pat.search(txt)):
                            ep_link = urllib.parse.urljoin(post_url, h)
                            try:
                                ep_resp = await client.get(ep_link, headers=HEADERS, timeout=8.0)
                                if ep_resp.status_code == 200:
                                    soup = BeautifulSoup(ep_resp.text, "html.parser")
                                    current_url = ep_link
                            except Exception:
                                pass
                            break

                # 1. Standalone Subtitle Extraction
                sub_srt_path = None
                dl_sub_urls = []
                soups_to_check = [(soup, current_url)]
                if season_soup and season_soup != soup:
                    soups_to_check.append((season_soup, post_url))

                for s_soup, ref_url in soups_to_check:
                    if sub_srt_path:
                        break
                    article_elem = s_soup.select_one(".entry-content, article, main, .download-links, .box-download") or s_soup
                    for a in article_elem.find_all("a", href=True):
                        h = a["href"].strip()
                        txt = a.get_text(" ", strip=True).lower()
                        if any(ign in h.lower() for ign in ("t.me", "telegram.me", "#", "facebook", "youtube", "imdb", "wikipedia")):
                            continue
                        if any(vm in txt or vm in h.lower() for vm in ("1080p", "720p", "480p", "x264", "x265", "hevc", "pixeldrain")):
                            continue
                        if not any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) and not any(k in h.lower() for k in ("/download/", "/downloads/", "sub-download", "download-sub", "action=sub_download")):
                            if "-sinhala-sub" in h.lower() or "-with-sinhala" in h.lower():
                                continue
                        if (
                            any(ext in h.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt"))
                            or any(k in h.lower() for k in ("/download/", "/downloads/", "sub-download", "download-sub", "subtitles", "action=sub_download"))
                            or (("උපසිරැසි" in txt or "sub" in txt) and ("බාගත" in txt or "download" in txt or "zip" in txt))
                            or "download-subtitle" in h.lower()
                        ):
                            full_h = urllib.parse.urljoin(ref_url, h)
                            if full_h not in dl_sub_urls and not full_h.startswith("magnet:"):
                                dl_sub_urls.append(full_h)

                for s_url in dl_sub_urls[:6]:
                    try:
                        s_resp = await client.get(s_url, headers={"Referer": current_url}, timeout=10.0)
                        if s_resp.status_code == 200 and len(s_resp.content) > 64:
                            content_type = s_resp.headers.get("content-type", "").lower()
                            if "text/html" in content_type or s_resp.content.startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                                sub_page_soup = BeautifulSoup(s_resp.text, "html.parser")
                                real_sub_url = None
                                for sa in sub_page_soup.find_all("a", href=True):
                                    sh = sa["href"].strip()
                                    st = sa.get_text(" ", strip=True).lower()
                                    if any(ext in sh.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) or "download" in st or "බාගත" in st:
                                        real_sub_url = urllib.parse.urljoin(s_url, sh)
                                        break
                                if real_sub_url:
                                    s_resp = await client.get(real_sub_url, headers={"Referer": s_url}, timeout=10.0)

                            if s_resp.status_code == 200 and len(s_resp.content) > 64:
                                srt_p, _ = subtitle_service._extract_srt_from_bytes(
                                    s_resp.content,
                                    temp_dir=temp_dir,
                                    season=season,
                                    episode=episode,
                                    prefix="baiscope_sub",
                                )
                                if srt_p and os.path.exists(srt_p):
                                    sub_srt_path = srt_p
                                    log.info("[Baiscope] Extracted standalone subtitle: %s", sub_srt_path)
                                    break
                    except Exception as sub_err:
                        log.debug("[Baiscope] Sub extraction error: %s", sub_err)

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
                                "ctx": ctx,
                                "raw_href": h,
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
                                    "ctx": ctx,
                                    "raw_href": h,
                                })
                        except Exception:
                            pass

                # Filter for requested episode if page contains multiple episode download links
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
                        if ep_pat.search(vl.get("ctx", "")) or ep_pat.search(vl.get("raw_href", "")) or ep_pat.search(vl.get("url", ""))
                    ]
                    if matching_ep_links:
                        video_links = matching_ep_links

                portal_label = "BaiscopeDownloads" if "baiscopedownloads" in base_url else "Baiscope"
                for vl in video_links:
                    results.append({
                        "portal": portal_label,
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
                log.debug("[Baiscope] Post inspect note: %s", e_post)

    return results
