"""
upload_pool.py — Multi-session parallel Telegram upload pool.

Loads up to 100 Pyrogram userbot session files from bot/sessions/*.session
and distributes 1080p/720p/480p video uploads across different sessions
for maximum parallel upload speed — similar to CineSubz multi-account upload.

Features:
- Background initialization: sessions start in asyncio.create_task() so bot startup is instant
- 2FA support: handles SessionPasswordNeeded via SESSION_2FA_PASSWORD env var (default: 2122138)
- Quality-to-session mapping: each quality gets a dedicated session subset
  · Sessions  0-33 → 1080p uploads
  · Sessions 34-66 → 720p uploads
  · Sessions 67-99 → 480p / 360p uploads
- Smart fallback: if pool is empty / all disconnected, falls back to main bot client
- Thread-safe round-robin via asyncio.Lock
"""

import asyncio
import glob
import logging
import os
import sqlite3
from typing import Dict, List, Optional

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
from services import telegram_upload

log = logging.getLogger(__name__)


def _ensure_pyrogram_session(session_path: str, api_id: int) -> None:
    """
    Check if session_path is a Telethon SQLite session format and convert it in-place
    to Pyrogram format so that Pyrogram starts without 'no such column: number' errors.
    """
    try:
        conn = sqlite3.connect(session_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='version'")
        if not cur.fetchone():
            conn.close()
            return
        cur.execute("PRAGMA table_info(version)")
        cols = [c[1] for c in cur.fetchall()]
        if "number" in cols:
            conn.close()
            return  # Already Pyrogram format
        cur.execute("PRAGMA table_info(sessions)")
        s_cols = [c[1] for c in cur.fetchall()]
        if "auth_key" not in s_cols:
            conn.close()
            return
        cur.execute("SELECT dc_id, auth_key FROM sessions")
        s_row = cur.fetchone()
        if not s_row:
            conn.close()
            return
        dc_id, auth_key = s_row[0], s_row[1]
        user_id = 0
        access_hash = 0
        username = None
        phone = None
        try:
            cur.execute("SELECT id, hash, username, phone FROM entities WHERE id != 0 LIMIT 1")
            ent = cur.fetchone()
            if ent:
                user_id, access_hash, username, phone = ent[0], ent[1], ent[2], ent[3]
        except Exception:
            pass
        cur.execute("DROP TABLE IF EXISTS sessions")
        cur.execute("DROP TABLE IF EXISTS entities")
        cur.execute("DROP TABLE IF EXISTS sent_files")
        cur.execute("DROP TABLE IF EXISTS update_state")
        cur.execute("DROP TABLE IF EXISTS version")
        cur.executescript("""
        CREATE TABLE sessions (
            dc_id INTEGER PRIMARY KEY,
            api_id INTEGER,
            test_mode INTEGER,
            auth_key BLOB,
            date INTEGER NOT NULL,
            user_id INTEGER,
            is_bot INTEGER
        );
        CREATE TABLE peers (
            id INTEGER PRIMARY KEY,
            access_hash INTEGER,
            type INTEGER NOT NULL,
            username TEXT,
            phone_number TEXT,
            last_update_on INTEGER NOT NULL DEFAULT (CAST(STRFTIME('%s', 'now') AS INTEGER))
        );
        CREATE TABLE version (
            number INTEGER PRIMARY KEY
        );
        """)
        cur.execute("INSERT INTO version VALUES (?)", (3,))
        cur.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dc_id, api_id, 0, auth_key, 0, user_id, 0)
        )
        if user_id:
            cur.execute(
                "INSERT INTO peers (id, access_hash, type, username, phone_number) VALUES (?, ?, ?, ?, ?)",
                (user_id, access_hash, 1, username, str(phone) if phone else None)
            )
        conn.commit()
        conn.close()
        log.info("[UploadPool] Converted Telethon session '%s' to Pyrogram format", os.path.basename(session_path))
    except Exception as conv_err:
        log.warning("[UploadPool] Note while checking session format for '%s': %s", session_path, conv_err)


# Quality tier → (start_index, end_index) within the sorted session list (inclusive)
_QUALITY_TIERS: Dict[str, tuple] = {
    "1080p":   (0,  33),
    "720p":    (34, 66),
    "480p":    (67, 99),
    "360p":    (67, 99),   # shares pool with 480p
    "default": (0,  99),
}


class TelegramUploadPool:
    """Manages up to 100 userbot sessions for parallel multi-quality Telegram uploads."""

    def __init__(self) -> None:
        self.clients: List[Client] = []
        self._main_client: Optional[Client] = None
        # Per-quality round-robin index — starts at each tier's start_idx
        self._indices: Dict[str, int] = {k: v[0] for k, v in _QUALITY_TIERS.items()}
        self._lock = asyncio.Lock()

    def set_main_client(self, client: Client) -> None:
        """Register the primary bot client as upload fallback."""
        self._main_client = client

    async def init(
        self,
        api_id: int,
        api_hash: str,
        sessions_dir: str = None,
        target_channel: Optional[int] = None,
    ) -> None:
        """
        Load all .session files from sessions_dir and authenticate each.

        Uses c.start() — the correct Pyrogram high-level startup that handles
        session authorization, reconnection, and 2FA prompts correctly.
        c.connect() is a low-level internal method and must NOT be used here.
        """
        # Auto-detect sessions_dir as an absolute path so it works on Colab
        # regardless of the current working directory.
        if sessions_dir is None:
            _services_dir = os.path.dirname(os.path.abspath(__file__))
            _bot_dir = os.path.dirname(_services_dir)
            sessions_dir = os.path.join(_bot_dir, "sessions")

        if not os.path.exists(sessions_dir):
            try:
                os.makedirs(sessions_dir, exist_ok=True)
            except Exception:
                pass

        session_files = sorted(glob.glob(os.path.join(sessions_dir, "*.session")))
        if not session_files:
            log.warning("[UploadPool] No .session files found in %s — pool will use main bot client.", sessions_dir)
            return

        password = os.getenv("SESSION_2FA_PASSWORD", "2122138")
        log.info("[UploadPool] Loading %d session files from %s ...", len(session_files), sessions_dir)

        for idx, s_path in enumerate(session_files):
            base_name = os.path.splitext(os.path.basename(s_path))[0]
            session_prefix = os.path.join(sessions_dir, base_name)

            # Skip if already loaded
            if any(getattr(c, "name", "") == session_prefix for c in self.clients):
                continue

            try:
                # Ensure session database is in Pyrogram SQLite format (migrates Telethon format if needed)
                _ensure_pyrogram_session(s_path, api_id)

                c = Client(
                    name=session_prefix,
                    api_id=api_id,
                    api_hash=api_hash,
                    no_updates=True,
                    max_concurrent_transmissions=10,
                )
                await c.start()
                self.clients.append(c)
                if (idx + 1) % 10 == 0 or idx == 0:
                    log.info("[UploadPool] Loaded %d/%d upload sessions...", len(self.clients), len(session_files))
            except SessionPasswordNeeded:
                # Session requires 2FA cloud password — provide it
                try:
                    await c.check_password(password)
                    self.clients.append(c)
                    log.info("[UploadPool] 2FA OK for session: %s", base_name)
                except Exception as pw_err:
                    log.warning("[UploadPool] 2FA failed for session '%s': %s", base_name, pw_err)
            except Exception as exc:
                log.warning("[UploadPool] Could not start session '%s': %s", base_name, exc)

        log.info("[UploadPool] Total active upload clients in pool: %d", len(self.clients))

        if target_channel:
            try:
                await self.join_channel(target_channel)
            except Exception as j_err:
                log.warning("[UploadPool] Channel auto-join error: %s", j_err)

    async def join_channel(self, target_channel: int) -> None:
        """Auto-join all loaded userbot sessions to the target Telegram channel so they can post."""
        if not self.clients or not target_channel:
            return

        invite_link = None
        if self._main_client and getattr(self._main_client, "is_connected", False):
            try:
                chat = await self._main_client.get_chat(target_channel)
                invite_link = getattr(chat, "invite_link", None)
                if not invite_link:
                    exported = await self._main_client.export_chat_invite_link(target_channel)
                    invite_link = exported
            except Exception as exp_err:
                log.debug("[UploadPool] Channel invite link note: %s", exp_err)

        if not invite_link:
            log.info("[UploadPool] No invite link available for auto-joining sessions to %s", target_channel)
            return

        log.info("[UploadPool] Auto-joining %d sessions to channel %s...", len(self.clients), target_channel)
        joined = 0
        for c in self.clients:
            if not getattr(c, "is_connected", False):
                continue
            try:
                await c.join_chat(invite_link)
                joined += 1
            except Exception as j_err:
                err_s = str(j_err).lower()
                if "already" in err_s or "user_already_participant" in err_s:
                    joined += 1
                else:
                    log.debug("[UploadPool] Session %s join note: %s", getattr(c, "name", "client"), j_err)

        log.info("[UploadPool] %d/%d sessions verified in channel %s.", joined, len(self.clients), target_channel)

    async def get_client_for_quality(
        self,
        quality: str = "default",
        fallback_client: Optional[Client] = None,
    ) -> Client:
        """Return the next available client for the given quality tier (round-robin)."""
        async with self._lock:
            if not self.clients:
                return fallback_client or self._main_client or (_ for _ in ()).throw(
                    RuntimeError("No active Telegram clients in upload pool.")
                )

            tier_start, tier_end = _QUALITY_TIERS.get(quality, _QUALITY_TIERS["default"])

            # Clamp to actual number of loaded sessions
            tier_start = min(tier_start, len(self.clients) - 1)
            tier_end = min(tier_end, len(self.clients) - 1)

            tier_clients = self.clients[tier_start : tier_end + 1]
            if not tier_clients:
                tier_clients = self.clients

            # Prefer connected clients within the tier
            connected = [c for c in tier_clients if getattr(c, "is_connected", False)]
            pool = connected if connected else tier_clients

            # Round-robin within the pool
            idx = self._indices.get(quality, 0) % len(pool)
            self._indices[quality] = (idx + 1) % len(pool)
            return pool[idx]

    async def upload_with_pool(
        self,
        file_path: str,
        target_chat: int,
        quality: str = "default",
        caption: str = "",
        file_name: str = "",
        progress_callback=None,
        fallback_client: Optional[Client] = None,
    ) -> dict:
        """
        Upload a video file using a session from the quality tier pool.

        Falls back to main bot client if the pool is empty or all sessions fail.
        Returns dict with file_id, message_id, stream_url.
        """
        client = await self.get_client_for_quality(quality, fallback_client)
        log.info(
            "[UploadPool] Uploading [%s] via session '%s' → chat %s",
            quality,
            getattr(client, "name", "main_bot"),
            target_chat,
        )
        try:
            return await telegram_upload.upload_video_file(
                bot_client=client,
                file_path=file_path,
                target_chat=target_chat,
                caption=caption,
                progress_callback=progress_callback,
                fallback_chat=0,
            )
        except Exception as exc:
            log.warning(
                "[UploadPool] Session upload failed for [%s]: %s — falling back to main client.",
                quality,
                exc,
            )
            fallback = fallback_client or self._main_client
            if fallback and fallback is not client:
                return await telegram_upload.upload_video_file(
                    bot_client=fallback,
                    file_path=file_path,
                    target_chat=target_chat,
                    caption=caption,
                    progress_callback=progress_callback,
                    fallback_chat=0,
                )
            raise

    def get_status(self) -> dict:
        """Return pool diagnostic info for the /status endpoint."""
        connected = [c for c in self.clients if getattr(c, "is_connected", False)]
        return {
            "total_sessions": len(self.clients),
            "connected_sessions": len(connected),
            "quality_tiers": {k: list(v) for k, v in _QUALITY_TIERS.items()},
        }


# Global singleton — initialized lazily in background at bot startup
upload_pool = TelegramUploadPool()
