---
title: Film Sub Bot
emoji: 🎬
colorFrom: red
colorTo: black
sdk: docker
app_port: 7860
pinned: false
---

# 🎬 Film Sub Bot & Streaming Engine

An enterprise-grade, fully automated Telegram movie indexing, acquisition, compression, and streaming engine with built-in Hugging Face Spaces Docker deployment.

---

## 🌟 System Overview & Architecture

**Film Sub Bot** acts as the central automation brain connecting movie search engines, cloud debrid torrent caching, high-efficiency media processing, Telegram cloud storage, and public web streaming portals.

```
                  ┌────────────────────────────────────────┐
                  │          Telegram User / Admin         │
                  └───────────────────┬────────────────────┘
                                      │ Commands & Files
                                      ▼
                      ┌───────────────────────────────┐
                      │    Pyrogram Bot Controller    │
                      │    (/find, /leech, /add)      │
                      └───────┬───────────────┬───────┘
                              │               │
       Acquisition Pipeline   │               │   Metadata & Sync
  ┌───────────────────────────┴──┐         ┌──┴─────────────────────────┐
  │  • Seedr Pool Cloud Debrid   │         │  • TMDB API Metadata       │
  │  • DDL Scrapers (PixelDrain) │         │  • GitHub Auto-Commit Sync │
  │  • 16-Thread aria2c Engine   │         │  • Subtitle Parsing (.srt) │
  │  • Telegram Source Channels  │         └────────────────────────────┘
  └───────────────┬──────────────┘
                  ▼
  ┌───────────────────────────────┐
  │   FFmpeg 1080p Compression    │
  │   (CRF, AAC/Opus, FastStart)  │
  └───────────────┬───────────────┘
                  ▼
  ┌───────────────────────────────┐
  │   Private Storage Channel     │
  │   (Telegram Cloud Storage)    │
  └───────────────┬───────────────┘
                  │
                  ▼
  ┌───────────────────────────────┐
  │   FastAPI Streaming Proxy     │
  │   (HTTP Range / Seeking 7860) │
  └───────────────┬───────────────┘
                  ▼
       HTML5 / Video.js Web Player
```

---

## ✨ Key Features

### ⚡ 1. Seedr Pool Cloud Debrid
- Multi-account round-robin pool (`seedr_accounts.json` / environment credentials) for zero-cost high-speed torrent caching.
- Bypasses residential bandwidth restrictions and ISP torrent throttling.
- Instant cloud unzipping and direct high-speed HTTP retrieval.

### 🎯 2. 4-Method Fallback Acquisition
The automated `/leech` and `/find` pipelines dynamically fall back across 4 independent sources:
1. **Method A (Direct DDL Scrapers):** Fast download links from PixelDrain, Pahe, and PSA.
2. **Method B (YTS Torrents):** High-speed torrents (< 1.95GB filtered for Telegram limits) downloaded via `aria2c` with 16 parallel threads and live tracker injection.
3. **Method C (Telegram Movie Channels):** Direct channel message indexing and file harvesting.
4. **Method D (Web Stream Extractors):** Direct stream extraction from web providers.

### 🎞️ 3. 1080p Smart Compression & FastStart
- Integrated FFmpeg processing pipeline with automatic duration and bitrate calculation.
- Smart H.264/CRF encoding optimized for mobile, web, and TV playback.
- Audio codec normalization to AAC/Opus stereo.
- Web-optimized `+faststart` MP4 atom positioning so videos buffer and play instantly.

### 📝 4. Auto Subtitle Handling
- Automated subtitle detection, extraction, and character encoding conversion (ensuring clean Sinhala and English Unicode UTF-8 output).
- Formats `.srt` and `.vtt` tracks with synchronization for embedded streaming.

### 🌐 5. Website Sync & Public Catalog Integration
- Enriches every title with TMDB posters, backdrops, cast, genres, ratings, and plot summaries.
- Automatically generates static movie pages and commits updates directly to your GitHub repository.
- Keeps your streaming frontend synchronized in real-time.

### 🚀 6. Built-in Seeking HTTP Range Stream Proxy
- Native FastAPI proxy exposing port `7860` with full HTTP Range request (`206 Partial Content`) support.
- Allows Video.js or native HTML5 `<video>` tags to seek through videos stored inside private Telegram channels.

---

## 🐳 Deploying on Hugging Face Spaces

You can host Film Sub Bot **24/7 for free** on Hugging Face Spaces using the Docker SDK.

### Step 1: Create a Space
1. Log in to [Hugging Face](https://huggingface.co/).
2. Click on **New Space** (`https://huggingface.co/new-space`).
3. Set your **Space Name** (e.g., `film-sub-bot`).
4. Select **Docker** as the Space SDK (**Blank** template).
5. Choose **Public** or **Private** visibility.
6. Select **CPU Basic** (Free tier).

### Step 2: Configure Secrets
Navigate to **Settings** → **Variables and secrets** → **New secret**, and add your configuration keys (see table below).

### Step 3: Push Repository to Hugging Face
Clone your Space repository or push your local repository:

```bash
# Add Hugging Face Space as a git remote
git remote add space https://huggingface.co/spaces/YOUR_USERNAME/YOUR_SPACE_NAME

# Push to main branch
git push space main -f
```

Hugging Face Spaces will automatically build the `Dockerfile`, install system dependencies (`ffmpeg`, `aria2c`), launch the FastAPI streaming server on port `7860`, and start the Telegram bot.

---

## 🔐 Environment Variables & Secrets Reference

Configure these in Hugging Face Spaces (**Settings → Variables and secrets**) or in your local `.env` file:

| Variable | Required | Description | Example / Format |
| :--- | :---: | :--- | :--- |
| `TG_API_ID` | **Yes** | Telegram API ID from [my.telegram.org](https://my.telegram.org) | `39254885` |
| `TG_API_HASH` | **Yes** | Telegram API Hash from [my.telegram.org](https://my.telegram.org) | `d92a5fdd19a8bc69ee73ee91cae09e98` |
| `BOT_TOKEN` | **Yes** | Telegram Bot Token from [@BotFather](https://t.me/BotFather) | `1234567890:ABCdefGhI...` |
| `ADMIN_IDS` | **Yes** | Comma-separated Telegram User IDs with admin access | `8762329226,123456789` |
| `PRIVATE_CHANNEL_ID` | **Yes** | Target private channel ID for raw video storage | `-1004325759505` |
| `PUBLIC_CHANNEL_ID` | **Yes** | Public channel ID for posting announcements | `-1004325759505` |
| `TMDB_API_KEY` | **Yes** | The Movie Database API Key v3 from [themoviedb.org](https://www.themoviedb.org/settings/api) | `dcbe3262c901719ed8b029...` |
| `STREAM_BASE_URL` | **Yes** | Base URL of your HF Space or streaming worker proxy | `https://your-space.hf.space` |
| `SITE_BASE_URL` | No | Frontend website base URL | `https://yoursite.lk` |
| `GITHUB_TOKEN` | No | GitHub Personal Access Token (repo scope) for auto-sync | `ghp_xxxxxxxxxxxx` |
| `GITHUB_REPO` | No | GitHub repository target for movie posts | `username/film-web-site` |
| `SESSION_NAME` | No | Pyrogram userbot session name (default: `session`) | `session` |
| `SEEDR_USERNAME` | No | Single Seedr account email for torrent debrid | `user@example.com` |
| `SEEDR_PASSWORD` | No | Single Seedr account password | `SecretPassword123` |
| `SEEDR_TOKEN` | No | Optional pre-authenticated Seedr OAuth2 token | `Bearer_Token...` |

---

## 🤖 Bot Slash Commands

All commands can be issued directly in private chat with your bot:

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `/start` | — | Greet user, check authorization, and display main options. |
| `/help` | — | Display complete command reference and acquisition methods guide. |
| `/status` | — | Display real-time VPS/Space health, active background tasks, and channels status. |
| `/ping` | — | Instant liveness check (`Pong!`). |
| `/find` | `<Movie Name / Year / IMDb ID>` | Search across all 4 acquisition scrapers and display results. |
| `/leech` | `<Name / Magnet / Direct URL>` | Automated one-click download, 1080p compression, TMDB tag, and channel upload. |
| `/boost` | `<Name / Magnet / Direct URL>` | Alias for `/leech`. |
| `/auto` | `<Name / Magnet / Direct URL>` | Alias for `/leech`. |
| `/add` | `[file_url] [sub_url] [name] [year]` | Start interactive upload wizard or direct batch upload. |
| `/drafts` | — | List, resume, or discard saved movie draft sessions. |
| `/cancel` | — | Immediately abort the current active task (leech/download/compress/upload). |

---

## 🛠️ Local Development & Testing

```bash
# Clone the repository
git clone https://github.com/yourusername/film_web_site.git
cd film_web_site

# Copy configuration template
cp bot/.env.example bot/.env

# Install Python requirements
pip install -r bot/requirements.txt

# Run streaming server
uvicorn bot.streaming.stream_server:app --host 0.0.0.0 --port 7860

# In another terminal, run Telegram bot
cd bot
python main.py
```

### Docker Run (Local)
```bash
docker build -t film-bot .
docker run -p 7860:7860 --env-file bot/.env film-bot
```

---

## 📄 License & Disclaimer

This project is open source and intended strictly for educational and self-hosting purposes. Ensure you comply with the Terms of Service of all integrated APIs and streaming platforms.
