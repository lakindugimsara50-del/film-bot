"""
play_handler.py — Inline-button handler for VIP Player selection.

Registers:
  • /player <movie_name>   – sends an inline keyboard with VIP 1/2/3 buttons.
  • callback add_player:<name> – stores the selected player in USER_ACTIVE_PLAYER
    and updates the Telegram message so the next upload uses that player.
"""

import logging
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.auth_service import auth_service
from services import player_service

log = logging.getLogger(__name__)

# In-memory store: user_id -> active player name ("vip1" | "vip2" | "vip3")
USER_ACTIVE_PLAYER: dict[int, str] = {}


def get_active_player(user_id: int) -> str:
    """Return the currently active player for user_id; defaults to 'vip1'."""
    return USER_ACTIVE_PLAYER.get(user_id, "vip1")


def set_active_player(user_id: int, player_name: str) -> None:
    """Persist the selected player for user_id."""
    USER_ACTIVE_PLAYER[user_id] = player_name
    log.info("[PlayHandler] User %d set active player → %s", user_id, player_name)


def register(app: Client) -> None:
    """Register /player command and add_player / player:cancel callbacks."""

    @app.on_message(filters.private & filters.command(["player", "setplayer"]))
    async def player_command(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        username = message.from_user.username if message.from_user else ""
        if not auth_service.is_authorized(user_id, username):
            await message.reply_text("⛔ Access denied.")
            return

        current = get_active_player(user_id)
        current_label = player_service.PLAYER_LABELS.get(current, current)

        buttons = [
            [InlineKeyboardButton(
                text=f"{'✅ ' if p == current else ''}{player_service.PLAYER_LABELS[p]}",
                callback_data=f"add_player:{p}",
            )]
            for p in player_service.PLAYER_ORDER
        ]
        buttons.append([InlineKeyboardButton("❌ Close", callback_data="player:cancel")])
        kb = InlineKeyboardMarkup(buttons)

        await message.reply_text(
            f"🎬 <b>VIP Player තෝරන්න (Select Player):</b>\n\n"
            f"📺 <b>දැනට ක්‍රියාත්මක Player:</b> {current_label}\n\n"
            f"<i>ඔබේ ඊළඟ Upload සඳහා ස්වයංක්‍රීයව මෙම Player භාවිතා කෙරේ.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    @app.on_callback_query(filters.regex(r"^add_player:"))
    async def add_player_callback(client: Client, query: CallbackQuery) -> None:
        user_id = query.from_user.id if query.from_user else 0
        username = query.from_user.username if query.from_user else ""
        if not auth_service.is_authorized(user_id, username):
            await query.answer("Unauthorized.", show_alert=True)
            return

        player_name = query.data.split(":", 1)[1].strip()
        if player_name not in player_service.PLAYER_ORDER:
            await query.answer("Invalid player.", show_alert=True)
            return

        set_active_player(user_id, player_name)
        label = player_service.PLAYER_LABELS.get(player_name, player_name)

        # Rebuild keyboard with checkmark on the newly selected player
        buttons = [
            [InlineKeyboardButton(
                text=f"{'✅ ' if p == player_name else ''}{player_service.PLAYER_LABELS[p]}",
                callback_data=f"add_player:{p}",
            )]
            for p in player_service.PLAYER_ORDER
        ]
        buttons.append([InlineKeyboardButton("❌ Close", callback_data="player:cancel")])
        kb = InlineKeyboardMarkup(buttons)

        await query.answer(f"✅ {label} ලෙස සකසා ඇත!")
        try:
            await query.message.edit_text(
                f"✅ <b>VIP Player සාර්ථකව සකසා ඇත!</b>\n\n"
                f"📺 <b>Active Player:</b> {label}\n\n"
                f"<i>ඔබේ ඊළඟ /leech Upload සඳහා ස්වයංක්‍රීයව මෙම Player භාවිතා කෙරේ.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception:
            pass

    @app.on_callback_query(filters.regex(r"^player:cancel$"))
    async def player_cancel_callback(client: Client, query: CallbackQuery) -> None:
        await query.answer("Closed.")
        try:
            await query.message.delete()
        except Exception:
            pass
