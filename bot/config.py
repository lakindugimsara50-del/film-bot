"""
config.py — Central configuration for the Telegram bot.
All values are read from environment variables so the same code works
on a local machine (via .env) and on a VPS (via exported shell variables).
"""

import os
from dotenv import load_dotenv

# Load .env file if it exists (development convenience)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    load_dotenv(dotenv_path=_env_path)
else:
    load_dotenv()

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
# PRIVATE_CHANNEL: where uploaded video files are stored (bot must be admin)
PRIVATE_CHANNEL_ID: int = int(os.getenv("PRIVATE_CHANNEL_ID", "0"))
# PUBLIC_CHANNEL: where movie announcements are posted
PUBLIC_CHANNEL_ID: int = int(os.getenv("PUBLIC_CHANNEL_ID", "0"))

# ── External APIs ────────────────────────────────────────────────────────────
# The Movie Database — https://www.themoviedb.org/settings/api
TMDB_API_KEY: str = os.getenv("TMDB_API_KEY", "")

# GitHub personal access token (needs repo scope)
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# GitHub repo that hosts the site, e.g. "john/filmsite"
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "username/repo")

# ── Streaming ────────────────────────────────────────────────────────────────
# Base URL of the Cloudflare Worker / FastAPI proxy that streams Telegram files
STREAM_BASE_URL: str = os.getenv(
    "STREAM_BASE_URL", "https://stream.yourdomain.workers.dev"
)

# ── Pyrogram session ─────────────────────────────────────────────────────────
# Name of the Pyrogram session file (userbot session, NOT the bot session)
SESSION_NAME: str = os.getenv("SESSION_NAME", "session")

# ── Site URL ─────────────────────────────────────────────────────────────────
SITE_BASE_URL: str = os.getenv("SITE_BASE_URL", "https://yoursite.lk")

# ── Seedr Cloud Debrid ───────────────────────────────────────────────────────
SEEDR_USERNAME: str = os.getenv("SEEDR_USERNAME", "")
SEEDR_PASSWORD: str = os.getenv("SEEDR_PASSWORD", "")
SEEDR_TOKEN: str = os.getenv("SEEDR_TOKEN", "")
