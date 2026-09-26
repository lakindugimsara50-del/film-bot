"""
main.py — Entry point for the Telegram bot.

Initialises a Pyrogram bot Client, registers all command handlers, and
starts polling with app.run().  Logging is configured here so every module
that calls logging.getLogger(__name__) gets consistent output.
"""

import asyncio
import logging
import os
import sys
import threading
from typing import Optional

_bot_dir = os.path.dirname(os.path.abspath(__file__))
if _bot_dir not in sys.path:
    sys.path.insert(0, _bot_dir)

import pyrogram.utils
pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999
pyrogram.utils.MIN_CHAT_ID = -999999999999

from pyrogram import Client, filters, idle
from pyrogram.enums import ParseMode
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from fastapi import FastAPI
import uvicorn

import config

# Ensure Windows console streams support UTF-8 encoding (Sinhala text)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Logging setup ─────────────────────────────────────────────────────────────
_log_file = os.path.join(os.path.dirname(_bot_dir), "bot.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_file, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Validate required config ──────────────────────────────────────────────────
_missing = [
    name
    for name, val in [
        ("TG_API_ID", config.API_ID),
        ("TG_API_HASH", config.API_HASH),
        ("BOT_TOKEN", config.BOT_TOKEN),
    ]
    if not val
]
if _missing:
    log.critical(
        "Missing required environment variable(s): %s  —  Cannot start bot.",
        ", ".join(_missing),
    )
    sys.exit(1)

# Ensure an active event loop exists for Pyrogram Dispatcher in Python 3.11+
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import multiprocessing as _mp
_on_colab_env = os.path.exists('/content')
# On Colab (12GB RAM): 16 Pyrogram workers = parallel MTProto upload sessions → 2-4x faster uploads
# On Render (512MB RAM): keep 4 workers to avoid OOM
_PYROGRAM_WORKERS = 16 if _on_colab_env else 4

app = Client(
    name="film_bot",
    in_memory=True,
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=_PYROGRAM_WORKERS,
)



# ── Health & Status Web Server ────────────────────────────────────────────────
web_app = FastAPI(title="Film Movie Bot Health & Stream Server")

from fastapi.middleware.cors import CORSMiddleware
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)

from streaming.stream_server import stream_router
web_app.include_router(stream_router)


@web_app.get("/")
async def root() -> dict:
    return {
        "status": "running",
        "bot": "@Filmsinhala200Bot",
        "service": "Telegram Movie Leech & Stream Bot",
    }


@web_app.get("/health")
async def health() -> dict:
    return {"status": "healthy"}


@web_app.get("/status")
async def status_endpoint() -> dict:
    seedr_count = 0
    try:
        from services.seedr_service import seedr_pool
        seedr_count = len(seedr_pool.services) if hasattr(seedr_pool, "services") else 0
    except Exception as exc:
        log.warning("Could not fetch seedr pool count: %s", exc)

    bot_connected = getattr(app, "is_connected", False)
    active_tasks = 0
    try:
        from services.task_tracker import tracker
        active_tasks = len(getattr(tracker, "_user_tasks", {}))
    except Exception:
        pass

    return {
        "status": "running" if bot_connected else "stopped",
        "bot_status": "online" if bot_connected else "offline",
        "bot": "@Filmsinhala200Bot",
        "seedr_pool_accounts": seedr_count,
        "active_tasks": active_tasks,
    }


_health_server_instance: Optional[uvicorn.Server] = None


def start_health_server_thread(port: int) -> threading.Thread:
    """
    Run FastAPI / Uvicorn in a dedicated daemon OS thread with its own independent
    asyncio event loop. Render's HTTP health checks (/health and /) respond in <1ms
    and are NEVER blocked or starved by Pyrogram, event loop contention, or MTProto encryption.
    """
    global _health_server_instance
    server_cfg = uvicorn.Config(
        app=web_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(server_cfg)
    _health_server_instance = server

    def _thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        except Exception as exc:
            log.error("[HealthServer] Web health server error: %s", exc)
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
            except Exception:
                pass

    t = threading.Thread(target=_thread_target, name="HealthServerThread", daemon=True)
    t.start()
    log.info("FastAPI health server started on port %d (dedicated daemon thread)", port)
    return t



# ─────────────────────────────────────────────────────────────────────────────
# Built-in command handlers
# ─────────────────────────────────────────────────────────────────────────────

import re
from services import draft_service, github_service


async def _find_movie_by_slug_or_title(query: str) -> Optional[dict]:
    """Look up movie in movies.json or drafts by slug or title."""
    if not query:
        return None
    q = query.strip().lower()
    clean_q = re.sub(r"[\W_]+", "", q)
    try:
        data, _ = await github_service.get_movies_json()
        movies = data.get("movies", [])
    except Exception:
        movies = []

    # 1. Exact or normalized match in movies.json
    for m in movies:
        m_slug = (m.get("slug") or m.get("id") or "").lower()
        m_title = (m.get("title") or "").lower()
        clean_slug = re.sub(r"[\W_]+", "", m_slug)
        clean_title = re.sub(r"[\W_]+", "", m_title)
        if q == m_slug or q == m_title or clean_q == clean_slug or clean_q == clean_title or (len(clean_q) >= 3 and (clean_q in clean_slug or clean_slug in clean_q or clean_q in clean_title)):
            return m

    # 2. Check saved drafts
    try:
        drafts = draft_service.list_drafts()
        for d in drafts:
            m_entry = d.get("movie_entry") or {}
            d_slug = (m_entry.get("slug") or d.get("id") or "").lower()
            d_title = (d.get("title_hint") or d.get("movie_name") or "").lower()
            clean_dslug = re.sub(r"[\W_]+", "", d_slug)
            clean_dtitle = re.sub(r"[\W_]+", "", d_title)
            if q == d_slug or q == d_title or clean_q == clean_dslug or clean_q == clean_dtitle:
                res = dict(m_entry) if m_entry else dict(d)
                res["channel_id"] = d.get("channel_id")
                return res
    except Exception:
        pass
    return None


async def deliver_movie_quality(client: Client, chat_id: int, slug: str, quality: Optional[str] = None) -> None:
    """Send requested movie quality directly to user via Telegram copy_message or send_video."""
    movie = await _find_movie_by_slug_or_title(slug)
    if not movie:
        await client.send_message(
            chat_id,
            f"❌ <b>'{slug}'</b> චිත්‍රපටය සොයාගත නොහැකි විය.\n\n🌐 වෙබ් අඩවිය: https://filmsub.pages.dev",
            parse_mode=ParseMode.HTML,
        )
        return

    title = movie.get("title") or "Movie"
    year = movie.get("year") or ""
    display_title = f"{title} ({year})" if year else title
    vm = movie.get("variant_media") or {}
    ch_id = movie.get("channel_id") or config.PRIVATE_CHANNEL_ID

    valid_qualities = ["1080p", "720p", "480p", "360p"]
    norm_q = (quality or "").lower().strip()
    if norm_q not in [q.lower() for q in valid_qualities]:
        # Quality not specified: present inline keyboard with resolution buttons
        buttons = []
        for q_tag in valid_qualities:
            q_info = vm.get(q_tag) or {}
            sz = ""
            if q_info.get("size_bytes"):
                from services.downloader import format_bytes
                sz = f" ({format_bytes(q_info['size_bytes'])})"
            buttons.append([InlineKeyboardButton(f"📥 {q_tag} HD Download{sz}", callback_data=f"dl_q:{movie.get('slug', slug)}:{q_tag}")])
        buttons.append([InlineKeyboardButton("🌐 Web එකෙන් බලන්න (Watch Online)", url=f"https://filmsub.pages.dev/movie.html?id={movie.get('slug', slug)}")])
        kb = InlineKeyboardMarkup(buttons)
        await client.send_message(
            chat_id,
            f"🎬 <b>{display_title}</b>\n\n⚡ බාගත කිරීමට අවශ්‍ය Video Quality එක තෝරන්න (Select Download Quality):",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    target_q = next((q for q in valid_qualities if q.lower() == norm_q), "1080p")
    target_info = vm.get(target_q) or {}
    target_msg_id = target_info.get("message_id")
    target_file_id = target_info.get("file_id")

    # Fallback to main file if specific resolution is not separate
    if not target_msg_id and not target_file_id:
        target_msg_id = movie.get("message_id")
        target_file_id = movie.get("file_id")
        target_q = movie.get("quality", "1080p")

    # Resilient channel ID and message ID resolution from stream_url or downloads
    if not ch_id or not target_msg_id:
        url_candidates = [
            movie.get("stream_url", ""),
            *(d.get("stream_url", "") for d in movie.get("downloads", []) if isinstance(d, dict)),
            *(d.get("url", "") for d in movie.get("downloads", []) if isinstance(d, dict)),
            *(s.get("stream_url", "") for s in movie.get("streams", []) if isinstance(s, dict)),
        ]
        for u in url_candidates:
            if not u:
                continue
            m_ch = re.search(r"/stream/channel/(-?\d+)/(\d+)", u)
            if m_ch:
                if not ch_id:
                    ch_id = int(m_ch.group(1))
                if not target_msg_id:
                    target_msg_id = int(m_ch.group(2))
                break
            m_tg = re.search(r"t\.me/c/(\d+)/(\d+)", u)
            if m_tg:
                if not ch_id:
                    ch_id = int(f"-100{m_tg.group(1)}")
                if not target_msg_id:
                    target_msg_id = int(m_tg.group(2))
                break

    caption = (
        f"🎬 <b>{display_title} [{target_q}]</b>\n\n"
        f"⚡ <b>Quality:</b> {target_q} (High-Speed Telegram Cloud)\n"
        f"💬 <b>සිංහල උපසිරැසි:</b> Video එකටම Soft-Mux කර ඇත\n"
        f"🌐 <b>Watch Online:</b> https://filmsub.pages.dev/movie.html?id={movie.get('slug', slug)}"
    )

    if target_msg_id and ch_id:
        try:
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=ch_id,
                message_id=target_msg_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as copy_err:
            log.warning("[MainBot] copy_message error (%s). Trying send_video...", copy_err)

    if target_file_id:
        try:
            await client.send_video(
                chat_id=chat_id,
                video=target_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as vid_err:
            log.error("[MainBot] send_video error: %s", vid_err)

    s_url = target_info.get("stream_url") or movie.get("stream_url")
    if s_url:
        await client.send_message(
            chat_id,
            f"🎬 <b>{display_title} [{target_q}]</b>\n\n"
            f"⚡ <b>Direct Stream/Download URL:</b>\n{s_url}\n\n"
            f"🌐 <b>Website:</b> https://filmsub.pages.dev/movie.html?id={movie.get('slug', slug)}",
            parse_mode=ParseMode.HTML,
        )
        return

    await client.send_message(chat_id, f"⚠️ {display_title} සඳහා Telegram video file එක ලබාගත නොහැකි විය.", parse_mode=ParseMode.HTML)


@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message) -> None:
    """Greet new users or route /start dl_<slug>_<quality> deep links."""
    command_parts = message.command
    if len(command_parts) > 1 and command_parts[1].startswith("dl_"):
        param = command_parts[1][3:].strip()
        req_q = None
        for q_cand in ["1080p", "720p", "480p", "360p"]:
            if param.lower().endswith(f"_{q_cand.lower()}"):
                req_q = q_cand
                param = param[:-len(f"_{q_cand.lower()}")]
                break
        await deliver_movie_quality(client, message.chat.id, param, req_q)
        return

    user_name = message.from_user.first_name if message.from_user else "යාලුවා"
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Movies Leech කරන්න (/leech)", switch_inline_query_current_chat="/leech "),
            InlineKeyboardButton("📂 Drafts බලන්න", callback_data="drafts:list"),
        ],
        [
            InlineKeyboardButton("☁️ Drive Quota (/drives)", callback_data="btn:drives"),
            InlineKeyboardButton("📖 සියලු විධාන (Help)", callback_data="btn:help"),
        ],
        [
            InlineKeyboardButton("🌐 Film Website එකට යන්න", url="https://filmsub.pages.dev"),
        ]
    ])
    await message.reply_text(
        f"👋 <b>ආයුබෝවන් {user_name}! FilmSub Official Bot වෙත සාදරයෙන් පිළිගනිමු!</b> 🎬\n\n"
        f"මම ඔබගේ චිත්‍රපට වෙබ් අඩවිය (<a href=\"https://filmsub.pages.dev\">filmsub.pages.dev</a>) කළමනාකරණය කරන ස්වයංක්‍රීය සහායකයා වෙමි.\n\n"
        f"<b>ප්‍රධාන පහසුකම්:</b>\n"
        f"• ⚡ <b>Ultra Auto-Leech:</b> ඕනෑම චිත්‍රපටයක් 1080p වලින් Google Drive සහ Telegram වෙත Upload කිරීම\n"
        f"• 💬 <b>Subtitle System:</b> සිංහල උපසිරැසි (.srt/.vtt) ස්වයංක්‍රීයව එක් කිරීම\n"
        f"• ☁️ <b>15TB Google Drive CDN:</b> Ultra Smooth 1080p, 720p, 480p Streaming\n"
        f"• 💾 <b>Drafts & Instant Publish:</b> වෙබ් අඩවියට දැන්ම හෝ පසුව දැමීමේ පූර්ණ පාලනය\n\n"
        f"<i>💡 චිත්‍රපටයක් එක් කිරීමට: <code>/leech &lt;චිත්‍රපටයේ නම&gt;</code> ලෙස එවන්න.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


@app.on_callback_query(filters.regex(r"^dl_q:"))
async def dl_quality_callback(client: Client, query: CallbackQuery) -> None:
    """Deliver requested video quality directly to user upon button click."""
    await query.answer("ගොනුව එවමින් පවතී...")
    parts = query.data.split(":", 2)
    if len(parts) >= 3:
        slug, quality = parts[1], parts[2]
        await deliver_movie_quality(client, query.message.chat.id, slug, quality)


@app.on_message(filters.command(["dl", "download"]) & filters.private)
async def dl_command_handler(client: Client, message: Message) -> None:
    """Download movie quality directly: /dl <movie_name_or_slug> [1080p|720p|480p|360p]"""
    tokens = (message.text or "").split()
    if len(tokens) < 2:
        await message.reply_text(
            "📥 <b>Movie Download විධානය භාවිතා කරන ආකාරය:</b>\n\n"
            "• <code>/dl &lt;Movie Name or Slug&gt;</code>\n"
            "• <code>/dl &lt;Movie Name or Slug&gt; 720p</code>\n"
            "• <code>/dl ice-age-01 480p</code>\n\n"
            "💡 <i>නම ලබාදුන් පසු ඔබට අවශ්‍ය Quality එක (1080p/720p/480p/360p) තෝරාගත හැක.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    query_text = tokens[1].strip()
    quality = tokens[2].strip() if len(tokens) >= 3 else None
    await deliver_movie_quality(client, message.chat.id, query_text, quality)


@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message) -> None:
    """Show command reference."""
    await message.reply_text(
        "<b>📚 Film Bot — Command Reference</b>\n\n"
        "🚀 <b>Ultra Auto-Leech & Uploader (/boost):</b>\n"
        "  • <code>/leech &lt;Movie Name&gt;</code> — Auto-download on VPS & upload to channel\n"
        "  • <code>/leech &lt;Movie Name&gt; &lt;Year&gt;</code> — With year filter\n"
        "  • <code>/leech tt1375666</code> — By IMDb ID\n"
        "  • <code>/leech &lt;Magnet/Direct URL&gt;</code> — Direct fast leech\n"
        "  • <i>Aliases: <code>/auto</code>, <code>/boost</code></i>\n\n"
        "🔍 <b>Movie Search (4-Method Auto Finder):</b>\n"
        "  • <code>/find &lt;Movie Name&gt;</code> — Auto-search via 4 methods\n"
        "  • <code>/find &lt;Movie Name&gt; &lt;Year&gt;</code> — With year filter\n"
        "  • <code>/find tt1375666</code> — Search by IMDb ID\n\n"
        "🎬 <b>Manual Movie Upload (Wizard):</b>\n"
        "  • <b>Video file / link</b> — Send to bot and follow wizard steps\n"
        "  • <code>/add &lt;film_url&gt; &lt;sub_url&gt; [name] [year]</code>\n\n"
        "📂 <b>Drafts, Queue & Control:</b>\n"
        "  • <code>/queue</code> — View pending sequential download queue\n"
        "  • <code>/drafts</code> — List saved drafts\n"
        "  • <code>/cancel</code> — Cancel active leech or upload task\n\n"
        "💬 <b>උපසිරැසි (Subtitles Management):</b>\n"
        "  • <code>/sub &lt;movie_name_or_slug&gt; &lt;sub_url&gt;</code> — Add subtitle anytime\n"
        "  • Reply to <code>.srt</code> or <code>.vtt</code> with <code>/sub &lt;name&gt;</code>\n\n"
        "👥 <b>කණ්ඩායම් අවසර (Team Access Control):</b>\n"
        "  • <code>/auth &lt;user_id / @username&gt;</code> — Grant upload permission\n"
        "  • <code>/unauth &lt;user_id / @username&gt;</code> — Revoke permission\n"
        "  • <code>/users</code> — List authorized uploaders\n\n"
        "☁️ <b>Cloud Drive Storage (OneDrive & Google Drive):</b>\n"
        "  • <code>/drives</code> — View storage quota and connected drives\n"
        "  • <code>/drives list &lt;id&gt;</code> — Movies on specific drive\n"
        "  • <code>/drives offline</code> — Check inactive drives & affected movies\n"
        "  • <code>/adddrive</code> — Add OneDrive / Google Drive\n\n"
        "☁️ <b>PikPak Cloud Debrid (10GB+):</b>\n"
        "  • <code>/pikpak</code> — View PikPak storage and connection\n"
        "  • <code>/pikpak login &lt;email&gt; &lt;pass&gt;</code> — Connect account\n"
        "  • <code>/pikpak clear</code> — Clean cloud storage\n\n"
        "⚙️ <b>Other:</b>\n"
        "  • <code>/status</code> — Bot & server status\n"
        "  • <code>/ping</code> — Liveness check\n\n"
        "🔄 <b>4-Method Fallback Acquisition:</b>\n"
        "  1️⃣ Method A: DDL Scrapers (PixelDrain, Pahe, PSA)\n"
        "  2️⃣ Method B: YTS Torrents (&lt; 1.95GB via aria2c)\n"
        "  3️⃣ Method C: Telegram Movie Channels\n"
        "  4️⃣ Method D: Web Stream Extractors (FlixHQ)",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@app.on_callback_query(filters.regex(r"^btn:"))
async def quick_button_callback(client: Client, query: CallbackQuery) -> None:
    """Handle quick buttons from /start greeting."""
    action = query.data.split(":", 1)[1]
    if action == "help":
        await query.answer()
        fake_msg = query.message
        fake_msg.from_user = query.from_user
        await help_handler(client, fake_msg)
    elif action == "drives":
        await query.answer()
        from handlers.drive_handler import drives_command
        fake_msg = query.message
        fake_msg.from_user = query.from_user
        await drives_command(client, fake_msg)
    else:
        await query.answer()


@app.on_message(filters.command("status"))
async def status_handler(client: Client, message: Message) -> None:
    """Show bot status, active/recent task progress, and configuration summary."""
    from services.task_tracker import tracker
    from handlers.wizard import USER_SESSIONS

    user_id = message.from_user.id if message.from_user else 0
    me = await client.get_me()

    wizard_session = USER_SESSIONS.get(user_id)
    wizard_note = ""
    if wizard_session:
        step = wizard_session.get("step", "IN_PROGRESS")
        step_desc = {
            "CHOICE": "Publish/Draft තෝරාගැනීම",
            "WAITING_NAME": "චිත්‍රපටයේ නම ලබාදීම බලාපොරොත්තුවෙන්",
            "WAITING_SUB": "උපසිරැසි ගොනුව ලබාදීම බලාපොරොත්තුවෙන්",
            "WAITING_QUALITY": "Quality තෝරාගැනීම",
        }.get(step, step)
        title_hint = wizard_session.get("title_hint") or wizard_session.get("file_name", "Movie")
        wizard_note = f"\n\n📝 <b>Wizard ක්‍රියාවලියක් ක්‍රියාත්මකයි:</b>\n🎬 {title_hint} (පියවර: {step_desc})"

    task_summary = tracker.get_status_summary(user_id=user_id)

    is_admin = message.from_user and message.from_user.id in config.ADMIN_IDS
    admin_details = ""
    if is_admin:
        admin_details = (
            f"\n\n⚙️ <b>පද්ධති විස්තර (System Details):</b>\n"
            f"📦 Private channel: <code>{config.PRIVATE_CHANNEL_ID}</code>\n"
            f"📢 Public channel:  <code>{config.PUBLIC_CHANNEL_ID}</code>\n"
            f"🌐 Stream base URL: <code>{config.STREAM_BASE_URL}</code>\n"
            f"🎥 TMDB key set:    {'✅' if config.TMDB_API_KEY else '❌'}"
        )

    await message.reply_text(
        f"🤖 <b>Film Bot තත්ත්වය (Status):</b>\n\n"
        f"Bot: @{me.username}\n\n"
        f"{task_summary}"
        f"{wizard_note}"
        f"{admin_details}",
        parse_mode=ParseMode.HTML,
    )


BOT_VERSION = "v2.6.3-live-drive-stats"
BOT_COMMIT = "live-drive-stats"
BOT_FEATURES = "✅ In-Memory Session | ✅ Live Drive Speed & ETA | ✅ Instant GitHub Raw Web Sync | ✅ Clean URL Rewrites"




@app.on_message(filters.command(["ping", "version"]))
async def ping_handler(client: Client, message: Message) -> None:
    """Check liveness and active build version."""
    await message.reply_text(
        f"🏓 <b>Pong! Bot is Live</b>\n\n"
        f"🔖 <b>Version:</b> <code>{BOT_VERSION}</code> (Commit <code>{BOT_COMMIT}</code>)\n"
        f"⚡ <b>Active Features:</b>\n{BOT_FEATURES}",
        parse_mode=ParseMode.HTML,
    )



# ─────────────────────────────────────────────────────────────────────────────
# Register external handlers
# ─────────────────────────────────────────────────────────────────────────────

def _register_handlers() -> None:
    """Import and register all handlers from the handlers package."""
    from handlers import wizard
    wizard.register(app)
    log.info("Handler registered: wizard (/add, /drafts, video detection)")

    from handlers import find_handler
    find_handler.register(app)
    log.info("Handler registered: find_handler (/find, /search, /movie)")

    from handlers import leech_handler
    leech_handler.register(app)
    log.info("Handler registered: leech_handler (/leech, /auto, /boost, /queue)")

    from handlers import auth_handler
    auth_handler.register(app)
    log.info("Handler registered: auth_handler (/auth, /unauth, /users)")

    from handlers import sub_handler
    sub_handler.register(app)
    log.info("Handler registered: sub_handler (/sub, /addsub)")

    from handlers import pikpak_handler
    pikpak_handler.register(app)
    log.info("Handler registered: pikpak_handler (/pikpak)")

    from handlers import drive_handler
    drive_handler.register(app)
    log.info("Handler registered: drive_handler (/drives, /storage, /adddrive)")



# ─────────────────────────────────────────────────────────────────────────────
# Startup / shutdown hooks
# ─────────────────────────────────────────────────────────────────────────────

async def _on_start(client: Client) -> None:
    """Called once when the bot connects to Telegram."""
    me = await client.get_me()
    log.info("=" * 60)
    log.info("  Film Bot started")
    log.info("  Bot username : @%s", me.username)
    log.info("  Bot user ID  : %d", me.id)
    log.info("  Admins       : %s", config.ADMIN_IDS)
    log.info("  Private ch.  : %d", config.PRIVATE_CHANNEL_ID)
    log.info("  Public ch.   : %d", config.PUBLIC_CHANNEL_ID)
    log.info("  GitHub repo  : %s", config.GITHUB_REPO)
    log.info("=" * 60)

    # Initialize Cloud Drive Manager (OneDrive / Google Drive)
    try:
        from services.cloud_drive import drive_manager
        drive_manager.initialize()
        log.info("[DriveManager] Cloud Drive storage initialized.")
    except Exception as dm_err:
        log.warning("[DriveManager] Initialization error: %s", dm_err)

    # Prime channel peers in session database to avoid [400 PEER_ID_INVALID]
    for ch_name, ch_id in [("Private channel (Filmhost)", config.PRIVATE_CHANNEL_ID), ("Public channel", config.PUBLIC_CHANNEL_ID)]:
        if ch_id:
            try:
                chat = await client.get_chat(ch_id)
                log.info("[PeerInit] Successfully primed %s: '%s' (ID: %s)", ch_name, getattr(chat, "title", "Channel"), chat.id)
            except Exception as ch_err:
                log.error("[PeerInit] Failed to prime %s (ID: %s): %s", ch_name, ch_id, ch_err)

    # Initialize Telegram streaming pool for web video streaming
    try:
        from streaming.session_pool import stream_pool
        stream_pool.set_main_client(client)
        await stream_pool.init_extra_sessions(config.API_ID, config.API_HASH)
        log.info("[StreamPool] Streaming pool initialized with main bot client.")
    except Exception as sp_err:
        log.warning("[StreamPool] Failed to initialize extra sessions in streaming pool: %s", sp_err)

    # Notify admins that the bot restarted
    service_name = os.getenv("RENDER_SERVICE_NAME", "") or os.getenv("RENDER_INSTANCE_ID", "")
    service_tag = f" <i>[Service: {service_name}]</i>" if service_name else ""
    for admin_id in config.ADMIN_IDS:
        try:
            await client.send_message(
                chat_id=admin_id,
                text=(
                    f"🤖 <b>Film Bot started!</b>{service_tag}\n"
                    f"Bot: @{me.username}\n"
                    "Use /help to see available commands."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            log.warning("Could not send startup message to admin %d: %s", admin_id, exc)

    # Check for any interrupted downloads from previous run (Render crash recovery)
    try:
        from services import resume_service
        await resume_service.notify_interrupted_downloads(client)
    except Exception as rs_err:
        log.warning("[ResumeService] Could not check interrupted downloads: %s", rs_err)


async def main() -> None:
    port = int(os.getenv("PORT", 7860))
    start_health_server_thread(port)

    # Start Async FIFO queue worker
    try:
        from services.queue_service import queue_service
        queue_service.start_worker()
        log.info("FIFO Queue worker started.")
    except Exception as q_err:
        log.warning("Could not start queue worker: %s", q_err)

    try:
        await app.start()
        await _on_start(app)
        log.info("Bot is now running and listening for commands...")
        await idle()
    finally:
        log.info("Shutting down bot and health server...")
        if _health_server_instance:
            _health_server_instance.should_exit = True
        if app.is_connected:
            await app.stop()


if __name__ == "__main__":
    _register_handlers()
    log.info("Starting Pyrogram event loop…")
    try:
        app.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by keyboard interrupt.")
    except Exception as exc:
        log.critical("Fatal error — bot crashed: %s", exc, exc_info=True)
        sys.exit(1)
