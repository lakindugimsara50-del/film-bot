"""
cinesubz.py — Dedicated Scraper for CineSubz.co.

Characteristics:
- Pre-hardsubbed: Sinhala subtitles are already burned into video (is_already_hardsubbed=True).
- Subtitle does NOT need to be burned again.
- Direct HTTP/DDL downloads: csplayer, drive.csplayer2.space, direct MP4, PixelDrain, Mega.
- STRICT: NO TORRENTS.
"""

import asyncio
import logging
import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://cinesubz.co"

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


async def _extract_cinesubz_zetaplayer_streams(
    client: httpx.AsyncClient,
    soup: BeautifulSoup,
    current_url: str,
) -> list[dict]:
    """
    Extract high-speed direct CDN streams from CineSubz ZetaPlayer options.
    Direct streams from supercloud2.space / setwenna.one / player endpoints contain
    genuine .mp4 files with pre-hardsubbed Sinhala subtitles.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://cinesubz.co/",
        "X-Requested-With": "XMLHttpRequest",
    }
    options = soup.select("#playeroptionsul li.zetaflix_player_option, #playeroptionsul li.dooplay_player_option")
    stream_links = []
    seen_urls = set()

    for opt in options:
        post_id = opt.get("data-post")
        p_type = opt.get("data-type")  # 'mv' or 'ep'
        nume = opt.get("data-nume")
        if not (post_id and p_type and nume) or nume == "trailer":
            continue

        api_url = f"https://cinesubz.co/wp-json/zetaplayer/v2/{post_id}/{p_type}/{nume}"
        try:
            r_api = await client.get(api_url, headers=headers, timeout=8.0)
            if r_api.status_code == 200:
                data = r_api.json()
                embed_url = data.get("embed_url") or ""
                if embed_url:
                    r_embed = await client.get(embed_url, headers=headers, timeout=8.0)
                    if r_embed.status_code == 200:
                        found_urls = re.findall(r"https?://[^\s\"\'<>]+\.(?:mp4|mkv)(?:\?[^\s\"\'<>]*)?", r_embed.text)
                        for fu in set(found_urls):
                            # Filter for real media stream hosts (supercloud, setwenna, csplayer CDN)
                            # Exclude ad lockers or non-media endpoints (e.g. drive.csplayer2.space)
                            if "drive.csplayer2.space" not in fu and any(k in fu for k in ["supercloud", "setwenna", "play=true", "csplayer", "cdn"]) and fu not in seen_urls:
                                seen_urls.add(fu)
                                fu_lower = fu.lower()
                                q = "1080p" if "1080p" in fu_lower else ("720p" if "720p" in fu_lower else ("480p" if "480p" in fu_lower else "720p"))
                                stream_links.append({
                                    "portal": "CineSubz",
                                    "post_url": current_url,
                                    "url": fu,
                                    "quality": q,
                                    "host_type": "cdn",
                                    "sub_srt_path": None,  # Pre-hardsubbed
                                    "is_already_hardsubbed": True,
                                })
        except Exception as e_zeta:
            log.debug("[CineSubz] ZetaPlayer stream extraction note (%s): %s", api_url, e_zeta)

    return stream_links


async def search(
    client: httpx.AsyncClient,
    clean_title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    temp_dir: str = "/tmp",
) -> list[dict]:
    """Search CineSubz for direct pre-hardsubbed downloads."""
    clean_t = clean_title.strip()
    search_queries = []
    if season and episode:
        search_queries.append(f"{clean_t} S{season:02d}E{episode:02d}")
        search_queries.append(f"{clean_t} E{episode:02d}")
        search_queries.append(clean_t)
    elif year:
        search_queries.append(f"{clean_t} {year}")
        search_queries.append(clean_t)
        search_queries.append(f"{clean_t} ({year})")
    else:
        search_queries.append(clean_t)

    slug_title = re.sub(r"[^a-z0-9]+", "-", clean_t.lower()).strip("-")
    direct_candidates = []
    if season and episode:
        direct_candidates.extend([
            f"{BASE_URL}/episodes/{slug_title}-{season}x{episode}/",
            f"{BASE_URL}/episodes/{slug_title}-{season}x{episode:02d}/",
            f"{BASE_URL}/episodes/{slug_title}-season-{season}-episode-{episode}/",
            f"{BASE_URL}/tvshows/{slug_title}/",
            f"{BASE_URL}/series/{slug_title}/",
            f"{BASE_URL}/{slug_title}/",
        ])
    elif season:
        direct_candidates.extend([
            f"{BASE_URL}/episodes/{slug_title}-{season}x1/",
            f"{BASE_URL}/tvshows/{slug_title}/",
            f"{BASE_URL}/series/{slug_title}/",
            f"{BASE_URL}/{slug_title}/",
        ])
    elif year:
        direct_candidates.extend([
            f"{BASE_URL}/movies/{slug_title}-{year}/",
            f"{BASE_URL}/movies/{slug_title}-{year}-sinhala-subtitles/",
            f"{BASE_URL}/{slug_title}-{year}/",
            f"{BASE_URL}/{slug_title}/",
            f"{BASE_URL}/tvshows/{slug_title}/",
        ])
    else:
        direct_candidates.extend([
            f"{BASE_URL}/movies/{slug_title}/",
            f"{BASE_URL}/tvshows/{slug_title}/",
            f"{BASE_URL}/series/{slug_title}/",
            f"{BASE_URL}/{slug_title}/",
        ])

    candidate_posts: list[tuple[str, int]] = []
    seen_posts = set()

    for q in search_queries:
        if any(sc >= 40 for _, sc in candidate_posts):
            break
        # 1. HTML Search
        try:
            s_url = f"{BASE_URL}/?s={urllib.parse.quote_plus(q)}"
            resp = await client.get(s_url, headers=HEADERS, timeout=6.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                selectors = ".display-item a, .item-box a, .result-item a, article a, h2 a, h3 a, .entry-title a, .search-item a"
                for a in soup.select(selectors):
                    raw_href = a.get("href", "").strip()
                    if not raw_href or raw_href.startswith("#"):
                        continue
                    full_href = urllib.parse.urljoin(BASE_URL, raw_href)
                    if "cinesubz" in full_href and not any(x in full_href for x in ("/category/", "/tag/", "/author/", "/page/", "#")):
                        if full_href not in seen_posts:
                            seen_posts.add(full_href)
                            txt = a.get_text(" ", strip=True) or a.get("title", "")
                            score = score_candidate_post(clean_t, full_href, txt, year=year, season=season, episode=episode)
                            if score > 0:
                                candidate_posts.append((full_href, score))
        except Exception as e_html:
            log.debug("[CineSubz] HTML search note: %s", e_html)

        # 2. WP API search
        if not candidate_posts:
            try:
                api_url = f"{BASE_URL}/wp-json/wp/v2/posts?search={urllib.parse.quote_plus(q)}&per_page=6"
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
                log.debug("[CineSubz] WP API note: %s", e_api)

    if not candidate_posts:
        for dc in direct_candidates:
            if dc not in seen_posts:
                seen_posts.add(dc)
                candidate_posts.append((dc, 20))

    candidate_posts.sort(key=lambda x: x[1], reverse=True)
    results: list[dict] = []

    for post_url, _ in candidate_posts[:6]:
        try:
            p_resp = await client.get(post_url, headers=HEADERS, timeout=6.0)
            if p_resp.status_code != 200:
                continue
            soup = BeautifulSoup(p_resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)

            verify_text = f"{post_url} {soup.title.get_text() if soup.title else ''} {soup.h1.get_text() if soup.h1 else ''} {page_text[:3000]}"
            is_matched = matches_title_and_year(clean_t, verify_text, year=year, season=season, episode=episode)
            if not is_matched and (season or episode):
                is_matched = matches_title_and_year(clean_t, verify_text, year=year, season=None, episode=None)
            if not is_matched:
                continue

            current_url = post_url
            if season and episode:
                ep_pat = re.compile(
                    rf"(?:s0*{season}\s*[-._xe/]\s*0*{episode}\b"
                    rf"|{season}x0*{episode}\b"
                    rf"|season[\s._-]*0*{season}[^a-z0-9]+(?:episode|ep|e)[-_\s]*0*{episode}\b"
                    rf"|(?:\b|[-_\[/])(?:ep|episode|e)\.?\s*0*{episode}(?:\b|[-_\]/]))",
                    re.IGNORECASE,
                )
                for a in soup.find_all("a", href=True):
                    h = a["href"].strip()
                    if ep_pat.search(h) or ep_pat.search(a.get_text(" ", strip=True)):
                        ep_link = urllib.parse.urljoin(post_url, h)
                        ep_resp = await client.get(ep_link, headers=HEADERS, timeout=8.0)
                        if ep_resp.status_code == 200:
                            soup = BeautifulSoup(ep_resp.text, "html.parser")
                            current_url = ep_link
                        break

            # 1. High-Speed ZetaPlayer Direct Streaming CDN Extraction (Top Priority for CineSubz)
            zetaplayer_streams = await _extract_cinesubz_zetaplayer_streams(client, soup, current_url)
            for zs in zetaplayer_streams:
                results.append(zs)

            # 2. Extract direct links (PixelDrain, Mega, etc.) while skipping non-media ad locker pages
            video_links = []
            intermediate_tasks = []

            # Only scan relevant container elements to avoid header/footer navigation
            scan_container = soup.find(id="directandtgdownload") or soup.find(id="links") or soup.find(class_="links-table") or soup.find("article") or soup
            for a in scan_container.find_all("a", href=True):
                h = a["href"].strip()
                if any(ign in h.lower() for ign in [
                    "t.me", "telegram.me", "#", "contact", "about", "terms", "privacy",
                    "dmca", "disclaimer", "wp-login", "account", "category", "tag",
                    "facebook", "twitter", "instagram", "youtube", "imdb", "wp-admin"
                ]):
                    continue
                if h.startswith("magnet:") or ".torrent" in h.lower():
                    continue

                ctx = a.get_text(" ", strip=True)
                q = detect_quality_from_context(ctx, h)

                # Check known direct patterns
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

                if not matched and any(k in h.lower() for k in ["api-", "/links/", "zt-links", "csplayer", "drive.google.com/server"]):
                    intermediate_tasks.append((h, q))

            if intermediate_tasks:
                async def _res_worker(link_u: str, link_q: str):
                    try:
                        resolved = await resolve_srilankan_intermediate_link(client, link_u, referer_url=current_url)
                        if resolved and not resolved.startswith("magnet:") and ".torrent" not in resolved.lower():
                            # Strictly filter out drive.csplayer2.space HTML ad pages
                            if "drive.csplayer2.space" in resolved:
                                return None
                            ht = "cdn" if "csplayer" in resolved else ("pixeldrain" if "pixeldrain" in resolved else "ddl")
                            return {
                                "url": resolved,
                                "quality": link_q,
                                "host_type": ht,
                            }
                    except Exception:
                        pass
                    return None

                resolved_list = await asyncio.gather(
                    *[_res_worker(u, q) for u, q in intermediate_tasks[:5]],
                    return_exceptions=True,
                )
                for r_item in resolved_list:
                    if isinstance(r_item, dict) and r_item.get("url"):
                        video_links.append(r_item)

            for vl in video_links:
                u_link = vl["url"]
                if "drive.csplayer2.space" in u_link:
                    continue  # Do not add non-downloadable HTML landing pages
                results.append({
                    "portal": "CineSubz",
                    "post_url": current_url,
                    "url": u_link,
                    "quality": vl["quality"],
                    "host_type": vl["host_type"],
                    "sub_srt_path": None,  # Pre-hardsubbed
                    "is_already_hardsubbed": True,
                })

            if results:
                break
        except Exception as e_post:
            log.debug("[CineSubz] Post inspect note: %s", e_post)

    return results
