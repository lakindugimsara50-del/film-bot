"""
auth_handler.py — Command handler for managing authorized uploaders.

Commands:
  /auth <user_id or @username>   — Grant uploader permissions
  /unauth <user_id or @username> — Revoke permissions
  /users or /admins              — List all authorized team members
"""

import logging
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
from services.auth_service import auth_service

log = logging.getLogger(__name__)


def register(app: Client) -> None:
    """Register /auth, /unauth, and /users command handlers."""

    @app.on_message(filters.private & filters.command(["auth", "addadmin", "adduser"]))
    async def auth_add_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not auth_service.is_owner(user_id):
            await message.reply_text("⛔ මෙම විධානය භාවිතා කළ හැක්කේ ප්‍රධාන Bot Admin හට පමණි.")
            return

        text = (message.text or "").strip()
        tokens = text.split(maxsplit=1)
        if len(tokens) < 2:
            await message.reply_text(
                "ℹ️ <b>භාවිතය (Usage):</b>\n\n"
                "• <code>/auth @username</code> — Username මඟින් අවසර ලබාදීමට\n"
                "• <code>/auth 123456789</code> — Telegram User ID මඟින් අවසර ලබාදීමට\n\n"
                "<i>අවසර ලත් අයට /leech, /add, /find ආදී විධාන ක්‍රියාත්මක කර චිත්‍රපට එක් කළ හැක.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        target = tokens[1].strip()
        result = auth_service.add_user(target, added_by=user_id)
        await message.reply_text(result, parse_mode=ParseMode.HTML)

    @app.on_message(filters.private & filters.command(["unauth", "removeuser", "deladmin"]))
    async def auth_remove_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not auth_service.is_owner(user_id):
            await message.reply_text("⛔ මෙම විධානය භාවිතා කළ හැක්කේ ප්‍රධාන Bot Admin හට පමණි.")
            return

        text = (message.text or "").strip()
        tokens = text.split(maxsplit=1)
        if len(tokens) < 2:
            await message.reply_text(
                "ℹ️ <b>භාවිතය:</b> <code>/unauth @username</code> හෝ <code>/unauth &lt;user_id&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        target = tokens[1].strip()
        result = auth_service.remove_user(target)
        await message.reply_text(result, parse_mode=ParseMode.HTML)

    @app.on_message(filters.private & filters.command(["users", "admins", "team"]))
    async def auth_list_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not auth_service.is_authorized(user_id):
            await message.reply_text("⛔ Access denied.")
            return

        users = auth_service.list_authorized()
        lines = []
        for i, u in enumerate(users, 1):
            name = u.get("username") or f"User ID: {u.get('id')}"
            role = u.get("type", "Uploader")
            lines.append(f"<b>{i}. {name}</b> — <i>{role}</i>")

        body = "\n".join(lines) if lines else "කිසිදු අමතර පරිශීලකයෙකු නැත."
        await message.reply_text(
            f"👥 <b>අවසර ලත් කණ්ඩායම් සාමාජිකයින් (Authorized Uploaders):</b>\n\n"
            f"{body}\n\n"
            f"➕ අලුත් අයෙක් එක් කිරීමට: <code>/auth @username</code>\n"
            f"➖ ඉවත් කිරීමට: <code>/unauth @username</code>",
            parse_mode=ParseMode.HTML,
        )
