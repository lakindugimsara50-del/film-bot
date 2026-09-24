"""
resume_service.py — Download checkpoint save/restore for crash-resistance on Render.

Saves download state to disk BEFORE starting large downloads.
On bot restart, detects orphaned checkpoints and notifies admins.

Why this matters:
- Render free tier restarts containers after inactivity or RAM spikes.
- Mid-download restarts lose all download state (no resume in aria2c HTTP mode).
- Checkpoints let admins know which movies were interrupted and re-trigger them.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
_CHECKPOINT_PREFIX = "leech_checkpoint_"


def _checkpoint_path(user_id: int) -> str:
    return os.path.join(DATA_DIR, f"{_CHECKPOINT_PREFIX}{user_id}.json")


def save_checkpoint(user_id: int, data: dict) -> None:
    """
    Save download checkpoint to disk before starting a large download.
    Call this right after finding a valid candidate and before download begins.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    entry = {
        "user_id": user_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    try:
        with open(_checkpoint_path(user_id), "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
        log.info("[ResumeService] Checkpoint saved for user %s: %s", user_id, data.get("display_title", "?"))
    except Exception as exc:
        log.warning("[ResumeService] Could not save checkpoint for user %s: %s", user_id, exc)


def delete_checkpoint(user_id: int) -> None:
    """Delete checkpoint after successful download + upload."""
    path = _checkpoint_path(user_id)
    if os.path.exists(path):
        try:
            os.remove(path)
            log.info("[ResumeService] Checkpoint deleted for user %s", user_id)
        except Exception as exc:
            log.warning("[ResumeService] Could not delete checkpoint for user %s: %s", user_id, exc)


def load_all_checkpoints() -> list[dict]:
    """Load all saved checkpoints — called on bot startup to detect interrupted downloads."""
    os.makedirs(DATA_DIR, exist_ok=True)
    checkpoints = []
    try:
        for fname in os.listdir(DATA_DIR):
            if fname.startswith(_CHECKPOINT_PREFIX) and fname.endswith(".json"):
                fpath = os.path.join(DATA_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        cp = json.load(f)
                    checkpoints.append(cp)
                    log.info("[ResumeService] Found orphaned checkpoint: %s", fname)
                except Exception as exc:
                    log.warning("[ResumeService] Could not read checkpoint %s: %s", fname, exc)
    except Exception as exc:
        log.warning("[ResumeService] Error scanning checkpoint dir: %s", exc)
    return checkpoints


def get_checkpoint(user_id: int) -> Optional[dict]:
    """Get checkpoint for a specific user if it exists."""
    path = _checkpoint_path(user_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


async def notify_interrupted_downloads(bot_client) -> None:
    """
    Called on bot startup. Finds any orphaned download checkpoints from previous runs
    and notifies admin(s) with a resume button.
    """
    import config
    checkpoints = load_all_checkpoints()
    if not checkpoints:
        return

    admin_ids = getattr(config, "ADMIN_IDS", []) or []
    if not admin_ids:
        log.warning("[ResumeService] No ADMIN_IDS configured — cannot notify about %d interrupted download(s).", len(checkpoints))
        return

    for cp in checkpoints:
        uid = cp.get("user_id", 0)
        title = cp.get("display_title") or cp.get("title", "Unknown Movie")
        saved_at = cp.get("saved_at", "?")
        candidate_name = cp.get("candidate_method_name", "Unknown source")
        query = cp.get("query_text", title)

        msg = (
            f"⚠️ <b>Bot Restart — Interrupted Download Detected!</b>\n\n"
            f"🎬 <b>Movie:</b> {title}\n"
            f"⚡ <b>Source:</b> {candidate_name}\n"
            f"👤 <b>Requested by User:</b> <code>{uid}</code>\n"
            f"🕐 <b>Started at:</b> {saved_at[:19].replace('T', ' ')} UTC\n\n"
            f"<i>The bot was restarted mid-download. You can re-trigger by sending:</i>\n"
            f"<code>/leech {query}</code>"
        )

        from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔄 Re-Leech: {title[:30]}", callback_data=f"releech:{uid}:{query[:50]}")],
            [InlineKeyboardButton("🗑 Dismiss", callback_data=f"dismiss_checkpoint:{uid}")],
        ])

        for admin_id in admin_ids[:3]:  # notify up to 3 admins
            try:
                from pyrogram.enums import ParseMode
                await bot_client.send_message(
                    admin_id,
                    msg,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            except Exception as exc:
                log.warning("[ResumeService] Could not notify admin %s: %s", admin_id, exc)
