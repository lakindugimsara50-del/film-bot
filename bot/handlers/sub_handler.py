"""
sub_handler.py — Command handler for adding or updating Sinhala subtitles for movies.

Enables adding subtitles at any time (even days after a movie is downloaded and published).
Supports:
1. /sub <movie_name_or_slug> <subtitle_url>
2. Replying to an .srt / .vtt file with: /sub <movie_name_or_slug>
3. Replying to an .srt / .vtt file with just: /sub (attaches to the most recently added movie!)
"""

import logging
import os
import tempfile
import urllib.parse
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
from services import github_service, subtitle_service
from services.auth_service import auth_service

log = logging.getLogger(__name__)


def register(app: Client) -> None:
    """Register /sub and /addsub command handlers."""

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
            # User replied with just /sub -> target the latest movie in database!
            query_name = "LATEST"
        else:
            await message.reply_text(
                "📝 <b>චිත්‍රපටයකට සිංහල උපසිරැසි (Sinhala Subtitle) එක් කිරීම:</b>\n\n"
                "<b>ක්‍රම 3ක් ඔස්සේ භාවිතා කළ හැක:</b>\n"
                "1️⃣ <b>Reply ක්‍රමය (වඩාත් පහසුම):</b>\n"
                "   .srt හෝ .vtt file එකක් Bot වෙත එවා, එයට Reply කර:\n"
                "   <code>/sub &lt;චිත්‍රපටයේ නම&gt;</code> ලෙස යවන්න.\n"
                "   <i>(නම නොදමා Reply කර <code>/sub</code> පමණක් යැවුවහොත් අන්තිමට එක්කළ චිත්‍රපටයට ස්වයංක්‍රීයව එක්වේ)</i>\n\n"
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

                # 2. Fetch movies.json
                data, sha = await github_service.get_movies_json()
                movies = data.get("movies", [])
                if not movies:
                    await status_msg.edit_text("❌ movies.json හි කිසිදු චිත්‍රපටයක් හමු නොවීය.")
                    return

                # 3. Find matching movie
                target_movie = None
                if query_name.upper() == "LATEST":
                    target_movie = movies[-1]
                else:
                    q_clean = query_name.lower().replace("-", " ")
                    for m in reversed(movies):
                        m_title = (m.get("title") or "").lower()
                        m_slug = (m.get("slug") or "").lower()
                        if q_clean in m_title or q_clean in m_slug:
                            target_movie = m
                            break

                if not target_movie:
                    await status_msg.edit_text(
                        f"❌ <b>'{query_name}'</b> නමින් චිත්‍රපටයක් වෙබ් අඩවියේ හමු නොවීය.\n"
                        f"කරුණාකර නම හෝ slug එක නිවැරදිව ලබාදෙන්න.",
                        parse_mode=ParseMode.HTML,
                    )
                    return

                movie_title = target_movie.get("title", "Movie")
                movie_slug = target_movie.get("slug", "movie")

                # 4. Upload subtitle or embed as data URI
                # Direct Data URI guarantees 100% instant universal load in Video.js without CORS or server dependencies
                vtt_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(vtt_text)}"

                # Also upload to repo if token configured
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

                # 5. Update movie subtitles
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

                # Commit update
                saved = await github_service.add_movie(target_movie)

                site_base = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
                watch_link = f"{site_base}/movie.html?id={movie_slug}"

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

            except Exception as exc:
                log.exception("[SubHandler] Subtitle upload error: %s", exc)
                await status_msg.edit_text(f"⚠️ උපසිරැසි එක්කිරීමේදී දෝෂයක් සිදු විය: <code>{exc}</code>", parse_mode=ParseMode.HTML)
