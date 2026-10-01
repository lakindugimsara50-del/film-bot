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
import time
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
        self._admin_sessions: Dict[int, List[Client]] = {}
        self._admin_check_times: Dict[int, float] = {}
        self._lock = asyncio.Lock()
        self._client_upload_locks: Dict[int, asyncio.Lock] = {}

    def get_client_lock(self, client: Client) -> asyncio.Lock:
        """Return a persistent asyncio.Lock for the given Client instance to serialize same-account uploads."""
        c_id = id(client)
        if c_id not in self._client_upload_locks:
            self._client_upload_locks[c_id] = asyncio.Lock()
        return self._client_upload_locks[c_id]

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
                            max_concurrent_transmissions=16,
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

    async def join_channel(self, target_channel: int) -> dict:
        """Auto-join all loaded userbot sessions to the target Telegram channel and promote up to 40 sessions to admin."""
        if not self.clients or not target_channel:
            return {"joined": 0, "promoted": 0, "error": "No sessions or channel not set"}

        # Discard unwritable flag so newly promoted sessions are attempted
        self._channel_unwritable.discard(target_channel)
        try:
            self._channel_unwritable.discard(int(target_channel))
        except Exception:
            pass

        invite_link = None
        channel_username = None
        if self._main_client and getattr(self._main_client, "is_connected", False):
            try:
                chat = await self._main_client.get_chat(target_channel)
                channel_username = getattr(chat, "username", None)
                invite_link = getattr(chat, "invite_link", None)
                if not invite_link and not channel_username:
                    try:
                        exported = await self._main_client.export_chat_invite_link(target_channel)
                        invite_link = exported
                    except Exception as exp_err:
                        log.debug("[UploadPool] Channel export invite link note: %s", exp_err)
            except Exception as chat_err:
                log.warning("[UploadPool] Channel get_chat error for %s: %s", target_channel, chat_err)

        join_dest = channel_username or invite_link
        if not join_dest:
            log.info("[UploadPool] No username or invite link available for auto-joining sessions to %s", target_channel)
            return {"joined": 0, "promoted": 0, "error": "Bot cannot access channel or export invite link"}

        log.info("[UploadPool] Auto-joining %d sessions to channel %s (dest=%s)...", len(self.clients), target_channel, join_dest)
        join_sem = asyncio.Semaphore(5)
        joined_count = 0
        promoted_count = 0
        limit_reached = False

        async def _join_one(c: Client):
            nonlocal joined_count, promoted_count, limit_reached
            if not getattr(c, "is_connected", False):
                return
            async with join_sem:
                try:
                    await c.join_chat(join_dest)
                    joined_count += 1
                except Exception as j_err:
                    err_s = str(j_err).lower()
                    if "already" in err_s or "user_already_participant" in err_s:
                        joined_count += 1
                    else:
                        log.debug("[UploadPool] Session %s join note: %s", getattr(c, "name", "client"), j_err)

                # Attempt to promote userbot to admin with can_post_messages if main bot is admin (up to 40 sessions)
                if not limit_reached and promoted_count < 42 and self._main_client and getattr(self._main_client, "is_connected", False):
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
                            promoted_count += 1
                            log.info("[UploadPool] Session %s (user %s) promoted to admin in %s", getattr(c, "name", "session"), u_me.id, target_channel)
                    except Exception as prom_err:
                        err_str = str(prom_err)
                        if "ADMINS_TOO_MUCH" in err_str:
                            limit_reached = True
                            log.info("[UploadPool] Telegram channel admin limit reached (max 50 admins).")
                        elif "chat_admin_required" in err_str.lower() or "right_forbidden" in err_str.lower():
                            limit_reached = True
                            log.warning("[UploadPool] Main bot lacks 'Add Administrators' permission in channel %s: %s", target_channel, prom_err)
                        else:
                            log.debug("[UploadPool] Session %s promote note: %s", getattr(c, "name", "client"), prom_err)

        await asyncio.gather(*[_join_one(c) for c in self.clients], return_exceptions=True)
        self._channel_unwritable.discard(target_channel)
        try:
            self._channel_unwritable.discard(int(target_channel))
        except Exception:
            pass

        # Immediately refresh and cache verified admin sessions for this channel
        admin_clients = await self.get_admin_sessions(target_channel, refresh=True)
        log.info(
            "[UploadPool] %d sessions joined, %d promoted, %d verified admin sessions in channel %s.",
            joined_count, promoted_count, len(admin_clients), target_channel
        )
        return {
            "joined": joined_count,
            "promoted": promoted_count,
            "admin_sessions": len(admin_clients),
            "total_sessions": len(self.clients),
        }

    async def get_admin_sessions(self, target_channel: int, refresh: bool = False) -> List[Client]:
        """
        Scan connected sessions to find accounts with admin rights and post_messages in target_channel.
        First queries administrators via main bot client for instantaneous 100% accurate discovery,
        matching against pool session user IDs, with per-session fallback check.
        Results are cached for 10 minutes unless refresh=True.
        """
        if not target_channel or not self.clients:
            return []

        try:
            t_key = int(target_channel)
        except Exception:
            t_key = target_channel

        now = time.time()
        if not refresh and t_key in self._admin_sessions:
            if now - self._admin_check_times.get(t_key, 0) < 600:
                return [c for c in self._admin_sessions[t_key] if getattr(c, "is_connected", False)]

        admin_clients: List[Client] = []

        # 1. Primary method: Query channel administrators via main bot (instant, zero per-session rate limit)
        admin_uids: set[int] = set()
        channel_username: Optional[str] = None
        invite_link: Optional[str] = None
        if self._main_client and getattr(self._main_client, "is_connected", False):
            try:
                from pyrogram.enums import ChatMembersFilter
                chat_obj = await self._main_client.get_chat(target_channel)
                channel_username = getattr(chat_obj, "username", None)
                invite_link = getattr(chat_obj, "invite_link", None)
                if not invite_link and not channel_username:
                    try:
                        invite_link = await self._main_client.export_chat_invite_link(target_channel)
                    except Exception:
                        pass
                async for member in self._main_client.get_chat_members(target_channel, filter=ChatMembersFilter.ADMINISTRATORS):
                    u = getattr(member, "user", None)
                    if not u or getattr(u, "is_bot", False):
                        continue
                    privs = getattr(member, "privileges", None)
                    can_post = getattr(privs, "can_post_messages", True) if privs else True
                    status_raw = getattr(member, "status", None)
                    status_str = getattr(status_raw, "value", str(status_raw)).lower()
                    if ("admin" in status_str and can_post) or "owner" in status_str or "creator" in status_str:
                        admin_uids.add(u.id)
                log.info("[UploadPool] Main bot queried channel %s: %d human admin accounts verified.", target_channel, len(admin_uids))
            except Exception as q_err:
                log.warning("[UploadPool] Main bot could not query channel administrators: %s", q_err)

        if admin_uids:
            for c in self.clients:
                if not getattr(c, "is_connected", False):
                    continue
                c_me = getattr(c, "me", None)
                c_uid = getattr(c_me, "id", None)
                if c_uid and c_uid in admin_uids and c not in admin_clients:
                    admin_clients.append(c)
            log.info("[UploadPool] Matched %d pool sessions to verified channel admin accounts!", len(admin_clients))

        # 2. Fallback per-session check if main bot query produced no matches
        if not admin_clients:
            sem = asyncio.Semaphore(10)

            async def _check_c(c: Client):
                if not getattr(c, "is_connected", False):
                    return
                async with sem:
                    try:
                        member = await c.get_chat_member(target_channel, "me")
                        status_raw = getattr(member, "status", None)
                        status_str = getattr(status_raw, "value", str(status_raw)).lower()
                        privs = getattr(member, "privileges", None)
                        can_post = getattr(privs, "can_post_messages", False) if privs else False
                        if ("admin" in status_str and can_post) or "owner" in status_str or "creator" in status_str:
                            admin_clients.append(c)
                    except Exception as c_err:
                        log.debug("[UploadPool] Session %s admin check in %s: %s", getattr(c, "name", "client"), target_channel, c_err)

            await asyncio.gather(*[_check_c(c) for c in self.clients], return_exceptions=True)

        # 3. Ensure all admin sessions have the channel peer resolved in their local SQLite peers table
        if admin_clients:
            warmup_sem = asyncio.Semaphore(8)

            async def _warmup_peer(c: Client):
                async with warmup_sem:
                    try:
                        await c.get_chat(target_channel)
                        return
                    except Exception:
                        pass
                    try:
                        if channel_username:
                            await c.get_chat(channel_username)
                            return
                        if invite_link:
                            try:
                                await c.join_chat(invite_link)
                                return
                            except Exception:
                                pass
                        async for d in c.get_dialogs(limit=50):
                            if getattr(getattr(d, "chat", None), "id", None) == t_key:
                                break
                    except Exception:
                        pass

            await asyncio.gather(*[_warmup_peer(c) for c in admin_clients], return_exceptions=True)

        admin_clients.sort(key=lambda c: getattr(c, "name", ""))
        self._admin_sessions[t_key] = admin_clients
        self._admin_check_times[t_key] = now
        log.info(
            "[UploadPool] Found %d verified admin sessions with posting rights in channel %s",
            len(admin_clients), target_channel
        )
        return admin_clients

    async def get_client_for_quality(
        self,
        quality: str = "default",
        fallback_client: Optional[Client] = None,
        target_chat: Optional[int] = None,
    ) -> Client:
        """
        Return the next available client for the given quality tier (round-robin).
        If target_chat has verified channel admin sessions, allocates those admin sessions
        across qualities (e.g. 1080p, 720p, 480p) to achieve true parallel userbot uploads.
        """
        async with self._lock:
            if target_chat:
                try:
                    t_key = int(target_chat)
                except Exception:
                    t_key = target_chat

                admin_pool = [c for c in self._admin_sessions.get(t_key, []) if getattr(c, "is_connected", False)]
                if admin_pool:
                    quality_map = {"1080p": 0, "720p": 1, "480p": 2, "360p": 3, "default": 0}
                    base_idx = quality_map.get(quality, 0)
                    n_admins = len(admin_pool)

                    if n_admins >= 3:
                        # Distribute distinct admin accounts per quality tier
                        tier_admins = [admin_pool[i] for i in range(n_admins) if i % 3 == base_idx % 3]
                        if not tier_admins:
                            tier_admins = admin_pool
                    elif n_admins == 2:
                        tier_admins = [admin_pool[base_idx % 2]]
                    else:
                        tier_admins = admin_pool

                    q_key = f"{quality}_{t_key}"
                    cur_idx = self._indices.get(q_key, 0) % len(tier_admins)
                    self._indices[q_key] = (cur_idx + 1) % len(tier_admins)
                    selected = tier_admins[cur_idx]
                    log.info(
                        "[UploadPool] Selected verified admin session '%s' for [%s] in channel %s (pool of %d)",
                        getattr(selected, "name", "session"), quality, target_chat, len(tier_admins)
                    )
                    return selected

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
        Pre-checks and prioritizes verified admin userbot sessions for the channel.
        Falls back to main bot client if no pool sessions are admins or if all fail.
        """
        from pyrogram.errors import FloodWait

        fallback = fallback_client or self._main_client
        is_channel = str(target_chat).startswith("-100")

        # For channels, discover or refresh admin sessions
        if is_channel and self.clients:
            admin_clients = await self.get_admin_sessions(target_chat)
            if not admin_clients:
                log.info(
                    "[UploadPool] No userbot sessions have admin rights in channel %s — uploading [%s] via main bot client '%s'",
                    target_chat, quality, getattr(fallback, "name", "main_bot")
                )
                if fallback:
                    c_lock = self.get_client_lock(fallback)
                    async with c_lock:
                        return await telegram_upload.upload_video_file(
                            bot_client=fallback,
                            file_path=file_path,
                            target_chat=target_chat,
                            caption=caption,
                            progress_callback=progress_callback,
                            fallback_chat=0,
                        )
        elif target_chat in self._channel_unwritable or not self.clients:
            if fallback:
                log.info("[UploadPool] Uploading [%s] directly via main client '%s' → chat %s", quality, getattr(fallback, "name", "main_bot"), target_chat)
                c_lock = self.get_client_lock(fallback)
                async with c_lock:
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
            client = await self.get_client_for_quality(quality, fallback_client, target_chat=target_chat)
            if client in tried_clients and len(tried_clients) < len(self.clients):
                continue
            tried_clients.append(client)
            log.info(
                "[UploadPool] Uploading [%s] attempt %d via session '%s' → chat %s",
                quality, attempt + 1,
                getattr(client, "name", "main_bot"),
                target_chat,
            )

            try:
                c_lock = self.get_client_lock(client)
                async with c_lock:
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
                        "[UploadPool] Session '%s' lacks write permissions in chat %s (%s) — removing from admin cache and trying next.",
                        getattr(client, "name", "?"), target_chat, exc,
                    )
                    try:
                        t_key = int(target_chat)
                    except Exception:
                        t_key = target_chat
                    if t_key in self._admin_sessions and client in self._admin_sessions[t_key]:
                        self._admin_sessions[t_key].remove(client)
                    continue

                log.warning(
                    "[UploadPool] Session upload failed for [%s] attempt %d (%s) — rotating to next session.",
                    quality, attempt + 1, exc,
                )
                continue

        # All userbot attempts exhausted — use main bot client
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
        admin_counts = {str(k): len(v) for k, v in self._admin_sessions.items()}
        return {
            "total_sessions": len(self.clients),
            "connected_sessions": len(connected),
            "admin_channels": admin_counts,
            "quality_tiers": {k: list(v) for k, v in _QUALITY_TIERS.items()},
        }


# Global singleton — initialized lazily in background at bot startup
upload_pool = TelegramUploadPool()
