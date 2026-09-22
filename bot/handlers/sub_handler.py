"""
sub_handler.py — Command handler for adding or updating Sinhala subtitles for movies.

Enables adding subtitles at any time (even days after a movie is downloaded and published).
Supports:
1. /sub <movie_name_or_slug> <subtitle_url>
2. Replying to an .srt / .vtt file with: /sub <movie_name_or_slug>
3. Replying to an .srt / .vtt file with just: /sub (attaches to the most recently added movie or draft!)
4. Callback queries from standalone subtitle uploads (sub_act:draft / sub_act:movie)
"""

import logging
import os
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
from handlers.announce import post_to_channel
from services import draft_service, github_service, subtitle_service
from services.auth_service import auth_service

log = logging.getLogger(__name__)

# Temporary in-memory cache for standalone subtitle document file_ids (user_id -> file_id)
# Avoids putting 70+ character Telegram file_ids directly into 64-byte callback_data
PENDING_SUB_DOCS: dict[int, str] = {}


def _clean_site_url(slug: str) -> str:
    base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
    if "yoursite.lk" in base_site:
        base_site = "https://filmsub.pages.dev"
    return f"{base_site}/movie.html?id={slug}"


def _matches_movie(query: str, title: str, slug: str, year=None, movie_id: str = "") -> bool:
    """Robust multi-criteria matcher for movie titles, slugs, and draft IDs."""
    if not query:
        return False
    q = query.strip().lower()
    t = (title or "").strip().lower()
    s = (slug or "").strip().lower()
    mid = str(movie_id or "").strip().lower()
    y = str(year or "").strip()

    if q == mid or q == s:
        return True

    # Normalize hyphens and punctuation
    q_norm = re.sub(r"[\W_]+", " ", q).strip()
    t_norm = re.sub(r"[\W_]+", " ", t).strip()
    s_norm = re.sub(r"[\W_]+", " ", s).strip()
    full_norm = f"{t_norm} {y}".strip()

    if q_norm == t_norm or q_norm == s_norm or q_norm == full_norm:
        return True

    if q_norm in t_norm or t_norm in q_norm:
        return True

    if q_norm in s_norm or s_norm in q_norm:
        return True

    if q_norm in full_norm or full_norm in q_norm:
        return True

    q_words = set(q_norm.split())
    combined_words = set(f"{t_norm} {s_norm} {y}".split())
    if q_words and q_words.issubset(combined_words):
        return True

    return False


def register(app: Client) -> None:
    """Register /sub and /addsub command handlers and sub_act callback queries."""

    @app.on_message(filters.private & filters.command(["sub", "addsub", "subtitle"]))
    async def handle_sub_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        username = message.from_user.username if message.from_user else ""
        if not auth_service.is_authorized(user_id, username):
            await message.reply_text("⛔ Access denied. You need uploader permission.")
            return

        text = (message.text or "").strip()
        tokens = text.split()

        # Check if replying to a document (SRT or VTT)
        reply = message.reply_to_message
        target_doc = None
        if reply and reply.document:
            fname = (reply.document.file_name or "").lower()
            if fname.endswith(".srt") or fname.endswith(".vtt"):
                target_doc = reply.document

        query_name = ""
        sub_url = ""

        if len(tokens) >= 3 and not target_doc:
            # /sub <movie_name> <sub_url>
            query_name = tokens[1].strip()
            sub_url = tokens[2].strip()
        elif len(tokens) >= 2:
            query_name = tokens[1].strip()
            if len(tokens) > 2 and (tokens[2].startswith("http://") or tokens[2].startswith("https://")):
                sub_url = tokens[2].strip()
        elif target_doc and len(tokens) == 1:
            # User replied with just /sub -> target the latest movie or draft!
            query_name = "LATEST"
        else:
            await message.reply_text(
                "📝 <b>චිත්‍රපටයකට සිංහල උපසිරැසි (Sinhala Subtitle) එක් කිරීම:</b>\n\n"
                "<b>ක්‍රම 3ක් ඔස්සේ භාවිතා කළ හැක:</b>\n"
                "1️⃣ <b>Reply ක්‍රමය (වඩාත් පහසුම):</b>\n"
                "   .srt හෝ .vtt file එකක් Bot වෙත එවා, එයට Reply කර:\n"
                "   <code>/sub &lt;චිත්‍රපටයේ නම&gt;</code> ලෙස යවන්න.\n"
                "   <i>(නම නොදමා Reply කර <code>/sub</code> පමණක් යැවුවහොත් අන්තිමට එක්කළ චිත්‍රපටයට හෝ Draft එකට ස්වයංක්‍රීයව එක්වේ)</i>\n\n"
                "2️⃣ <b>Link ක්‍රමය:</b>\n"
                "   <code>/sub &lt;චිත්‍රපටයේ නම&gt; &lt;subtitle_url&gt;</code>\n\n"
                "💡 <i>උපසිරැසි එක්කළ වහාම වෙබ් අඩවියේ Player එකෙහි ස්වයංක්‍රීයව Play වේ.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        status_msg = await message.reply_text("⏳ <b>උපසිරැසි ගොනුව සකසමින් පවතී...</b>", parse_mode=ParseMode.HTML)

        with tempfile.TemporaryDirectory(prefix="sub_upload_") as tmpdir:
            local_srt = os.path.join(tmpdir, "subtitle.srt")
            local_vtt = os.path.join(tmpdir, "subtitle.vtt")

            try:
                # 1. Download the subtitle file
                if target_doc:
                    downloaded = await client.download_media(message=target_doc.file_id, file_name=local_srt)
                    if downloaded and downloaded.endswith(".vtt"):
                        local_vtt = downloaded
                    else:
                        local_vtt = subtitle_service.srt_to_vtt(downloaded)
                elif sub_url:
                    saved_srt = await subtitle_service.download_subtitle(sub_url, local_srt)
                    local_vtt = subtitle_service.srt_to_vtt(saved_srt)
                else:
                    await status_msg.edit_text("❌ උපසිරැසි Link එකක් හෝ .srt/.vtt file එකක් හමු නොවීය.")
                    return

                # Read VTT content
                with open(local_vtt, "r", encoding="utf-8", errors="replace") as vf:
                    vtt_text = vf.read()

                vtt_data_uri = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(vtt_text)}"

                # 2. Fetch movies.json
                data, sha = await github_service.get_movies_json()
                movies = data.get("movies", [])

                # 3. Find matching movie in movies.json
                target_movie = None
                if query_name.upper() == "LATEST" and movies:
                    target_movie = movies[-1]
                elif movies:
                    for m in reversed(movies):
                        if _matches_movie(
                            query=query_name,
                            title=m.get("title", ""),
                            slug=m.get("slug", ""),
                            year=m.get("year"),
                            movie_id=m.get("id", ""),
                        ):
                            target_movie = m
                            break

                # If found in movies.json, update it!
                if target_movie:
                    movie_title = target_movie.get("title", "Movie")
                    movie_slug = target_movie.get("slug", "movie")
                    vtt_url = vtt_data_uri

                    if config.GITHUB_TOKEN and config.GITHUB_REPO:
                        try:
                            remote_url = await subtitle_service.upload_subtitle_to_github(
                                vtt_path=local_vtt,
                                filename=f"{movie_slug}-si.vtt",
                                github_token=config.GITHUB_TOKEN,
                                repo=config.GITHUB_REPO,
                            )
                            if remote_url:
                                vtt_url = remote_url
                        except Exception as gh_err:
                            log.warning("[SubHandler] GitHub subtitle upload fallback to data URI: %s", gh_err)

                    target_movie["subtitles"] = [
                        {
                            "language": "Sinhala",
                            "label": "සිංහල උපසිරැසි",
                            "url": vtt_url,
                            "default": True,
                        }
                    ]
                    target_movie["subtitle_language"] = "Sinhala"
                    target_movie["has_sinhala_sub"] = True

                    saved = await github_service.add_movie(target_movie)
                    watch_link = _clean_site_url(movie_slug)

                    await status_msg.edit_text(
                        f"✅ <b>සිංහල උපසිරැසි සාර්ථකව එක් කරන ලදී (Subtitle Added)!</b>\n\n"
                        f"🎬 <b>චිත්‍රපටය:</b> {movie_title}\n"
                        f"📝 <b>භාෂාව:</b> සිංහල (Sinhala)\n"
                        f"🌐 <b>වෙබ් පිටුව:</b> <a href=\"{watch_link}\">{watch_link}</a>\n\n"
                        f"⚡ <i>වෙබ් අඩවියෙන් නරඹන විට හෝ බාගත කරන විට උපසිරැසි ස්වයංක්‍රීයව ක්‍රියාත්මක වේ.</i>",
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                    log.info("[SubHandler] Subtitle successfully attached to '%s'", movie_title)
                    return

                # 4. If not found in movies.json, search in drafts!
                drafts = draft_service.list_drafts()
                target_draft = None
                if query_name.upper() == "LATEST" and drafts:
                    target_draft = drafts[-1]
                elif drafts:
                    for d in reversed(drafts):
                        d_title = d.get("title_hint") or d.get("movie_name") or ""
                        d_slug = d.get("movie_entry", {}).get("slug") if d.get("movie_entry") else ""
                        if _matches_movie(
                            query=query_name,
                            title=d_title,
                            slug=d_slug,
                            year=d.get("year"),
                            movie_id=d.get("id", ""),
                        ):
                            target_draft = d
                            break

                if target_draft:
                    d_title = target_draft.get("title_hint") or target_draft.get("movie_name") or "Movie"
                    # If the draft has a prepared movie_entry, update and publish it right now!
                    movie_entry = target_draft.get("movie_entry")
                    if movie_entry:
                        movie_entry["subtitles"] = [
                            {
                                "language": "Sinhala",
                                "label": "සිංහල උපසිරැසි",
                                "url": vtt_data_uri,
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
                                log.warning("[SubHandler] Announcement error: %s", ann_err)

                        draft_service.delete_draft(target_draft["id"])
                        watch_link = _clean_site_url(movie_entry.get("slug"))

                        kb_done = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=watch_link)],
                        ])

                        await status_msg.edit_text(
                            f"🎉 <b>සිංහල උපසිරැසි සමඟ Draft චිත්‍රපටය සාර්ථකව Web එකට Publish කරන ලදී!</b>\n\n"
                            f"🎬 <b>{movie_entry.get('title')} ({movie_entry.get('year')})</b>\n"
                            f"📝 <b>උපසිරැසි:</b> සිංහල (Sinhala VTT Attached)\n\n"
                            f"🌐 <b>Live Link:</b> <a href=\"{watch_link}\">{watch_link}</a>\n"
                            f"📢 <b>Telegram Channel:</b> Announcement Post කරන ලදී!\n"
                            f"⚡ <b>Cloudflare Pages:</b> Auto-deployed!",
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb_done,
                            disable_web_page_preview=False,
                        )
                        return

                    # Otherwise, just attach to the draft so it's ready when published
                    draft_service.update_draft(target_draft["id"], {
                        "subtitle_url": vtt_data_uri,
                        "subtitles": [
                            {
                                "language": "Sinhala",
                                "label": "සිංහල උපසිරැසි",
                                "url": vtt_data_uri,
                                "default": True,
                            }
                        ]
                    })
                    kb_pub = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data=f"draft:pub:{target_draft['id']}")],
                    ])
                    await status_msg.edit_text(
                        f"✅ <b>සිංහල උපසිරැසි සාර්ථකව Draft එකට එක් කරන ලදී!</b>\n\n"
                        f"🎬 <b>Draft:</b> {d_title}\n"
                        f"📝 <b>උපසිරැසි:</b> සිංහල (Sinhala Subtitle Attached)\n\n"
                        f"වෙබ් අඩවියට දැන්ම Publish කිරීමට පහත බොත්තම ඔබන්න:",
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb_pub,
                    )
                    return

                # Not found anywhere
                await status_msg.edit_text(
                    f"❌ <b>'{query_name}'</b> නමින් චිත්‍රපටයක් හෝ Draft එකක් හමු නොවීය.\n"
                    f"කරුණාකර නම නිවැරදිව ලබාදෙන්න හෝ /drafts පරීක්ෂා කරන්න.",
                    parse_mode=ParseMode.HTML,
                )

            except Exception as exc:
                log.exception("[SubHandler] Subtitle upload error: %s", exc)
                await status_msg.edit_text(f"⚠️ උපසිරැසි එක්කිරීමේදී දෝෂයක් සිදු විය: <code>{exc}</code>", parse_mode=ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^sub_act:"))
    async def sub_action_callback(client: Client, query: CallbackQuery) -> None:
        user_id = query.from_user.id
        if not auth_service.is_authorized(user_id, query.from_user.username or ""):
            await query.answer("Unauthorized.", show_alert=True)
            return

        data = query.data  # format: sub_act:draft:<draft_id> OR sub_act:movie:<slug> (or legacy with file_id)
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("Invalid callback data.", show_alert=True)
            return

        act_type = parts[1]
        target_id = parts[2]
        file_id = parts[3] if len(parts) >= 4 else PENDING_SUB_DOCS.get(user_id, "")
        if not file_id:
            await query.answer("Subtitle session expired. Please upload .srt again.", show_alert=True)
            return

        await query.answer("උපසිරැසි සකස් කරමින් පවතී...")
        status_msg = await query.message.reply_text("⏳ <b>උපසිරැසි ගොනුව සකසමින් පවතී...</b>", parse_mode=ParseMode.HTML)

        with tempfile.TemporaryDirectory(prefix="sub_cb_") as tmpdir:
            local_srt = os.path.join(tmpdir, "sub.srt")
            try:
                downloaded = await client.download_media(message=file_id, file_name=local_srt)
                if downloaded and downloaded.endswith(".vtt"):
                    local_vtt = downloaded
                else:
                    local_vtt = subtitle_service.srt_to_vtt(downloaded)

                with open(local_vtt, "r", encoding="utf-8", errors="replace") as f:
                    vtt_text = f.read()

                vtt_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(vtt_text)}"

                if act_type == "draft":
                    draft = draft_service.get_draft(target_id)
                    if not draft:
                        await status_msg.edit_text("❌ Draft සොයාගත නොහැකි විය.")
                        return

                    movie_entry = draft.get("movie_entry")
                    if movie_entry:
                        movie_entry["subtitles"] = [{"language": "Sinhala", "label": "සිංහල උපසිරැසි", "url": vtt_url, "default": True}]
                        movie_entry["has_sinhala_sub"] = True
                        movie_entry["subtitle_language"] = "Sinhala"
                        await status_msg.edit_text("⏳ <b>වෙබ් අඩවිය යාවත්කාලීන කරමින් පවතී...</b>", parse_mode=ParseMode.HTML)
                        saved = await github_service.add_movie(movie_entry)
                        if not saved:
                            await status_msg.edit_text("⚠️ වෙබ් අඩවිය යාවත්කාලීන කිරීම අසාර්ථක විය.")
                            return
                        if config.PUBLIC_CHANNEL_ID:
                            try:
                                await post_to_channel(client, movie_entry, config.PUBLIC_CHANNEL_ID)
                            except Exception:
                                pass
                        draft_service.delete_draft(target_id)
                        watch_link = _clean_site_url(movie_entry.get("slug"))
                        kb_done = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=watch_link)]])
                        await status_msg.edit_text(
                            f"🎉 <b>සිංහල උපසිරැසි සමඟ Draft චිත්‍රපටය සාර්ථකව Web එකට Publish කරන ලදී!</b>\n\n"
                            f"🎬 <b>{movie_entry.get('title')}</b>\n\n"
                            f"🌐 <b>Live Link:</b> <a href=\"{watch_link}\">{watch_link}</a>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb_done,
                            disable_web_page_preview=False,
                        )
                        return
                    else:
                        draft_service.update_draft(target_id, {"subtitle_url": vtt_url})
                        kb_pub = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 දැන්ම Web එකට දාන්න (Publish Now)", callback_data=f"draft:pub:{target_id}")]])
                        await status_msg.edit_text(
                            f"✅ <b>උපසිරැසිය Draft එකට සාර්ථකව එක් කරන ලදී!</b>\n\n"
                            f"Publish කිරීමට පහත බොත්තම ඔබන්න:",
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb_pub,
                        )
                        return

                elif act_type == "movie":
                    data, _ = await github_service.get_movies_json()
                    movies = data.get("movies", [])
                    target_movie = next((m for m in movies if (m.get("slug") or "") == target_id or (m.get("slug") or "").startswith(target_id) or (m.get("id") or "") == target_id), None)
                    if not target_movie:
                        await status_msg.edit_text("❌ චිත්‍රපටය movies.json හි හමු නොවීය.")
                        return

                    target_movie["subtitles"] = [{"language": "Sinhala", "label": "සිංහල උපසිරැසි", "url": vtt_url, "default": True}]
                    target_movie["has_sinhala_sub"] = True
                    target_movie["subtitle_language"] = "Sinhala"
                    await status_msg.edit_text("⏳ <b>වෙබ් අඩවිය යාවත්කාලීන කරමින් පවතී...</b>", parse_mode=ParseMode.HTML)
                    saved = await github_service.add_movie(target_movie)
                    watch_link = _clean_site_url(target_id)
                    kb_done = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=watch_link)]])
                    await status_msg.edit_text(
                        f"🎉 <b>සිංහල උපසිරැසි සාර්ථකව චිත්‍රපටයට එක් කරන ලදී!</b>\n\n"
                        f"🎬 <b>{target_movie.get('title')}</b>\n\n"
                        f"🌐 <b>Live Link:</b> <a href=\"{watch_link}\">{watch_link}</a>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb_done,
                        disable_web_page_preview=False,
                    )
                    return
            except Exception as exc:
                log.exception("[SubHandler] sub_action_callback error: %s", exc)
                await status_msg.edit_text(f"⚠️ දෝෂයක් සිදු විය: <code>{exc}</code>", parse_mode=ParseMode.HTML)
