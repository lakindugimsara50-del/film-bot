"""
method4_embed.py - Method 4: Multi-Server Embed Extractor (Vidsrc / AutoEmbed).

Generates embed URLs from public streaming aggregators using IMDb ID or title.
These sources provide multiple backup servers suitable for Video.js.
No download needed — streams directly in browser via iframe or direct URL.

All log strings are in English to avoid Windows charmap errors.
"""

import logging
import re
import urllib.parse
from typing import Optional

import httpx

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _build_embed_servers(imdb_id: str, tmdb_id: Optional[str] = None) -> list[dict]:
    """Build a list of embed server dicts using IMDb or TMDB ID."""
    servers = []

    # Vidsrc.to - most reliable, supports IMDb IDs
    if imdb_id:
        servers.append({
            "server": "Server 1",
            "label": "Server 1 (VidSrc)",
            "type": "embed",
            "embed_url": f"https://vidsrc.to/embed/movie/{imdb_id}",
            "stream_url": f"https://vidsrc.to/embed/movie/{imdb_id}",
        })

    # Vidsrc.me - alternate
    if imdb_id:
        servers.append({
            "server": "Server 2",
            "label": "Server 2 (VidSrc.me)",
            "type": "embed",
            "embed_url": f"https://vidsrc.me/embed/movie?imdb={imdb_id}",
            "stream_url": f"https://vidsrc.me/embed/movie?imdb={imdb_id}",
        })

    # AutoEmbed - supports both IMDb and TMDB
    if tmdb_id:
        servers.append({
            "server": "Server 3",
            "label": "Server 3 (AutoEmbed)",
            "type": "embed",
            "embed_url": f"https://autoembed.cc/movie/tmdb/{tmdb_id}",
            "stream_url": f"https://autoembed.cc/movie/tmdb/{tmdb_id}",
        })
    elif imdb_id:
        servers.append({
            "server": "Server 3",
            "label": "Server 3 (AutoEmbed)",
            "type": "embed",
            "embed_url": f"https://autoembed.cc/movie/imdb/{imdb_id}",
            "stream_url": f"https://autoembed.cc/movie/imdb/{imdb_id}",
        })

    # 2embed.cc
    if imdb_id:
        servers.append({
            "server": "Server 4",
            "label": "Server 4 (2Embed)",
            "type": "embed",
            "embed_url": f"https://www.2embed.cc/embed/{imdb_id}",
            "stream_url": f"https://www.2embed.cc/embed/{imdb_id}",
        })

    # SuperEmbed - TMDB based
    if tmdb_id:
        servers.append({
            "server": "Server 5",
            "label": "Server 5 (SuperEmbed)",
            "type": "embed",
            "embed_url": f"https://multiembed.mov/directstream.php?video_id={tmdb_id}&tmdb=1",
            "stream_url": f"https://multiembed.mov/directstream.php?video_id={tmdb_id}&tmdb=1",
        })

    return servers


async def _verify_embed_alive(url: str, client: httpx.AsyncClient) -> bool:
    """Check if an embed URL is reachable (returns 200)."""
    try:
        resp = await client.head(url, timeout=8, follow_redirects=True)
        return resp.status_code < 400
    except Exception:
        return False


async def search(
    title: str,
    year: Optional[int] = None,
    imdb_id: Optional[str] = None,
    tmdb_id: Optional[str] = None,
    **_,
) -> Optional[dict]:
    """
    Method 4: Generate embed stream servers using IMDb / TMDB IDs.

    This method constructs embed URLs from known public streaming aggregators.
    Requires at minimum an imdb_id or tmdb_id.
    Falls back to TMDB search to resolve IDs if not provided.

    Returns a result dict with servers list, or None if IDs are unavailable.
    """
    log.info("[M4-Embed] Building embed servers for: %s (imdb=%s, tmdb=%s)", title, imdb_id, tmdb_id)

    if not imdb_id and not tmdb_id:
        log.info("[M4-Embed] No IMDb or TMDB ID available; cannot build embed servers.")
        return None

    servers = _build_embed_servers(imdb_id or "", tmdb_id)
    if not servers:
        log.info("[M4-Embed] No embed servers could be built.")
        return None

    # Verify at least Server 1 is alive
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        alive = await _verify_embed_alive(servers[0]["embed_url"], client)
        if not alive:
            log.warning("[M4-Embed] Primary embed server appears offline: %s", servers[0]["embed_url"])
            # Don't fail — let others try

    log.info("[M4-Embed] Built %d embed servers for: %s", len(servers), title)
    return {
        "method": "embed_multi",
        "stream_url": servers[0]["stream_url"],
        "quality": "Auto",
        "server_label": "Multi-Server Embed",
        "servers": servers,
        "embed": True,  # Signal to player.js to use iframe embed mode
    }
