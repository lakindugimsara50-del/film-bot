"""
add_movie.py — The /add command handler for the Telegram bot.

Command syntax:
    /add <film_url_or_file> <subtitle_url> [movie_name] [year] [quality] [lang]

Examples:
    /add https://mega.nz/xxx https://sub.link/file.srt "Avatar 3" 2025 1080p Sinhala
    /add (reply to video message) https://sub.link/file.srt
    /add (reply to video message) https://sub.link/file.srt "Avatar 3" 2025 1080p Sinhala

The handler:
  1. Authenticates the admin.
  2. Parses arguments from the command or the replied-to message.
  3. Fetches TMDB metadata.
  4. Downloads/uploads the video file (or reads file_id from reply).
  5. Downloads and converts the subtitle (SRT → VTT).
  6. Uploads the VTT to GitHub.
  7. Builds the complete movie dict and pushes it to movies.json on GitHub.
  8. Posts an announcement to the public channel.
  9. Sends a success summary back to the admin.
"""

import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
from services import tmdb_service, telegram_upload, subtitle_service, github_service
from handlers.announce import post_to_channel, _slugify

log = logging.getLogger(__name__)

def _check_admin(_, __, message: Message) -> bool:
    if message.from_user and message.from_user.id in config.ADMIN_IDS:
        return True
    if message.chat and message.chat.id in (config.PRIVATE_CHANNEL_ID, config.PUBLIC_CHANNEL_ID):
        return True
    return False

admin_filter = filters.create(_check_admin) & filters.command("add")


# ─────────────────────────────────────────────────────────────────────────────
def register(app: Client) -> None:
    """Register the /add handler on the given Pyrogram Client."""

    @app.on_message(admin_filter)
    async def add_movie_handler(client: Client, message: Message) -> None:
        await _handle_add(client, message)


# ─────────────────────────────────────────────────────────────────────────────
async def _handle_add(client: Client, message: Message) -> None:
    """
    Core /add logic.  Every major step sends or edits a progress message so
    the admin always knows what is happening.
    """
    progress_msg = await message.reply("⏳ Processing movie…")

    try:
        # ── Step 1: Parse command arguments ───────────────────────────────────
        await _edit(progress_msg, "⏳ <b>Step 1/8</b> — Parsing arguments…")
        args = _parse_args(message)
        log.info("/add args: %s", args)

        film_url: str = args.get("film_url", "")
        subtitle_url: str = args.get("subtitle_url", "")
        movie_name: str = args.get("movie_name", "")
        year: int = args.get("year")
        quality: str = args.get("quality", "1080p")
        lang: str = args.get("lang", "Sinhala Sub")

        # Replied-to message provides the video file_id directly
        reply = message.reply_to_message
        replied_file_id: str = (
            reply.video.file_id
            if reply and reply.video
            else ""
        )

        if not replied_file_id and not film_url:
            await _edit(
                progress_msg,
                "❌ Please either reply to a video or provide a film URL.\n\n"
                "Usage:\n<code>/add &lt;film_url&gt; &lt;sub_url&gt; [name] [year] [quality] [lang]</code>",
            )
            return

        if not subtitle_url:
            await _edit(progress_msg, "❌ Subtitle URL is required.")
            return

        # ── Step 2: Fetch TMDB metadata ───────────────────────────────────────
        await _edit(progress_msg, "⏳ <b>Step 2/8</b> — Fetching movie metadata from TMDB…")
        meta = await tmdb_service.fetch_metadata(movie_name, year)
        log.info("Metadata fetched: %s (%s)", meta.get("title"), meta.get("year"))

        # ── Step 3: Handle the video file ────────────────────────────────────
        file_id: str = replied_file_id
        stream_url: str = ""
        message_id: int = 0
        file_name: str = ""
        file_size: int = 0

        if replied_file_id:
            # Video already in Telegram — just build the stream URL
            await _edit(progress_msg, "⏳ <b>Step 3/8</b> — Using replied video file…")
            stream_url = telegram_upload.get_file_stream_url(replied_file_id)
            file_name = reply.video.file_name or f"{meta['title']}.mp4"
            file_size = reply.video.file_size or 0

        else:
            # Download from URL and upload to private channel
            await _edit(
                progress_msg,
                "⏳ <b>Step 3/8</b> — Downloading and uploading video to Telegram…\n"
                "(this may take several minutes for large files)",
            )

            last_update = [time.monotonic()]

            async def upload_progress(current: int, total: int) -> None:
                """Throttled progress updater — edits message at most every 5 s."""
                now = time.monotonic()
                if total and now - last_update[0] >= 5:
                    pct = current * 100 // total
                    await _edit(
                        progress_msg,
                        f"⏳ <b>Step 3/8</b> — Uploading… {pct}%  "
                        f"({_human_size(current)} / {_human_size(total)})",
                    )
                    last_update[0] = now

            upload_info = await telegram_upload.download_and_upload(
                film_url,
                bot_client=client,
                fallback_chat_id=message.chat.id,
                progress_callback=upload_progress,
            )
            file_id = upload_info["file_id"]
            stream_url = upload_info["stream_url"]
            message_id = upload_info["message_id"]
            file_name = upload_info["file_name"]
            file_size = upload_info["file_size"]

        # Check if file is small enough for direct Telegram streaming (<20MB)
        if file_id and config.BOT_TOKEN and (file_size < 20 * 1024 * 1024):
            direct_stream = await telegram_upload.get_telegram_direct_url(file_id, config.BOT_TOKEN)
            if direct_stream:
                stream_url = direct_stream

        # ── Step 4: Download subtitle ─────────────────────────────────────────
        await _edit(progress_msg, "⏳ <b>Step 4/8</b> — Downloading subtitle…")
        with tempfile.TemporaryDirectory() as tmp_dir:
            srt_path = os.path.join(tmp_dir, "subtitle.srt")
            srt_path = await subtitle_service.download_subtitle(subtitle_url, srt_path)

            # ── Step 5: Convert SRT → VTT ─────────────────────────────────────
            await _edit(progress_msg, "⏳ <b>Step 5/8</b> — Converting subtitle SRT → VTT…")
            vtt_path = subtitle_service.srt_to_vtt(srt_path)

            # ── Step 6: Upload VTT to GitHub ──────────────────────────────────
            await _edit(progress_msg, "⏳ <b>Step 6/8</b> — Uploading subtitle to GitHub…")
            slug = _slugify(meta.get("title", movie_name))
            vtt_filename = f"{slug}-{year or meta.get('year', '')}-{lang.lower().replace(' ', '-')}.vtt"
            subtitle_public_url = await subtitle_service.upload_subtitle_to_github(
                vtt_path,
                filename=vtt_filename,
                github_token=config.GITHUB_TOKEN,
                repo=config.GITHUB_REPO,
            )

        # ── Step 7: Build the movies.json entry ──────────────────────────────
        await _edit(progress_msg, "⏳ <b>Step 7/8</b> — Updating movies.json on GitHub…")

        base_site = (config.SITE_BASE_URL or "https://filmsub.pages.dev").rstrip("/")
        if "yoursite.lk" in base_site:
            base_site = "https://filmsub.pages.dev"
        site_url = f"{base_site}/movie.html?id={slug}"
        movie_dict = _build_movie_dict(
            meta=meta,
            slug=slug,
            quality=quality,
            lang=lang,
            file_id=file_id,
            stream_url=stream_url,
            message_id=message_id,
            file_name=file_name,
            file_size=file_size,
            subtitle_url=subtitle_public_url,
            site_url=site_url,
        )

        success = await github_service.add_movie(movie_dict)
        if not success:
            await _edit(
                progress_msg,
                "⚠️ Movie uploaded but <b>failed to update movies.json</b>. "
                "Please update manually.",
            )
            return

        # ── Step 8: Announce in public channel ────────────────────────────────
        await _edit(progress_msg, "⏳ <b>Step 8/8</b> — Posting announcement to public channel…")
        await post_to_channel(client, movie_dict, config.PUBLIC_CHANNEL_ID)

        # ── Done — send summary card ──────────────────────────────────────────
        summary = _build_summary(movie_dict)
        await _edit(progress_msg, summary)
        log.info("Movie '%s' successfully added.", meta.get("title"))

    except Exception as exc:
        log.exception("Unexpected error in /add handler")
        await _edit(
            progress_msg,
            f"❌ <b>Error:</b> <code>{exc}</code>\n\nCheck the bot logs for details.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(message: Message) -> dict:
    """
    Extract /add arguments from the command text.

    Tokens beyond the command name:
      [0] film_url     — URL or omitted when replying to a video
      [1] subtitle_url — mandatory
      [2] movie_name   — optional (may be quoted)
      [3] year         — optional int
      [4] quality      — optional (e.g. '1080p')
      [5] lang         — optional (e.g. 'Sinhala')

    Returns a dict with the parsed values (missing → None / '').
    """
    # Split keeping quoted strings together
    raw = message.text or ""
    # Remove the /add command itself
    raw = re.sub(r"^/add\S*\s*", "", raw).strip()

    # Tokenise: quoted strings are kept as one token
    tokens = re.findall(r'"[^"]*"|\S+', raw)
    tokens = [t.strip('"') for t in tokens]

    result: dict = {
        "film_url": "",
        "subtitle_url": "",
        "movie_name": "",
        "year": None,
        "quality": "1080p",
        "lang": "Sinhala Sub",
    }

    # When the user replies to a video the first real arg is the subtitle URL
    reply = message.reply_to_message
    has_reply_video = reply and reply.video

    idx = 0

    if not has_reply_video and idx < len(tokens):
        result["film_url"] = tokens[idx]
        idx += 1

    if idx < len(tokens):
        result["subtitle_url"] = tokens[idx]
        idx += 1

    if idx < len(tokens):
        result["movie_name"] = tokens[idx]
        idx += 1

    if idx < len(tokens) and tokens[idx].isdigit():
        result["year"] = int(tokens[idx])
        idx += 1

    if idx < len(tokens):
        result["quality"] = tokens[idx]
        idx += 1

    if idx < len(tokens):
        result["lang"] = " ".join(tokens[idx:])

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Dict builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_movie_dict(
    meta: dict,
    slug: str,
    quality: str,
    lang: str,
    file_id: str,
    stream_url: str,
    message_id: int,
    file_name: str,
    file_size: int,
    subtitle_url: str,
    site_url: str,
) -> dict:
    """
    Assemble the complete movie dict that matches the movies.json schema.
    """
    movie_id = _generate_id(meta.get("title", ""), meta.get("year", 0))
    rating_val = str(meta.get("rating", "N/A"))
    poster_val = meta.get("poster_url", "") or meta.get("poster", "")
    backdrop_val = meta.get("backdrop_url", "") or meta.get("backdrop", "")
    raw_duration = meta.get("duration", 0)
    if isinstance(raw_duration, int) and raw_duration > 0:
        duration_str = f"{raw_duration} min"
    elif isinstance(raw_duration, str) and raw_duration:
        duration_str = raw_duration
    else:
        duration_str = ""

    return {
        # ── Identity ──────────────────────────────────────────────────────────
        "id": movie_id,
        "slug": slug,
        "title": meta.get("title", ""),
        "title_si": meta.get("title_si", ""),
        "year": meta.get("year", 0),

        # ── TMDB / IMDb ───────────────────────────────────────────────────────
        "tmdb_id": str(meta.get("tmdb_id", "")),
        "imdb_id": meta.get("imdb_id", ""),
        "imdb": rating_val,
        "rating": rating_val,

        # ── Images ────────────────────────────────────────────────────────────
        "poster": poster_val,
        "poster_url": poster_val,
        "backdrop": backdrop_val,
        "backdrop_url": backdrop_val,

        # ── Details ───────────────────────────────────────────────────────────
        "genres": meta.get("genres", []),
        "duration": duration_str,
        "description": meta.get("description", ""),
        "description_si": meta.get("description_si", ""),
        "director": meta.get("director", ""),
        "cast": meta.get("cast", []),

        # ── File info ─────────────────────────────────────────────────────────
        "quality": quality,
        "lang": lang,
        "language": "English",
        "subtitle_language": lang,
        "file_id": file_id,
        "message_id": message_id,
        "file_name": file_name,
        "file_size": file_size,

        # ── URLs ──────────────────────────────────────────────────────────────
        "stream_url": stream_url,
        "subtitle_url": subtitle_url,
        "site_url": site_url,

        # ── Display flags ─────────────────────────────────────────────────────
        "trending": True,
        "featured": False,

        # ── Streams array ─────────────────────────────────────────────────────
        "streams": [
            {
                "server": "Server 1",
                "label": "Server 1 (Telegram)",
                "type": "video/mp4",
                "stream_url": stream_url,
            }
        ] if stream_url else [],

        # ── Downloads array ───────────────────────────────────────────────────
        "downloads": [
            {
                "quality": quality,
                "size": _human_size(file_size),
                "url": stream_url,
                "format": "MP4",
            }
        ] if stream_url else [],

        # ── Subtitles array ───────────────────────────────────────────────────
        "subtitles": [
            {
                "language": lang,
                "label": "සිංහල උපසිරැසි",
                "url": subtitle_url,
                "default": True,
            }
        ] if subtitle_url else [],

        # ── Multiple quality files (can be extended later) ─────────────────
        "files": [
            {
                "quality": quality,
                "file_id": file_id,
                "stream_url": stream_url,
                "file_size": file_size,
            }
        ],

        # ── Metadata ──────────────────────────────────────────────────────────
        "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "added_by": "bot",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_summary(movie: dict) -> str:
    """Build the final success card sent to the admin."""
    title = movie.get("title", "?")
    year = movie.get("year", "")
    rating = movie.get("rating", "N/A")
    genres = ", ".join(movie.get("genres", [])[:3])
    stream_url = movie.get("stream_url", "")
    site_url = movie.get("site_url", "")
    subtitle_url = movie.get("subtitle_url", "")
    file_size = _human_size(movie.get("file_size", 0))

    return (
        f"✅ <b>Movie added!</b>  Site will update in ~60 seconds.\n\n"
        f"🎬 <b>{title}</b> ({year})\n"
        f"⭐ {rating}  |  🎭 {genres}\n"
        f"📦 Size: {file_size}\n\n"
        f'🌐 <a href="{site_url}">Site page</a>\n'
        f'▶️ <a href="{stream_url}">Stream URL</a>\n'
        f'📄 <a href="{subtitle_url}">Subtitle</a>'
    )


def _human_size(n: int) -> str:
    """Convert bytes to a human-readable size string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _generate_id(title: str, year: int) -> str:
    """Generate a simple deterministic ID from title + year."""
    slug = _slugify(title)
    return f"{slug}-{year}" if year else slug


async def _edit(msg: Message, text: str) -> None:
    """Edit a Telegram message, ignoring 'message not modified' errors."""
    try:
        await msg.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception:
        pass  # Ignore if text is unchanged or message deleted
