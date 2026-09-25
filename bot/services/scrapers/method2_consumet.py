"""
method2_consumet.py - Method 2: Consumet / FlixHQ Streaming API.

Queries an open-source Consumet API instance to get direct .mp4 / .m3u8
stream URLs for a movie, given its title or IMDb ID.

All log strings are in English to avoid Windows charmap errors.
Docs: https://docs.consumet.org
"""

import logging
import re
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# You can self-host Consumet API (free, open-source) or use a public instance.
# Public instance (may be rate-limited): https://api.consumet.org
# Self-hosted (recommended for production): https://github.com/consumet/api.consumet.org
CONSUMET_BASE = "https://api.consumet.org"

# Alternate public instances as fallback
CONSUMET_MIRRORS = [
    "https://api.consumet.org",
    "https://consumet-api.onrender.com",
    "https://consumet.netlify.app/api",
]


async def _search_flixhq(title: str, year: Optional[int], client: httpx.AsyncClient) -> Optional[str]:
    """Search FlixHQ for the movie and return its FlixHQ media ID."""
    for base in CONSUMET_MIRRORS:
        try:
            resp = await client.get(
                f"{base}/movies/flixhq/{httpx.URL(title).path}",
                params={"query": title},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            results = data.get("results", [])
            if not results:
                continue
            # Filter by year if provided
            for item in results:
                item_year = item.get("releaseDate", "")[:4]
                if year and item_year and str(year) != item_year:
                    continue
                if item.get("type", "").lower() == "movie":
                    log.info("[M2-Consumet] Found FlixHQ match: %s (%s) id=%s", item.get("title"), item_year, item.get("id"))
                    return item.get("id")
            # If no exact year match, return first movie result
            for item in results:
                if item.get("type", "").lower() == "movie":
                    return item.get("id")
        except Exception as exc:
            log.warning("[M2-Consumet] FlixHQ search error on %s: %s", base, exc)
            continue
    return None


async def _get_stream_url(media_id: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Fetch episode info and stream URLs for a FlixHQ media ID."""
    for base in CONSUMET_MIRRORS:
        try:
            # Get episodes/seasons info first
            info_resp = await client.get(
                f"{base}/movies/flixhq/info",
                params={"id": media_id},
                timeout=10,
            )
            if info_resp.status_code != 200:
                continue
            info = info_resp.json()
            episodes = info.get("episodes", [])
            if not episodes:
                continue

            episode_id = episodes[0].get("id")
            if not episode_id:
                continue

            # Get actual stream sources
            stream_resp = await client.get(
                f"{base}/movies/flixhq/watch",
                params={"episodeId": episode_id, "mediaId": media_id},
                timeout=15,
            )
            if stream_resp.status_code != 200:
                continue
            stream_data = stream_resp.json()
            sources = stream_data.get("sources", [])
            if not sources:
                continue

            # Prefer 1080p, then 720p, then first available
            quality_priority = ["1080p", "720p", "480p", "auto", "default"]
            selected = None
            for q in quality_priority:
                for src in sources:
                    if q.lower() in src.get("quality", "").lower():
                        selected = src
                        break
                if selected:
                    break
            if not selected:
                selected = sources[0]

            # Collect all sources as server tabs
            servers = []
            for i, src in enumerate(sources[:4], 1):
                servers.append({
                    "server": f"Server {i}",
                    "label": f"Server {i} ({src.get('quality', 'Auto')})",
                    "type": "application/x-mpegURL" if ".m3u8" in src.get("url", "") else "video/mp4",
                    "stream_url": src.get("url", ""),
                })

            return {
                "method": "consumet_flixhq",
                "stream_url": selected.get("url", ""),
                "quality": selected.get("quality", "Auto"),
                "server_label": "FlixHQ (Consumet)",
                "servers": servers,
                "subtitles": stream_data.get("subtitles", []),
            }
        except Exception as exc:
            log.warning("[M2-Consumet] Stream fetch error on %s: %s", base, exc)
            continue
    return None


async def search(title: str, year: Optional[int] = None, imdb_id: Optional[str] = None, **_) -> Optional[dict]:
    """
    Method 2: Query Consumet/FlixHQ API.

    Returns result dict with stream_url + servers list, or None if not found.
    """
    log.info("[M2-Consumet] Searching for: %s (%s)", title, year or "?")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        media_id = await _search_flixhq(title, year, client)
        if not media_id:
            log.info("[M2-Consumet] No FlixHQ match found for: %s", title)
            return None
        result = await _get_stream_url(media_id, client)
        if result:
            log.info("[M2-Consumet] SUCCESS - stream_url obtained: %s", result["stream_url"][:80])
        else:
            log.info("[M2-Consumet] Could not get stream URL for media_id: %s", media_id)
        return result
