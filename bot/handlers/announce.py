"""
announce.py — Post a formatted movie announcement to the public Telegram channel.

This handler is called after a movie is successfully added to the site so that
followers see it immediately with a high-definition banner and premium layout.
"""

import logging
import os
import re
import urllib.parse
from typing import Any, Optional

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
async def post_to_channel(
    client: Client,
    movie: dict,
    public_channel_id: int = 0,
) -> Optional[Any]:
    """
    Send a beautiful, premium formatted announcement with banner to the public Telegram channel.

    Args:
        client:            The Pyrogram bot Client that is already started.
        movie:             The full movie dict (same schema as movies.json).
        public_channel_id: Telegram channel ID (negative integer for channels).

    Returns:
        The sent Pyrogram Message object if successful, else None.
    """
    target_channel = public_channel_id or getattr(config, "PUBLIC_CHANNEL_ID", -1004301214474)
    if not target_channel:
        log.warning("[Announce] No public channel ID configured; skipping announcement.")
        return None

    try:
        bot_username = getattr(client, "username", None) or os.getenv("BOT_USERNAME", "Filmsinhala200Bot")
        caption = _build_message(movie, bot_username)
        reply_markup = _build_keyboard(movie, bot_username)

        # Collect high-res banner candidates (cinematic 16:9 backdrop preferred, fallback to poster)
        banner_candidates = _get_banner_candidates(movie)

        sent_msg = None
        for photo_url in banner_candidates:
            try:
                sent_msg = await client.send_photo(
                    chat_id=target_channel,
                    photo=photo_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
                if sent_msg:
                    log.info(
                        "[Announce] Successfully posted photo announcement to %s for '%s' (msg_id: %s)",
                        target_channel,
                        movie.get("title"),
                        getattr(sent_msg, "id", None),
                    )
                    break
            except Exception as photo_err:
                log.warning("[Announce] send_photo failed for %s: %s", photo_url[:60], photo_err)

        if not sent_msg:
            # Fallback: text-only announcement with inline keyboard
            sent_msg = await client.send_message(
                chat_id=target_channel,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=False,
            )
            log.info(
                "[Announce] Posted text announcement to %s for '%s' (msg_id: %s)",
                target_channel,
                movie.get("title"),
                getattr(sent_msg, "id", None),
            )

        return sent_msg

    except Exception as exc:
        log.error("Failed to post announcement to %s for '%s': %s", target_channel, movie.get("title"), exc)
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
    target_channel = public_channel_id or getattr(config, "PUBLIC_CHANNEL_ID", -1004301214474)
    if not target_channel or not message_id:
        return False

    try:
        bot_username = getattr(client, "username", None) or os.getenv("BOT_USERNAME", "Filmsinhala200Bot")
        caption = _build_message(movie, bot_username)
        reply_markup = _build_keyboard(movie, bot_username)

        banner_candidates = _get_banner_candidates(movie)

        if banner_candidates:
            await client.edit_message_caption(
                chat_id=target_channel,
                message_id=message_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            await client.edit_message_text(
                chat_id=target_channel,
                message_id=message_id,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=False,
            )
        log.info("[Announce] Announcement updated in channel %s for message %s", target_channel, message_id)
        return True
    except Exception as exc:
        log.error("[Announce] Failed to update announcement %s in channel %s: %s", message_id, target_channel, exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
def _get_banner_candidates(movie: dict) -> list[str]:
    """Collect banner candidates in priority order: 1280px backdrop, original backdrop, poster."""
    candidates = []
    backdrop = movie.get("backdrop_url") or movie.get("backdrop") or ""
    poster = movie.get("poster_url") or movie.get("poster") or ""

    if backdrop and backdrop.startswith("http"):
        if "/original/" in backdrop:
            candidates.append(backdrop.replace("/original/", "/w1280/"))
        candidates.append(backdrop)

    if poster and poster.startswith("http"):
        if "/w500/" in poster:
            candidates.append(poster.replace("/w500/", "/w780/"))
        candidates.append(poster)

    return candidates


def _build_keyboard(movie: dict, bot_username: str = "") -> InlineKeyboardMarkup:
    """Compose user-friendly premium Telegram inline keyboard buttons."""
    if not bot_username:
        bot_username = getattr(config, "BOT_USERNAME", "Filmsinhala200Bot")

    title = movie.get("title", "Movie")
    clean_title = re.sub(r"\s*--[a-zA-Z0-9_-]+", "", title).strip()
    slug = movie.get("slug", _slugify(title))
    site_base = getattr(config, "SITE_BASE_URL", "https://filmsub.pages.dev").rstrip("/")
    watch_link = f"{site_base}/movie.html?id={slug}"

    buttons = [
        [InlineKeyboardButton("🌐 Watch Online (නරඹන්න)", url=watch_link)]
    ]

    # Extract quality buttons
    dls = movie.get("downloads", []) or []
    quality_btns = []
    seen_q = set()
    for d in dls:
        q_raw = d.get("quality", "")
        mq = re.search(r"\b(1080p|720p|480p|360p)\b", q_raw, re.I)
        if mq:
            q_norm = mq.group(1).lower()
            if q_norm not in seen_q:
                seen_q.add(q_norm)
                sz = d.get("size", "")
                label = f"📥 {q_norm.upper()}" + (f" ({sz})" if sz else "")
                bot_url = f"https://t.me/{bot_username}?start=dl_{slug}_{q_norm}"
                quality_btns.append(InlineKeyboardButton(label, url=bot_url))

    # Add quality buttons in rows of 2
    for i in range(0, len(quality_btns), 2):
        buttons.append(quality_btns[i : i + 2])

    # Footer row: Bot link & Share link
    share_text = urllib.parse.quote_plus(f"Watch {clean_title} with Sinhala Subtitles on FilmSub!")
    share_url = f"https://t.me/share/url?url={urllib.parse.quote_plus(watch_link)}&text={share_text}"

    buttons.append([
        InlineKeyboardButton("🤖 Bot Download", url=f"https://t.me/{bot_username}?start=dl_{slug}"),
        InlineKeyboardButton("👥 Share Film", url=share_url),
    ])

    return InlineKeyboardMarkup(buttons)


def _build_message(movie: dict, bot_username: str = "") -> str:
    """
    Compose the Telegram HTML caption from a movie dict.
    Strictly guarantees length <= 1024 characters for Telegram photo captions.
    """
    if not bot_username:
        bot_username = getattr(config, "BOT_USERNAME", "Filmsinhala200Bot")

    title: str = movie.get("title", "Unknown")
    clean_title = re.sub(r"\s*--[a-zA-Z0-9_-]+", "", title).strip()
    m_yr = re.search(r"\b(19\d\d|20\d\d)\b", clean_title)
    clean_name = re.sub(r"\s*\b(19\d\d|20\d\d)\b.*$", "", clean_title).strip() or clean_title
    year: Any = movie.get("year") or (m_yr.group(1) if m_yr else "")

    genres: list = movie.get("genres", [])
    genres_text = ", ".join(genres[:3]) if genres else "Action, Adventure"

    raw_rating = str(movie.get("rating") or movie.get("imdb") or "N/A").strip()
    rating_str = f"⭐ <b>IMDb:</b> {raw_rating} / 10 🌟" if raw_rating and raw_rating.lower() != "n/a" else "⭐ <b>IMDb:</b> Top Rated 🌟"

    dur = str(movie.get("duration", "")).replace("min", "").strip()
    duration_str = f"⏱ {dur} min" if dur and dur != "0" else ""
    dur_sep = f"  |  {duration_str}" if duration_str else ""

    slug: str = movie.get("slug", _slugify(title))
    site_base = getattr(config, "SITE_BASE_URL", "https://filmsub.pages.dev").rstrip("/")
    watch_link = f"{site_base}/movie.html?id={slug}"

    # Extract quality lines
    dls = movie.get("downloads", []) or []
    quality_lines = []
    seen_q = set()
    for d in dls:
        q_raw = d.get("quality", "")
        mq = re.search(r"\b(1080p|720p|480p|360p)\b", q_raw, re.I)
        if mq:
            q_norm = mq.group(1).lower()
            if q_norm not in seen_q:
                seen_q.add(q_norm)
                sz = d.get("size", "")
                sz_str = f" ({sz})" if sz else ""
                quality_lines.append(f"• 📥 <b>{q_norm.upper()}</b>{sz_str}")

    if not quality_lines:
        quality_lines = [
            "• 📥 <b>1080p Full HD</b>",
            "• 📥 <b>720p HD</b>",
            "• 📥 <b>480p SD</b>",
        ]

    downloads_block = "\n".join(quality_lines)
    year_str = f" ({year})" if year else ""
    header_block = (
        f"🎬 <b>{clean_name}{year_str}</b>\n"
        f"<i>සිංහල උපසිරැසි සමඟ | Sinhala Subtitles Merged</i> ✅\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎭 <b>කාණ්ඩය (Genre):</b> {genres_text}\n"
        f"{rating_str}{dur_sep}\n"
        f"🔊 <b>ශ්‍රව්‍ය (Audio):</b> English / Multi\n"
        f"🎥 <b>තත්ත්ව (Quality):</b> 1080p FHD • 720p HD • 480p SD\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
    )

    footer_block = (
        f"⬇️ <b>ලබාගත හැකි පිටපත් (Downloads):</b>\n"
        f"{downloads_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Website:</b> <a href=\"{watch_link}\">filmsub.pages.dev</a>\n"
        f"👇 <b>නැරඹීමට සහ Download කිරීමට පහත Buttons භාවිතා කරන්න:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <i>FilmSub Official Cinema Network | Share with Friends!</i> ❤️"
    )

    wrapper_len = len("📖 <i>\"\"</i>\n━━━━━━━━━━━━━━━━━━━━━\n")
    avail_chars = 950 - len(header_block) - len(footer_block) - wrapper_len
    raw_desc = movie.get("description", "") or ""
    desc_block = ""
    if raw_desc and avail_chars >= 50:
        clean_desc = re.sub(r"<[^>]+>", "", raw_desc).strip()
        max_len = min(avail_chars, 160)
        if len(clean_desc) > max_len:
            clean_desc = clean_desc[:max_len].rsplit(" ", 1)[0] + "…"
        desc_block = f"📖 <i>\"{clean_desc}\"</i>\n━━━━━━━━━━━━━━━━━━━━━\n"

    caption = header_block + desc_block + footer_block
    if len(caption) > 1020:
        caption = header_block + footer_block
    return caption


def _slugify(title: str, year: Any = None) -> str:
    """Convert 'Avatar 3', 2025 → 'avatar-3-2025' or 'avatar-3' for use in URLs."""
    full = f"{title} {year}" if year else title
    slug = str(full).lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug)
