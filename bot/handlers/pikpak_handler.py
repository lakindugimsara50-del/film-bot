"""
pikpak_handler.py — Telegram bot commands for managing PikPak Cloud Debrid.

Provides:
- /pikpak or /pikpak status: View PikPak connection and cloud storage quota
- /pikpak login <email> <password>: Connect or update PikPak account credentials
- /pikpak clear: Purge all tasks and files to restore 100% free storage
"""

import logging
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
from services.auth_service import auth_service
from services.pikpak_service import pikpak_service

log = logging.getLogger(__name__)


def register(app: Client) -> None:
    """Register all PikPak management handlers."""

    @app.on_message(filters.command("pikpak"))
    async def pikpak_command_handler(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not auth_service.is_authorized(user_id):
            await message.reply_text(
                "⛔ <b>අවසර නොමැත (Access Denied)</b>\n\nමෙම විධානය භාවිතා කිරීමට ඔබට අවසර නොමැත.",
                parse_mode=ParseMode.HTML,
            )
            return

        text = (message.text or "").strip()
        parts = text.split()

        subcommand = parts[1].lower() if len(parts) > 1 else "status"

        # ── /pikpak login <user> <pass> ─────────────────────────────────────
        if subcommand == "login":
            if not auth_service.is_admin(user_id):
                await message.reply_text(
                    "🔒 <b>Admin Only</b>: PikPak ගිණුම සම්බන්ධ කිරීමට Bot Admin හට පමණක් අවසර ඇත.",
                    parse_mode=ParseMode.HTML,
                )
                return

            if len(parts) < 4:
                await message.reply_text(
                    "ℹ️ <b>PikPak Login Format:</b>\n\n"
                    "<code>/pikpak login &lt;email_or_phone&gt; &lt;password&gt;</code>\n\n"
                    "උදාහරණයක්:\n"
                    "<code>/pikpak login user@gmail.com mysecretpass123</code>",
                    parse_mode=ParseMode.HTML,
                )
                return

            pp_user = parts[2]
            pp_pass = parts[3]

            status_msg = await message.reply_text(
                "⏳ <b>PikPak ගිණුම තහවුරු කරමින් පවතී (Verifying credentials)...</b>",
                parse_mode=ParseMode.HTML,
            )

            try:
                pikpak_service.save_credentials(pp_user, pp_pass)
                # Test login
                quota = await pikpak_service.get_quota_summary()
                await status_msg.edit_text(
                    f"✅ <b>PikPak ගිණුම සාර්ථකව සම්බන්ධ කරන ලදී!</b>\n\n"
                    f"👤 <b>ගිණුම:</b> <code>{pp_user}</code>\n"
                    f"☁️ <b>මුළු ධාරිතාව:</b> {quota['limit_gb']} GB\n"
                    f"📦 <b>භාවිත කළ ඉඩ:</b> {quota['usage_gb']} GB\n"
                    f"🆓 <b>ඉතිරි නිදහස් ඉඩ:</b> {quota['free_gb']} GB\n\n"
                    f"🚀 දැන් 2GB ට වැඩි විශාල Torrents & TV Series ද PikPak හරහා සුපිරි වේගයෙන් බාගත වේ!",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                log.error("[PikPakHandler] Login verification failed: %s", exc)
                await status_msg.edit_text(
                    f"❌ <b>PikPak Login අසාර්ථක විය!</b>\n\n"
                    f"දෝෂය: <code>{exc}</code>\n\n"
                    f"කරුණාකර ඔබ ලබා දුන් Email/Phone සහ Password නිවැරදිදැයි පරීක්ෂා කරන්න.",
                    parse_mode=ParseMode.HTML,
                )
            return

        # ── /pikpak clear ───────────────────────────────────────────────────
        if subcommand == "clear":
            if not pikpak_service.is_configured():
                await message.reply_text(
                    "⚠️ PikPak ගිණුමක් සකසා නොමැත. කරුණාකර පළමුව <code>/pikpak login</code> භාවිතා කරන්න.",
                    parse_mode=ParseMode.HTML,
                )
                return

            status_msg = await message.reply_text(
                "🧹 <b>PikPak Cloud Storage පිරිසිදු කරමින් පවතී (Clearing all files)...</b>",
                parse_mode=ParseMode.HTML,
            )

            ok = await pikpak_service.clean_storage()
            if ok:
                try:
                    quota = await pikpak_service.get_quota_summary()
                    await status_msg.edit_text(
                        f"✅ <b>PikPak Storage එක 100% පිරිසිදු කරන ලදී!</b>\n\n"
                        f"🆓 <b>දැනට පවතින නිදහස් ඉඩ:</b> {quota['free_gb']} GB / {quota['limit_gb']} GB",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    await status_msg.edit_text("✅ <b>PikPak Storage එක සම්පූර්ණයෙන්ම පිරිසිදු කරන ලදී!</b>", parse_mode=ParseMode.HTML)
            else:
                await status_msg.edit_text("❌ Storage එක පිරිසිදු කිරීමේදී දෝෂයක් සිදුවිය.", parse_mode=ParseMode.HTML)
            return

        # ── /pikpak (status) ────────────────────────────────────────────────
        if not pikpak_service.is_configured():
            await message.reply_text(
                "☁️ <b>PikPak Cloud Debrid තත්ත්වය</b>\n\n"
                "❌ <b>තත්ත්වය:</b> PikPak සම්බන්ධ කර නොමැත.\n\n"
                "10GB+ විශාල Torrents බාගත කරගැනීමට PikPak සම්බන්ධ කරන්න:\n"
                "<code>/pikpak login &lt;email&gt; &lt;password&gt;</code>\n\n"
                "💡 <i>නැතහොත් Render Dashboard එකේ PIKPAK_USER සහ PIKPAK_PASS ලබා දෙන්න.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        try:
            quota = await pikpak_service.get_quota_summary()
            await message.reply_text(
                f"☁️ <b>PikPak Cloud Debrid තත්ත්වය</b>\n\n"
                f"✅ <b>තත්ත්වය:</b> සම්බන්ධ වී ඇත (Active & Ready)\n"
                f"👤 <b>User:</b> <code>{quota['username']}</code>\n"
                f"📊 <b>මුළු ධාරිතාව:</b> {quota['limit_gb']} GB\n"
                f"📦 <b>භාවිත කළ ඉඩ:</b> {quota['usage_gb']} GB\n"
                f"🆓 <b>නිදහස් ඉඩ:</b> {quota['free_gb']} GB\n\n"
                f"<b>විධාන (Commands):</b>\n"
                f"• <code>/pikpak clear</code> — Cloud storage එක 100% පිරිසිදු කිරීම\n"
                f"• <code>/pikpak login &lt;user&gt; &lt;pass&gt;</code> — වෙනත් ගිණුමක් මාරු කිරීම",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            log.error("[PikPakHandler] Could not get quota: %s", exc)
            await message.reply_text(
                f"☁️ <b>PikPak Cloud Debrid තත්ත්වය</b>\n\n"
                f"⚠️ Credentials සකසා ඇතත් සම්බන්ධ වීමේ ගැටලුවක් පවතී: <code>{exc}</code>\n\n"
                f"නැවත login වීමට: <code>/pikpak login &lt;email&gt; &lt;password&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
