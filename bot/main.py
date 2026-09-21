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

_bot_dir = os.path.dirname(os.path.abspath(__file__))
if _bot_dir not in sys.path:
    sys.path.insert(0, _bot_dir)

from pyrogram import Client, filters, idle
from pyrogram.enums import ParseMode
from pyrogram.types import Message

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

# ── Create Pyrogram bot Client ────────────────────────────────────────────────
# NOTE: Bots use BOT_TOKEN; userbots (telegram_upload / stream_server)
#       use the SESSION_NAME .session file with API_ID + API_HASH.
app = Client(
    name="film_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
)


# ── Health & Status Web Server ────────────────────────────────────────────────
web_app = FastAPI(title="Film Movie Bot Health Server")


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



# ─────────────────────────────────────────────────────────────────────────────
# Built-in command handlers
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message) -> None:
    """Greet new users."""
    await message.reply_text(
        "👋 <b>Welcome to Film Bot!</b>\n\n"
        "I automate movie uploads to the streaming site.\n\n"
        "<b>Admin commands:</b>\n"
        "  /add — Add a new movie\n"
        "  /status — Check bot & server status\n"
        "  /help — Show this message",
        parse_mode=ParseMode.HTML,
    )


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
        "📂 <b>Drafts & Control:</b>\n"
        "  • <code>/drafts</code> — List saved drafts\n"
        "  • <code>/cancel</code> — Cancel active leech or upload task\n\n"
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


@app.on_message(filters.command("ping"))
async def ping_handler(client: Client, message: Message) -> None:
    """Simple liveness check."""
    await message.reply_text("🏓 Pong!")


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
    log.info("Handler registered: leech_handler (/leech, /auto, /boost)")



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

    # Notify admins that the bot restarted
    for admin_id in config.ADMIN_IDS:
        try:
            await client.send_message(
                chat_id=admin_id,
                text=(
                    f"🤖 <b>Film Bot started!</b>\n"
                    f"Bot: @{me.username}\n"
                    "Use /help to see available commands."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            log.warning("Could not send startup message to admin %d: %s", admin_id, exc)


async def main() -> None:
    port = int(os.getenv("PORT", 7860))
    server_cfg = uvicorn.Config(
        app=web_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(server_cfg)

    # Run uvicorn web server concurrently as a background task
    server_task = asyncio.create_task(server.serve())
    log.info("FastAPI health server started on port %d", port)

    try:
        await app.start()
        await _on_start(app)
        log.info("Bot is now running and listening for commands...")
        await idle()
    finally:
        log.info("Shutting down bot and health server...")
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            server_task.cancel()
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
