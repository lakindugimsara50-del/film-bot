"""
admin_control.py — Admin commands for bot management and AI assistance.

Commands:
    /1, /restart, /update — Pull latest GitHub code and restart the bot process on Colab/VPS.
    /gr <query>           — Query Gemini AI assistant or system status/logs from Telegram.
    /setkey <api_key>     — Set or update the GEMINI_API_KEY directly from Telegram.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Optional

import httpx
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config

log = logging.getLogger(__name__)

RESTART_INFO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "restart_info.json"
)


def _is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def get_repo_dir() -> str:
    """Return the git repository root directory."""
    if os.path.exists("/content/film_web_site"):
        return "/content/film_web_site"
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def check_and_notify_restart(client: Client) -> None:
    """Check if the bot was restarted via /1 and notify the admin of success."""
    if not os.path.exists(RESTART_INFO_FILE):
        return
    try:
        with open(RESTART_INFO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.remove(RESTART_INFO_FILE)

        chat_id = data.get("chat_id")
        msg_id = data.get("message_id")
        if not chat_id:
            return

        # Get latest commit info
        repo_dir = get_repo_dir()
        try:
            c_res = subprocess.run(
                ["git", "-C", repo_dir, "log", "-1", "--oneline"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            commit_str = c_res.stdout.strip() or "Latest"
        except Exception:
            commit_str = "Latest"

        success_text = (
            f"✅ <b>Bot සාර්ථකව Restart විය! නවතම GitHub Code ක්‍රියාත්මකයි.</b>\n\n"
            f"🔖 <b>Commit:</b> <code>{commit_str}</code>\n"
            f"⚡ <b>තත්ත්වය:</b> සක්‍රීයයි (Active & Ready)\n"
            f"💡 විස්තර බැලීමට: <code>/status</code> | AI සහයක: <code>/gr</code>"
        )

        if msg_id:
            try:
                await client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=success_text,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception:
                pass

        await client.send_message(chat_id=chat_id, text=success_text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        log.warning("[AdminControl] check_and_notify_restart note: %s", exc)


def register(app: Client) -> None:
    """Register admin control handlers (/1, /restart, /update, /gr, /setkey)."""

    @app.on_message(filters.command(["1", "restart", "update"]) & filters.private)
    async def restart_command_handler(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not _is_admin(user_id):
            return

        status_msg = await message.reply_text(
            "🔄 <b>GitHub වෙතින් නවතම Code ලබාගනිමින් පවතී...</b>",
            parse_mode=ParseMode.HTML,
        )

        repo_dir = get_repo_dir()
        log.info("[AdminControl] Admin %d requested restart via /1. Repo dir: %s", user_id, repo_dir)

        # 1. Git pull latest changes
        pull_output = ""
        try:
            subprocess.run(["git", "-C", repo_dir, "checkout", "--", "."], capture_output=True, timeout=15)
            p_res = subprocess.run(
                ["git", "-C", repo_dir, "pull", "origin", "main"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            pull_output = p_res.stdout.strip() or p_res.stderr.strip() or "Up to date."
        except Exception as git_err:
            pull_output = f"Git pull error: {git_err}"
            log.error("[AdminControl] Git pull failed: %s", git_err)

        # 2. Re-apply Colab limits patch if running in /content
        if os.path.exists("/content"):
            try:
                for _f, _p, _r in [
                    (f"{repo_dir}/bot/services/scrapers/torrent_finder.py", r"MAX_FILE_SIZE_BYTES\s*=\s*int\([^)]+\)", "MAX_FILE_SIZE_BYTES = int(4 * 1024 * 1024 * 1024)"),
                    (f"{repo_dir}/bot/services/downloader.py", r"1\.85\s*\*\s*1024\s*\*\s*1024\s*\*\s*1024", "4 * 1024 * 1024 * 1024"),
                    (f"{repo_dir}/bot/services/leech_service.py", r"1\.85\s*\*\s*1024\s*\*\s*1024\s*\*\s*1024", "4 * 1024 * 1024 * 1024"),
                ]:
                    if os.path.exists(_f):
                        _c = open(_f, encoding="utf-8").read()
                        _cn = re.sub(_p, _r, _c)
                        if _c != _cn:
                            open(_f, "w", encoding="utf-8").write(_cn)
            except Exception as patch_err:
                log.debug("[AdminControl] Colab patch note: %s", patch_err)

        # 3. Save restart notification info
        try:
            os.makedirs(os.path.dirname(RESTART_INFO_FILE), exist_ok=True)
            with open(RESTART_INFO_FILE, "w", encoding="utf-8") as f:
                json.dump({"chat_id": message.chat.id, "message_id": status_msg.id}, f)
        except Exception as save_err:
            log.warning("[AdminControl] Could not save restart info: %s", save_err)

        # 4. Notify admin of impending restart
        try:
            await status_msg.edit_text(
                f"✅ <b>Git Pull සාර්ථකයි!</b>\n\n"
                f"<pre>{pull_output[:350]}</pre>\n\n"
                f"🚀 <b>Bot දැන් නැවත ආරම්භ වේ (Restarting)...</b>\n"
                f"<i>තත්පර කිහිපයකින් මෙම පණිවිඩය කොළ පැහැයට හැරෙනු ඇත.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        # 5. Clean up SQLite journals so there are no locked database errors
        bot_dir = os.path.join(repo_dir, "bot") if os.path.exists(os.path.join(repo_dir, "bot")) else os.getcwd()
        for root, _, files in os.walk(bot_dir):
            for file in files:
                if file.endswith(("-journal", "-shm", "-wal")):
                    try:
                        os.remove(os.path.join(root, file))
                    except Exception:
                        pass

        # 6. Cleanly stop client and trigger restart
        await asyncio.sleep(1.0)
        try:
            if client.is_connected:
                await client.stop()
        except Exception:
            pass

        # Re-execute process
        main_py = os.path.join(bot_dir, "main.py")
        if not os.path.exists(main_py):
            main_py = os.path.abspath(sys.argv[0])

        log.info("[AdminControl] Executing process restart: %s %s", sys.executable, main_py)
        try:
            os.execv(sys.executable, [sys.executable, main_py])
        except Exception as exec_err:
            log.warning("[AdminControl] os.execv note (%s), exiting with code 42 for runner loop", exec_err)
            sys.exit(42)

    @app.on_message(filters.command("setkey") & filters.private)
    async def set_key_handler(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not _is_admin(user_id):
            return

        parts = (message.text or "").strip().split(None, 1)
        if len(parts) < 2:
            await message.reply_text(
                "❌ <b>API Key එක ඇතුළත් කරන්න!</b>\n\n"
                "📖 <b>භාවිතය:</b> <code>/setkey &lt;YOUR_GEMINI_API_KEY&gt;</code>\n\n"
                "නොමිලේ Gemini API Key එකක් ලබාගැනීමට:\n"
                "👉 <a href='https://aistudio.google.com/app/apikey'>Google AI Studio</a>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return

        new_key = parts[1].strip()
        config.GEMINI_API_KEY = new_key

        # Update bot/.env file
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if not os.path.exists(env_path):
            env_path = os.path.join(os.getcwd(), ".env")

        try:
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            key_found = False
            for idx, line in enumerate(lines):
                if line.startswith("GEMINI_API_KEY="):
                    lines[idx] = f"GEMINI_API_KEY={new_key}\n"
                    key_found = True
                    break
            if not key_found:
                lines.append(f"\nGEMINI_API_KEY={new_key}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            await message.reply_text(
                "✅ <b>Gemini API Key සාර්ථකව සුරකින ලදී!</b>\n\n"
                "දැන් ඔබට <code>/gr &lt;ප්‍රශ්නය&gt;</code> මඟින් AI සහයකගෙන් උපදෙස් සහ උදව් ලබාගත හැක.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as err:
            await message.reply_text(
                f"⚠️ <b>Key එක මතකයේ සක්‍රීය විය, නමුත් .env ලිවීමේදී දෝෂයක්:</b> {err}",
                parse_mode=ParseMode.HTML,
            )

    @app.on_message(filters.command("gr") & filters.private)
    async def gr_command_handler(client: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not _is_admin(user_id):
            return

        cmd_text = (message.text or "").strip()
        query = re.sub(r"^/gr\s*", "", cmd_text, flags=re.IGNORECASE).strip()

        if not query:
            await message.reply_text(
                "🤖 <b>Antigravity / Gemini AI සහයක</b>\n\n"
                "<b>භාවිතය (Usage):</b>\n"
                "  • <code>/gr &lt;ප්‍රශ්නය හෝ කාර්යය&gt;</code> — AI ගෙන් උදව් සහ විසඳුම් විමසන්න\n"
                "  • <code>/gr status</code> — CPU, RAM, Disk, GPU සජීවී තත්ත්වය\n"
                "  • <code>/gr log</code> — අවසන් පද්ධති සටහන් (Last 25 logs)\n"
                "  • <code>/gr clean</code> — Temp ගොනු සහ Cache පිරිසිදු කරන්න\n"
                "  • <code>/1</code> — GitHub වෙතින් නවතම code pull කර Bot restart කරන්න",
                parse_mode=ParseMode.HTML,
            )
            return

        # Quick subcommand: /gr status
        if query.lower() == "status":
            await _handle_system_status(message)
            return

        # Quick subcommand: /gr log or /gr logs
        if query.lower() in ("log", "logs"):
            await _handle_system_logs(message)
            return

        # Quick subcommand: /gr clean
        if query.lower() == "clean":
            await _handle_system_clean(message)
            return

        # General AI query using Gemini API
        await _handle_gemini_query(message, query)


async def _handle_system_status(message: Message) -> None:
    """Show live CPU, RAM, Disk, GPU status."""
    import shutil

    # Disk usage
    total_b, used_b, free_b = shutil.disk_usage("/")
    disk_free_gb = free_b / (1024 ** 3)
    disk_total_gb = total_b / (1024 ** 3)

    # Shm (RAM disk) usage
    shm_info = ""
    if os.path.exists("/dev/shm"):
        try:
            s_tot, s_used, s_free = shutil.disk_usage("/dev/shm")
            shm_info = f"💾 <b>RAM Disk (/dev/shm):</b> {s_used / (1024**3):.1f} GB used / {s_tot / (1024**3):.1f} GB\n"
        except Exception:
            pass

    # GPU status
    gpu_info = "❌ NVIDIA GPU හමු නොවීය (CPU Mode)"
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if smi.returncode == 0 and smi.stdout.strip():
            gpu_info = f"⚡ <b>NVIDIA GPU:</b> <code>{smi.stdout.strip()}</code>"
    except Exception:
        pass

    # RAM info from /proc/meminfo if on Linux
    ram_info = ""
    if os.path.exists("/proc/meminfo"):
        try:
            mem_dict = {}
            with open("/proc/meminfo", "r") as mf:
                for line in mf:
                    parts = line.split(":")
                    if len(parts) == 2:
                        mem_dict[parts[0].strip()] = parts[1].strip()
            tot_k = int(mem_dict.get("MemTotal", "0 kB").split()[0])
            avail_k = int(mem_dict.get("MemAvailable", "0 kB").split()[0])
            used_gb = (tot_k - avail_k) / (1024 ** 2)
            tot_gb = tot_k / (1024 ** 2)
            ram_info = f"🧠 <b>System RAM:</b> {used_gb:.1f} GB / {tot_gb:.1f} GB ({avail_k / (1024**2):.1f} GB නිදහස්)\n"
        except Exception:
            pass

    tunnel_url = os.getenv("STREAM_BASE_URL", "N/A")

    await message.reply_text(
        f"📊 <b>සජීවී පද්ධති තත්ත්වය (System Telemetry):</b>\n\n"
        f"{ram_info}"
        f"{shm_info}"
        f"💽 <b>Root Disk:</b> {disk_free_gb:.1f} GB නිදහස් / {disk_total_gb:.1f} GB\n"
        f"{gpu_info}\n"
        f"🌐 <b>Stream Tunnel:</b> <code>{tunnel_url}</code>\n"
        f"⏱ <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</code>",
        parse_mode=ParseMode.HTML,
    )


async def _handle_system_logs(message: Message) -> None:
    """Fetch and display recent system logs."""
    log_text = ""
    # Check common log files
    log_candidates = [
        "bot.log",
        "/content/film_web_site/bot/bot.log",
        "/tmp/bot.log",
    ]
    for lp in log_candidates:
        if os.path.exists(lp):
            try:
                with open(lp, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                log_text = "".join(lines[-25:])
                break
            except Exception:
                pass

    if not log_text:
        log_text = "දැනට වෙනම log file එකක් හමු නොවීය. Bot සජීවීව ක්‍රියාත්මක වේ."

    await message.reply_text(
        f"📜 <b>අවසන් පද්ධති සටහන් (Recent Logs):</b>\n\n"
        f"<pre>{log_text[-3000:]}</pre>",
        parse_mode=ParseMode.HTML,
    )


async def _handle_system_clean(message: Message) -> None:
    """Clean temp storage and caches."""
    cleaned = []
    # Clean /tmp and /dev/shm of old mp4/mkv files
    for target_dir in ("/tmp", "/dev/shm"):
        if os.path.exists(target_dir):
            try:
                for item in os.listdir(target_dir):
                    if item.endswith((".mp4", ".mkv", ".aria2", ".srt", ".vtt")):
                        fp = os.path.join(target_dir, item)
                        if os.path.isfile(fp):
                            os.remove(fp)
                            cleaned.append(item)
            except Exception:
                pass

    await message.reply_text(
        f"🧹 <b>පිරිසිදු කිරීම සාර්ථකයි!</b>\n\n"
        f"ඉවත් කරන ලද తాత్కాలික ගොනු ගණන: <code>{len(cleaned)}</code>\n"
        f"RAM Disk සහ /tmp ඉඩ නිදහස් කරන ලදී.",
        parse_mode=ParseMode.HTML,
    )


async def _handle_gemini_query(message: Message, query: str) -> None:
    """Query Google Gemini API with the user's prompt."""
    gemini_key = getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        await message.reply_text(
            "🤖 <b>Gemini AI API Key සකසා නොමැත.</b>\n\n"
            "නොමිලේ Gemini API Key එකක් ලබාගෙන සකසන්න:\n"
            "1. 👉 <a href='https://aistudio.google.com/app/apikey'>Google AI Studio</a> වෙතින් Key එකක් ගන්න.\n"
            "2. Bot වෙත මෙසේ එවන්න:\n"
            "   <code>/setkey &lt;YOUR_GEMINI_KEY&gt;</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    wait_msg = await message.reply_text("🤔 <i>Antigravity AI සිතමින් පවතී...</i>", parse_mode=ParseMode.HTML)

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": (
                        "You are Antigravity AI, the intelligent pair programming and operational assistant for FilmBot "
                        "and filmsub.pages.dev. You help the admin manage the Telegram bot, download torrents, troubleshoot "
                        "Colab/GPU issues, video conversion, and subtitle muxing. Be concise, direct, and helpful. "
                        "You can respond in Sinhala or English depending on the user's language."
                    )
                }
            ]
        },
        "contents": [
            {
                "parts": [{"text": query}]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    reply_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if reply_text:
                        # Format for Telegram HTML safety
                        safe_reply = (
                            reply_text.replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        )
                        # Re-allow basic tags or send as formatted text
                        await wait_msg.edit_text(
                            f"🤖 <b>Antigravity AI:</b>\n\n{safe_reply[:4000]}",
                            parse_mode=ParseMode.HTML,
                        )
                        return
            elif resp.status_code == 404:
                # Fallback to gemini-1.5-flash if 2.5 is not accessible
                endpoint_fallback = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
                resp2 = await client.post(endpoint_fallback, json=payload)
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    candidates2 = data2.get("candidates", [])
                    if candidates2:
                        reply_text2 = candidates2[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        safe_reply2 = reply_text2.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        await wait_msg.edit_text(
                            f"🤖 <b>Antigravity AI:</b>\n\n{safe_reply2[:4000]}",
                            parse_mode=ParseMode.HTML,
                        )
                        return

            await wait_msg.edit_text(
                f"⚠️ <b>Gemini API ප්‍රතිචාර දැක්වීම අසාර්ථකයි (HTTP {resp.status_code}):</b>\n<code>{resp.text[:300]}</code>",
                parse_mode=ParseMode.HTML,
            )
    except Exception as exc:
        log.warning("[AdminControl] Gemini API error: %s", exc)
        await wait_msg.edit_text(
            f"❌ <b>AI විමසුමේදී දෝෂයක් සිදු විය:</b>\n<code>{str(exc)[:300]}</code>",
            parse_mode=ParseMode.HTML,
        )
