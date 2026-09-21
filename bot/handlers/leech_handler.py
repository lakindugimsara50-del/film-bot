"""
leech_handler.py — Command handler for /leech, /auto, and /boost.

Enables automated movie acquisition, multi-method download on VPS,
fast Telegram channel upload, and site publishing.

All log strings are in English to avoid Windows charmap errors.
"""

import asyncio
import logging
from typing import Optional

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import re
import os

import config
from services import leech_service, seedr_service, task_tracker
from services.auth_service import auth_service
from services.queue_service import queue_service

log = logging.getLogger(__name__)


def _is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


async def setup_seedr_account(client: Client, message: Message, email: str, password: str) -> bool:
    """Authenticate with Seedr.cc and persist credentials in SeedrPool."""
    user_id = message.from_user.id if message.from_user else 0
    log.info("[LeechHandler] Setting up Seedr.cc for user_id=%s, email=%s", user_id, email)

    status_reply = await message.reply_text("⏳ <b>Seedr.cc සමඟ සම්බන්ධ වෙමින් පවතී (Authenticating)...</b>", parse_mode=ParseMode.HTML)

    ok, msg = await seedr_service.seedr_pool.add_account(email, password)
    if not ok:
        await status_reply.edit_text(
            f"❌ <b>Seedr.cc පිවිසීම අසාර්ථකයි (Authentication Failed)!</b>\n\n{msg}",
            parse_mode=ParseMode.HTML,
        )
        return False

    # Also update .env for backward compatibility
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for target_dir in [root_dir, bot_dir]:
        env_file = os.path.join(target_dir, ".env")
        lines = []
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        new_lines = [l for l in lines if not l.startswith("SEEDR_USERNAME=") and not l.startswith("SEEDR_PASSWORD=")]
        new_lines.append(f"SEEDR_USERNAME={email}\n")
        new_lines.append(f"SEEDR_PASSWORD={password}\n")
        try:
            with open(env_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception:
            pass

    total_accs = len(seedr_service.seedr_pool.services)
    total_gb = total_accs * 2.0
    await status_reply.edit_text(
        f"✅ <b>Seedr.cc ගිණුම සාර්ථකව Pool එකට එක් කරන ලදී!</b>\n\n"
        f"📧 <b>ගිණුම:</b> <code>{email}</code>\n"
        f"📊 <b>Pool හි මුළු ගිණුම් ගණන:</b> {total_accs}\n"
        f"📦 <b>මුළු Cloud Storage එක:</b> ~{total_gb:.1f} GB\n\n"
        f"💡 <i>තවත් ගිණුමක් එක් කිරීමට: <code>/seedr add &lt;email&gt; &lt;password&gt;</code></i>\n"
        f"📋 <i>සියලු ගිණුම් බැලීමට: <code>/seedr status</code></i>\n"
        f"🎬 <i>චිත්‍රපටයක් බාගත කිරීමට: <code>/leech Inception</code></i>",
        parse_mode=ParseMode.HTML,
    )
    return True


def register(app: Client) -> None:
    """Register /leech, /auto, /boost, and /seedr command and callback handlers."""

    @app.on_message(filters.private & filters.command("seedr"))
    async def seedr_setup_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not _is_admin(user_id):
            await message.reply_text("⛔ Access denied. Admin only command.")
            return

        text = (message.text or "").strip()
        tokens = text.split()

        # Subcommand: /seedr status or /seedr list
        if len(tokens) >= 2 and tokens[1].lower() in ("list", "status"):
            accs = await seedr_service.seedr_pool.list_accounts_status()
            if not accs:
                await message.reply_text(
                    "❌ <b>Seedr Pool එකෙහි කිසිදු ගිණුමක් තවම සම්බන්ධ කර නැත.</b>\n\n"
                    "<code>/seedr add &lt;email&gt; &lt;password&gt;</code> මඟින් ගිණුමක් එක් කරන්න.",
                    parse_mode=ParseMode.HTML,
                )
                return

            lines = []
            total_storage = sum(a.get("total_gb", 2.0) for a in accs)
            total_used = sum(a.get("used_gb", 0.0) for a in accs)
            for a in accs:
                lines.append(f"<b>{a['index']}. {a['username']}</b>\n   {a['status']} | 📦 {a['used_gb']}GB / {a['total_gb']}GB")

            acc_text = "\n\n".join(lines)
            await message.reply_text(
                f"☁️ <b>Seedr Multi-Account Pool ({len(accs)} Accounts):</b>\n\n"
                f"{acc_text}\n\n"
                f"📦 <b>මුළු Cloud ධාරිතාව:</b> {total_used:.2f} GB / {total_storage:.1f} GB\n\n"
                f"➕ තවත් ගිණුමක් එක් කිරීමට:\n<code>/seedr add &lt;email&gt; &lt;password&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Subcommand: /seedr remove <email>
        if len(tokens) >= 3 and tokens[1].lower() == "remove":
            target_email = tokens[2].strip()
            removed = await seedr_service.seedr_pool.remove_account(target_email)
            if removed:
                await message.reply_text(f"🗑️ <b>{target_email}</b> ගිණුම Seedr Pool එකෙන් ඉවත් කරන ලදී.", parse_mode=ParseMode.HTML)
            else:
                await message.reply_text(f"❌ <b>{target_email}</b> නමින් ගිණුමක් Pool එකෙහි හමු නොවීය.", parse_mode=ParseMode.HTML)
            return

        # Subcommand: /seedr add <email> <password>
        if len(tokens) >= 4 and tokens[1].lower() == "add":
            email = tokens[2].strip()
            password = tokens[3].strip()
            await setup_seedr_account(client, message, email, password)
            return

        # Direct /seedr <email> <password>
        if len(tokens) >= 3 and not tokens[1].startswith("/"):
            email = tokens[1].strip()
            password = tokens[2].strip()
            await setup_seedr_account(client, message, email, password)
            return

        # Default guide
        acc_count = len(seedr_service.seedr_pool.services)
        cfg_status = f"✅ සම්බන්ධ කර ඇත ({acc_count} Accounts)" if acc_count > 0 else "❌ සම්බන්ධ කර නැත"
        await message.reply_text(
            f"☁️ <b>Seedr Multi-Account Pool පද්ධතිය:</b>\n\n"
            f"📊 <b>වත්මන් තත්ත්වය:</b> {cfg_status}\n\n"
            f"<b>විධාන (Commands):</b>\n"
            f"• <code>/seedr add &lt;email&gt; &lt;password&gt;</code> — නව ගිණුමක් එක් කරන්න\n"
            f"• <code>/seedr status</code> — සියලුම ගිණුම්වල ඉඩ සහ තත්ත්වය බලන්න\n"
            f"• <code>/seedr remove &lt;email&gt;</code> — ගිණුමක් ඉවත් කරන්න\n\n"
            f"<i>💡 ඔබට Seedr ගිණුම් 6ක් (හෝ ඊට වැඩි) මෙයට එකතු කළ හැක. එවිට 12GB - 30GB Cloud Storage එකක් ලැබෙන අතර, ගිණුම් අතර ස්වයංක්‍රීයව Rotation සිදුවේ.</i>",
            parse_mode=ParseMode.HTML,
        )

    @app.on_message(
        filters.private & filters.command(["leech", "auto", "boost"])
    )
    async def leech_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        username = message.from_user.username if message.from_user else ""
        if not auth_service.is_authorized(user_id, username):
            await message.reply_text("⛔ මෙම විධානය ක්‍රියාත්මක කිරීමට ඔබට අවසර (Access) නැත. කරුණාකර Admin අමතන්න.")
            return

        text = (message.text or "").strip()
        tokens = text.split(maxsplit=1)
        query_arg = tokens[1].strip() if len(tokens) > 1 else ""

        # Check if replying to a message with a link or file
        reply_media = None
        if message.reply_to_message:
            reply = message.reply_to_message
            if reply.video:
                reply_media = {
                    "type": "video",
                    "file_id": reply.video.file_id,
                    "file_name": getattr(reply.video, "file_name", "") or "movie.mp4",
                    "file_size": getattr(reply.video, "file_size", 0),
                    "caption": reply.caption or "",
                }
            elif reply.document:
                doc_name = getattr(reply.document, "file_name", "") or ""
                doc_lower = doc_name.lower()
                is_torrent = doc_lower.endswith(".torrent")
                is_video = any(doc_lower.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".mov", ".webm"]) or "video" in (reply.document.mime_type or "")
                if is_torrent or is_video:
                    reply_media = {
                        "type": "torrent" if is_torrent else "video",
                        "file_id": reply.document.file_id,
                        "file_name": doc_name or ("movie.torrent" if is_torrent else "movie.mp4"),
                        "file_size": getattr(reply.document, "file_size", 0),
                        "caption": reply.caption or "",
                    }
            elif not query_arg:
                if reply.text:
                    query_arg = reply.text.strip()
                elif reply.caption:
                    query_arg = reply.caption.strip()

        if not query_arg and reply_media:
            # Derive clean movie name from caption or file name
            raw_title = reply_media.get("caption") or reply_media.get("file_name") or ""
            clean_title = re.sub(r"\.(mp4|mkv|avi|webm|torrent)$", "", raw_title, flags=re.IGNORECASE)
            clean_title = re.sub(r"[._-]", " ", clean_title).strip()
            query_arg = clean_title or "Telegram Media"

        if not query_arg and not reply_media:
            await message.reply_text(
                "🚀 <b>Ultra Auto-Leech & Uploader (/boost)</b>\n\n"
                "ඕනෑම චිත්‍රපටයක් ඔබගේ Data වැය නොවී VPS එකට Download කර Channel එකට Upload කිරීම.\n\n"
                "<b>භාවිතය (Usage):</b>\n"
                "  • <code>/leech &lt;Movie Name&gt;</code> — උදා: <code>/leech Inception</code>\n"
                "  • <code>/leech &lt;Movie Name&gt; &lt;Year&gt;</code> — උදා: <code>/leech Deadpool 2024</code>\n"
                "  • <code>/leech &lt;IMDb ID&gt;</code> — උදා: <code>/leech tt1375666</code>\n"
                "  • <code>/leech &lt;Magnet/Direct URL&gt;</code> — Direct download & upload\n"
                "  • <code>/queue</code> — බාගත වීමට ඇති පෝලිම බලන්න\n"
                "  • <code>/cancel</code> — ක්‍රියාත්මක කාර්යය නවත්වන්න\n\n"
                "<b>සහාය දක්වන ක්‍රම (4 Acquisition Methods):</b>\n"
                "  1️⃣ Method A: DDL Scrapers (PixelDrain, Pahe, PSA)\n"
                "  2️⃣ Method B: YTS Torrents (&lt; 1.95GB via aria2c)\n"
                "  3️⃣ Method C: Telegram Movie Channels\n"
                "  4️⃣ Method D: Web Stream Extractors (FlixHQ)\n\n"
                "<i>Aliases: /auto, /boost</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        display_hint = query_arg[:40] if query_arg else "Movie"

        # Check if an active task is running
        is_busy = not queue_service.is_idle()

        status_msg = await message.reply_text(
            f"⏳ <b>Auto-Leech පද්ධතියට එක්කරමින් පවතී...</b>\n🎬 {display_hint}",
            parse_mode=ParseMode.HTML,
        )

        pos = await queue_service.add_to_queue(
            client=client,
            status_msg=status_msg,
            user_id=user_id,
            query_text=query_arg,
            reply_media=reply_media,
            title_hint=display_hint,
        )

        if is_busy and pos > 1:
            await status_msg.edit_text(
                f"📥 <b>චිත්‍රපටය පෝලිමට (Queue) එක් කරන ලදී!</b>\n\n"
                f"🎬 <b>චිත්‍රපටය:</b> {display_hint}\n"
                f"🔢 <b>පෝලිමේ ස්ථානය (Queue Position):</b> #{pos}\n\n"
                f"💡 <i>දැනට ක්‍රියාත්මක කාර්යය අවසන් වූ වහාම මෙම චිත්‍රපටය කිසිදු බාධාවකින් තොරව ස්වයංක්‍රීයව බාගත වේ.</i>\n"
                f"📋 <i>පෝලිම බැලීමට: <code>/queue</code></i>",
                parse_mode=ParseMode.HTML,
            )

    @app.on_message(filters.private & filters.command(["queue", "q"]))
    async def queue_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        username = message.from_user.username if message.from_user else ""
        if not auth_service.is_authorized(user_id, username):
            await message.reply_text("⛔ Access denied.")
            return

        items = queue_service.get_queue_status()
        if not items:
            await message.reply_text("🟢 <b>බාගත කිරීමේ පෝලිම හිස්ය (Queue is Empty).</b>\n\nනව චිත්‍රපටයක් බාගත කිරීමට <code>/leech &lt;Movie Name&gt;</code> භාවිතා කරන්න.", parse_mode=ParseMode.HTML)
            return

        lines = []
        for i, it in enumerate(items, 1):
            lines.append(f"<b>{i}. {it['title']}</b>\n   ⚡ <i>{it['status']}</i>")

        body = "\n\n".join(lines)
        await message.reply_text(
            f"📋 <b>වත්මන් බාගත කිරීමේ පෝලිම (Movie Download Queue):</b>\n\n"
            f"{body}\n\n"
            f"💡 <i>සියලුම චිත්‍රපට පිළිවෙලින් එකිනෙක ස්වයංක්‍රීයව බාගත වේ.</i>",
            parse_mode=ParseMode.HTML,
        )

    @app.on_message(filters.private & filters.command(["cancel", "stop"]))
    async def cancel_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        username = message.from_user.username if message.from_user else ""
        if not auth_service.is_authorized(user_id, username):
            await message.reply_text("⛔ Access denied.")
            return

        cancelled = await task_tracker.cancel_all_user_operations(user_id)
        if cancelled:
            await message.reply_text(
                "❌ <b>ක්‍රියාත්මක වෙමින් පැවති කාර්යය සාර්ථකව අවලංගු කරන ලදී (Cancelled).</b>\n\n"
                "🗑️ <i>Seedr ගිණුමේ ගබඩාව සහ බාගත කිරීම් (Downloads) සියල්ල පිරිසිදු කරන ලදී.</i>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text(
                "ℹ️ <b>දැනට අවලංගු කිරීමට ක්‍රියාකාරී කාර්යයක් නොමැත.</b>\n\n"
                "🗑️ <i>Seedr ගිණුම සහ තාවකාලික දත්ත පිරිසිදු කරන ලදී.</i>",
                parse_mode=ParseMode.HTML,
            )

    @app.on_callback_query(filters.regex(r"^leech:"))
    async def leech_callback_handler(client: Client, query: CallbackQuery) -> None:
        user_id = query.from_user.id if query.from_user else 0
        username = query.from_user.username if query.from_user else ""
        if not auth_service.is_authorized(user_id, username):
            await query.answer("Unauthorized.", show_alert=True)
            return

        action = query.data.split(":")[-1]
        if action == "cancel":
            cancelled = await task_tracker.cancel_all_user_operations(user_id)
            await query.answer("ක්‍රියාවලිය අවලංගු කරන ලදී (Cancelled).", show_alert=True)
            try:
                await query.message.edit_text(
                    "❌ <b>Auto-Leech කාර්යය සාර්ථකව අවලංගු කරන ලදී (Cancelled).</b>\n\n"
                    "🗑️ <i>Seedr ගිණුමේ ගබඩාව සහ බාගත කිරීම් (Downloads) සියල්ල පිරිසිදු කරන ලදී.</i>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

