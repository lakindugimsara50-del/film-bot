"""
find_handler.py - /find and /search command handler.

Triggers the 4-method cascading movie finder and presents results
to the admin with inline buttons:
  [Publish to Site]  [Save as Draft]  [Cancel]

All log strings are in English to avoid Windows charmap errors.
"""

import asyncio
import logging
import re
from typing import Optional

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from services import tmdb_service, github_service, subtitle_service
from services.movie_finder import find_movie, FindResult

log = logging.getLogger(__name__)

# Cache of pending find results: user_id -> {result, metadata, msg_id}
_PENDING: dict[int, dict] = {}


def _is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def _parse_query(text: str) -> tuple[str, Optional[int], Optional[str]]:
    """
    Parse a /find or /search command text.
    Supports:
      /find Inception
      /find Inception 2010
      /find tt1375666
    Returns (title_or_id, year, imdb_id).
    """
    # Strip the command prefix
    text = re.sub(r"^/\w+\s*", "", text.strip())

    # Check if it's an IMDb ID
    imdb_match = re.match(r"^(tt\d+)$", text, re.IGNORECASE)
    if imdb_match:
        return "", None, imdb_match.group(1)

    # Check for title + year at the end
    year_match = re.match(r"^(.+?)\s+(\d{4})$", text)
    if year_match:
        return year_match.group(1).strip(), int(year_match.group(2)), None

    return text, None, None


def _result_summary(title: str, year: Optional[int], result: FindResult) -> str:
    """Build a human-readable summary of the find result."""
    method_labels = {
        "telegram": "Telegram Channel",
        "consumet": "Consumet/FlixHQ Stream",
        "ddl": "DDL Site (Pahe/PSArips)",
        "embed": "Embed Aggregator (Vidsrc/AutoEmbed)",
    }
    method_label = method_labels.get(result.method, result.method)
    servers_info = f"{len(result.servers)} server(s)" if result.servers else "No direct servers"
    downloads_info = f"{len(result.downloads)} download link(s)" if result.downloads else "No DDL links"

    return (
        f"**Movie Found!**\n"
        f"**Title:** {title}" + (f" ({year})" if year else "") + "\n"
        f"**Method:** {method_label}\n"
        f"**Quality:** {result.quality}\n"
        f"**Streams:** {servers_info}\n"
        f"**Downloads:** {downloads_info}\n"
        f"**Embed Mode:** {'Yes' if result.embed else 'No'}\n"
    )


def register(app: Client) -> None:
    """Register /find and /search command handlers."""

    @app.on_message(
        filters.private & filters.command(["find", "search", "movie"])
    )
    async def find_command(client: Client, message: Message) -> None:
        if not _is_admin(message.from_user.id):
            await message.reply("Access denied.")
            return

        uid = message.from_user.id
        text = message.text or ""
        title, year, imdb_id = _parse_query(text)

        if not title and not imdb_id:
            await message.reply(
                "Usage:\n"
                "  /find <Movie Name>\n"
                "  /find <Movie Name> <Year>\n"
                "  /find <IMDb ID>  (e.g. tt1375666)\n"
            )
            return

        # ── Step 1: Fetch TMDB metadata ──────────────────────────────────────
        status_msg = await message.reply("Searching TMDB for metadata...")
        try:
            if imdb_id:
                tmdb_meta = await tmdb_service.fetch_by_imdb_id(imdb_id)
                if tmdb_meta:
                    title = tmdb_meta.get("title", title)
                    year = tmdb_meta.get("year", year)
            else:
                tmdb_meta = await tmdb_service.fetch_metadata(title, year)
        except Exception as exc:
            log.warning("[FindHandler] TMDB fetch failed: %s", exc)
            tmdb_meta = {}

        if not tmdb_meta:
            tmdb_meta = {}
            await status_msg.edit_text(
                f"TMDB metadata not found for: {title or imdb_id}\n"
                f"Proceeding with basic info..."
            )
        else:
            title = tmdb_meta.get("title", title) or title
            year = tmdb_meta.get("year", year) or year
            imdb_id = imdb_id or tmdb_meta.get("imdb_id")
            tmdb_id_val = str(tmdb_meta.get("tmdb_id", ""))

        tmdb_id_val = str(tmdb_meta.get("tmdb_id", ""))
        poster_url = tmdb_meta.get("poster_url") or tmdb_meta.get("poster", "")

        # ── Step 2: Run 4-method cascade ────────────────────────────────────
        await status_msg.edit_text(
            f"Searching 4 sources for: **{title}** ({year or '?'})\n"
            f"Method 1/4: Telegram channels...",
            parse_mode=ParseMode.MARKDOWN,
        )

        result: Optional[FindResult] = await find_movie(
            title=title,
            year=year,
            imdb_id=imdb_id,
            tmdb_id=tmdb_id_val,
        )

        if not result:
            await status_msg.edit_text(
                f"**Not found** in any of the 4 sources:\n"
                f"- Telegram channels\n"
                f"- Consumet/FlixHQ\n"
                f"- DDL sites (Pahe/PSArips)\n"
                f"- Embed aggregators (Vidsrc/AutoEmbed)\n\n"
                f"Try uploading the video file directly to the bot.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Step 3: Cache result and present to admin ────────────────────────
        _PENDING[uid] = {
            "result": result,
            "meta": tmdb_meta,
            "title": title,
            "year": year,
            "imdb_id": imdb_id,
            "tmdb_id": tmdb_id_val,
        }

        summary = _result_summary(title, year, result)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Publish to Site", callback_data="find:publish"),
                InlineKeyboardButton("Save as Draft", callback_data="find:draft"),
            ],
            [
                InlineKeyboardButton("Try Next Source", callback_data="find:next"),
                InlineKeyboardButton("Cancel", callback_data="find:cancel"),
            ],
        ])

        # Send poster + summary if we have it
        if poster_url:
            try:
                await status_msg.delete()
                await client.send_photo(
                    chat_id=uid,
                    photo=poster_url,
                    caption=summary,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kb,
                )
            except Exception:
                await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        else:
            await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    # ── Callback button handlers ─────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^find:"))
    async def find_callback(client: Client, query: CallbackQuery) -> None:
        uid = query.from_user.id
        if not _is_admin(uid):
            await query.answer("Access denied.", show_alert=True)
            return

        action = query.data.split(":")[1]
        pending = _PENDING.get(uid)

        if not pending and action != "cancel":
            await query.answer("Session expired. Run /find again.", show_alert=True)
            return

        if action == "cancel":
            _PENDING.pop(uid, None)
            await query.message.edit_text("Cancelled.")
            await query.answer("Cancelled.")
            return

        if action == "publish":
            await query.answer("Publishing to site...")
            await query.message.edit_text("Publishing movie to site, please wait...")
            await _publish_movie(client, query.message, uid, pending)

        elif action == "draft":
            await query.answer("Saving as draft...")
            await query.message.edit_text("Saved as draft. Use /drafts to publish later.")
            # Store minimal draft info
            from services import draft_service
            result: FindResult = pending["result"]
            meta = pending["meta"]
            slug = re.sub(r"[^a-z0-9]+", "-", (pending["title"] or "movie").lower()).strip("-")
            if pending.get("year"):
                slug += f"-{pending['year']}"
            draft_service.save_draft(uid, {
                "slug": slug,
                "title": pending["title"],
                "year": pending["year"],
                "imdb_id": pending.get("imdb_id"),
                "tmdb_id": pending.get("tmdb_id"),
                "streams": result.to_streams_list(),
                "downloads": result.to_downloads_list(),
                "quality": result.quality,
                "embed": result.embed,
                "method": result.method,
                **meta,
            })
            _PENDING.pop(uid, None)

        elif action == "next":
            await query.answer("Trying next source... (coming soon)")


async def _publish_movie(client: Client, message, uid: int, pending: dict) -> None:
    """Build movie entry and publish directly to movies.json + Cloudflare."""
    import re
    from handlers.announce import post_to_channel, _slugify
    from services import github_service, subtitle_service

    result: FindResult = pending["result"]
    meta = pending.get("meta", {})
    title = pending["title"]
    year = pending.get("year")
    imdb_id = pending.get("imdb_id")
    tmdb_id = pending.get("tmdb_id")

    # Build slug
    slug = _slugify(title, year)

    # Default Sinhala subtitle placeholder
    default_sub_url = subtitle_service.generate_placeholder_vtt(title, year)

    # Build streams and downloads from result
    streams = result.to_streams_list()
    downloads = result.to_downloads_list()

    # If DDL-only, add embed servers as fallback streams
    if not streams and imdb_id:
        from services.scrapers.method4_embed import _build_embed_servers
        embed_servers = _build_embed_servers(imdb_id, tmdb_id)
        for s in embed_servers:
            streams.append({
                "server": s["server"],
                "label": s["label"],
                "type": "embed",
                "stream_url": s.get("stream_url", s.get("embed_url", "")),
                "embed": True,
            })

    movie_entry = {
        "id": slug,
        "slug": slug,
        "title": title,
        "title_si": meta.get("title_si", ""),
        "year": year,
        "imdb": meta.get("rating") or meta.get("imdb", ""),
        "imdb_id": imdb_id or "",
        "tmdb_id": tmdb_id or "",
        "poster": meta.get("poster_url") or meta.get("poster", ""),
        "backdrop": meta.get("backdrop_url") or meta.get("backdrop", ""),
        "genres": meta.get("genres", []),
        "language": "English",
        "subtitle_language": "Sinhala",
        "quality": result.quality,
        "duration": meta.get("duration", ""),
        "description": meta.get("description", ""),
        "description_si": meta.get("description_si", ""),
        "director": meta.get("director", ""),
        "cast": meta.get("cast", []),
        "featured": False,
        "trending": True,
        "streams": streams,
        "downloads": downloads,
        "subtitles": [
            {
                "language": "Sinhala",
                "label": "Sinhala Subtitle",
                "url": default_sub_url,
                "default": True,
            }
        ],
        "source_method": result.method,
        "site_url": f"{config.SITE_BASE_URL}/movie.html?id={slug}",
        "added_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "added_by": "bot_find",
    }

    try:
        success = await github_service.add_movie(movie_entry)
        if not success:
            raise RuntimeError("github_service.add_movie returned False")
    except Exception as exc:
        log.error("[FindHandler] Failed to add movie to database: %s", exc)
        await message.edit_text(f"Failed to publish: {exc}")
        return

    site_url = f"{config.SITE_BASE_URL}/movie.html?id={slug}"
    await message.edit_text(
        f"Movie published successfully!\n"
        f"Title: {title} ({year})\n"
        f"Method: {result.method}\n"
        f"Servers: {len(streams)}\n"
        f"Site: {site_url}"
    )

    # Announce to channel
    try:
        from handlers.announce import post_to_channel
        await post_to_channel(client, movie_entry, config.PUBLIC_CHANNEL_ID)
    except Exception as exc:
        log.warning("[FindHandler] Channel announcement failed: %s", exc)

    _PENDING.pop(uid, None)
