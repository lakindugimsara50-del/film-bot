"""
branding_service.py — Unified branding, filename sanitization, and presentation service.

Ensures that all movies, TV series, video uploads, and bot delivery messages
carry FilmSub / FilmZilla branding:
1. Replaces foreign portal tags ([CineSubz.co], [BaiscopeLK], etc.) with [FilmSub.lk].
2. Generates clean, branded video filenames:
   e.g. "Aasha (2026) Malayalam WEB-[FilmSub.lk]-480p.mp4"
3. Supplies crisp 16:9 branded video thumbnails (filmzilla_branded_thumb.jpg)
   for Telegram preview cards.
4. Generates rich HTML captions with file size, Sinhala subtitles note, and site link.
5. Builds interactive inline keyboards with Watch Online, Official Channel,
   and Request Bot buttons.
"""

import logging
import os
import re
import shutil
import urllib.parse
from typing import Optional

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config

log = logging.getLogger(__name__)

# Base assets directory
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
BRANDED_THUMB_PATH = os.path.join(_ASSETS_DIR, "filmzilla_branded_thumb.jpg")
INTRO_CARD_PATH = os.path.join(_ASSETS_DIR, "filmzilla_intro_card.png")

# Regex to catch foreign site tags in filenames or text
_FOREIGN_BRAND_RE = re.compile(
    r"\[?\s*(?:www\.)?(?:cinesubz(?:\.[a-z]{2,4})?|baiscopelk(?:\.com)?|baiscope|piratelk(?:\.com)?|zoom\.lk|subzlk)\s*\]?",
    re.IGNORECASE,
)


def clean_source_branding(text: str) -> str:
    """Strip external website watermarks and portal tags from filenames or captions."""
    if not text:
        return ""
    cleaned = _FOREIGN_BRAND_RE.sub("", text)
    # Clean up double dashes, double dots, or orphan brackets left behind
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def format_branded_filename(
    title: str,
    year: str = "",
    quality: str = "",
    language: str = "",
    original_name: str = "",
) -> str:
    """
    Format a clean, branded filename with [FilmSub.lk] for Telegram video delivery.

    Examples:
        Input: "Aasha (2026) Malayalam WEB-[CineSubz.co]-480p.mp4"
        Output: "Aasha (2026) Malayalam WEB-[FilmSub.lk]-480p.mp4"

        Input: title="Sugriva", year="2026", quality="1080p", language="Telugu"
        Output: "Sugriva (2026) Telugu [1080p] [FilmSub.lk].mp4"
    """
    ext = ".mp4"
    if original_name:
        _, orig_ext = os.path.splitext(original_name)
        if orig_ext.lower() in [".mp4", ".mkv", ".webm", ".avi"]:
            ext = orig_ext.lower()

    # If original_name is a substantial release name (not just an internal temp slug like fs_... or ep_...)
    is_temp_slug = bool(
        re.match(r"^(?:fs_|ep_|temp_|dl_|vid_|video_|\d+_)", original_name.lower())
        or len(original_name.split("-")) <= 3 and "_" in original_name
    )

    if original_name and not is_temp_slug:
        base, _ = os.path.splitext(original_name)
        # Check if source branding is present
        if _FOREIGN_BRAND_RE.search(base):
            branded_base = _FOREIGN_BRAND_RE.sub("[FilmSub.lk]", base)
        else:
            # Does not have foreign brand; append [FilmSub.lk] if not already present
            if "[FilmSub.lk]" not in base and "FilmSub" not in base:
                # Insert before quality if present, else append
                q_match = re.search(r"[-_\s]?(1080p|720p|480p|360p)\b", base, re.I)
                if q_match:
                    start = q_match.start()
                    branded_base = f"{base[:start].strip()} [FilmSub.lk] {base[start:].strip()}"
                else:
                    branded_base = f"{base.strip()} [FilmSub.lk]"
            else:
                branded_base = base

        # Clean any awkward formatting
        branded_base = re.sub(r"-{2,}", "-", branded_base)
        branded_base = re.sub(r"\s{2,}", " ", branded_base)
        branded_base = re.sub(r"\[FilmSub\.lk\]\s*\[FilmSub\.lk\]", "[FilmSub.lk]", branded_base, flags=re.I)
        # Normalize extension
        safe_name = re.sub(r'[\\/*?:"<>|]', "", branded_base).strip()
        return f"{safe_name}{ext}"

    # Build fresh branded release name
    clean_title = clean_source_branding(title).strip()
    year_str = f"({year})" if (year and str(year) not in clean_title) else ""
    lang_str = f" {language.strip()}" if language and language.lower() not in clean_title.lower() else ""
    q_str = f" [{quality.strip()}]" if quality else ""

    parts = [clean_title]
    if year_str:
        parts.append(year_str)
    if lang_str:
        parts.append(lang_str.strip())
    if q_str:
        parts.append(q_str.strip())
    parts.append("[FilmSub.lk]")

    joined = " ".join(p for p in parts if p)
    joined = re.sub(r"\s{2,}", " ", joined)
    safe_name = re.sub(r'[\\/*?:"<>|]', "", joined).strip()
    return f"{safe_name}{ext}"


def get_branded_thumbnail(target_path: Optional[str] = None) -> Optional[str]:
    """
    Get a guaranteed 16:9 widescreen branded thumbnail JPEG (640x360).
    If target_path is specified, copies the branded thumbnail to target_path.
    """
    source_thumb = None
    if os.path.exists(BRANDED_THUMB_PATH) and os.path.getsize(BRANDED_THUMB_PATH) > 1024:
        source_thumb = BRANDED_THUMB_PATH
    elif os.path.exists(INTRO_CARD_PATH):
        source_thumb = INTRO_CARD_PATH

    if not source_thumb:
        return None

    if target_path:
        try:
            # If source is already JPEG, direct copy
            if source_thumb.lower().endswith(".jpg") or source_thumb.lower().endswith(".jpeg"):
                shutil.copyfile(source_thumb, target_path)
                return target_path
            # Else convert to JPEG with PIL
            from PIL import Image
            im = Image.open(source_thumb).convert("RGB")
            thumb = im.resize((640, 360), Image.Resampling.LANCZOS)
            thumb.save(target_path, "JPEG", quality=92)
            return target_path
        except Exception as exc:
            log.warning("[BrandingService] Failed copying branded thumbnail to %s: %s", target_path, exc)
            return source_thumb

    return source_thumb


def build_branded_caption(
    title: str,
    year: str = "",
    quality: str = "",
    file_size: int = 0,
    slug: str = "",
    site_base_url: str = "",
) -> str:
    """
    Build a user-friendly, high-conversion HTML caption for Telegram video posts.
    Strictly stays under 1024 characters.
    """
    clean_title = clean_source_branding(title).strip()
    year_str = f" ({year})" if year and str(year) not in clean_title else ""
    display_title = f"{clean_title}{year_str}"
    q_str = f" [{quality}]" if quality else ""

    site_base = (site_base_url or getattr(config, "SITE_BASE_URL", "https://filmsub.pages.dev")).rstrip("/")
    if "/movie.html" in site_base:
        watch_url = site_base
    elif slug:
        watch_url = f"{site_base}/movie.html?id={slug}"
    else:
        watch_url = site_base

    size_line = ""
    if file_size > 0:
        from services.downloader import format_bytes
        size_line = f"📦 <b>File Size:</b> {format_bytes(file_size)}\n"

    caption = (
        f"🎬 <b>{display_title}{q_str}</b>\n\n"
        f"⚡ <b>Quality:</b> {quality or 'HD'} (High-Speed Telegram Cloud)\n"
        f"{size_line}"
        f"💬 <b>සිංහල උපසිරැසි:</b> Video එකටම Soft-Mux කර ඇත (Sinhala Subtitles)\n"
        f"🌐 <b>Watch Online:</b> {watch_url}"
    )
    return caption


def build_branded_inline_keyboard(
    slug: str = "",
    site_base_url: str = "",
    bot_username: str = "",
) -> InlineKeyboardMarkup:
    """
    Build clean Telegram inline keyboard buttons identical to CineSubz structure.
    Row 1: Watch Online (Website)
    Row 2: Official Channel & Film Request Bot
    Row 3: Discussion Group & Admin Support
    """
    site_base = (site_base_url or getattr(config, "SITE_BASE_URL", "https://filmsub.pages.dev")).rstrip("/")
    bot_user = bot_username or getattr(config, "BOT_USERNAME", "Filmsinhala200Bot")

    if "/movie.html" in site_base:
        watch_url = site_base
    elif slug:
        watch_url = f"{site_base}/movie.html?id={slug}"
    else:
        watch_url = site_base
    channel_url = getattr(config, "PUBLIC_CHANNEL_LINK", "") or f"https://t.me/{bot_user}?start=channel"
    bot_url = f"https://t.me/{bot_user}"
    help_url = f"https://t.me/{bot_user}?start=help"
    admin_url = f"https://t.me/{bot_user}?start=admin"

    buttons = [
        [
            InlineKeyboardButton("🌐 Watch Online / වෙබ් අඩවියෙන්", url=watch_url)
        ],
        [
            InlineKeyboardButton("📢 Official Channel", url=channel_url),
            InlineKeyboardButton("🤖 Film Request Bot", url=bot_url),
        ],
        [
            InlineKeyboardButton("💬 Support & Chat", url=help_url),
            InlineKeyboardButton("👤 Admin", url=admin_url),
        ],
    ]
    return InlineKeyboardMarkup(buttons)
