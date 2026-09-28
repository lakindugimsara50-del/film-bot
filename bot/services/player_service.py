"""
player_service.py — VIP 1-3 Player URL management.

Provides centralised player URL building for Server 1 (VidLink Pro),
Server 2 (AutoEmbed HD), and Server 3 (MultiEmbed Fast).

Usage:
    from services.player_service import get_player_url, PLAYER_ORDER

    url = get_player_url("vip1", movie, season=1, episode=3)
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Player order ──────────────────────────────────────────────────────────────
# Controls the display order in Telegram inline buttons.
# "super" intentionally excluded → handled natively by Video.js chunk-stream.
PLAYER_ORDER = ["vip1", "vip2", "vip3"]

# Human-readable labels shown on Telegram buttons / website tabs
PLAYER_LABELS = {
    "vip1": "🎬 VIP Player 1 (VidLink Pro Ultra HD)",
    "vip2": "⚡ VIP Player 2 (AutoEmbed HD)",
    "vip3": "🚀 VIP Player 3 (MultiEmbed Fast)",
}

# super_player = None → we never expose the internal Telegram Cloud URL
# to Telegram messages; it is only used inside the website's Video.js player.
super_player: Optional[str] = None


def _resolve_ids(movie: dict) -> tuple[str, str]:
    """Extract (imdb_id, tmdb_id) from a movie dict, trying multiple fields."""
    import re
    imdb_id = (movie.get("imdb_id") or movie.get("imdbId") or "").strip()
    tmdb_id = str(movie.get("tmdb_id") or movie.get("tmdbId") or "").strip()

    # imdb field may contain a raw tt-ID
    if not imdb_id:
        raw_imdb = (movie.get("imdb") or "").strip()
        if re.match(r"^tt\d+$", raw_imdb, re.IGNORECASE):
            imdb_id = raw_imdb

    # Scan URLs/slugs as a last resort
    if not imdb_id or not tmdb_id:
        candidates = [
            movie.get("stream_url", ""),
            movie.get("slug", ""),
            movie.get("id", ""),
        ]
        for url in candidates:
            if not imdb_id:
                m = re.search(r"(tt\d{6,10})", url, re.IGNORECASE)
                if m:
                    imdb_id = m.group(1)
            if not tmdb_id:
                m = re.search(r"tmdb[=_/](\d{3,10})", url, re.IGNORECASE)
                if m:
                    tmdb_id = m.group(1)
            if imdb_id and tmdb_id:
                break

    return imdb_id, tmdb_id


def get_player_url(
    player_name: str,
    movie: dict,
    season: int = 1,
    episode: int = 1,
    subtitle_url: Optional[str] = None,
) -> str:
    """
    Build and return the embed URL for the requested VIP player.

    Args:
        player_name:  One of "vip1", "vip2", "vip3".
        movie:        Movie dict from movies.json.
        season:       Season number (for series).
        episode:      Episode number (for series).
        subtitle_url: Optional external VTT subtitle URL to inject into VidLink.

    Returns:
        The iframe-embeddable URL string.
    """
    import urllib.parse

    if not movie:
        return ""

    imdb_id, tmdb_id = _resolve_ids(movie)
    is_series = (
        movie.get("type") == "series"
        or bool(movie.get("seasons"))
        or season > 1
        or episode > 1
    )
    s, e = season or 1, episode or 1
    player_name = (player_name or "vip1").lower().strip()

    # ── VIP 1: VidLink Pro ────────────────────────────────────────────────────
    if player_name == "vip1":
        sub_param = ""
        if subtitle_url:
            sub_param = f"?primaryColor=ffeb3b&sub.Sinhala={urllib.parse.quote(subtitle_url, safe='')}"

        if is_series:
            if tmdb_id:
                return f"https://vidlink.pro/tv/{tmdb_id}/{s}/{e}{sub_param}"
            if imdb_id:
                return f"https://autoembed.co/tv/imdb/{imdb_id}-{s}-{e}"
            return f"https://vidlink.pro/tv/1399/{s}/{e}"
        else:
            if tmdb_id:
                return f"https://vidlink.pro/movie/{tmdb_id}{sub_param}"
            if imdb_id:
                return f"https://autoembed.co/movie/imdb/{imdb_id}"
            return "https://vidlink.pro/movie/564147"

    # ── VIP 2: AutoEmbed ──────────────────────────────────────────────────────
    if player_name == "vip2":
        if is_series:
            if imdb_id:
                return f"https://autoembed.co/tv/imdb/{imdb_id}-{s}-{e}"
            if tmdb_id:
                return f"https://autoembed.co/tv/tmdb/{tmdb_id}-{s}-{e}"
        else:
            if imdb_id:
                return f"https://autoembed.co/movie/imdb/{imdb_id}"
            if tmdb_id:
                return f"https://autoembed.co/movie/tmdb/{tmdb_id}"
        # Absolute fallback: re-use vip1 URL
        return get_player_url("vip1", movie, season, episode, subtitle_url)

    # ── VIP 3: MultiEmbed ─────────────────────────────────────────────────────
    if player_name == "vip3":
        embed_key = imdb_id or tmdb_id or urllib.parse.quote(movie.get("title", "movie"))
        if is_series:
            return f"https://multiembed.mov/?video_id={embed_key}&s={s}&e={e}"
        return f"https://multiembed.mov/?video_id={embed_key}"

    log.warning("[PlayerService] Unknown player_name '%s'; defaulting to vip1.", player_name)
    return get_player_url("vip1", movie, season, episode, subtitle_url)


def build_player_keyboard(movie: dict, season: int = 1, episode: int = 1):
    """
    Build a Pyrogram InlineKeyboardMarkup with one button per VIP player.
    Stores the selected player name in callback_data so the handler can
    write `user_data["active_player"]`.
    """
    try:
        from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError:
        return None

    buttons = [
        [InlineKeyboardButton(
            text=PLAYER_LABELS.get(p, p),
            callback_data=f"add_player:{p}",
        )]
        for p in PLAYER_ORDER
    ]
    buttons.append(
        [InlineKeyboardButton("❌ Cancel", callback_data="player:cancel")]
    )
    return InlineKeyboardMarkup(buttons)
