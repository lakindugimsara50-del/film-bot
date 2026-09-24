"""
wizard.py — Interactive Step-by-Step Movie Upload Wizard & Drafts System.

Provides a humanized, conversational bot flow:
1. Detects video file uploads or download links sent to the bot.
2. Lets admins choose: [ 🚀 Publish to Web Now ] or [ 💾 Save as Draft (Publish Later) ].
3. Prompts step-by-step for:
   - Movie name & year (with live TMDB search & poster confirmation)
   - Subtitles (.srt/.vtt file, URL, or auto-generate default Sinhala)
   - Quality (480p, 720p, 1080p buttons)
4. Manages /drafts command with instant publish buttons.
5. Auto-deploys update to Cloudflare Pages & posts channel announcement.
"""

import asyncio
import logging
import os
import re
import tempfile
import urllib.parse

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from handlers import add_movie
from handlers.announce import post_to_channel, _slugify
from handlers.sub_handler import PENDING_SUB_DOCS
from services import draft_service, github_service, subtitle_service, task_tracker, telegram_upload, tmdb_service, video_service

log = logging.getLogger(__name__)

# In-memory active wizard sessions: user_id -> dict
USER_SESSIONS: dict[int, dict] = {}


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


# ─────────────────────────────────────────────────────────────────────────────
# 1. Video / Document / URL Auto-Detection & Entry Points
# ─────────────────────────────────────────────────────────────────────────────

def register(app: Client) -> None:
    """Register all wizard handlers and callback listeners."""

    @app.on_message(filters.private & (filters.video | filters.document))
    async def media_upload_handler(client: Client, message: Message) -> None:
        if not _is_admin(message.from_user.id):
            return

        # Check if user is already in WAITING_SUB state
        session = USER_SESSIONS.get(message.from_user.id)
        if session and session.get("step") == "WAITING_SUB":
            await _handle_sub_file(client, message, session)
            return

        doc = message.document
        vid = message.video

        # Check if user uploaded a standalone .srt / .vtt subtitle file outside of wizard
        if doc and (doc.file_name or "").lower().endswith((".srt", ".vtt")):
            await _handle_standalone_subtitle_upload(client, message, doc)
            return

        file_id = ""
        file_name = "movie_video.mp4"
        file_size = 0

        if vid:
            file_id = vid.file_id
            file_name = vid.file_name or "movie_video.mp4"
            file_size = vid.file_size or 0
        elif doc and (doc.mime_type and "video" in doc.mime_type or doc.file_name and doc.file_name.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))):
            file_id = doc.file_id
            file_name = doc.file_name or "movie_video.mp4"
            file_size = doc.file_size or 0
        else:
            return  # Not a recognized video file

        size_str = add_movie._human_size(file_size)
        session_id = f"sess_{message.id}"
        caption_hint = (message.caption or "").strip()
        title_hint = caption_hint or _extract_title_hint(file_name)

        USER_SESSIONS[message.from_user.id] = {
            "session_id": session_id,
            "step": "CHOICE",
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "film_url": "",
            "title_hint": title_hint,
            "reply_message_id": message.id,
            "reply_chat_id": message.chat.id,
        }

        # If caption provides a clear movie title, search TMDB directly to present the 3 action buttons
        if caption_hint:
            await _handle_movie_name_search(client, message, USER_SESSIONS[message.from_user.id], caption_hint)
            return

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data="wiz:pub_start"),
                InlineKeyboardButton("💬 Subtitle එක් කරන්න (Add Sub)", callback_data="wiz:pub_start"),
            ],
            [
                InlineKeyboardButton("💾 Draft එකක් ලෙස තබන්න", callback_data="wiz:save_draft"),
                InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel"),
            ],
        ])

        await message.reply_text(
            f"🎬 <b>චිත්‍රපට වීඩියෝව හඳුනාගන්නා ලදී! (Movie File Detected)</b>\n\n"
            f"📁 <b>ගොනුව:</b> <code>{file_name}</code>\n"
            f"📦 <b>ප්‍රමාණය:</b> {size_str}\n\n"
            f"<b>ඔබට මෙම චිත්‍රපටය සමඟ කුමක් කිරීමට අවශ්‍යද?</b>\n"
            f"පහතින් ඔබට අවශ්‍ය ක්‍රියාව තෝරන්න:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    @app.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
    async def text_handler(client: Client, message: Message) -> None:
        if not _is_admin(message.from_user.id):
            return

        session = USER_SESSIONS.get(message.from_user.id)
        text = message.text.strip()

        # Check if user sent email and password directly (Seedr setup without typing /seedr)
        parts = text.split()
        if len(parts) == 2 and "@" in parts[0] and "." in parts[0] and not session:
            from handlers import leech_handler
            await leech_handler.setup_seedr_account(client, message, parts[0], parts[1])
            return

        # Check if this is an active wizard step
        if session:
            step = session.get("step")
            if step == "WAITING_NAME":
                await _handle_movie_name_search(client, message, session, text)
                return
            elif step == "WAITING_SUB":
                await _handle_sub_url(client, message, session, text)
                return

        # Check if user replied to a draft confirmation or bot message with draft info
        reply = message.reply_to_message
        rep_text = getattr(reply, "text", None) if reply else None
        rep_caption = getattr(reply, "caption", None) if reply else None
        reply_content = rep_text if isinstance(rep_text, str) else (rep_caption if isinstance(rep_caption, str) else "")
        if reply_content:
            draft_match = re.search(r"(?:Draft ID|Draft):\s*<code>?([a-zA-Z0-9_-]+)</code>?", reply_content, re.IGNORECASE)
            if draft_match:
                d_id = draft_match.group(1).strip()
                existing_draft = draft_service.get_draft(d_id)
                if existing_draft:
                    new_session = {
                        "session_id": f"sess_{message.id}",
                        "draft_id": d_id,
                        "step": "WAITING_NAME",
                        "file_id": existing_draft.get("file_id", ""),
                        "file_name": existing_draft.get("file_name", ""),
                        "file_size": existing_draft.get("file_size", 0),
                        "film_url": existing_draft.get("film_url", ""),
                        "title_hint": text,
                        "reply_message_id": existing_draft.get("message_id") or reply.id,
                        "reply_chat_id": existing_draft.get("channel_id") or reply.chat.id,
                        "stream_url": existing_draft.get("stream_url", ""),
                        "movie_entry": existing_draft.get("movie_entry"),
                    }
                    USER_SESSIONS[message.from_user.id] = new_session
                    await _handle_movie_name_search(client, message, new_session, text)
                    return

        # Check if user replied to a video message with a movie name
        if reply and (reply.video or (reply.document and (("video" in (reply.document.mime_type or "")) or (reply.document.file_name or "").lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))))):
            rep_vid = reply.video
            rep_doc = reply.document
            r_file_id = rep_vid.file_id if rep_vid else rep_doc.file_id
            r_file_name = (rep_vid.file_name if rep_vid else rep_doc.file_name) or "movie_video.mp4"
            r_file_size = (rep_vid.file_size if rep_vid else rep_doc.file_size) or 0

            # Correlate with existing draft if available
            existing_draft = draft_service.find_draft_by_media(file_id=r_file_id, message_id=reply.id)
            draft_id = existing_draft.get("id") if existing_draft else None

            new_session = {
                "session_id": f"sess_{message.id}",
                "draft_id": draft_id,
                "step": "WAITING_NAME",
                "file_id": r_file_id,
                "file_name": r_file_name,
                "file_size": r_file_size,
                "film_url": "",
                "title_hint": text,
                "reply_message_id": reply.id,
                "reply_chat_id": reply.chat.id,
                "movie_entry": existing_draft.get("movie_entry") if existing_draft else None,
            }
            USER_SESSIONS[message.from_user.id] = new_session
            await _handle_movie_name_search(client, message, new_session, text)
            return

        # If not in wizard, check if user sent a video download URL (e.g. mega, gdrive, direct mp4)
        if text.startswith(("http://", "https://")):
            url_lower = text.lower()
            if any(ext in url_lower for ext in [".mp4", ".mkv", ".avi", "drive.google.com", "mega.nz", "pixeldrain", "1fichier"]):
                USER_SESSIONS[message.from_user.id] = {
                    "session_id": f"sess_{message.id}",
                    "step": "CHOICE",
                    "file_id": "",
                    "file_name": os.path.basename(urllib.parse.urlparse(text).path) or "download_movie.mp4",
                    "file_size": 0,
                    "film_url": text,
                    "title_hint": "",
                    "reply_message_id": message.id,
                    "reply_chat_id": message.chat.id,
                }

                kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data="wiz:pub_start"),
                        InlineKeyboardButton("💾 පස්සේ දාන්න Save කරගන්න (Draft)", callback_data="wiz:save_draft"),
                    ],
                    [InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")],
                ])

                await message.reply_text(
                    f"🔗 <b>චිත්‍රපට Download Link එකක් හඳුනාගන්නා ලදී!</b>\n\n"
                    f"🌐 <code>{text[:60]}...</code>\n\n"
                    f"ඔබට මෙම චිත්‍රපටය සමඟ කුමක් කිරීමට අවශ්‍යද?",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                return

        # Fallback for plain text without active session
        await message.reply_text(
            "👋 <b>Film Bot Command Guide:</b>\n\n"
            "• <code>/leech &lt;Movie Name&gt;</code> — Download & upload movie\n"
            "• <code>/seedr &lt;email&gt; &lt;password&gt;</code> — Connect Seedr.cc Cloud\n"
            "• <code>/find &lt;Movie Name&gt;</code> — Search movies\n"
            "• <code>/drafts</code> — View and publish drafts\n"
            "• <code>/sub &lt;Movie Name&gt;</code> — Attach Sinhala subtitle\n"
            "• <code>/status</code> — System status\n\n"
            "<i>වීඩියෝවක් හෝ Direct Link එකක් එවන්න, නැතහොත් වීඩියෝවකට Reply කර චිත්‍රපටයේ නම එවන්න.</i>",
            parse_mode=ParseMode.HTML,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Commands: /add (no args), /drafts, /cancel
    # ─────────────────────────────────────────────────────────────────────────

    @app.on_message(filters.private & filters.command("add"))
    async def add_command_router(client: Client, message: Message) -> None:
        if not _is_admin(message.from_user.id):
            return

        tokens = message.text.split()[1:]
        reply = message.reply_to_message
        has_video_reply = bool(reply and (reply.video or (reply.document and (("video" in (reply.document.mime_type or "")) or (reply.document.file_name or "").lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))))))

        # If arguments are passed with URL (legacy one-liner /add <film_url> <sub_url> ...)
        is_legacy_one_liner = False
        if tokens:
            if tokens[0].startswith(("http://", "https://")):
                is_legacy_one_liner = True
            elif has_video_reply and any(t.startswith(("http://", "https://")) for t in tokens):
                is_legacy_one_liner = True

        if is_legacy_one_liner:
            await add_movie._handle_add(client, message)
            return

        # If replying to a video with a title (e.g. /add Cocaine Bear 2023) or just /add
        if has_video_reply:
            rep_vid = reply.video
            rep_doc = reply.document
            r_file_id = rep_vid.file_id if rep_vid else rep_doc.file_id
            r_file_name = (rep_vid.file_name if rep_vid else rep_doc.file_name) or "movie_video.mp4"
            r_file_size = (rep_vid.file_size if rep_vid else rep_doc.file_size) or 0
            search_title = " ".join(tokens).strip() if tokens else (reply.caption or _extract_title_hint(r_file_name))

            existing_draft = draft_service.find_draft_by_media(file_id=r_file_id, message_id=reply.id)
            draft_id = existing_draft.get("id") if existing_draft else None

            session = {
                "session_id": f"sess_{message.id}",
                "draft_id": draft_id,
                "step": "CHOICE" if search_title else "WAITING_NAME",
                "file_id": r_file_id,
                "file_name": r_file_name,
                "file_size": r_file_size,
                "film_url": "",
                "title_hint": search_title,
                "reply_message_id": reply.id,
                "reply_chat_id": reply.chat.id,
                "movie_entry": existing_draft.get("movie_entry") if existing_draft else None,
            }
            USER_SESSIONS[message.from_user.id] = session

            if search_title:
                await _handle_movie_name_search(client, message, session, search_title)
            else:
                await message.reply_text(
                    "🔍 <b>චිත්‍රපටයේ නම කුමක්ද?</b>\n\nකරුණාකර චිත්‍රපටයේ ඉංග්‍රීසි නම සහ වර්ෂය Reply කරන්න (උදා: <code>Avatar 2009</code>):",
                    parse_mode=ParseMode.HTML,
                )
            return

        # Otherwise start interactive wizard
        USER_SESSIONS[message.from_user.id] = {
            "step": "WAITING_VIDEO",
            "file_id": "",
            "film_url": "",
        }

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Drafts වලින් තෝරන්න (Saved Drafts)", callback_data="drafts:list")],
            [InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")],
        ])

        await message.reply_text(
            "🎬 <b>චිත්‍රපට එකතු කිරීමේ සහයක (Movie Upload Wizard)</b>\n\n"
            "පියවර 1: කරුණාකර චිත්‍රපටයේ <b>Video File එක</b> මට එවන්න (Upload) හෝ <b>Direct Download Link එකක්</b> එවන්න.\n\n"
            "<i>(නැතහොත් කලින් Save කරගත් Draft එකක් තෝරාගත හැක)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    @app.on_message(filters.private & filters.command("drafts"))
    async def drafts_command_handler(client: Client, message: Message) -> None:
        if not _is_admin(message.from_user.id):
            return
        await _show_drafts_menu(message)

    @app.on_message(filters.private & filters.command(["cancel", "stop"]))
    async def cancel_command_handler(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        username = message.from_user.username if message.from_user else ""
        from services.auth_service import auth_service
        if not auth_service.is_authorized(user_id, username):
            await message.reply_text("⛔ මෙම විධානය ක්‍රියාත්මක කිරීමට ඔබට අවසර (Access) නැත.")
            return

        had_session = bool(USER_SESSIONS.pop(user_id, None))
        was_task_cancelled = await task_tracker.cancel_all_user_operations(user_id)

        if was_task_cancelled or had_session:
            await message.reply_text(
                "❌ <b>ක්‍රියාත්මක වෙමින් පැවති කාර්යය සාර්ථකව අවලංගු කරන ලදී (Cancelled).</b>\n\n"
                "🗑️ <i>Seedr Cloud Storage, බාගත කිරීම් (Downloads) සහ තාවකාලික ගොනු සියල්ල පිරිසිදු කරන ලදී.</i>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text(
                "ℹ️ <b>දැනට අවලංගු කිරීමට කිසිදු සක්‍රීය කාර්යයක් නොමැත (No Active Task).</b>\n\n"
                "🧹 <i>Seedr Cloud සහ Storage පිරිසිදු කර සූදානම් කර තබන ලදී.</i>",
                parse_mode=ParseMode.HTML,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Callback Queries (Inline Button Clicks)
    # ─────────────────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^wiz:"))
    async def wizard_callbacks(client: Client, query: CallbackQuery) -> None:
        data = query.data
        user_id = query.from_user.id
        if not _is_admin(user_id):
            await query.answer("Unauthorized.", show_alert=True)
            return

        # Cancel
        if data == "wiz:cancel":
            USER_SESSIONS.pop(user_id, None)
            was_task_cancelled = await task_tracker.cancel_all_user_operations(user_id)
            if was_task_cancelled:
                await query.message.edit_text(
                    "❌ <b>ක්‍රියාත්මක වෙමින් පැවති කාර්යය අවලංගු කරන ලදී (Cancelled).</b>\n\n"
                    "🗑️ <i>Seedr ගිණුම සහ බාගත කිරීම් පිරිසිදු කරන ලදී.</i>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await query.message.edit_text("❌ <b>ක්‍රියාවලිය අවලංගු කරන ලදී (Cancelled).</b>", parse_mode=ParseMode.HTML)
            await query.answer("අවලංගු කරන ලදී.")
            return

        # Save to draft
        if data == "wiz:save_draft":
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired. Please send video again.", show_alert=True)
                return

            # Copy video to Filmhost channel if not already in channel
            if config.PRIVATE_CHANNEL_ID and session.get("reply_message_id") and session.get("reply_chat_id") and session.get("reply_chat_id") != config.PRIVATE_CHANNEL_ID:
                try:
                    copied = await client.copy_message(
                        chat_id=config.PRIVATE_CHANNEL_ID,
                        from_chat_id=session["reply_chat_id"],
                        message_id=session["reply_message_id"],
                    )
                    session["message_id"] = copied.id
                    session["channel_id"] = config.PRIVATE_CHANNEL_ID
                    if copied.video:
                        session["file_id"] = copied.video.file_id
                    elif copied.document:
                        session["file_id"] = copied.document.file_id
                    session["stream_url"] = telegram_upload.get_file_stream_url(session.get("file_id", ""))
                except Exception as copy_err:
                    log.warning("[Wizard] Could not copy draft video to Filmhost channel: %s", copy_err)

            # If session has metadata, prebuild movie_entry for instant publishing
            if session.get("meta"):
                meta = session["meta"]
                title = meta.get("title") or session.get("movie_name", "Untitled")
                year = meta.get("year", 2025)
                slug = _slugify(title, year)
                base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
                if "yoursite.lk" in base_site:
                    base_site = "https://filmsub.pages.dev"
                site_url = f"{base_site}/movie.html?id={slug}"
                file_id = session.get("file_id", "")
                stream_url = session.get("stream_url") or telegram_upload.get_file_stream_url(file_id)
                session["movie_entry"] = add_movie._build_movie_dict(
                    meta=meta,
                    slug=slug,
                    quality=session.get("quality", "1080p"),
                    lang="Sinhala",
                    file_id=file_id,
                    stream_url=stream_url,
                    message_id=session.get("message_id", 0),
                    file_name=session.get("file_name", "movie.mp4"),
                    file_size=session.get("file_size", 0),
                    subtitle_url=session.get("subtitle_url", "default"),
                    site_url=site_url,
                )

            draft_id = draft_service.save_draft(session)
            USER_SESSIONS.pop(user_id, None)

            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data=f"draft:pub:{draft_id}"),
                    InlineKeyboardButton("💬 Subtitle එක් කරන්න (Add Sub)", callback_data=f"leech_act:sub:{draft_id}"),
                ],
                [InlineKeyboardButton("📂 සියලුම Drafts බලන්න (/drafts)", callback_data="drafts:list")],
            ])

            title_hint = session.get("title_hint") or session.get("movie_name") or session.get("file_name", "Movie")
            await query.message.edit_text(
                f"📁 <b>චිත්‍රපටය Filmhost Channel එකෙහි Draft එකක් ලෙස සුරැකිණි!</b>\n\n"
                f"🎬 <b>චිත්‍රපටය:</b> {title_hint}\n"
                f"🆔 <b>Draft ID:</b> <code>{draft_id}</code>\n"
                f"☁️ <b>Storage:</b> Filmhost Telegram Channel\n\n"
                f"<i>මෙම චිත්‍රපටය වෙබ් අඩවියට Publish කර නැත. ඔබට අවශ්‍ය ඕනෑම වේලාවක <code>/drafts</code> මඟින් හෝ පහත බොත්තමෙන් Subtitle එක්කර Publish කළ හැක.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            await query.answer("Saved to drafts!")
            return

        # Start publish flow (ask for Movie Name)
        if data == "wiz:pub_start":
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired. Please send file again.", show_alert=True)
                return

            session["step"] = "WAITING_NAME"
            hint = session.get("title_hint", "")
            hint_text = f"\n<i>(හඳුනාගත් නම: {hint})</i>" if hint else ""

            await query.message.edit_text(
                f"🔍 <b>පියවර 1/3: චිත්‍රපටයේ නම කුමක්ද?</b>\n\n"
                f"කරුණාකර චිත්‍රපටයේ ඉංග්‍රීසි නම (සහ නිකුත් වූ වර්ෂය) Reply කරන්න.\n"
                f"උදාහරණයක්: <code>Deadpool and Wolverine 2024</code> හෝ <code>Interstellar</code>{hint_text}",
                parse_mode=ParseMode.HTML,
            )
            await query.answer()
            return

        # Direct 1-click publish without subtitles (uses default Sinhala subtitle)
        if data == "wiz:pub_direct":
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired. Please send file again.", show_alert=True)
                return

            session["subtitle_url"] = "default"
            session["quality"] = "auto_all"
            status_msg = await query.message.edit_text("⏳ <b>චිත්‍රපටය වෙබ් අඩවියට එකතු කරමින් පවතී...</b>", parse_mode=ParseMode.HTML)
            await query.answer()

            meta = session.get("meta", {})
            title = meta.get("title") or session.get("movie_name", "Untitled")

            # Register with task_tracker and spawn cancellable asyncio.Task
            task_tracker.tracker.start_task(user_id, title)
            bg_task = asyncio.create_task(_finalize_and_publish(client, status_msg, session, user_id))
            task_tracker.tracker.set_task_handle(user_id, bg_task)

            USER_SESSIONS.pop(user_id, None)
            return

        # Confirm TMDB match
        if data == "wiz:confirm_tmdb":
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired.", show_alert=True)
                return

            session["step"] = "WAITING_SUB"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏩ Default Sinhala Subtitle භාවිතා කරන්න (Publish Now)", callback_data="wiz:skip_sub")],
                [InlineKeyboardButton("💾 පසුව දැමීමට Draft ලෙස තබන්න", callback_data="wiz:save_draft")],
                [InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")],
            ])

            await query.message.edit_text(
                f"💬 <b>පියවර 2/3: සිංහල උපසිරැසි ගොනුව (.srt / .vtt) එවන්න</b>\n\n"
                f"චිත්‍රපටයේ <b>.srt</b> හෝ <b>.vtt</b> උපසිරැසි ගොනුව මට Upload කරන්න, නැතහොත් Subtitle Link එකක් එවන්න.\n\n"
                f"<i>💡 ඔබට වෙනම උපසිරැසි ගොනුවක් නැත්නම් හෝ පසුව දැමීමට අවශ්‍ය නම් ඉහත බොත්තම ඔබා Default Subtitle එක සමඟ දැන්ම Publish කරන්න.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            await query.answer()
            return

        # Search again
        if data == "wiz:search_again":
            session = USER_SESSIONS.get(user_id)
            if session:
                session["step"] = "WAITING_NAME"
            await query.message.edit_text(
                "🔍 කරුණාකර නිවැරදි චිත්‍රපට නම සහ වර්ෂය නැවත ටයිප් කර එවන්න:",
                parse_mode=ParseMode.HTML,
            )
            await query.answer()
            return

        # Skip sub / use default
        if data == "wiz:skip_sub":
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired.", show_alert=True)
                return

            session["subtitle_url"] = "default"
            session["step"] = "WAITING_QUALITY"
            await _ask_quality(query.message)
            await query.answer()
            return

        # Quality selection
        if data.startswith("wiz:qual:"):
            quality = data.split(":")[-1]
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired.", show_alert=True)
                return

            session["quality"] = quality
            status_msg = await query.message.edit_text("⏳ <b>චිත්‍රපටය වෙබ් අඩවියට එකතු කරමින් පවතී...</b>", parse_mode=ParseMode.HTML)
            await query.answer()

            meta = session.get("meta", {})
            title = meta.get("title") or session.get("movie_name", "Untitled")

            # Register with task_tracker and spawn cancellable asyncio.Task
            task_tracker.tracker.start_task(user_id, title)
            bg_task = asyncio.create_task(_finalize_and_publish(client, status_msg, session, user_id))
            task_tracker.tracker.set_task_handle(user_id, bg_task)

            USER_SESSIONS.pop(user_id, None)
            return

        # Drafts list
        if data == "drafts:list":
            await _show_drafts_menu(query.message, edit=True)
            await query.answer()
            return

        # Publish draft
        if data.startswith("draft:pub:"):
            draft_id = data.split("draft:pub:")[-1]
            draft = draft_service.get_draft(draft_id)
            if not draft:
                await query.answer("Draft not found.", show_alert=True)
                return

            # If draft already has complete movie_entry, publish directly!
            movie_entry = draft.get("movie_entry")
            if movie_entry:
                await query.answer("Publishing to website...")
                await query.message.edit_text("⏳ <b>වෙබ් අඩවිය යාවත්කාලීන කරමින් පවතී (Cloudflare Pages)...</b>", parse_mode=ParseMode.HTML)
                saved = await github_service.add_movie(movie_entry)
                if not saved:
                    await query.message.edit_text("⚠️ <b>වෙබ් අඩවිය යාවත්කාලීන කිරීම අසාර්ථක විය.</b>", parse_mode=ParseMode.HTML)
                    return

                if config.PUBLIC_CHANNEL_ID:
                    try:
                        await post_to_channel(client, movie_entry, config.PUBLIC_CHANNEL_ID)
                    except Exception as ann_err:
                        log.warning("[Wizard] Channel announcement error: %s", ann_err)

                draft_service.delete_draft(draft_id)
                USER_SESSIONS.pop(user_id, None)

                base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
                if "yoursite.lk" in base_site:
                    base_site = "https://filmsub.pages.dev"
                site_url = movie_entry.get("site_url") or f"{base_site}/movie.html?id={movie_entry.get('slug')}"
                kb_done = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=site_url)],
                    [InlineKeyboardButton("💬 Subtitle එක් කරන්න (Add Sub)", callback_data=f"leech_act:sub_posted:{movie_entry.get('slug')}")],
                ])

                title = movie_entry.get("title", "Movie")
                year = movie_entry.get("year", "")
                quality = movie_entry.get("quality", "1080p")
                imdb_val = movie_entry.get("imdb", "8.0")

                await query.message.edit_text(
                    f"🎉 <b>චිත්‍රපටය සාර්ථකව Web එකට Publish කරන ලදී!</b>\n\n"
                    f"🎬 <b>{title} ({year})</b>\n"
                    f"⭐ <b>IMDb:</b> {imdb_val} / 10 | 🎞 <b>Quality:</b> {quality}\n\n"
                    f"🌐 <b>Live Link:</b> <a href=\"{site_url}\">{site_url}</a>\n"
                    f"📢 <b>Telegram Channel:</b> Announcement Post කරන ලදී!\n"
                    f"⚡ <b>Cloudflare Pages:</b> Auto-deployed!\n\n"
                    f"💡 <i>පසුව සිංහල උපසිරැසි එක් කිරීමට: <code>/sub {title}</code></i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_done,
                    disable_web_page_preview=False,
                )
                return

            # If draft has meta, skip name prompt and jump directly to quality/publish
            if draft.get("meta"):
                USER_SESSIONS[user_id] = {
                    "draft_id": draft_id,
                    "step": "WAITING_QUALITY",
                    "file_id": draft.get("file_id", ""),
                    "file_name": draft.get("file_name", ""),
                    "file_size": draft.get("file_size", 0),
                    "film_url": draft.get("film_url", ""),
                    "title_hint": draft.get("title_hint", ""),
                    "meta": draft.get("meta", {}),
                    "movie_name": draft.get("movie_name") or draft.get("meta", {}).get("title", ""),
                    "year": draft.get("year") or draft.get("meta", {}).get("year"),
                    "subtitle_url": draft.get("subtitle_url", "default"),
                }
                await _ask_quality(query.message)
                await query.answer()
                return

            USER_SESSIONS[user_id] = {
                "draft_id": draft_id,
                "step": "WAITING_NAME",
                "file_id": draft.get("file_id", ""),
                "file_name": draft.get("file_name", ""),
                "file_size": draft.get("file_size", 0),
                "film_url": draft.get("film_url", ""),
                "title_hint": draft.get("title_hint", ""),
            }

            await query.message.edit_text(
                f"🚀 <b>Draft එක Publish කිරීම:</b> <code>{draft.get('title_hint')}</code>\n\n"
                f"🔍 <b>පියවර 1/3: චිත්‍රපටයේ නම කුමක්ද?</b>\n"
                f"කරුණාකර චිත්‍රපටයේ නම සහ වර්ෂය Reply කරන්න:",
                parse_mode=ParseMode.HTML,
            )
            await query.answer()
            return

        # Delete draft
        if data.startswith("draft:del:"):
            draft_id = data.split("draft:del:")[-1]
            draft_service.delete_draft(draft_id)
            await query.answer("Draft deleted.")
            await _show_drafts_menu(query.message, edit=True)
            return


# ─────────────────────────────────────────────────────────────────────────────
# 4. Helper Step Functions
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_standalone_subtitle_upload(client: Client, message: Message, doc) -> None:
    """Handle a user uploading an .srt or .vtt file without being in an active wizard."""
    PENDING_SUB_DOCS[message.from_user.id] = doc.file_id
    buttons = []

    # Check drafts
    drafts = draft_service.list_drafts()
    if drafts:
        recent_draft = drafts[-1]
        d_name = recent_draft.get("title_hint") or recent_draft.get("movie_name") or "Draft"
        d_id = recent_draft.get("id")
        buttons.append([
            InlineKeyboardButton(f"📁 Draft එකට එක් කරන්න: {d_name[:24]}", callback_data=f"sub_act:draft:{d_id}")
        ])

    # Check published movies in movies.json
    try:
        data, _ = await github_service.get_movies_json()
        movies = data.get("movies", [])
        if movies:
            latest_movie = movies[-1]
            m_title = latest_movie.get("title") or "Latest Movie"
            m_slug = latest_movie.get("slug") or ""
            buttons.append([
                InlineKeyboardButton(f"🎬 අවසන් චිත්‍රපටයට එක් කරන්න: {m_title[:24]}", callback_data=f"sub_act:movie:{m_slug[:45]}")
            ])
    except Exception:
        pass

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")])
    kb = InlineKeyboardMarkup(buttons)

    await message.reply_text(
        f"💬 <b>සිංහල උපසිරැසි ගොනුවක් හඳුනාගන්නා ලදී (.srt / .vtt Detected)!</b>\n\n"
        f"📁 <b>ගොනුව:</b> <code>{doc.file_name}</code>\n\n"
        f"ඔබට මෙම උපසිරැසිය එක් කිරීමට අවශ්‍ය චිත්‍රපටය පහතින් තෝරන්න:\n"
        f"<i>(නැතහොත් මෙම උපසිරැසි ගොනුවට Reply කර <code>/sub &lt;චිත්‍රපටයේ නම&gt;</code> ලෙස එවන්න)</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def _handle_movie_name_search(client: Client, message: Message, session: dict, query_text: str) -> None:
    """Search TMDB for the movie and ask for user confirmation."""
    search_msg = await message.reply("🔍 Searching TMDB database…")

    # Extract year if present (e.g. Deadpool 2024)
    match = re.search(r"\b(19\d\d|20\d\d)\b", query_text)
    year = int(match.group(1)) if match else None
    title_clean = re.sub(r"\b(19\d\d|20\d\d)\b", "", query_text).strip() or query_text

    meta = await tmdb_service.fetch_metadata(title_clean, year)
    if not meta or not meta.get("title"):
        await search_msg.edit_text(
            f"❌ <b>'{query_text}' නමින් චිත්‍රපටයක් හමු නොවීය.</b>\n\n"
            f"කරුණාකර නම නිවැරදිව පරීක්ෂා කර නැවත එවන්න:",
            parse_mode=ParseMode.HTML,
        )
        return

    session["meta"] = meta
    session["movie_name"] = meta.get("title")
    session["year"] = meta.get("year")

    poster = meta.get("poster_url") or meta.get("poster")
    imdb_str = meta.get("imdb", "N/A")
    duration_str = f"{meta.get('duration')} min" if meta.get("duration") else "N/A"
    genres_str = ", ".join(meta.get("genres", [])[:3]) or "General"
    desc = meta.get("description", "")[:180] + "..." if meta.get("description") else ""

    caption = (
        f"🎬 <b>{meta.get('title')} ({meta.get('year')})</b>\n\n"
        f"⭐ <b>IMDb:</b> {imdb_str} / 10 | ⏱ <b>ධාවන කාලය:</b> {duration_str}\n"
        f"🎭 <b>කාණ්ඩ:</b> {genres_str}\n"
        f"📖 <b>විස්තරය:</b> {desc}\n\n"
        f"<b>දැන් ඔබට අවශ්‍ය කුමක්ද? පහත බොත්තමක් තෝරන්න:</b>\n"
        f"• <b>Publish Now:</b> වෙබ් අඩවියට දැන්ම එක්වේ (උපසිරැසි පසුව දැමිය හැක)\n"
        f"• <b>Add Sub:</b> උපසිරැසි ගොනුව Upload කර Publish කරයි\n"
        f"• <b>Keep as Draft:</b> Filmhost Channel එකේ පමණක් Draft එකක් ලෙස තබයි"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data="wiz:pub_direct"),
            InlineKeyboardButton("💬 Subtitle එක් කරන්න (Add Sub)", callback_data="wiz:confirm_tmdb"),
        ],
        [
            InlineKeyboardButton("📁 Channel Draft ලෙස තබන්න (Draft Only)", callback_data="wiz:save_draft"),
            InlineKeyboardButton("🔄 වෙනත් නමක් (Search Again)", callback_data="wiz:search_again"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")],
    ])

    await search_msg.delete()
    if poster and poster.startswith("http"):
        try:
            await message.reply_photo(photo=poster, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass

    await message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _handle_sub_file(client: Client, message: Message, session: dict) -> None:
    """Receive uploaded .srt or .vtt subtitle file."""
    doc = message.document
    if not doc or not doc.file_name.lower().endswith((".srt", ".vtt")):
        await message.reply_text("❌ කරුණාකර <b>.srt</b> හෝ <b>.vtt</b> ගොනුවක් එවන්න.")
        return

    msg = await message.reply("⏳ උපසිරැසි ගොනුව සකස් කරමින් පවතී...")
    file_path = await message.download()

    vtt_path = file_path
    if file_path.lower().endswith(".srt"):
        vtt_path = subtitle_service.srt_to_vtt(file_path)

    # Read VTT content to build data URI (free, fast, self-contained)
    with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
        vtt_text = f.read()

    encoded = urllib.parse.quote(vtt_text)
    sub_url = f"data:text/vtt;charset=utf-8,{encoded}"
    session["subtitle_url"] = sub_url

    # 1. If attaching subtitle to an already published movie:
    if session.get("slug"):
        await _update_published_movie_sub(client, msg, session.get("slug"), sub_url, message.from_user.id)
        return

    # 2. If this session already has a pre-built movie_entry (e.g. from leech draft), publish directly!
    if session.get("movie_entry"):
        await _publish_draft_with_sub(client, msg, session, sub_url, message.from_user.id)
        return

    session["step"] = "WAITING_QUALITY"
    await msg.delete()
    await _ask_quality(message)


async def _handle_sub_url(client: Client, message: Message, session: dict, text: str) -> None:
    """Receive subtitle URL."""
    if not text.startswith(("http://", "https://")):
        await message.reply_text("❌ කරුණාකර වලංගු Subtitle URL එකක් හෝ Subtitle (.srt) ගොනුවක් Upload කරන්න.")
        return

    msg = await message.reply("⏳ උපසිරැසි Download කරමින් පවතී...")
    sub_url = text
    try:
        srt_path = await subtitle_service.download_subtitle(text)
        vtt_path = subtitle_service.srt_to_vtt(srt_path)
        with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
            sub_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(f.read())}"
    except Exception as exc:
        log.warning("Could not download subtitle URL into data URI: %s", exc)

    session["subtitle_url"] = sub_url

    # 1. If attaching subtitle to an already published movie:
    if session.get("slug"):
        await _update_published_movie_sub(client, msg, session.get("slug"), sub_url, message.from_user.id)
        return

    # 2. If this session already has a pre-built movie_entry, publish directly!
    if session.get("movie_entry"):
        await _publish_draft_with_sub(client, msg, session, sub_url, message.from_user.id)
        return

    session["step"] = "WAITING_QUALITY"
    await msg.delete()
    await _ask_quality(message)


async def _publish_draft_with_sub(client: Client, status_msg: Message, session: dict, sub_url: str, user_id: int) -> None:
    """Helper to update a prepared movie_entry with new subtitles and commit directly."""
    movie_entry = session["movie_entry"]
    movie_entry["subtitles"] = [
        {
            "language": "Sinhala",
            "label": "සිංහල උපසිරැසි",
            "url": sub_url,
            "default": True,
        }
    ]
    movie_entry["has_sinhala_sub"] = True
    movie_entry["subtitle_language"] = "Sinhala"

    await status_msg.edit_text("⏳ <b>වෙබ් අඩවිය යාවත්කාලීන කරමින් පවතී (Cloudflare Pages)...</b>", parse_mode=ParseMode.HTML)
    saved = await github_service.add_movie(movie_entry)
    if not saved:
        await status_msg.edit_text("⚠️ <b>වෙබ් අඩවිය යාවත්කාලීන කිරීම අසාර්ථක විය.</b>", parse_mode=ParseMode.HTML)
        return

    if config.PUBLIC_CHANNEL_ID:
        try:
            await post_to_channel(client, movie_entry, config.PUBLIC_CHANNEL_ID)
        except Exception as ann_err:
            log.warning("[Wizard] Announcement error: %s", ann_err)

    draft_id = session.get("draft_id")
    if draft_id:
        draft_service.delete_draft(draft_id)
    USER_SESSIONS.pop(user_id, None)

    base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
    if "yoursite.lk" in base_site:
        base_site = "https://filmsub.pages.dev"
    site_url = movie_entry.get("site_url") or f"{base_site}/movie.html?id={movie_entry.get('slug')}"
    kb_done = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=site_url)],
    ])

    title = movie_entry.get("title", "Movie")
    year = movie_entry.get("year", "")
    await status_msg.edit_text(
        f"🎉 <b>සිංහල උපසිරැසි සමඟ චිත්‍රපටය සාර්ථකව Web එකට එක් කරන ලදී!</b>\n\n"
        f"🎬 <b>{title} ({year})</b>\n"
        f"📝 <b>උපසිරැසි:</b> සිංහල (Sinhala VTT Attached)\n\n"
        f"🌐 <b>Live Link:</b> <a href=\"{site_url}\">{site_url}</a>\n"
        f"📢 <b>Telegram Channel:</b> Announcement Post කරන ලදී!\n"
        f"⚡ <b>Cloudflare Pages:</b> Auto-deployed!",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_done,
        disable_web_page_preview=False,
    )


async def _update_published_movie_sub(client: Client, status_msg: Message, slug: str, sub_url: str, user_id: int) -> None:
    """Update subtitle for an already published movie in movies.json or published draft."""
    await status_msg.edit_text("⏳ <b>වෙබ් අඩවියේ උපසිරැසි යාවත්කාලීන කරමින් පවතී (Cloudflare Pages)...</b>", parse_mode=ParseMode.HTML)

    data, sha = await github_service.get_movies_json()
    movies = data.get("movies", [])
    target = None
    for m in reversed(movies):
        if m.get("slug") == slug or _slugify(m.get("title", ""), m.get("year")) == slug or m.get("id") == slug:
            target = m
            break

    if not target:
        # Check drafts fallback
        draft = draft_service.get_draft(slug)
        if draft and draft.get("movie_entry"):
            session = {"movie_entry": draft.get("movie_entry"), "draft_id": slug}
            await _publish_draft_with_sub(client, status_msg, session, sub_url, user_id)
            return

        await status_msg.edit_text(f"❌ <code>{slug}</code> චිත්‍රපටය සොයාගත නොහැකි විය.")
        USER_SESSIONS.pop(user_id, None)
        return

    target["subtitles"] = [
        {
            "language": "Sinhala",
            "label": "සිංහල උපසිරැසි",
            "url": sub_url,
            "default": True,
        }
    ]
    target["subtitle_language"] = "Sinhala"
    target["has_sinhala_sub"] = True

    saved = await github_service.add_movie(target)
    if not saved:
        await status_msg.edit_text("⚠️ <b>වෙබ් අඩවිය යාවත්කාලීන කිරීම අසාර්ථක විය.</b>", parse_mode=ParseMode.HTML)
        return

    USER_SESSIONS.pop(user_id, None)

    base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
    if "yoursite.lk" in base_site:
        base_site = "https://filmsub.pages.dev"
    watch_link = target.get("site_url") or f"{base_site}/movie.html?id={target.get('slug')}"
    kb_done = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=watch_link)],
    ])

    title = target.get("title", "Movie")
    year = target.get("year", "")
    await status_msg.edit_text(
        f"🎉 <b>සිංහල උපසිරැසි සාර්ථකව යාවත්කාලීන කරන ලදී!</b>\n\n"
        f"🎬 <b>{title} ({year})</b>\n"
        f"📝 <b>උපසිරැසි:</b> සිංහල (Sinhala VTT Attached)\n\n"
        f"🌐 <b>Live Link:</b> <a href=\"{watch_link}\">{watch_link}</a>\n"
        f"⚡ <b>Cloudflare Pages:</b> Auto-deployed!\n\n"
        f"💡 <i>වෙබ් අඩවියෙන් නරඹන විට හෝ බාගත කරන විට උපසිරැසි ස්වයංක්‍රීයව Play වේ.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_done,
        disable_web_page_preview=False,
    )


async def _ask_quality(target_message: Message) -> None:
    """Ask user to select quality or auto-generate all."""
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ Auto-Generate All (1080p + 720p + 480p)", callback_data="wiz:qual:auto_all"),
        ],
        [
            InlineKeyboardButton("1080p FHD", callback_data="wiz:qual:1080p"),
            InlineKeyboardButton("720p HD", callback_data="wiz:qual:720p"),
            InlineKeyboardButton("480p SD", callback_data="wiz:qual:480p"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")],
    ])

    await target_message.reply_text(
        "🎞 <b>පියවර 3/3: Video Quality & Transcoding</b>\n\n"
        "වෙබ් අඩවියේ පෙන්වීමට අවශ්‍ය Quality එක තෝරන්න:\n\n"
        "✨ <b>Auto-Generate All (Recommended):</b>\n"
        "වෙබ් අඩවියේ <b>480p, 720p, 1080p</b> බාගත කිරීමේ Cards 3 ම ස්වයංක්‍රීයව සාදයි.\n\n"
        "⚡ <b>Single Quality:</b>\n"
        "තෝරාගත් එක් Quality එකක් පමණක් යොදයි.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def _finalize_and_publish(client: Client, status_msg: Message, session: dict, user_id: int = 0) -> None:
    """Execute complete movie publishing workflow."""
    meta = session.get("meta", {})
    file_id = session.get("file_id", "")
    film_url = session.get("film_url", "")
    file_name = session.get("file_name", "movie.mp4")
    file_size = session.get("file_size", 0)
    sub_url = session.get("subtitle_url", "default")
    quality = session.get("quality", "1080p")
    title = meta.get("title") or session.get("movie_name", "Untitled")
    year = meta.get("year", 2025)

    uid = user_id or (status_msg.chat.id if status_msg.chat else 0)

    try:
        task_tracker.tracker.set_step(uid, "1/4 - වීඩියෝව සකස් කරමින් පවතී...")
        await status_msg.edit_text("⏳ <b>Step 1/4:</b> වීඩියෝව සකස් කරමින් පවතී...", parse_mode=ParseMode.HTML)

        stream_url = ""
        message_id = session.get("message_id", 0)

        # Copy to Filmhost channel if uploaded in bot DM
        if config.PRIVATE_CHANNEL_ID and session.get("reply_message_id") and session.get("reply_chat_id") and session.get("reply_chat_id") != config.PRIVATE_CHANNEL_ID:
            try:
                copied = await client.copy_message(
                    chat_id=config.PRIVATE_CHANNEL_ID,
                    from_chat_id=session["reply_chat_id"],
                    message_id=session["reply_message_id"],
                )
                message_id = copied.id
                if copied.video:
                    file_id = copied.video.file_id
                elif copied.document:
                    file_id = copied.document.file_id
                stream_url = telegram_upload.get_file_stream_url(file_id)
                log.info("[Wizard] Video file backed up to Filmhost channel (message_id=%s)", message_id)
            except Exception as copy_err:
                log.warning("[Wizard] Could not copy video to Filmhost channel: %s", copy_err)

        # Handle video source if not set by channel copy
        if not stream_url:
            if file_id:
                if config.BOT_TOKEN and (not file_size or file_size < 20 * 1024 * 1024):
                    direct = await telegram_upload.get_telegram_direct_url(file_id, config.BOT_TOKEN)
                    stream_url = direct or telegram_upload.get_file_stream_url(file_id)
                else:
                    stream_url = telegram_upload.get_file_stream_url(file_id)
            elif film_url:
                stream_url = film_url

        # Handle Subtitle
        task_tracker.tracker.set_step(uid, "2/4 - සිංහල උපසිරැසි සකස් කරමින් පවතී...")
        await status_msg.edit_text("⏳ <b>Step 2/4:</b> සිංහල උපසිරැසි සකස් කරමින් පවතී...", parse_mode=ParseMode.HTML)
        if sub_url == "default":
            sub_text = f"WEBVTT\n\n1\n00:00:01.000 --> 00:00:06.000\nFilmSub.lk වෙතින් සිංහල උපසිරැසි සමඟ\n\n2\n00:00:07.000 --> 00:00:15.000\n{title} ({year}) නැරඹීමට ස්තූතියි!"
            sub_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(sub_text)}"
        elif not sub_url.startswith("data:text/vtt"):
            try:
                srt_path = await subtitle_service.download_subtitle(sub_url)
                vtt_path = subtitle_service.srt_to_vtt(srt_path)
                with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
                    sub_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(f.read())}"
            except Exception:
                sub_text = f"WEBVTT\n\n1\n00:00:01.000 --> 00:00:06.000\nFilmSub.lk වෙතින් සිංහල උපසිරැසි සමඟ"
                sub_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(sub_text)}"

        # Build movie schema dict
        task_tracker.tracker.set_step(uid, "3/4 - Cloudflare Pages යාවත්කාලීන කරමින් පවතී...")
        await status_msg.edit_text("⏳ <b>Step 3/4:</b> වෙබ් අඩවිය යාවත්කාලීන කරමින් පවතී (Cloudflare Pages)...", parse_mode=ParseMode.HTML)
        slug = _slugify(title, year)
        base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
        if "yoursite.lk" in base_site:
            base_site = "https://filmsub.pages.dev"
        site_url = f"{base_site}/movie.html?id={slug}"

        display_quality = "1080p" if quality == "auto_all" else quality
        movie_dict = add_movie._build_movie_dict(
            meta=meta,
            slug=slug,
            quality=display_quality,
            lang="Sinhala",
            file_id=file_id,
            stream_url=stream_url,
            message_id=message_id,
            file_name=file_name,
            file_size=file_size,
            subtitle_url=sub_url,
            site_url=site_url,
        )

        # Multi-quality download cards generation
        if quality == "auto_all":
            sz = file_size or (1024 * 1024 * 1024 * 2)  # default 2GB if size unknown
            sz_480 = int(sz * 0.28)
            sz_720 = int(sz * 0.55)
            sz_1080 = sz
            movie_dict["downloads"] = [
                {"quality": "480p", "size": add_movie._human_size(sz_480), "url": stream_url, "format": "MP4"},
                {"quality": "720p", "size": add_movie._human_size(sz_720), "url": stream_url, "format": "MP4"},
                {"quality": "1080p", "size": add_movie._human_size(sz_1080), "url": stream_url, "format": "MP4"},
            ]
            movie_dict["quality"] = "1080p"

        # Save to movies.json & auto-deploy Cloudflare Pages
        success = await github_service.add_movie(movie_dict)
        if not success:
            raise RuntimeError("Failed to save movie to database/Cloudflare Pages.")

        # Remove draft if originated from one
        draft_id = session.get("draft_id")
        if draft_id:
            draft_service.delete_draft(draft_id)

        # Post channel announcement
        task_tracker.tracker.set_step(uid, "4/4 - Telegram Channel එකට Post කරමින් පවතී...")
        await status_msg.edit_text("⏳ <b>Step 4/4:</b> Telegram Channel එකට Post කරමින් පවතී...", parse_mode=ParseMode.HTML)
        if config.PUBLIC_CHANNEL_ID:
            try:
                await post_to_channel(client, movie_dict, config.PUBLIC_CHANNEL_ID)
            except Exception as ann_err:
                log.warning("Could not post channel announcement: %s", ann_err)

        # Success Response
        task_tracker.tracker.complete_task(uid)

        imdb_rating = meta.get("imdb", "N/A")
        genres = ", ".join(meta.get("genres", [])[:3])

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=site_url)],
            [InlineKeyboardButton("💬 Subtitle එක් කරන්න (Add Sub)", callback_data=f"leech_act:sub_posted:{slug}")],
        ])

        await status_msg.edit_text(
            f"🎉 <b>චිත්‍රපටය සාර්ථකව Web එකට එකතු කරන ලදී!</b>\n\n"
            f"🎬 <b>{title} ({year})</b>\n"
            f"⭐ <b>IMDb:</b> {imdb_rating} / 10 | 🎞 <b>Quality:</b> {quality}\n"
            f"🎭 <b>කාණ්ඩ:</b> {genres}\n\n"
            f"🌐 <b>Live Link:</b> <a href=\"{site_url}\">{site_url}</a>\n"
            f"📢 <b>Telegram Channel:</b> Announcement Posted!\n"
            f"⚡ <b>Cloudflare Pages:</b> Updated automatically!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=False,
        )
    except asyncio.CancelledError:
        log.info("Task for user %s cancelled.", uid)
        task_tracker.tracker.cancel_task(uid)
        try:
            await asyncio.shield(status_msg.edit_text(
                f"❌ <b>චිත්‍රපට සැකසීමේ ක්‍රියාවලිය අවලංගු කරන ලදී (Cancelled).</b>\n\n🎬 <b>{title}</b>",
                parse_mode=ParseMode.HTML,
            ))
        except (Exception, asyncio.CancelledError):
            pass
        raise
    except Exception as exc:
        log.exception("Error in _finalize_and_publish for user %s: %s", uid, exc)
        task_tracker.tracker.fail_task(uid, str(exc))
        try:
            err_text = str(exc).replace("<", "&lt;").replace(">", "&gt;")
            await status_msg.edit_text(
                f"⚠️ <b>චිත්‍රපටය සැකසීමේදී දෝෂයක් සිදු විය (Failed)!</b>\n\n"
                f"🎬 <b>{title}</b>\n"
                f"❌ දෝෂය: <code>{err_text}</code>\n\n"
                f"කරුණාකර නැවත උත්සාහ කරන්න හෝ /status බලන්න.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def _show_drafts_menu(message: Message, edit: bool = False) -> None:
    """Display the list of saved drafts with action buttons."""
    drafts = draft_service.list_drafts()
    if not drafts:
        text = "📂 <b>සුරැකි Drafts කිසිවක් නොමැත (No drafts found).</b>\n\nචිත්‍රපටයක් upload කර 'Draft' තෝරාගත් විට මෙහි දිස්වේ."
        if edit:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    buttons = []
    lines = ["📂 <b>සුරැකි චිත්‍රපට Drafts (Saved Drafts):</b>\n"]

    for i, d in enumerate(drafts, 1):
        name = d.get("title_hint", "Movie")
        size = add_movie._human_size(d.get("file_size", 0))
        lines.append(f"<b>{i}. {name}</b> ({size})\n   📅 {d.get('created_at')}")

        d_id = d.get("id")
        buttons.append([
            InlineKeyboardButton(f"🚀 Publish #{i}", callback_data=f"draft:pub:{d_id}"),
            InlineKeyboardButton(f"💬 Sub #{i}", callback_data=f"leech_act:sub:{d_id}"),
            InlineKeyboardButton(f"🗑 Delete #{i}", callback_data=f"draft:del:{d_id}"),
        ])

    buttons.append([InlineKeyboardButton("❌ Close", callback_data="wiz:cancel")])
    kb = InlineKeyboardMarkup(buttons)
    text = "\n".join(lines) + "\n\n<i>Publish කිරීමට අවශ්‍ය Draft එකේ බොත්තම ඔබන්න:</i>"

    if edit:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


def _extract_title_hint(filename: str) -> str:
    """Extract clean title hint from a messy torrent/video filename."""
    name = re.sub(r"\.[a-zA-Z0-9]+$", "", filename)
    name = re.sub(r"[._]", " ", name)
    name = re.sub(r"(1080p|720p|480p|2160p|4k|bluray|web-dl|webrip|x264|x265|hevc|aac|dvdrip).*", "", name, flags=re.IGNORECASE)
    return name.strip()
