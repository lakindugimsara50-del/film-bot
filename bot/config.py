"""
config.py — Central configuration for the Telegram bot.
All values are read from environment variables so the same code works
on a local machine (via .env) and on a VPS (via exported shell variables).
"""

import os
from dotenv import load_dotenv

# Load .env file (checks bot/.env, project root .env, or current directory)
_bot_env = os.path.join(os.path.dirname(__file__), ".env")
_root_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_bot_env):
    load_dotenv(dotenv_path=_bot_env)
elif os.path.exists(_root_env):
    load_dotenv(dotenv_path=_root_env)
else:
    load_dotenv()

# ── Monkeypatch Pyrogram for 64-bit Telegram Channel IDs ─────────────────────
# Pyrogram 2.0.x hardcodes MIN_CHANNEL_ID = -1002147483647 (32-bit limit).
# Modern Telegram channels like Filmhost (-1004325759505) exceed this, causing
# get_peer_type to throw ValueError: Peer id invalid: -1004325759505.
try:
    import pyrogram.utils
    pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999
    pyrogram.utils.MIN_CHAT_ID = -999999999999
except Exception:
    pass

# ── Telegram credentials ────────────────────────────────────────────────────
# Obtain these from https://my.telegram.org/apps
API_ID: int = int(os.getenv("TG_API_ID", "0"))
API_HASH: str = os.getenv("TG_API_HASH", "")

# Bot token from @BotFather
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# Comma-separated Telegram user IDs that are allowed to run admin commands
_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = (
    list(map(int, _raw_admin_ids.split(","))) if _raw_admin_ids else []
)

# ── Channel IDs ─────────────────────────────────────────────────────────────
# PRIVATE_CHANNEL: where uploaded video files are stored (Filmhost, bot must be admin)
PRIVATE_CHANNEL_ID: int = int(os.getenv("PRIVATE_CHANNEL_ID", "0"))
# PUBLIC_CHANNEL: where movie announcements are posted
PUBLIC_CHANNEL_ID: int = int(os.getenv("PUBLIC_CHANNEL_ID", "0"))

# ── External APIs ────────────────────────────────────────────────────────────
# The Movie Database — https://www.themoviedb.org/settings/api
TMDB_API_KEY: str = os.getenv("TMDB_API_KEY", "")

# GitHub personal access token (needs repo scope)
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# GitHub repo that hosts the site, e.g. "john/filmsite"
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "lakindugimsara50-del/film-bot")

# ── Streaming ────────────────────────────────────────────────────────────────
# Base URL of the Cloudflare Worker / FastAPI proxy that streams Telegram files
_raw_stream_url = os.getenv("STREAM_BASE_URL", "").strip()
if not _raw_stream_url or "yourdomain.workers.dev" in _raw_stream_url or "yoursite.lk" in _raw_stream_url:
    STREAM_BASE_URL: str = "https://film-bot-2.onrender.com"
else:
    STREAM_BASE_URL: str = _raw_stream_url.rstrip("/")

# ── Pyrogram session ─────────────────────────────────────────────────────────
# Name of the Pyrogram session file (userbot session, NOT the bot session)
SESSION_NAME: str = os.getenv("SESSION_NAME", "session")

# ── Site URL ─────────────────────────────────────────────────────────────────
# Always default to actual Cloudflare Pages URL; never use dummy domain yoursite.lk
_raw_site_url = os.getenv("SITE_BASE_URL", "").strip()
if not _raw_site_url or "yoursite.lk" in _raw_site_url:
    SITE_BASE_URL: str = "https://filmsub.pages.dev"
else:
    SITE_BASE_URL: str = _raw_site_url.rstrip("/")

# ── Seedr Cloud Debrid ───────────────────────────────────────────────────────
SEEDR_USERNAME: str = os.getenv("SEEDR_USERNAME", "")
SEEDR_PASSWORD: str = os.getenv("SEEDR_PASSWORD", "")
SEEDR_TOKEN: str = os.getenv("SEEDR_TOKEN", "")

# ── PikPak Cloud Debrid (10GB+ Free Tier) ───────────────────────────────────
PIKPAK_USER: str = os.getenv("PIKPAK_USER", "lakindugimsara50@gmail.com")
PIKPAK_PASS: str = os.getenv("PIKPAK_PASS", "Indika@2122138")

# ── High-Speed Pipeline Optimization Flags ──────────────────────────────────
# Generate true distinct 720p, 480p, 360p MP4 variants in single-pass /dev/shm RAM
ENABLE_MULTI_QUALITY_RAM: bool = os.getenv("ENABLE_MULTI_QUALITY_RAM", "true").strip().lower() in ("1", "true", "yes")
# Race direct aria2c against Seedr/PikPak Cloud Debrid to eliminate double-hop caching delay
ENABLE_TORRENT_RACING: bool = os.getenv("ENABLE_TORRENT_RACING", "true").strip().lower() in ("1", "true", "yes")


