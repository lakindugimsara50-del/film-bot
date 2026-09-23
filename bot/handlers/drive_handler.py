"""
drive_handler.py — Telegram bot commands for Cloud Drive Management & Health Monitoring.

Commands:
- /drives or /storage — View live status, quota, and movie count of all connected drives.
- /drives list <drive_id> — List all movies stored on a specific drive.
- /drives offline — List any inactive drives and affected movies.
- /adddrive onedrive <id> <name> <refresh_token> [client_id] [client_secret]
- /adddrive gdrive <id> <name> <refresh_token> <client_id> <client_secret>
- /rmdrive <drive_id>
"""

import logging
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
from services.auth_service import auth_service
from services.cloud_drive import drive_manager

log = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return auth_service.is_owner(user_id) or auth_service.is_authorized(user_id)


def register(app: Client) -> None:
    """Register all Cloud Drive management handlers."""

    @app.on_message(filters.command(["drives", "storage"]))
    async def drives_command(client: Client, message: Message) -> None:
        """Show status of all connected Cloud Drives or handle subcommands."""
        if not _is_admin(message.from_user.id if message.from_user else 0):
            await message.reply_text("⛔ මෙම විධානය භාවිත කළ හැක්කේ Administrators ලට පමණි.")
            return

    args = message.text.strip().split()
    subcommand = args[1].lower() if len(args) > 1 else ""

    # Subcommand: /drives offline
    if subcommand in ("offline", "lost", "inactive"):
        await _show_offline_movies(client, message)
        return

    # Subcommand: /drives list <drive_id>
    if subcommand == "list" and len(args) > 2:
        drive_id = args[2]
        await _list_drive_movies(client, message, drive_id)
        return

    # Default: Show summary of all connected drives
    wait_msg = await message.reply_text("🔍 Cloud Drive තත්ත්වයන් පරීක්ෂා කරමින් පවතී...")
    try:
        statuses = await drive_manager.get_drives_status()
        if not statuses:
            text = (
                "☁️ <b>Cloud Drive Storage තත්ත්වය</b>\n\n"
                "ℹ️ තවමත් කිසිදු Cloud Drive එකක් (OneDrive / Google Drive) සම්බන්ධ කර නොමැත.\n\n"
                "<b>නව Drive එකක් එක් කිරීමට:</b>\n"
                "• <code>/adddrive onedrive &lt;id&gt; &lt;name&gt; &lt;refresh_token&gt;</code>\n"
                "• <code>/adddrive gdrive &lt;id&gt; &lt;name&gt; &lt;refresh_token&gt; &lt;client_id&gt; &lt;client_secret&gt;</code>"
            )
            await wait_msg.edit_text(text, parse_mode=ParseMode.HTML)
            return

        lines = ["☁️ <b>සම්බන්ධිත Cloud Drive ගිණුම් තත්ත්වය</b>\n"]
        total_quota = 0.0
        total_used = 0.0

        for d in statuses:
            icon = "✅" if d["is_active"] else "❌"
            p_name = "Microsoft OneDrive" if d["provider"] == "onedrive" else "Google Drive"
            status_text = "Active (සක්‍රිය)" if d["is_active"] else f"Offline ({d.get('error', 'Error')[:40]})"

            lines.append(
                f"{icon} <b>{d['name']}</b> ({p_name})\n"
                f"   • ID: <code>{d['drive_id']}</code>\n"
                f"   • තත්ත්වය: {status_text}\n"
                f"   • මුළු ඉඩ: {d['total_gb']} GB\n"
                f"   • භාවිත කළ: {d['used_gb']} GB\n"
                f"   • නිදහස් ඉඩ: <b>{d['remaining_gb']} GB</b>\n"
                f"   • ගබඩා කළ චිත්‍රපට: <b>{d['movie_count']}</b>\n"
            )
            total_quota += d["total_gb"]
            total_used += d["used_gb"]

        lines.append(
            f"📊 <b>මුළු Cloud Storage:</b> {round(total_used, 1)} GB / {round(total_quota, 1)} GB\n\n"
            f"<b>විධාන:</b>\n"
            f"• <code>/drives list &lt;drive_id&gt;</code> — Drive එකේ ඇති චිත්‍රපට ලැයිස්තුව\n"
            f"• <code>/drives offline</code> — අක්‍රිය වූ Drive සහ බලපෑ චිත්‍රපට\n"
            f"• <code>/rmdrive &lt;drive_id&gt;</code> — Drive එකක් ඉවත් කිරීම"
        )
        await wait_msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)

    except Exception as exc:
        log.error("[DriveHandler] Error checking drives: %s", exc)
        await wait_msg.edit_text(f"❌ දෝෂයක් සිදුවිය: {exc}")


async def _show_offline_movies(client: Client, message: Message) -> None:
    """Show any movies affected by inactive/disconnected drives."""
    wait_msg = await message.reply_text("🔍 අක්‍රිය Drive පරීක්ෂා කරමින් පවතී...")
    try:
        report = await drive_manager.get_offline_movies()
        offline_count = report["total_offline_drives"]
        affected_count = report["total_affected_movies"]

        if offline_count == 0:
            text = (
                "✅ <b>සියලුම Cloud Drives සක්‍රියව පවතී!</b>\n\n"
                "කිසිදු අක්‍රිය Drive එකක් හෝ නොමැති චිත්‍රපට හමු නොවීය."
            )
            await wait_msg.edit_text(text, parse_mode=ParseMode.HTML)
            return

        lines = [
            f"⚠️ <b>අක්‍රිය Cloud Drive අනතුරු ඇඟවීම!</b>\n",
            f"❌ අක්‍රිය Drive ගණන: <b>{offline_count}</b>",
            f"🎬 බලපෑමට ලක්වූ චිත්‍රපට ගණන: <b>{affected_count}</b>\n",
        ]

        for m in report["affected_movies"][:25]:
            lines.append(f"• <b>{m.get('title', m.get('movie_slug'))}</b> (Drive: {m.get('drive_name')})")

        if affected_count > 25:
            lines.append(f"\n<i>...සහ තවත් චිත්‍රපට {affected_count - 25} ක්.</i>")

        lines.append("\n💡 <i>කරුණාකර අදාළ Drive එක නැවත Login හෝ Refresh Token එක අලුත් කරන්න.</i>")
        await wait_msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)

    except Exception as exc:
        await wait_msg.edit_text(f"❌ දෝෂයක් සිදුවිය: {exc}")


async def _list_drive_movies(client: Client, message: Message, drive_id: str) -> None:
    """List movies stored on a specific drive."""
    movies = drive_manager.get_movies_on_drive(drive_id)
    if not movies:
        await message.reply_text(f"ℹ️ <code>{drive_id}</code> මත ගබඩා කර ඇති චිත්‍රපට කිසිවක් හමු නොවීය.")
        return

    lines = [f"📁 <b>Drive: <code>{drive_id}</code> හි ගබඩා කර ඇති චිත්‍රපට ({len(movies)})</b>\n"]
    for i, m in enumerate(movies[:30], 1):
        lines.append(f"{i}. <b>{m.get('title', m.get('movie_slug'))}</b> ({round(m.get('size', 0) / (1024**2), 1)} MB)")

    if len(movies) > 30:
        lines.append(f"\n<i>...සහ තවත් චිත්‍රපට {len(movies) - 30} ක්.</i>")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


    @app.on_message(filters.command(["adddrive"]))
    async def adddrive_command(client: Client, message: Message) -> None:
        """Add a new OneDrive or Google Drive."""
        if not _is_admin(message.from_user.id if message.from_user else 0):
            await message.reply_text("⛔ මෙම විධානය භාවිත කළ හැක්කේ Administrators ලට පමණි.")
            return

        args = message.text.strip().split()
        if len(args) < 5:
            help_text = (
                "📖 <b>Cloud Drive එකක් සම්බන්ධ කරන ආකාරය</b>\n\n"
                "<b>1. OneDrive එකතු කිරීම:</b>\n"
                "<code>/adddrive onedrive &lt;id&gt; &lt;name&gt; &lt;refresh_token&gt; [client_id] [client_secret]</code>\n"
                "උදා: <code>/adddrive onedrive sab_one Sabaragamuwa_1 0.ARwA...</code>\n\n"
                "<b>2. Google Drive එකතු කිරීම:</b>\n"
                "<code>/adddrive gdrive &lt;id&gt; &lt;name&gt; &lt;refresh_token&gt; &lt;client_id&gt; &lt;client_secret&gt;</code>"
            )
            await message.reply_text(help_text, parse_mode=ParseMode.HTML)
            return

        provider = args[1].lower()
        d_id = args[2]
        d_name = args[3]
        refresh_token = args[4]
        client_id = args[5] if len(args) > 5 else None
        client_secret = args[6] if len(args) > 6 else None

        if provider not in ("onedrive", "gdrive", "googledrive"):
            await message.reply_text("❌ අසත්‍ය Provider වර්ගයකි. 'onedrive' හෝ 'gdrive' පමණක් භාවිත කරන්න.")
            return

        try:
            drive_manager.add_drive(
                drive_id=d_id,
                name=d_name,
                provider=provider,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            await message.reply_text(
                f"✅ <b>Drive සාර්ථකව එක් කරන ලදී!</b>\n\n"
                f"• ID: <code>{d_id}</code>\n"
                f"• නම: {d_name}\n"
                f"• වර්ගය: {provider.upper()}\n\n"
                f"තත්ත්වය බැලීමට <code>/drives</code> භාවිත කරන්න.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            await message.reply_text(f"❌ Drive එකතු කිරීම අසාර්ථක විය: {exc}")

    @app.on_message(filters.command(["rmdrive"]))
    async def rmdrive_command(client: Client, message: Message) -> None:
        """Remove a drive."""
        if not _is_admin(message.from_user.id if message.from_user else 0):
            await message.reply_text("⛔ Administrators only.")
            return

        args = message.text.strip().split()
        if len(args) < 2:
            await message.reply_text("භාවිතය: <code>/rmdrive &lt;drive_id&gt;</code>", parse_mode=ParseMode.HTML)
            return

        d_id = args[1]
        success = drive_manager.remove_drive(d_id)
        if success:
            await message.reply_text(f"✅ Drive <code>{d_id}</code> සාර්ථකව ඉවත් කරන ලදී.", parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(f"❌ Drive <code>{d_id}</code> සොයාගත නොහැකි විය.", parse_mode=ParseMode.HTML)

