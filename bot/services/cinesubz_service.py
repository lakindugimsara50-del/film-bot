"""
cinesubz_service.py — Scrape and resolve CS Player & Evo Player from CineSubz.co.

Provides:
  resolve_cinesubz_players(title, year, season, episode) -> dict with:
    - cs_player: direct MP4 stream URL with Sinhala subtitles
    - evo_player: EvoStream embed URL (https://evostream.top/video/...)
    - page_url: CineSubz source URL
"""

import asyncio
import logging
import re
import urllib.parse
from typing import Optional, Dict

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_BASE_URL = "https://cinesubz.co"


async def resolve_cinesubz_players(
    title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    timeout: float = 12.0,
) -> Dict[str, Optional[str]]:
    """
    Search CineSubz for a movie or TV episode and resolve both CS Player and Evo Player.
    
    Returns:
      {
        "cs_player": "https://player1.setwenna.one/...mp4" or None,
        "evo_player": "https://evostream.top/video/..." or None,
        "page_url": "https://cinesubz.co/..." or None
      }
    """
    result: Dict[str, Optional[str]] = {
        "cs_player": None,
        "evo_player": None,
        "page_url": None,
    }

    if not title:
        return result

    clean_title = re.sub(r"[^\w\s]", " ", title).strip()
    words = clean_title.split()
    if not words:
        return result

    # 1. Search CineSubz
    search_q = "+".join(words[:4])
    search_url = f"{_BASE_URL}/?s={search_q}"

    try:
        async with httpx.AsyncClient(headers=_DEFAULT_HEADERS, follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(search_url)
            if resp.status_code != 200:
                log.debug("[CineSubz] Search request status: %d", resp.status_code)
                return result

            soup = BeautifulSoup(resp.text, "html.parser")
            target_link = None
            is_tv = bool(season and episode)

            for a in soup.find_all("a", href=True):
                h = a["href"]
                if is_tv and "/tvshows/" in h:
                    if words[0].lower() in h.lower():
                        target_link = h
                        break
                elif not is_tv and "/movies/" in h:
                    if words[0].lower() in h.lower():
                        target_link = h
                        break

            # Fallback check
            if not target_link:
                for a in soup.find_all("a", href=True):
                    h = a["href"]
                    if ("/movies/" in h or "/tvshows/" in h) and not any(x in h for x in ["/category/", "/genre/", "/tag/", "#"]):
                        if words[0].lower() in h.lower():
                            target_link = h
                            break

            if not target_link:
                log.debug("[CineSubz] No target link found for '%s'", title)
                return result

            result["page_url"] = target_link
            ep_page_url = target_link

            # 2. For TV Series, find specific episode page link
            if is_tv:
                ep_resp = await client.get(target_link)
                if ep_resp.status_code == 200:
                    ep_soup = BeautifulSoup(ep_resp.text, "html.parser")
                    # Patterns: /episodes/game-of-thrones-3x8/ or 3x08
                    s_token = f"{season}x{episode}"
                    s_token2 = f"{season}x{episode:02d}"
                    for ea in ep_soup.find_all("a", href=True):
                        eh = ea["href"]
                        if "/episodes/" in eh and (s_token in eh or s_token2 in eh):
                            ep_page_url = eh
                            result["page_url"] = eh
                            break

            # 3. Load episode or movie page and extract zetaflix player options
            page_resp = await client.get(ep_page_url)
            if page_resp.status_code != 200:
                return result

            p_soup = BeautifulSoup(page_resp.text, "html.parser")
            player_opts = p_soup.select("li.zetaflix_player_option")

            for opt in player_opts:
                post_id = opt.get("data-post")
                opt_num = opt.get("data-nume")
                opt_type = opt.get("data-type") or ("ep" if is_tv else "mv")
                opt_text = opt.get_text(strip=True).lower()

                if not (post_id and opt_num and opt_num != "trailer"):
                    continue

                api_url = f"{_BASE_URL}/wp-json/zetaplayer/v2/{post_id}/{opt_type}/{opt_num}"
                try:
                    r_api = await client.get(api_url, headers={"Referer": ep_page_url}, timeout=8.0)
                    if r_api.status_code == 200:
                        data = r_api.json()
                        embed_raw = data.get("embed_url", "")
                        # Parse iframe src if embed_raw is an iframe tag
                        m_if = re.search(r'src=["\']([^"\']+)["\']', embed_raw)
                        stream_src = m_if.group(1) if m_if else embed_raw

                        if not stream_src:
                            continue

                        # Check if CS Player or direct MP4 with Sinhala sub
                        if "cs player" in opt_text or ".mp4" in stream_src.lower() or "setwenna" in stream_src or "csplayer" in stream_src:
                            result["cs_player"] = stream_src
                            log.info("[CineSubz] Resolved CS Player for '%s': %s", title, stream_src)
                        # Check if Evo Player or EvoStream
                        elif "evo" in opt_text or "evostream" in stream_src or "evoload" in stream_src:
                            result["evo_player"] = stream_src
                            log.info("[CineSubz] Resolved Evo Player for '%s': %s", title, stream_src)
                except Exception as api_err:
                    log.debug("[CineSubz] API inspect note: %s", api_err)

    except Exception as exc:
        log.warning("[CineSubz] Resolver error for '%s': %s", title, exc)

    return result
