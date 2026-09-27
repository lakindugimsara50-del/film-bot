"""
announce.py — Post a formatted movie announcement to the public Telegram channel.

This handler is called after a movie is successfully added to the site so that
followers see it immediately.
"""

import logging
import re
import textwrap
from typing import Any, Optional

from pyrogram import Client
from pyrogram.enums import ParseMode

import config

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
async def post_to_channel(
    client: Client,
    movie: dict,
    public_channel_id: int,
) -> Optional[Any]:
    """
    Send a beautiful, formatted announcement to the public Telegram channel.

    Args:
        client:            The Pyrogram bot Client that is already started.
        movie:             The full movie dict (same schema as movies.json).
        public_channel_id: Telegram channel ID (negative integer for channels).

    Returns:
        The sent Pyrogram Message object if successful, else None.
    """
    try:
        text = _build_message(movie)
        poster_url: str = movie.get("poster_url", "")

        if poster_url:
            # Send the announcement with the movie poster as a photo
            msg = await client.send_photo(
                chat_id=public_channel_id,
                photo=poster_url,
                caption=text,
                parse_mode=ParseMode.HTML,
            )
        else:
            # Fallback: text-only announcement
            msg = await client.send_message(
                chat_id=public_channel_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )

        log.info("Announcement posted to channel %s for '%s' (msg_id: %s)", public_channel_id, movie.get("title"), getattr(msg, "id", None))
        return msg

    except Exception as exc:
        log.error("Failed to post announcement: %s", exc)
        # Don't re-raise — a failed announcement should not abort the /add workflow
        return None


async def update_channel_post(
    client: Client,
    public_channel_id: int,
    message_id: int,
    movie: dict,
) -> bool:
    """
    Update an existing channel announcement when downloads become available.
    """
    try:
        text = _build_message(movie)
        poster_url: str = movie.get("poster_url", "")

        if poster_url:
            await client.edit_message_caption(
                chat_id=public_channel_id,
                message_id=message_id,
                caption=text,
                parse_mode=ParseMode.HTML,
            )
        else:
            await client.edit_message_text(
                chat_id=public_channel_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
        log.info("Announcement updated in channel %s for message %s", public_channel_id, message_id)
        return True
    except Exception as exc:
        log.error("Failed to update announcement %s in channel %s: %s", message_id, public_channel_id, exc)
        return False



# ─────────────────────────────────────────────────────────────────────────────
def _build_message(movie: dict) -> str:
    """
    Compose the Telegram HTML caption from a movie dict.

    Output format:
    ─────────────────────────────────────────
    🎬 <b>Avatar 3</b> (2025)

    ⭐ IMDb: 8.1  |  🎭 Action, Adventure, Sci-Fi
    ⏱ 162 min  |  📺 1080p  |  🔊 Sinhala Sub

    📝 A paraplegic Marine is dispatched to the moon…

    🌐 <a href="https://yoursite.lk/movie/avatar-3">Watch Online</a>
    ⬇️ Download:  [480p]  [720p]  [1080p]

    🔔 @YourChannel
    ─────────────────────────────────────────
    """

    title: str = movie.get("title", "Unknown")
    year: int = movie.get("year", "")
    rating: str = movie.get("rating", "N/A")
    genres: list = movie.get("genres", [])
    duration: int = movie.get("duration", 0)
    description: str = movie.get("description", "")
    slug: str = movie.get("slug", _slugify(title))
    quality: str = movie.get("quality", "")
    lang: str = movie.get("lang", "")
    site_url: str = movie.get("site_url", "")
    files: list = movie.get("files", [])

    # ── Genres line ───────────────────────────────────────────────────────────
    genres_text = ", ".join(genres[:3]) if genres else "Movie"

    # ── Duration ─────────────────────────────────────────────────────────────
    duration_text = f"⏱ {duration} min  |  " if duration else ""

    # ── Short description (max 200 chars) ─────────────────────────────────────
    short_desc = textwrap.shorten(description, width=200, placeholder="…") if description else ""

    # ── Watch link ────────────────────────────────────────────────────────────
    raw_watch_link = site_url or f"{config.SITE_BASE_URL.rstrip('/')}/movie.html?id={slug}"
    watch_link = raw_watch_link.replace("https://yoursite.lk", "https://filmsub.pages.dev").replace("http://yoursite.lk", "https://filmsub.pages.dev")
    if not watch_link or "yoursite.lk" in watch_link:
        watch_link = f"https://filmsub.pages.dev/movie.html?id={slug}"

    # ── Download buttons (per resolution entry) ───────────────────────────────
    download_parts: list[str] = []
    seen_labels = set()
    downloads = movie.get("downloads", [])
    if downloads:
        # Prioritize bot deep-link downloads (?start=dl_) over private channel links (https://t.me/c/)
        sorted_dls = sorted(
            downloads,
            key=lambda d: 0 if "?start=dl_" in d.get("url", "") else (1 if not d.get("url", "").startswith("https://t.me/c/") else 2)
        )
        for d in sorted_dls:
            label = d.get("quality", "")
            dl_url = d.get("telegram_url", "") or d.get("url", "")
            if label and dl_url:
                m_q = re.search(r"\b(1080p|720p|480p|360p)\b", label, re.I)
                btn_lbl = m_q.group(1) if m_q else label
                if btn_lbl not in seen_labels:
                    seen_labels.add(btn_lbl)
                    download_parts.append(f'<a href="{dl_url}">[{btn_lbl}]</a>')
    else:
        for f in files:
            label = f.get("quality", "")
            dl_url = f.get("telegram_url", "") or f.get("url", "") or f.get("stream_url", "")
            if label and dl_url:
                m_q = re.search(r"\b(1080p|720p|480p|360p)\b", label, re.I)
                btn_lbl = m_q.group(1) if m_q else label
                if btn_lbl not in seen_labels:
                    seen_labels.add(btn_lbl)
                    download_parts.append(f'<a href="{dl_url}">[{btn_lbl}]</a>')

    if download_parts:
        download_line = "⬇️ Download:  " + "  ".join(download_parts)
    elif movie.get("telegram_status") in ("queued", "uploading"):
        download_line = "⏳ <b>Telegram Downloads:</b> <i>Upload වෙමින් පවතී... (Soon)</i>"
    else:
        download_line = ""

    # ── Assemble the message ──────────────────────────────────────────────────
    lines = [
        f"🎬 <b>{title}</b> ({year})\n",
        f"⭐ IMDb: {rating}  |  🎭 {genres_text}",
        f"{duration_text}📺 {quality}  |  🔊 {lang}".strip(" |"),
    ]

    if short_desc:
        lines.append("")
        lines.append(f"📝 {short_desc}")

    lines.append("")
    lines.append(f'🌐 <a href="{watch_link}">Watch Online</a>')

    if download_line:
        lines.append(download_line)

    lines.append("")
    lines.append("🔔 Stay tuned for more movies!")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
def _slugify(title: str, year: Any = None) -> str:
    """Convert 'Avatar 3', 2025 → 'avatar-3-2025' or 'avatar-3' for use in URLs."""
    import re
    full = f"{title} {year}" if year else title
    slug = str(full).lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug
