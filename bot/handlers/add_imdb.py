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
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
from services import tmdb_service, github_service, subtitle_service, task_tracker
from services.auth_service import auth_service
from handlers.announce import post_to_channel, _slugify

log = logging.getLogger(__name__)


def _is_admin(uid: int) -> bool:
    return uid in getattr(config, "ADMIN_IDS", [])


def register(app: Client) -> None:
    """Register /add (IMDb ID flow) and /queue and /addsession handlers."""

    @app.on_message(filters.command("add") & filters.private)
    async def add_imdb_handler(client: Client, message: Message):
        if not _is_admin(message.from_user.id if message.from_user else 0):
            return
        # If it doesn't have an IMDb ID, we should skip and let add_movie handle it.
        # However, pyrogram doesn't have easy "fallthrough".
        # Let's check for 'tt' and handle, otherwise we can call add_movie's handler directly.
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


def _parse_add_args(text: str) -> dict:
    """
    Parse /add command arguments.
    Supports:
        /add tt0944947
        /add tt0944947 S01E02
        /add tt0944947 S01E02 https://...
        /add tt0944947 S01E02 s01 e02  (loose format)
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
        return result  # No IMDb ID found
    
    # Find season/episode in remaining parts
    for p in remaining:
        se = re.match(r'^[Ss](\d{1,2})[Ee](\d{1,2})$', p)
        if se:
            result["season"] = int(se.group(1))
            result["episode"] = int(se.group(2))
            continue
        # Subtitle URL
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
            f"⏳ <b>Step 1/3</b> — TMDB metadata ලබාගනිමින්...\n"
            f"🔍 IMDb ID: <code>{imdb_id}</code>",
            parse_mode=ParseMode.HTML
        )
        
        meta = await tmdb_service.fetch_by_imdb_id(imdb_id, season=season, episode=episode)
        title = meta.get("title", imdb_id)
        year = meta.get("year", "")
        media_type = meta.get("type", "movie")
        slug = meta.get("slug", _slugify(f"{title} {year}"))
        
        # ── Stage 1B: Handle subtitle if provided ──────────────────────────────
        vtt_github_url = ""
        if subtitle_url:
            await status_msg.edit_text(
                f"⏳ <b>Step 1/3</b> — Subtitle ලබාගනිමින়...\n📥 {subtitle_url[:60]}...",
                parse_mode=ParseMode.HTML
            )
            try:
                import tempfile
                with tempfile.TemporaryDirectory() as tmpdir:
                    srt_path = os.path.join(tmpdir, "sub.srt")
                    local_path = await subtitle_service.download_subtitle(subtitle_url, srt_path)
                    if local_path and subtitle_service.is_genuine_sinhala_subtitle(local_path):
                        vtt_path = await subtitle_service.srt_to_vtt(local_path)
                        ep_sfx = f"-s{season:02d}e{episode:02d}" if season else ""
                        fname = f"{slug}{ep_sfx}-si.vtt"
                        vtt_github_url = await subtitle_service.upload_subtitle_to_github(vtt_path, fname)
            except Exception as sub_err:
                log.warning("[AddImdb] Subtitle error (continuing): %s", sub_err)
        
        # Inject subtitle into VidLink URL if available
        if vtt_github_url:
            meta["subtitle_url"] = vtt_github_url
            for stream in meta.get("streams", []):
                if "vidlink.pro" in stream.get("stream_url", ""):
                    import urllib.parse
                    encoded = urllib.parse.quote(vtt_github_url, safe='')
                    stream["stream_url"] += f"?sub.Sinhala={encoded}"
        
        # ── Stage 1C: Commit to GitHub ─────────────────────────────────────────
        await status_msg.edit_text(
            f"⏳ <b>Step 2/3</b> — Site post publish කරමින়...\n"
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
        
        # ── Stage 1D: Announce to public channel ───────────────────────────────
        site_url = f"{(getattr(config, 'SITE_BASE_URL', 'https://filmsub.pages.dev')).rstrip('/')}/movie.html?id={slug}"
        
        await status_msg.edit_text(
            f"✅ <b>Stage 1 සම්පූර්ණයි! Site post LIVE!</b>\n\n"
            f"🎬 <b>{title}</b> ({year})\n"
            f"🌐 <a href='{site_url}'>Site ලිංකය</a>\n\n"
            f"⏳ <b>Stage 2:</b> Telegram upload queue එකට ඇතුළු විය...\n"
            f"📋 Queue status: <code>/queue</code>",
            parse_mode=ParseMode.HTML
        )
        
        # ── Stage 2: Enqueue background Telegram upload ────────────────────────
        from services.queue_service import queue_service
        pos = await queue_service.add_to_queue(
            client=client,
            status_msg=status_msg,
            user_id=message.from_user.id,
            query_text=f"{title} {f'S{season:02d}E{episode:02d}' if season else ''}".strip(),
            title_hint=title,
            auto_publish=False,
            movie_slug=slug,    # NEW: pass slug so Stage2 patcher knows what to update
            imdb_id=imdb_id,
        )
        
        if pos > 1:
            await message.reply(
                f"📋 <b>Queue Position: #{pos}</b>\n"
                f"⏳ {pos-1} upload(s) complete venakm randeemata siduwe.",
                parse_mode=ParseMode.HTML
            )
    
    except RuntimeError as e:
        await status_msg.edit_text(
            f"❌ <b>Error:</b> {str(e)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.exception("[AddImdb] Unexpected error for %s", imdb_id)
        await status_msg.edit_text(
            f"❌ <b>Unexpected Error:</b> {str(e)[:300]}",
            parse_mode=ParseMode.HTML
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
    
    # Find next available slot
    existing = [f for f in os.listdir(sessions_dir) if f.endswith(".session")]
    next_num = len(existing) + 1
    session_name = f"session_{next_num:03d}"
    
    # Save session using Pyrogram (it will create the .session file)
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
        
        # Hot-reload into pool
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
