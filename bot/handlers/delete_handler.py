"""
delete_handler.py — Admin commands to completely delete/remove movies from the website.

Commands:
    /delete <query or slug or url>
    /del <query or slug or url>
    /remove <query or slug or url>

Allows admins to remove any published movie completely from movies.json, movies_data.js,
and the live website catalog with full GitHub sync and optional Telegram post cleanup.
"""

import asyncio
import logging
import re
import urllib.parse
from typing import Optional

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from services import github_service

log = logging.getLogger(__name__)


def _is_admin(uid: int) -> bool:
    from services.auth_service import auth_service
    return uid in config.ADMIN_IDS or auth_service.is_admin(uid)


def _extract_slug_or_query(text: str) -> str:
    """Extract clean query or slug from command text or website URL."""
    cleaned = re.sub(r"^/(delete|del|remove)(@\w+)?\s*", "", text.strip(), flags=re.IGNORECASE).strip()
    if not cleaned:
        return ""
    # Check if a website URL was passed (e.g., https://filmsub.pages.dev/movie?id=ice-age-2006)
    if "movie?id=" in cleaned or "movie.html?id=" in cleaned:
        try:
            parsed = urllib.parse.urlparse(cleaned)
            qs = urllib.parse.parse_qs(parsed.query)
            if "id" in qs and qs["id"]:
                return qs["id"][0].strip()
        except Exception:
            pass
    return cleaned


def register(app: Client) -> None:
    """Register /delete, /del, /remove command handlers and callbacks."""

    @app.on_message(filters.command(["delete", "del", "remove"]) & filters.private)
    async def delete_command_handler(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not _is_admin(user_id):
            await message.reply_text("⛔ <b>Access Denied:</b> Admin අවසර අවශ්‍යයි.", parse_mode=ParseMode.HTML)
            return

        raw_text = message.text or ""
        query = _extract_slug_or_query(raw_text)

        if not query:
            # Show interactive guide & recent movies
            try:
                data, _ = await github_service.get_movies_json()
                movies = data.get("movies", [])
            except Exception:
                movies = []

            recent_buttons = []
            for m in reversed(movies[-5:]):
                title = m.get("title", "Untitled")
                year = m.get("year", "")
                slug = m.get("slug") or m.get("id") or ""
                label = f"🗑️ {title} ({year})" if year else f"🗑️ {title}"
                if len(label) > 40:
                    label = label[:37] + "..."
                if slug:
                    recent_buttons.append([InlineKeyboardButton(label, callback_data=f"del_pick:{slug}")])

            help_text = (
                "🗑️ <b>චිත්‍රපටයක් Site එකෙන් සම්පූර්ණයෙන්ම ඉවත් කිරීම (Delete Movie)</b>\n\n"
                "වෙබ් අඩවියෙන් ඉවත් කිරීමට අවශ්‍ය චිත්‍රපටයේ නම, Slug එක හෝ Link එක Command එක සමඟ ලබා දෙන්න:\n\n"
                "📌 <b>භාවිතය (Usage):</b>\n"
                "• <code>/delete &lt;Movie Title&gt;</code> (උදා: <code>/delete Ice Age</code>)\n"
                "• <code>/delete &lt;Slug&gt;</code> (උදා: <code>/delete ice-age-the-meltdown-2006</code>)\n"
                "• <code>/delete &lt;Website URL&gt;</code>\n\n"
                "<i>හෝ පහතින් මෑතකදී එක් කළ චිත්‍රපටයක් තෝරා ඉවත් කරන්න:</i>"
            )

            reply_markup = InlineKeyboardMarkup(recent_buttons) if recent_buttons else None
            await message.reply_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            return

        status_msg = await message.reply_text(
            f"🔍 <b>'{query}' සොයමින් පවතී...</b>",
            parse_mode=ParseMode.HTML,
        )

        matches = await github_service.search_movies(query, limit=8)

        if not matches:
            await status_msg.edit_text(
                f"❌ <b>චිත්‍රපටය හමු නොවිණි!</b>\n\n"
                f"<code>'{query}'</code> නමින් චිත්‍රපටයක් movies.json තුළ නැත.\n"
                f"කරුණාකර නිවැරදි නම හෝ Slug එක පරීක්ෂා කර නැවත උත්සාහ කරන්න.",
                parse_mode=ParseMode.HTML,
            )
            return

        # If exactly 1 match or an exact slug match
        exact_match = next((m for m in matches if (m.get("slug") or "").lower() == query.lower() or (m.get("id") or "").lower() == query.lower()), None)
        target_movie = exact_match or (matches[0] if len(matches) == 1 else None)

        if target_movie:
            slug = target_movie.get("slug") or target_movie.get("id") or ""
            title = target_movie.get("title", "Untitled")
            year = target_movie.get("year", "N/A")
            qual = target_movie.get("quality", "HD")

            confirm_text = (
                f"⚠️ <b>මෙම චිත්‍රපටය වෙබ් අඩවියෙන් සම්පූර්ණයෙන්ම ඉවත් කිරීමට අවශ්‍යද?</b>\n\n"
                f"🎬 <b>Title:</b> {title} ({year})\n"
                f"🆔 <b>Slug:</b> <code>{slug}</code>\n"
                f"📊 <b>Quality:</b> {qual}\n\n"
                f"<i>තහවුරු කළ පසු movies.json, movies_data.js සහ Live Site එකෙන් මෙම චිත්‍රපටය සම්පූර්ණයෙන්ම මැකී යනු ඇත.</i>"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("❌ ඔව්, සම්පූර්ණයෙන්ම මකන්න (Delete)", callback_data=f"del_yes:{slug}"),
                ],
                [
                    InlineKeyboardButton("🔙 අවලංගු කරන්න (Cancel)", callback_data=f"del_no:{slug}"),
                ],
            ])
            await status_msg.edit_text(confirm_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            return

        # Multiple matches found
        buttons = []
        for m in matches:
            m_slug = m.get("slug") or m.get("id") or ""
            m_title = m.get("title", "Untitled")
            m_year = m.get("year", "")
            lbl = f"🗑️ {m_title} ({m_year})" if m_year else f"🗑️ {m_title}"
            if len(lbl) > 42:
                lbl = lbl[:39] + "..."
            if m_slug:
                buttons.append([InlineKeyboardButton(lbl, callback_data=f"del_pick:{m_slug}")])

        buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="del_cancel_all")])

        await status_msg.edit_text(
            f"🔍 <b>'{query}' සඳහා චිත්‍රපට කිහිපයක් හමු විය.</b>\nකරුණාකර ඉවත් කිරීමට අවශ්‍ය එක තෝරන්න:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.HTML,
        )

    @app.on_callback_query(filters.regex(r"^del_(pick|yes|no|cancel_all)(:(.+))?$"))
    async def delete_callback_handler(client: Client, cq: CallbackQuery) -> None:
        user_id = cq.from_user.id if cq.from_user else 0
        if not _is_admin(user_id):
            await cq.answer("⛔ Admin අවසර නැත!", show_alert=True)
            return

        data_str = cq.data or ""
        parts = data_str.split(":", 2)
        action = parts[0]
        slug = parts[1] if len(parts) > 1 else ""

        if action == "del_cancel_all" or action == "del_no":
            await cq.answer("ක්‍රියාවලිය අවලංගු කරන ලදී.")
            try:
                await cq.edit_message_text(
                    "❌ <b>චිත්‍රපටය ඉවත් කිරීම අවලංගු කරන ලදී (Cancelled).</b>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        if action == "del_pick":
            await cq.answer()
            # Fetch movie details for slug to show confirmation
            matches = await github_service.search_movies(slug, limit=1)
            target = matches[0] if matches else None
            title = target.get("title", slug) if target else slug
            year = target.get("year", "") if target else ""
            qual = target.get("quality", "HD") if target else "HD"

            confirm_text = (
                f"⚠️ <b>මෙම චිත්‍රපටය වෙබ් අඩවියෙන් සම්පූර්ණයෙන්ම ඉවත් කිරීමට අවශ්‍යද?</b>\n\n"
                f"🎬 <b>Title:</b> {title} ({year})\n"
                f"🆔 <b>Slug:</b> <code>{slug}</code>\n"
                f"📊 <b>Quality:</b> {qual}\n\n"
                f"<i>තහවුරු කළ පසු movies.json, movies_data.js සහ Live Site එකෙන් මෙම චිත්‍රපටය සදහටම මැකී යනු ඇත.</i>"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("❌ ඔව්, සම්පූර්ණයෙන්ම මකන්න (Delete)", callback_data=f"del_yes:{slug}"),
                ],
                [
                    InlineKeyboardButton("🔙 අවලංගu කරන්න (Cancel)", callback_data=f"del_no:{slug}"),
                ],
            ])
            try:
                await cq.edit_message_text(confirm_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            except Exception:
                pass
            return

        if action == "del_yes":
            await cq.answer("චිත්‍රපටය ඉවත් කරමින් පවතී...")
            try:
                await cq.edit_message_text(
                    f"⏳ <b>'{slug}' වෙබ් අඩවියෙන් සම්පූර්ණයෙන්ම ඉවත් කරමින් පවතී...</b>\n"
                    f"<i>GitHub repo & Cloudflare CDN auto-sync වෙමින් පවතී.</i>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

            success, deleted = await github_service.delete_movie(slug)

            if not success or not deleted:
                try:
                    await cq.edit_message_text(
                        f"❌ <b>චිත්‍රපටය ඉවත් කිරීම අසාර්ථක විය!</b>\n"
                        f"<code>'{slug}'</code> සොයාගත නොහැකි විය හෝ GitHub API දෝෂයක් සිදුවිය.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
                return

            # Optionally clean up channel post if message_id exists
            ch_msg_id = deleted.get("message_id")
            ch_deleted_note = ""
            if ch_msg_id and config.PRIVATE_CHANNEL_ID:
                try:
                    await client.delete_messages(config.PRIVATE_CHANNEL_ID, [ch_msg_id])
                    ch_deleted_note = "\n🗑️ <i>Telegram Channel Post එකද සාර්ථකව ඉවත් කෙරිණි.</i>"
                except Exception as ch_err:
                    log.debug("[DeleteHandler] Channel msg delete note: %s", ch_err)

            # Get remaining movies count
            try:
                cat, _ = await github_service.get_movies_json()
                remaining_count = len(cat.get("movies", []))
            except Exception:
                remaining_count = 0

            d_title = deleted.get("title", slug)
            d_year = deleted.get("year", "")
            d_slug = deleted.get("slug") or slug

            success_banner = (
                f"✅ <b>චිත්‍රපටය සාර්ථකව වෙබ් අඩවියෙන් ඉවත් කරන ලදී!</b>\n\n"
                f"🎬 <b>Title:</b> {d_title} ({d_year})\n"
                f"🆔 <b>Slug:</b> <code>{d_slug}</code>\n"
                f"📊 <b>දැනට Site එකේ ඇති මුළු චිත්‍රපට ගණන:</b> <b>{remaining_count}</b>"
                f"{ch_deleted_note}\n\n"
                f"🌐 <b>Website:</b> <a href=\"https://filmsub.pages.dev\">filmsub.pages.dev</a>\n"
                f"⚡ Cloudflare Pages auto-update ක්‍රියාත්මකයි."
            )

            try:
                await cq.edit_message_text(success_banner, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except Exception:
                pass
            log.info("[DeleteHandler] Movie '%s' (%s) deleted by admin %d", d_title, d_slug, user_id)
