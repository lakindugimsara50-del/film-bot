"""
add_imdb.py — /add <imdb_id> [S<n>E<n>] [subtitle_url] handler.

Stage 1: Instantly posts to website with VIP embed players.
Stage 2: Queues Telegram download+upload in background.

Examples:
    /add tt0944947              # Game of Thrones (full series)
    /add tt0944947 S01E02       # GoT Season 1 Episode 2
    /add tt0944947 S01E02 https://sub.url/si.srt  # With subtitle
    /add tt0111161              # The Shawshank Redemption (movie)
"""

import asyncio
import logging
import re
import os
import urllib.parse
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from services import tmdb_service, github_service, subtitle_service, task_tracker
from services.auth_service import auth_service
from handlers.announce import post_to_channel, _slugify

log = logging.getLogger(__name__)

# Active sessions waiting for subtitle: user_id -> dict
PENDING_IMDB_SESSIONS: dict[int, dict] = {}


def _is_admin(uid: int) -> bool:
    return uid in getattr(config, "ADMIN_IDS", [])


def register(app: Client) -> None:
    """Register /add (IMDb ID flow) and /queue, /addsession, and subtitle callbacks."""

    @app.on_message(filters.command("add") & filters.private)
    async def add_imdb_handler(client: Client, message: Message):
        if not _is_admin(message.from_user.id if message.from_user else 0):
            return
        cmd_text = message.text or ""
        args_text = re.sub(r'^/add\s*', '', cmd_text, flags=re.IGNORECASE).strip()
        args = _parse_add_args(args_text)
        imdb_id = args.get("imdb_id", "")
        if imdb_id:
            await _handle_add_imdb(client, message, args)
        else:
            message.continue_propagation()

    @app.on_message(filters.command("queue") & filters.private)
    async def queue_status_handler(client: Client, message: Message):
        if not _is_admin(message.from_user.id if message.from_user else 0):
            return
        await _show_queue(client, message)

    @app.on_message(filters.command("addsession") & filters.private)
    async def add_session_handler(client: Client, message: Message):
        if not _is_admin(message.from_user.id if message.from_user else 0):
            return
        await _handle_add_session(client, message)

    @app.on_callback_query(filters.regex(r"^asub:(add|skip|auto):(.+)$"))
    async def imdb_sub_callback_handler(client: Client, query: CallbackQuery):
        user_id = query.from_user.id if query.from_user else 0
        if not _is_admin(user_id):
            await query.answer("⛔ අවසර නැත", show_alert=True)
            return

        match = re.match(r"^asub:(add|skip|auto):(.+)$", query.data)
        if not match:
            return
        action = match.group(1)
        slug = match.group(2)

        sess = PENDING_IMDB_SESSIONS.get(user_id)
        if not sess or sess.get("slug") != slug:
            # Auto-recover session from movies.json so inline buttons never expire
            recovered = None
            try:
                data, _ = await github_service.get_movies_json()
                for m in data.get("movies", []):
                    if m.get("slug") == slug or m.get("id") == slug:
                        recovered = {
                            "user_id": user_id,
                            "imdb_id": m.get("imdb_id", ""),
                            "season": m.get("season"),
                            "episode": m.get("episode"),
                            "title": m.get("title", ""),
                            "year": m.get("year", ""),
                            "slug": slug,
                            "meta": m,
                            "site_url": m.get("site_url", f"https://filmsub.pages.dev/movie.html?id={slug}"),
                            "vtt_github_url": m.get("subtitle_url", ""),
                            "waiting_sub": False,
                        }
                        break
            except Exception as rec_err:
                log.warning("[AddImdb] Session auto-recovery from movies.json failed: %s", rec_err)

            if recovered:
                sess = recovered
                PENDING_IMDB_SESSIONS[user_id] = sess
                log.info("[AddImdb] Successfully auto-recovered session for '%s'", slug)
            else:
                await query.answer("⚠️ Session එක කල් ඉකුත් වී ඇත. නැවත /add කරන්න.", show_alert=True)
                return

        await query.answer()

        if action == "auto":
            status_edit = await query.message.reply_text("🔍 <b>PirateLK හරහා සිංහල උපසිරැසි සොයමින් පවතී...</b>", parse_mode=ParseMode.HTML)
            import tempfile
            with tempfile.TemporaryDirectory(prefix="auto_sub_") as tmpdir:
                clean_name = sess.get("title", "")
                srt_path = await subtitle_service.fetch_sri_lankan_sinhala_subtitle(
                    title=clean_name,
                    year=sess.get("year"),
                    season=sess.get("season"),
                    episode=sess.get("episode"),
                    temp_dir=tmpdir,
                )
                if srt_path and os.path.exists(srt_path):
                    vtt_path = subtitle_service.srt_to_vtt(srt_path)
                    ep_sfx = f"-s{sess.get('season'):02d}e{sess.get('episode'):02d}" if sess.get("season") and sess.get("episode") else ""
                    fname = f"{slug}{ep_sfx}-si.vtt"
                    vtt_url = ""
                    if getattr(config, "GITHUB_TOKEN", "") and getattr(config, "GITHUB_REPO", ""):
                        try:
                            vtt_url = await subtitle_service.upload_subtitle_to_github(vtt_path, fname)
                        except Exception:
                            pass
                    if not vtt_url:
                        with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
                            vtt_url = f"data:text/vtt;charset=utf-8,{urllib.parse.quote(f.read())}"

                    data, _ = await github_service.get_movies_json()
                    for m in data.get("movies", []):
                        if m.get("slug") == slug or m.get("id") == slug:
                            m["subtitle_url"] = vtt_url
                            m["subtitles"] = [{"language": "Sinhala", "label": "සිංහල උපසිරැසි", "url": vtt_url, "default": True}]
                            m["has_sinhala_sub"] = True
                            for stream in m.get("streams", []):
                                if "vidlink.pro" in stream.get("stream_url", ""):
                                    base_u = stream["stream_url"].split("?")[0]
                                    stream["stream_url"] = f"{base_u}?sub_file={urllib.parse.quote(vtt_url, safe='')}&sub_label=Sinhala&sub=true"
                            await github_service.add_movie(m)
                            break
                    sess["vtt_github_url"] = vtt_url
                    await status_edit.edit_text(
                        f"✅ <b>PirateLK සිංහල උපසිරැසි සාර්ථකව එක් කරන ලදී!</b>\n\n"
                        f"🎬 <b>{sess['title']}</b>\n"
                        f"🌐 Web Link: <a href='{sess['site_url']}'>{sess['site_url']}</a>",
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await status_edit.edit_text(
                        f"⚠️ <b>PirateLK හි උපසිරැසි හමු නොවීය.</b>\n"
                        f"කරුණාකර <code>[✍️ Manual Custom Subtitle Upload]</code> බොත්තම මඟින් .srt ගොනුව එවන්න.",
                        parse_mode=ParseMode.HTML,
                    )
            return

        if action == "skip":
            await query.message.edit_text(
                f"🚀 <b>Subtitle නොමැතිව Telegram Upload Queue එකට එක් කරමින්...</b>\n\n"
                f"🎬 <b>{sess['title']} ({sess['year']})</b>\n"
                f"🌐 Web Link: <a href='{sess['site_url']}'>{sess['site_url']}</a>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            # Spawn Stage 2 download in a fresh message
            await _start_stage2_download(client, query.message.chat.id, sess)
            PENDING_IMDB_SESSIONS.pop(user_id, None)

        elif action == "add":
            sess["waiting_sub"] = True
            PENDING_IMDB_SESSIONS[user_id] = sess
            await query.message.reply_text(
                f"📥 <b>සිංහල උපසිරැසි (.srt හෝ .vtt) ගොනුවක් බලාපොරොත්තු වේ...</b>\n\n"
                f"🎬 <b>{sess['title']} ({sess['year']})</b> සඳහා:\n"
                f"1. .srt හෝ .vtt Subtitle ගොනුවක් Bot වෙත Upload කරන්න.\n"
                f"2. නැතහොත් Subtitle Direct Download URL එකක් Chat එකට එවන්න.\n\n"
                f"💡 <i>(අවලංගු කිරීමට /cancel යවන්න)</i>",
                parse_mode=ParseMode.HTML,
            )

    @app.on_message(filters.private & (filters.document | filters.text) & ~filters.command(["add", "cancel", "sub", "queue", "addsession", "start", "help"]))
    async def imdb_sub_input_handler(client: Client, message: Message):
        user_id = message.from_user.id if message.from_user else 0
        sess = PENDING_IMDB_SESSIONS.get(user_id)

        # If user didn't explicitly click "Add Subtitle" button or session was cleared,
        # check if there's any queued movie in movies.json waiting for subtitle upload
        if not sess:
            try:
                data, _ = await github_service.get_movies_json()
                for m in data.get("movies", []):
                    if m.get("telegram_status") == "queued" or not m.get("has_sinhala_sub"):
                        sess = {
                            "user_id": user_id,
                            "imdb_id": m.get("imdb_id", ""),
                            "season": m.get("season"),
                            "episode": m.get("episode"),
                            "title": m.get("title", ""),
                            "year": m.get("year", ""),
                            "slug": m.get("slug", ""),
                            "meta": m,
                            "site_url": m.get("site_url", f"https://filmsub.pages.dev/movie.html?id={m.get('slug', '')}"),
                            "vtt_github_url": m.get("subtitle_url", ""),
                            "waiting_sub": True,
                        }
                        PENDING_IMDB_SESSIONS[user_id] = sess
                        log.info("[AddImdb] Auto-matched subtitle to queued movie: %s", m.get("title"))
                        break
            except Exception as auto_rec:
                log.warning("[AddImdb] Auto-matching queued movie note: %s", auto_rec)

        if not sess or not sess.get("waiting_sub"):
            message.continue_propagation()
            return

        # Handle subtitle document or link
        target_doc = message.document
        text_val = (message.text or "").strip()

        sub_url = ""
        is_sub_doc = False
        if target_doc:
            fname_lower = (target_doc.file_name or "").lower()
            if fname_lower.endswith((".srt", ".vtt", ".sub", ".txt")) or target_doc.file_size < 10 * 1024 * 1024:
                is_sub_doc = True
        elif text_val.startswith(("http://", "https://")):
            sub_url = text_val
        else:
            message.continue_propagation()
            return

        status_msg = await message.reply_text("⏳ <b>උපසිරැසි ගොනුව සකසමින් පවතී...</b>", parse_mode=ParseMode.HTML)

        import tempfile
        with tempfile.TemporaryDirectory(prefix="imdb_sub_") as tmpdir:
            local_srt = os.path.join(tmpdir, "sub.srt")
            local_vtt = os.path.join(tmpdir, "sub.vtt")

            try:
                if is_sub_doc:
                    downloaded = await client.download_media(message=target_doc.file_id, file_name=local_srt)
                    if downloaded and downloaded.endswith(".vtt"):
                        local_vtt = downloaded
                    else:
                        local_vtt = subtitle_service.srt_to_vtt(downloaded)
                elif sub_url:
                    saved_srt = await subtitle_service.download_subtitle(sub_url, local_srt)
                    local_vtt = subtitle_service.srt_to_vtt(saved_srt)

                # Validate genuine Sinhala characters
                if not subtitle_service.is_genuine_sinhala_subtitle(local_vtt) and not subtitle_service.is_genuine_sinhala_subtitle(local_srt):
                    await status_msg.edit_text(
                        "❌ <b>මෙම උපසිරැසි ගොනුව තුළ සිංහල උපසිරැසි (Sinhala Unicode) අඩංගු නොවේ!</b>\n\n"
                        "⚠️ කරුණාකර නියම සිංහල .srt හෝ .vtt ගොනුවක් එවන්න.",
                        parse_mode=ParseMode.HTML,
                    )
                    return

                # Upload to GitHub
                slug = sess["slug"]
                season = sess.get("season")
                episode = sess.get("episode")
                ep_sfx = f"-s{season:02d}e{episode:02d}" if season and episode else ""
                fname = f"{slug}{ep_sfx}-si.vtt"

                # Cache local subtitle in system temp so Stage 2 FFmpeg can mux it directly into video
                try:
                    import shutil
                    cached_srt = os.path.join(tempfile.gettempdir(), f"sub_{slug}.srt")
                    if os.path.exists(local_srt) and os.path.getsize(local_srt) > 0:
                        shutil.copyfile(local_srt, cached_srt)
                    elif os.path.exists(local_vtt) and os.path.getsize(local_vtt) > 0:
                        shutil.copyfile(local_vtt, os.path.join(tempfile.gettempdir(), f"sub_{slug}.vtt"))
                except Exception as c_err:
                    log.warning("[AddImdb] Failed to cache subtitle for Stage 2: %s", c_err)

                vtt_github_url = await subtitle_service.upload_subtitle_to_github(
                    vtt_path=local_vtt,
                    filename=fname,
                    github_token=getattr(config, "GITHUB_TOKEN", ""),
                    repo=getattr(config, "GITHUB_REPO", ""),
                )
                sess["vtt_github_url"] = vtt_github_url

                # Update live website VidLink embed player with ?sub.Sinhala=
                if vtt_github_url:
                    data, sha = await github_service.get_movies_json()
                    for m in data.get("movies", []):
                        if m.get("slug") == slug or m.get("id") == slug:
                            m["subtitle_url"] = vtt_github_url
                            m["subtitles"] = [{
                                "language": "Sinhala",
                                "label": "සිංහල උපසිරැසි",
                                "url": vtt_github_url,
                                "default": True,
                            }]
                            m["has_sinhala_sub"] = True
                            for stream in m.get("streams", []):
                                if "vidlink.pro" in stream.get("stream_url", ""):
                                    base_url = stream["stream_url"].split("?")[0]
                                    encoded = urllib.parse.quote(vtt_github_url, safe='')
                                    stream["stream_url"] = f"{base_url}?sub_file={encoded}&sub_label=Sinhala&sub=true"
                            break
                    await github_service._commit_movies_json(
                        data, sha, f"feat(sub): attach Sinhala subtitle to {slug}"
                    )

                await status_msg.edit_text(
                    f"✅ <b>සිංහල උපසිරැසි සාර්ථකව Live Web Player එකට එක් කරන ලදී!</b>\n\n"
                    f"🎬 <b>{sess['title']} ({sess['year']})</b>\n"
                    f"🌐 Web Link: <a href='{sess['site_url']}'>{sess['site_url']}</a>\n\n"
                    f"⚡ <i>දැන් Telegram Download (Hard-Sub Merged) ගොනුව සකස් කිරීම ආරම්භ වේ...</i>",
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )

                # Now spawn Stage 2 download with subtitle
                await _start_stage2_download(client, message.chat.id, sess)
                PENDING_IMDB_SESSIONS.pop(user_id, None)

            except Exception as exc:
                log.exception("[AddImdb] Error attaching subtitle: %s", exc)
                await status_msg.edit_text(f"❌ <b>Subtitle Error:</b> {str(exc)[:200]}", parse_mode=ParseMode.HTML)


def _parse_add_args(text: str) -> dict:
    """
    Parse /add command arguments.
    Supports:
        /add tt0944947
        /add tt0944947 S01E02
        /add tt0944947 S01E02 https://...
    """
    parts = text.strip().split()
    if not parts:
        return {}

    result = {"imdb_id": "", "season": None, "episode": None, "subtitle_url": ""}

    # Find IMDb ID (tt followed by digits)
    for i, p in enumerate(parts):
        if re.match(r'^tt\d{5,10}$', p, re.IGNORECASE):
            result["imdb_id"] = p
            remaining = parts[i+1:]
            break
    else:
        return result

    # Find season/episode in remaining parts
    for p in remaining:
        se = re.match(r'^[Ss](\d{1,2})[Ee](\d{1,2})$', p)
        if se:
            result["season"] = int(se.group(1))
            result["episode"] = int(se.group(2))
            continue
        if p.startswith("http"):
            result["subtitle_url"] = p

    return result


async def _handle_add_imdb(client: Client, message: Message, args: dict) -> None:
    """Main handler for /add tt<id> [S01E02] [sub_url]"""
    imdb_id = args.get("imdb_id", "")
    season = args.get("season")
    episode = args.get("episode")
    subtitle_url = args.get("subtitle_url", "")

    status_msg = await message.reply(
        f"⏳ <b>Stage 1 ආරම්භ වෙමින්...</b>\n"
        f"🔍 TMDB හි <code>{imdb_id}</code> සොයමින්...",
        parse_mode=ParseMode.HTML
    )

    try:
        # ── Stage 1A: Fetch TMDB metadata ──────────────────────────────────────
        await status_msg.edit_text(
            f"⏳ <b>Step 1/2</b> — TMDB metadata ලබාගනිමින්...\n"
            f"🔍 IMDb ID: <code>{imdb_id}</code>",
            parse_mode=ParseMode.HTML
        )

        meta = await tmdb_service.fetch_by_imdb_id(imdb_id, season=season, episode=episode)
        title = meta.get("title", imdb_id)
        year = meta.get("year", "")
        slug = meta.get("slug", _slugify(f"{title} {year}"))

        # ── Stage 1B: Handle subtitle if provided inline in command ────────────
        vtt_github_url = ""
        if subtitle_url:
            await status_msg.edit_text(
                f"⏳ Subtitle ලබාගනිමින්...\n📥 {subtitle_url[:60]}...",
                parse_mode=ParseMode.HTML
            )
            try:
                import tempfile
                with tempfile.TemporaryDirectory() as tmpdir:
                    srt_path = os.path.join(tmpdir, "sub.srt")
                    local_path = await subtitle_service.download_subtitle(subtitle_url, srt_path)
                    if local_path and subtitle_service.is_genuine_sinhala_subtitle(local_path):
                        vtt_path = subtitle_service.srt_to_vtt(local_path)
                        ep_sfx = f"-s{season:02d}e{episode:02d}" if season and episode else ""
                        fname = f"{slug}{ep_sfx}-si.vtt"
                        vtt_github_url = await subtitle_service.upload_subtitle_to_github(
                            vtt_path=vtt_path,
                            filename=fname,
                            github_token=getattr(config, "GITHUB_TOKEN", ""),
                            repo=getattr(config, "GITHUB_REPO", ""),
                        )
            except Exception as sub_err:
                log.warning("[AddImdb] Inline subtitle error (continuing): %s", sub_err)

        if vtt_github_url:
            meta["subtitle_url"] = vtt_github_url
            for stream in meta.get("streams", []):
                if "vidlink.pro" in stream.get("stream_url", ""):
                    encoded = urllib.parse.quote(vtt_github_url, safe='')
                    stream["stream_url"] += f"?sub_file={encoded}&sub_label=Sinhala&sub=true"

        site_url = f"{(getattr(config, 'SITE_BASE_URL', 'https://filmsub.pages.dev')).rstrip('/')}/movie.html?id={slug}"
        meta["site_url"] = site_url
        ep_info = f"- S{season:02d}E{episode:02d}" if season and episode else ""

        # ── Immediately announce on Telegram channel with VIP "Watch Online" link ──
        channel_post_id = None
        target_ch = getattr(config, "PUBLIC_CHANNEL_ID", 0) or getattr(config, "PRIVATE_CHANNEL_ID", 0)
        if target_ch and client:
            try:
                meta_ann = dict(meta)
                meta_ann["site_url"] = site_url
                meta_ann["downloads"] = []
                meta_ann["telegram_status"] = "queued"
                ch_msg = await post_to_channel(client, meta_ann, target_ch)
                if ch_msg:
                    channel_post_id = getattr(ch_msg, "id", None)
                    meta["channel_post_id"] = channel_post_id
                    meta["channel_chat_id"] = target_ch
                    log.info("[AddImdb] Immediate channel announcement posted to chat %s: msg_id=%s", target_ch, channel_post_id)
            except Exception as ann_err:
                log.warning("[AddImdb] Immediate channel announcement failed: %s", ann_err)

        # ── Stage 1C: Commit to GitHub (Site Live!) ────────────────────────────
        await status_msg.edit_text(
            f"⏳ <b>Step 2/2</b> — Site post publish කරමින්...\n"
            f"🎬 <b>{title}</b> ({year}) GitHub commit...",
            parse_mode=ParseMode.HTML
        )

        ok = await github_service.add_movie(meta)
        if not ok:
            await status_msg.edit_text(
                f"❌ <b>GitHub commit අසාර්ථකයි!</b>\nනැවත උත්සාහ කරන්න.",
                parse_mode=ParseMode.HTML
            )
            return

        # ── Stage 1D: If subtitle was already given, start Stage 2 directly ───
        sess_data = {
            "user_id": message.from_user.id,
            "imdb_id": imdb_id,
            "season": season,
            "episode": episode,
            "title": title,
            "year": year,
            "slug": slug,
            "meta": meta,
            "site_url": site_url,
            "vtt_github_url": vtt_github_url,
            "waiting_sub": False,
        }

        if vtt_github_url:
            # Subtitle is already attached! Permanent Stage 1 success message
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Live)", url=site_url)],
            ])
            await status_msg.edit_text(
                f"🎉 <b>Stage 1 සාර්ථකයි! චිත්‍රපටය Web එකට Live කරන ලදී!</b>\n\n"
                f"🎬 <b>{title} ({year}) {ep_info}</b>\n"
                f"📝 <b>උපසිරැසි:</b> සිංහල (Sinhala Subtitle Attached)\n"
                f"🌐 <b>Web Link:</b> <a href='{site_url}'>{site_url}</a>\n\n"
                f"⚡ <i>VIP Players (VidLink / AutoEmbed / MultiEmbed) හරහා දැන්ම නරඹන්න!</i>\n"
                f"⏳ <b>Stage 2:</b> Telegram Download ගොනුව Upload කිරීම ආරම්භ වේ...",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=False,
            )
            # Spawn Stage 2 in a fresh message so Stage 1 message is never deleted!
            await _start_stage2_download(client, message.chat.id, sess_data)
            return

        # ── Fully Automated Stage 2 with Sri Lankan (PirateLK) Auto-Subtitle ───
        PENDING_IMDB_SESSIONS[message.from_user.id] = sess_data

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Live)", url=site_url)],
            [
                InlineKeyboardButton("⚡ Auto PirateLK Subtitle", callback_data=f"asub:auto:{slug}"),
                InlineKeyboardButton("✍️ Manual Subtitle Upload", callback_data=f"asub:add:{slug}"),
            ],
        ])

        await status_msg.edit_text(
            f"🎉 <b>Stage 1 සාර්ථකයි! චිත්‍රපටය Web එකට Live කරන ලදී!</b>\n\n"
            f"🎬 <b>{title} ({year}) {ep_info}</b>\n"
            f"🌐 <b>Web Link:</b> <a href='{site_url}'>{site_url}</a>\n"
            f"⚡ <i>VIP Embed Players (VidLink / AutoEmbed / MultiEmbed) මඟින් දැන්ම නරඹන්න!</i>\n\n"
            f"🇱🇰 <b>PirateLK ස්වයංක්‍රීය සිංහල උපසිරැසි සහ Telegram Upload ක්‍රියාවලිය ආරම්භ විය...</b>\n"
            f"<i>(ඔබ සතුව වෙනම Subtitle එකක් ඇත්නම් පහත බොත්තම ඔබා එවන්න, නැතහොත් Auto-Pilot ක්‍රියාත්මක වේ)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=False,
        )

        # Immediately launch Stage 2 in background without waiting for user action!
        await _start_stage2_download(client, message.chat.id, sess_data)

    except RuntimeError as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> {str(e)}", parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("[AddImdb] Unexpected error for %s", imdb_id)
        await status_msg.edit_text(f"❌ <b>Unexpected Error:</b> {str(e)[:300]}", parse_mode=ParseMode.HTML)


async def _start_stage2_download(client: Client, chat_id: int, sess: dict) -> None:
    """Spawn Stage 2 Telegram download and upload in a NEW message."""
    title = sess["title"]
    season = sess.get("season")
    episode = sess.get("episode")
    slug = sess["slug"]
    imdb_id = sess["imdb_id"]
    user_id = sess["user_id"]

    ep_tag = f"S{season:02d}E{episode:02d}" if season and episode else ""
    query = f"{title} {ep_tag}".strip()

    # Send a dedicated NEW status message for Stage 2 progress
    dl_status_msg = await client.send_message(
        chat_id=chat_id,
        text=f"⏳ <b>Stage 2 ආරම්භ වෙමින් පවතී...</b>\n🔍 <code>{query}</code> සඳහා Torrent/Direct Link සොයමින්...",
        parse_mode=ParseMode.HTML,
    )

    from services.queue_service import queue_service
    pos = await queue_service.add_to_queue(
        client=client,
        status_msg=dl_status_msg,  # Dedicated message! Stage 1 message remains intact!
        user_id=user_id,
        query_text=query,
        title_hint=title,
        auto_publish=False,
        movie_slug=slug,
        imdb_id=imdb_id,
    )

    if pos > 1:
        await client.send_message(
            chat_id=chat_id,
            text=f"📋 <b>Queue Position: #{pos}</b>\n⏳ පෙර Task අවසන් වූ පසු මෙය ස්වයංක්‍රීයව ආරම්භ වේ.",
            parse_mode=ParseMode.HTML,
        )


async def _show_queue(client: Client, message: Message) -> None:
    """Show Stage 2 upload queue status."""
    from services.queue_service import queue_service
    items = queue_service.get_queue_status()
    if not items:
        await message.reply(
            "✅ <b>Queue හිස් ය (Empty)!</b>\nඅලුත් film එකක් add කිරීමට: <code>/add tt&lt;id&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    lines = ["📋 <b>Upload Queue Status:</b>\n"]
    for i, item in enumerate(items, 1):
        status = item.get("status", "")
        title = item.get("title", "Unknown")
        icon = "🔄" if "Active" in status or "ක්‍රියාත්මක" in status else "⏳"
        lines.append(f"{icon} <b>#{i}</b> — {title}\n   <i>{status}</i>")

    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)


async def _handle_add_session(client: Client, message: Message) -> None:
    """Save a Pyrogram session string to bot/sessions/ and hot-reload."""
    parts = (message.text or "").split(None, 2)
    if len(parts) < 2:
        await message.reply(
            "❌ <b>Session string missing!</b>\n\n"
            "💡 <code>/addsession &lt;pyrogram_session_string&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    session_string = parts[1].strip()
    sessions_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sessions")
    os.makedirs(sessions_dir, exist_ok=True)

    existing = [f for f in os.listdir(sessions_dir) if f.endswith(".session")]
    next_num = len(existing) + 1
    session_name = f"session_{next_num:03d}"

    try:
        test_client = Client(
            name=os.path.join(sessions_dir, session_name),
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=session_string,
            no_updates=True,
        )
        await test_client.start()
        me = await test_client.get_me()
        await test_client.stop()

        from streaming.session_pool import stream_pool
        await stream_pool.init_extra_sessions(
            config.API_ID, config.API_HASH, sessions_dir
        )

        await message.reply(
            f"✅ <b>Session Added!</b>\n"
            f"👤 <b>Account:</b> {me.first_name} (@{me.username})\n"
            f"📋 <b>Session ID:</b> {session_name}\n"
            f"📊 <b>Total Sessions:</b> {next_num}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.reply(
            f"❌ <b>Session invalid or expired!</b>\n{str(e)[:200]}",
            parse_mode=ParseMode.HTML
        )
