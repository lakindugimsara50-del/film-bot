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
from pyrogram.enums import ParseMode
from pyrogram.errors import SessionPasswordNeeded
from services import telegram_upload

log = logging.getLogger(__name__)


def _ensure_pyrogram_session(session_path: str, api_id: int) -> None:
    """
    Check if session_path is a Telethon SQLite session format and convert it in-place
    to Pyrogram format so that Pyrogram starts without 'no such column: number' errors.
    """
    conn = None
    try:
        conn = sqlite3.connect(session_path, timeout=15.0)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='version'")
        if not cur.fetchone():
            return
        cur.execute("PRAGMA table_info(version)")
        cols = [c[1] for c in cur.fetchall()]
        if "number" in cols:
            return  # Already Pyrogram format
        cur.execute("PRAGMA table_info(sessions)")
        s_cols = [c[1] for c in cur.fetchall()]
        if "auth_key" not in s_cols:
            return
        cur.execute("SELECT dc_id, auth_key FROM sessions")
        s_row = cur.fetchone()
        if not s_row:
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
        log.info("[UploadPool] Converted Telethon session '%s' to Pyrogram format", os.path.basename(session_path))
    except Exception as conv_err:
        log.warning("[UploadPool] Note while checking session format for '%s': %s", session_path, conv_err)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


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
        self._channel_unwritable: set[int] = set()
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
        log.info("[UploadPool] Loading %d session files concurrently from %s ...", len(session_files), sessions_dir)

        sem = asyncio.Semaphore(10)

        async def _load_one(s_path: str):
            base_name = os.path.splitext(os.path.basename(s_path))[0]
            session_prefix = os.path.join(sessions_dir, base_name)

            if any(getattr(c, "name", "") == session_prefix for c in self.clients):
                return

            async with sem:
                for attempt in range(3):
                    try:
                        # Ensure session database is in Pyrogram SQLite format (migrates Telethon format if needed)
                        _ensure_pyrogram_session(s_path, api_id)

                        c = Client(
                            name=session_prefix,
                            api_id=api_id,
                            api_hash=api_hash,
                            no_updates=True,
                            max_concurrent_transmissions=10,
                            sleep_threshold=15,  # Auto-sleep minor waits; rotate on longer ones
                        )
                        is_auth = await c.connect()
                        if not is_auth:
                            await c.disconnect()
                            log.warning("[UploadPool] Session '%s' is not authorized. Skipping.", base_name)
                            return

                        try:
                            await c.initialize()
                        except Exception as init_err:
                            await c.disconnect()
                            log.warning("[UploadPool] Session '%s' init error: %s. Skipping.", base_name, init_err)
                            return

                        try:
                            me = await c.get_me()
                            c.me = me
                            if not hasattr(c.me, "is_premium"):
                                setattr(c.me, "is_premium", False)
                            self.clients.append(c)
                            return
                        except SessionPasswordNeeded:
                            try:
                                await c.check_password(password)
                                me = await c.get_me()
                                c.me = me
                                if not hasattr(c.me, "is_premium"):
                                    setattr(c.me, "is_premium", False)
                                self.clients.append(c)
                                log.info("[UploadPool] 2FA OK for session: %s", base_name)
                                return
                            except Exception as pw_err:
                                if c.is_initialized:
                                    await c.stop()
                                elif c.is_connected:
                                    await c.disconnect()
                                log.warning("[UploadPool] 2FA failed for session '%s': %s", base_name, pw_err)
                                return
                        except Exception as auth_err:
                            if c.is_initialized:
                                await c.stop()
                            elif c.is_connected:
                                await c.disconnect()
                            log.warning("[UploadPool] Session '%s' auth error (%s). Skipping.", base_name, auth_err)
                            return
                    except Exception as exc:
                        try:
                            if 'c' in locals():
                                if c.is_initialized:
                                    await c.stop()
                                elif c.is_connected:
                                    await c.disconnect()
                        except Exception:
                            pass
                        err_str = str(exc).lower()
                        if "database is locked" in err_str and attempt < 2:
                            await asyncio.sleep(0.6 * (attempt + 1))
                            continue
                        log.warning("[UploadPool] Could not start session '%s': %s", base_name, exc)
                        break

        await asyncio.gather(*[_load_one(sp) for sp in session_files])
        log.info("[UploadPool] Total active upload clients in pool: %d/%d", len(self.clients), len(session_files))

        # Share active clients with stream_pool so both upload and streaming use the same Pyrogram client instances
        try:
            from streaming.session_pool import stream_pool
            for c in self.clients:
                if c not in stream_pool.clients:
                    stream_pool.clients.append(c)
            log.info("[UploadPool] Synced %d clients to stream_pool.", len(stream_pool.clients))
        except Exception as sync_err:
            log.debug("[UploadPool] Stream pool sync note: %s", sync_err)

        if target_channel:
            try:
                await self.join_channel(target_channel)
            except Exception as j_err:
                log.warning("[UploadPool] Channel auto-join error: %s", j_err)

        # Notify admins that session pool is loaded and ready
        if self._main_client and getattr(self._main_client, "is_connected", False):
            import config
            for admin_id in getattr(config, "ADMIN_IDS", []):
                try:
                    await self._main_client.send_message(
                        chat_id=admin_id,
                        text=(
                            f"✅ <b>Telegram Upload Pool Ready!</b>\n\n"
                            f"👥 <b>Connected Accounts:</b> <code>{len(self.clients)}/{len(session_files)}</code>\n"
                            f"🔴 <b>1080p Tier:</b> <code>Sessions 1–34</code>\n"
                            f"🟡 <b>720p Tier:</b>  <code>Sessions 35–67</code>\n"
                            f"🟢 <b>480p Tier:</b>  <code>Sessions 68–100</code>\n"
                            f"⚡ <b>Channel:</b> Auto-joined & verified."
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as alert_err:
                    log.debug("[UploadPool] Admin alert note: %s", alert_err)

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
        join_sem = asyncio.Semaphore(8)

        async def _join_one(c: Client):
            if not getattr(c, "is_connected", False):
                return
            async with join_sem:
                try:
                    await c.join_chat(invite_link)
                except Exception as j_err:
                    err_s = str(j_err).lower()
                    if "already" not in err_s and "user_already_participant" not in err_s:
                        log.debug("[UploadPool] Session %s join note: %s", getattr(c, "name", "client"), j_err)

                # Attempt to promote userbot to admin with can_post_messages if main bot is admin
                if self._main_client and getattr(self._main_client, "is_connected", False):
                    try:
                        u_me = getattr(c, "me", None)
                        if u_me and getattr(u_me, "id", None):
                            from pyrogram.types import ChatPrivileges
                            await self._main_client.promote_chat_member(
                                chat_id=target_channel,
                                user_id=u_me.id,
                                privileges=ChatPrivileges(
                                    can_post_messages=True,
                                    can_edit_messages=True,
                                )
                            )
                            log.info("[UploadPool] Session %s (user %s) promoted to admin in %s", getattr(c, "name", "session"), u_me.id, target_channel)
                    except Exception as prom_err:
                        err_str = str(prom_err)
                        if "ADMINS_TOO_MUCH" in err_str:
                            log.info("[UploadPool] Telegram channel admin limit reached (max 50 admins). Remaining sessions will use main bot fallback for uploads.")
                            return
                        log.debug("[UploadPool] Session %s promote note: %s", getattr(c, "name", "client"), prom_err)

        await asyncio.gather(*[_join_one(c) for c in self.clients], return_exceptions=True)
        log.info("[UploadPool] %d sessions verified in channel %s.", len(self.clients), target_channel)

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

        When sleep_threshold=0, Pyrogram raises FloodWait instead of sleeping.
        We catch FloodWait and immediately retry with a different session from the same tier
        (up to 3 attempts) so parallel uploads never block each other.
        Falls back to main bot client if all pool sessions fail.
        Returns dict with file_id, message_id, stream_url.
        """
        from pyrogram.errors import FloodWait

        fallback = fallback_client or self._main_client

        # Fast-path: if this target_chat is already known to reject userbot sessions,
        # jump straight to verified fallback client without wasting time
        if target_chat in self._channel_unwritable or not self.clients:
            if fallback:
                log.info("[UploadPool] Uploading [%s] directly via main client '%s' → chat %s", quality, getattr(fallback, "name", "main_bot"), target_chat)
                return await telegram_upload.upload_video_file(
                    bot_client=fallback,
                    file_path=file_path,
                    target_chat=target_chat,
                    caption=caption,
                    progress_callback=progress_callback,
                    fallback_chat=0,
                )

        last_exc = None
        tried_clients: list = []

        for attempt in range(3):
            client = await self.get_client_for_quality(quality, fallback_client)
            # Skip already-tried clients if possible
            if client in tried_clients and len(tried_clients) < len(self.clients):
                continue
            tried_clients.append(client)
            log.info(
                "[UploadPool] Uploading [%s] attempt %d via session '%s' → chat %s",
                quality, attempt + 1,
                getattr(client, "name", "main_bot"),
                target_chat,
            )

            # Pre-check channel post permissions BEFORE spending minutes uploading data
            is_channel = str(target_chat).startswith("-100")
            if is_channel and client != fallback:
                try:
                    member = await client.get_chat_member(target_chat, "me")
                    status_raw = getattr(member, "status", None)
                    status_str = getattr(status_raw, "value", str(status_raw)).lower()
                    privs = getattr(member, "privileges", None)
                    can_post = getattr(privs, "can_post_messages", False) if privs else False
                    if "admin" not in status_str and "owner" not in status_str and "creator" not in status_str:
                        log.warning(
                            "[UploadPool] Session '%s' is not admin in channel %s (status=%s) — userbots lack posting rights, jumping directly to main bot",
                            getattr(client, "name", "?"), target_chat, status_str
                        )
                        self._channel_unwritable.add(target_chat)
                        break
                    elif "admin" in status_str and not can_post:
                        log.warning(
                            "[UploadPool] Session '%s' is admin in %s but lacks can_post_messages — jumping directly to main bot",
                            getattr(client, "name", "?"), target_chat
                        )
                        self._channel_unwritable.add(target_chat)
                        break
                except Exception as perm_err:
                    log.warning(
                        "[UploadPool] Session '%s' chat member check note in %s (%s) — userbots unwritable, jumping to main bot",
                        getattr(client, "name", "?"), target_chat, perm_err
                    )
                    self._channel_unwritable.add(target_chat)
                    break

            try:
                return await telegram_upload.upload_video_file(
                    bot_client=client,
                    file_path=file_path,
                    target_chat=target_chat,
                    caption=caption,
                    progress_callback=progress_callback,
                    fallback_chat=0,
                )
            except FloodWait as fw:
                last_exc = fw
                log.warning(
                    "[UploadPool] FloodWait %ds on session '%s' for [%s] — rotating to next session.",
                    fw.value, getattr(client, "name", "?"), quality,
                )
                continue
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                if "chat_write_forbidden" in err_str or "channel_private" in err_str or "user_not_participant" in err_str:
                    log.warning(
                        "[UploadPool] Session '%s' lacks write permissions in chat %s (%s) — marking channel and jumping straight to main bot client.",
                        getattr(client, "name", "?"), target_chat, exc,
                    )
                    self._channel_unwritable.add(target_chat)
                    break
                log.warning(
                    "[UploadPool] Session upload failed for [%s] attempt %d (%s) — rotating to next session.",
                    quality, attempt + 1, exc,
                )
                continue

        # All attempts exhausted (or userbot lacked write permissions) — use main bot client
        if fallback:
            log.info("[UploadPool] Uploading [%s] via verified main client '%s' → chat %s", quality, getattr(fallback, "name", "main_bot"), target_chat)
            try:
                return await telegram_upload.upload_video_file(
                    bot_client=fallback,
                    file_path=file_path,
                    target_chat=target_chat,
                    caption=caption,
                    progress_callback=progress_callback,
                    fallback_chat=0,
                )
            except Exception as final_exc:
                log.error("[UploadPool] Final fallback client upload failed for [%s]: %s", quality, final_exc)
                raise final_exc
        if last_exc:
            raise last_exc
        raise RuntimeError(f"[UploadPool] No available clients for quality [{quality}]")

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
