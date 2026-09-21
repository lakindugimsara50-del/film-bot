"""
method1_telegram.py - Method 1 / Method C: Telegram Movie Channel Search & Resolver.

Strategy:
1. If direct Telegram link is provided (e.g. https://t.me/c/... or https://t.me/channel/123),
   resolves message and extracts video file_id directly.
2. If Pyrogram client is available, queries accessible channels or index channels
   for video messages matching movie title and year.

All log strings are in English to avoid Windows charmap errors.
"""

import logging
import re
from typing import Optional, Any

log = logging.getLogger(__name__)

# Public Telegram channels to check
SEARCH_CHANNELS = [
    "MoviesFlixPro",
    "Tamilmv_org",
    "DirectMovieLinks4u",
]


def parse_telegram_message_link(url: str) -> Optional[tuple[Any, int]]:
    """
    Parse a Telegram message link.
    Supports:
      https://t.me/c/1234567890/42  -> (-1001234567890, 42)
      https://t.me/username/42      -> ('username', 42)
    """
    # Private channel link
    m_priv = re.match(r"https?://t\.me/c/(\d+)/(\d+)", url)
    if m_priv:
        raw_id, msg_id = int(m_priv.group(1)), int(m_priv.group(2))
        chat_id = -1000000000000 - raw_id if raw_id > 0 else raw_id
        return chat_id, msg_id

    # Public channel link
    m_pub = re.match(r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)", url)
    if m_pub:
        return m_pub.group(1), int(m_pub.group(2))

    return None


async def resolve_telegram_link(client: Any, url: str) -> Optional[dict]:
    """Fetch video info directly from a Telegram message link."""
    parsed = parse_telegram_message_link(url)
    if not parsed or not client:
        return None

    chat_id, msg_id = parsed
    try:
        msg = await client.get_messages(chat_id, msg_id)
        if msg and (msg.video or msg.document):
            media = msg.video or msg.document
            file_id = media.file_id
            file_name = getattr(media, "file_name", "movie.mp4") or "movie.mp4"
            file_size = getattr(media, "file_size", 0)
            return {
                "method": "telegram",
                "file_id": file_id,
                "message": msg,
                "file_name": file_name,
                "file_size": file_size,
                "quality": "1080p",
                "server_label": "Telegram Direct Link",
            }
    except Exception as exc:
        log.warning("[M1-Telegram] Failed to resolve message %s: %s", url, exc)
    return None


async def search(
    title: str,
    year: Optional[int] = None,
    imdb_id: Optional[str] = None,
    bot_token: str = "",
    client: Any = None,
) -> Optional[dict]:
    """
    Search Telegram channels for matching video.
    """
    query = f"{title} {year}" if year else title
    log.info("[M1-Telegram] Searching Telegram for: %s", query)

    # If Pyrogram client is provided, try searching channels
    if client:
        for ch in SEARCH_CHANNELS:
            try:
                async for message in client.search_messages(ch, query=title, limit=10):
                    if message.video or (message.document and "video" in (message.document.mime_type or "")):
                        media = message.video or message.document
                        f_name = getattr(media, "file_name", "").lower()
                        # Check basic matching
                        if title.lower() in f_name or (message.caption and title.lower() in message.caption.lower()):
                            log.info("[M1-Telegram] Found video match in @%s: %s", ch, f_name)
                            return {
                                "method": "telegram",
                                "file_id": media.file_id,
                                "message": message,
                                "file_name": getattr(media, "file_name", "movie.mp4"),
                                "file_size": getattr(media, "file_size", 0),
                                "quality": "1080p" if "1080" in f_name else "720p",
                                "server_label": f"Telegram (@{ch})",
                            }
            except Exception as exc:
                log.debug("[M1-Telegram] Could not search @%s: %s", ch, exc)
                continue

    log.info("[M1-Telegram] No results found via Telegram channels.")
    return None
