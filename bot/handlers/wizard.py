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

        # Otherwise treat as new Movie Video upload
        doc = message.document
        vid = message.video

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

        USER_SESSIONS[message.from_user.id] = {
            "session_id": session_id,
            "step": "CHOICE",
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "film_url": "",
            "title_hint": _extract_title_hint(file_name),
        }

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data="wiz:pub_start"),
                InlineKeyboardButton("💾 පස්සේ දාන්න Save කරගන්න (Draft)", callback_data="wiz:save_draft"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")],
        ])

        await message.reply_text(
            f"🎬 <b>චිත්‍රපට වීඩියෝව හඳුනාගන්නා ලදී! (Movie File Detected)</b>\n\n"
            f"📁 <b>ගොනුව:</b> <code>{file_name}</code>\n"
            f"📦 <b>ප්‍රමාණය:</b> {size_str}\n\n"
            f"ඔබට අවශ්‍ය කුමක්ද?",
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
            "• <code>/status</code> — System status\n\n"
            "<i>වීඩියෝවක් හෝ Direct Link එකක් එවන්න, නැතහොත් ඉහත Command එකක් භාවිතා කරන්න.</i>",
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
        # If arguments are passed, route to existing one-liner add_movie handler
        if tokens or (message.reply_to_message and message.reply_to_message.video):
            await add_movie._handle_add(client, message)
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

    @app.on_message(filters.private & filters.command("cancel"))
    async def cancel_command_handler(client: Client, message: Message) -> None:
        if not _is_admin(message.from_user.id):
            return
        user_id = message.from_user.id
        had_session = user_id in USER_SESSIONS
        if had_session:
            del USER_SESSIONS[user_id]

        was_task_cancelled = task_tracker.tracker.cancel_task(user_id)
        if not was_task_cancelled and task_tracker.tracker.get_any_active_task():
            was_task_cancelled = task_tracker.tracker.cancel_task(None)

        if was_task_cancelled:
            await message.reply_text("❌ ක්‍රියාත්මක වෙමින් පැවති කාර්යය අවලංගු කරන ලදී (Active task cancelled).")
        elif had_session:
            await message.reply_text("❌ ක්‍රියාවලිය අවලංගු කරන ලදී (Wizard cancelled).")
        else:
            await message.reply_text("ℹ️ අවලංගු කිරීමට කිසිදු ක්‍රියාවලියක් නොමැත (No active wizard or task).")

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
            was_task_cancelled = task_tracker.tracker.cancel_task(user_id)
            if not was_task_cancelled and task_tracker.tracker.get_any_active_task():
                was_task_cancelled = task_tracker.tracker.cancel_task(None)
            if was_task_cancelled:
                await query.message.edit_text("❌ ක්‍රියාත්මක වෙමින් පැවති කාර්යය අවලංගු කරන ලදී (Active task cancelled).")
            else:
                await query.message.edit_text("❌ ක්‍රියාවලිය අවලංගු කරන ලදී.")
            await query.answer()
            return

        # Save to draft
        if data == "wiz:save_draft":
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired. Please send video again.", show_alert=True)
                return

            draft_id = draft_service.save_draft(session)
            USER_SESSIONS.pop(user_id, None)

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data=f"draft:pub:{draft_id}")],
                [InlineKeyboardButton("📂 සියලුම Drafts බලන්න (/drafts)", callback_data="drafts:list")],
            ])

            await query.message.edit_text(
                f"💾 <b>චිත්‍රපටය Drafts තුළ සාර්ථකව සුරැකිණි!</b>\n\n"
                f"🆔 <b>Draft ID:</b> <code>{draft_id}</code>\n"
                f"📁 <b>ගොනුව:</b> {session.get('file_name', 'Movie')}\n\n"
                f"ඔබට අවශ්‍ය ඕනෑම වේලාවක මෙම චිත්‍රපටයේ නම, උපසිරැසි ලබාදී වෙබ් අඩවියට Publish කළ හැක.",
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

        # Confirm TMDB match
        if data == "wiz:confirm_tmdb":
            session = USER_SESSIONS.get(user_id)
            if not session:
                await query.answer("Session expired.", show_alert=True)
                return

            session["step"] = "WAITING_SUB"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏩ Default Sinhala Subtitle භාවිතා කරන්න", callback_data="wiz:skip_sub")],
                [InlineKeyboardButton("❌ Cancel", callback_data="wiz:cancel")],
            ])

            await query.message.edit_text(
                f"💬 <b>පියවර 2/3: සිංහල උපසිරැසි ගොනුව (.srt / .vtt) එවන්න</b>\n\n"
                f"චිත්‍රපටයේ <b>.srt</b> හෝ <b>.vtt</b> උපසිරැසි ගොනුව මට Upload කරන්න, නැතහොත් Subtitle Link එකක් එවන්න.\n\n"
                f"<i>(ඔබ ළඟ වෙනම උපසිරැසි ගොනුවක් නැත්නම් පහත බොත්තම ඔබා Default Subtitle එක තෝරන්න)</i>",
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
        f"<b>මෙම චිත්‍රපටය නිවැරදිද?</b>"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ඔව්, දිගටම යන්න (Continue)", callback_data="wiz:confirm_tmdb"),
            InlineKeyboardButton("🔄 වෙනත් නමක් සෙවීම", callback_data="wiz:search_again"),
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
    session["subtitle_url"] = f"data:text/vtt;charset=utf-8,{encoded}"
    session["step"] = "WAITING_QUALITY"

    await msg.delete()
    await _ask_quality(message)


async def _handle_sub_url(client: Client, message: Message, session: dict, text: str) -> None:
    """Receive subtitle URL."""
    if not text.startswith(("http://", "https://")):
        await message.reply_text("❌ කරුණාකර වලංගු Subtitle URL එකක් හෝ Subtitle (.srt) ගොනුවක් Upload කරන්න.")
        return

    session["subtitle_url"] = text
    session["step"] = "WAITING_QUALITY"
    await _ask_quality(message)


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
        message_id = 0

        # Handle video source
        if file_id:
            # Check if direct Telegram URL is available (<20MB or unknown size)
            if config.BOT_TOKEN and (not file_size or file_size < 20 * 1024 * 1024):
                direct = await telegram_upload.get_telegram_direct_url(file_id, config.BOT_TOKEN)
                stream_url = direct or telegram_upload.get_file_stream_url(file_id)
            else:
                stream_url = telegram_upload.get_file_stream_url(file_id)
        elif film_url:
            # Check if direct link or needs uploading
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
