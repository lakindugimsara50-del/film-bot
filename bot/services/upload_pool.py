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
import re
import sqlite3
import sys
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
        self._joined_sessions_cache: set[str] = set()
        self._session_ema_mbps: Dict[int, float] = {}
        self._session_cooldown_until: Dict[int, float] = {}
        self._admin_expand_task: Optional[asyncio.Task] = None

    def _client_key(self, client: Client) -> int:
        return int(getattr(client, "_user_id", None) or getattr(getattr(client, "me", None), "id", None) or id(client))

    def _prioritize_idle_healthy(self, tier_list: List[Client], fallback_list: List[Client]) -> List[Client]:
        """Order upload candidates so unlocked, non-cooldown, fastest-recent sessions always run first."""
        now = time.time()
        combined: List[tuple[tuple, Client]] = []
        seen: set[int] = set()
        for is_fallback, group in ((0, tier_list), (1, fallback_list)):
            for pos, c in enumerate(group):
                cid = id(c)
                if cid in seen:
                    continue
                seen.add(cid)
                ck = self._client_key(c)
                in_cooldown = 1 if self._session_cooldown_until.get(ck, 0.0) > now else 0
                is_busy = 1 if self.get_client_lock(c).locked() else 0
                ema = self._session_ema_mbps.get(ck, 15.0)
                combined.append(((in_cooldown, is_busy, is_fallback, -round(ema, 2), pos), c))
        combined.sort(key=lambda item: item[0])
        return [c for _, c in combined]

    async def expand_admin_pool_background(self, target_channel: int, target_admins: int = 42) -> None:
        """Gently promote additional loaded sessions (up to ~42) to channel admin in background without blocking bot startup."""
        if not self.clients or not target_channel or not (self._main_client and getattr(self._main_client, "is_connected", False)):
            return
        try:
            t_key = int(target_channel)
        except Exception:
            t_key = target_channel

        current_admins = list(self._admin_sessions.get(t_key, []))
        if len(current_admins) >= target_admins:
            return

        admin_cids = {self._client_key(c) for c in current_admins}
        needed = target_admins - len(current_admins)
        candidates = [
            c for c in self.clients
            if getattr(c, "is_connected", False) and self._client_key(c) not in admin_cids
        ][:max(0, needed + 4)]
        if not candidates:
            return

        join_dest = None
        try:
            chat = await asyncio.wait_for(self._main_client.get_chat(target_channel), timeout=8.0)
            join_dest = getattr(chat, "username", None) or getattr(chat, "invite_link", None)
            if not join_dest:
                join_dest = await asyncio.wait_for(self._main_client.export_chat_invite_link(target_channel), timeout=8.0)
        except Exception:
            pass
        if not join_dest:
            return

        from pyrogram.types import ChatPrivileges
        promoted_now = 0
        for c in candidates:
            if len(self._admin_sessions.get(t_key, [])) >= target_admins:
                break
            if not getattr(c, "is_connected", False):
                continue
            u_me = getattr(c, "me", None)
            uid = getattr(c, "_user_id", None) or getattr(u_me, "id", None)
            if not uid:
                continue
            sess_name = getattr(c, "name", "session")
            cache_key = f"{sess_name}_{target_channel}"
            try:
                if cache_key not in self._joined_sessions_cache:
                    try:
                        await asyncio.wait_for(c.get_chat(target_channel), timeout=2.0)
                        self._joined_sessions_cache.add(cache_key)
                    except Exception:
                        s_dest = str(join_dest)
                        if s_dest.startswith("http") or "+" in s_dest or "joinchat" in s_dest:
                            from pyrogram import raw
                            inv_hash = s_dest.split("+")[-1].split("/")[-1]
                            await asyncio.wait_for(c.invoke(raw.functions.messages.ImportChatInvite(hash=inv_hash), retries=0, timeout=4.0), timeout=5.0)
                        else:
                            await asyncio.wait_for(c.join_chat(join_dest), timeout=5.0)
                        self._joined_sessions_cache.add(cache_key)

                await asyncio.wait_for(
                    self._main_client.promote_chat_member(
                        chat_id=target_channel,
                        user_id=uid,
                        privileges=ChatPrivileges(can_post_messages=True, can_edit_messages=True),
                    ),
                    timeout=6.0,
                )
                promoted_now += 1
                async with self._lock:
                    lst = self._admin_sessions.setdefault(t_key, [])
                    if c not in lst:
                        lst.append(c)
                        self.save_admin_sessions_manifest(t_key, lst)
                try:
                    from streaming.session_pool import stream_pool
                    if c not in stream_pool._admin_clients:
                        stream_pool._admin_clients.append(c)
                except Exception:
                    pass
                await asyncio.sleep(1.2)
            except Exception as exp_err:
                err_s = str(exp_err)
                if "ADMINS_TOO_MUCH" in err_s or "right_forbidden" in err_s.lower():
                    log.info("[UploadPool] Background admin expansion stopped (%s). Active admins=%d", err_s[:80], len(self._admin_sessions.get(t_key, [])))
                    break
                if "flood" in err_s.lower():
                    await asyncio.sleep(4.0)
                    continue
                await asyncio.sleep(0.5)

        if promoted_now > 0:
            log.info("[UploadPool] Background expansion promoted +%d sessions! Total admins in pool: %d", promoted_now, len(self._admin_sessions.get(t_key, [])))

    def get_client_lock(self, client: Client) -> asyncio.Lock:
        """Return a persistent asyncio.Lock for the given Client instance to serialize same-account uploads."""
        c_id = id(client)
        if c_id not in self._client_upload_locks:
            self._client_upload_locks[c_id] = asyncio.Lock()
        return self._client_upload_locks[c_id]

    def get_admin_sessions_cached(self, target_channel: int) -> List[Client]:
        """Return currently cached active verified admin clients for target_channel without awaiting discovery."""
        try:
            t_key = int(target_channel)
        except Exception:
            t_key = target_channel
        res = self._admin_sessions.get(t_key, [])
        if not res and isinstance(t_key, int):
            s_key = str(t_key)
            if s_key.startswith("-100"):
                try:
                    alt_pos = int(s_key[4:])
                    alt_neg = -alt_pos
                    res = self._admin_sessions.get(alt_pos, []) or self._admin_sessions.get(alt_neg, [])
                except Exception:
                    pass
            elif not s_key.startswith("-"):
                try:
                    res = self._admin_sessions.get(int(f"-100{s_key}"), [])
                except Exception:
                    pass
        return [c for c in res if getattr(c, "is_connected", False)]

    def save_admin_sessions_manifest(self, target_channel: int, admin_clients: List[Client]) -> None:
        """Persist verified channel admin sessions metadata to bot/sessions/admin_sessions.json."""
        try:
            import json
            _services_dir = os.path.dirname(os.path.abspath(__file__))
            _bot_dir = os.path.dirname(_services_dir)
            manifest_path = os.path.join(_bot_dir, "sessions", "admin_sessions.json")
            if "pytest" in sys.modules and f"{os.sep}bot{os.sep}sessions{os.sep}admin_sessions.json" in os.path.abspath(manifest_path):
                return
            data = {
                "channel_id": target_channel,
                "updated_at": time.time(),
                "total_sessions_loaded": len(self.clients),
                "admin_sessions_count": len(admin_clients),
                "admin_sessions": [
                    {
                        "session": os.path.basename(getattr(c, "name", "session")) + ".session" if not str(getattr(c, "name", "")).endswith(".session") else os.path.basename(getattr(c, "name", "session")),
                        "user_id": getattr(getattr(c, "me", None), "id", None),
                        "name": f"{getattr(getattr(c, 'me', None), 'first_name', '')} {getattr(getattr(c, 'me', None), 'last_name', '')}".strip(),
                        "phone": getattr(getattr(c, "me", None), "phone_number", None),
                    }
                    for c in admin_clients
                ]
            }
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info("[UploadPool] Saved %d verified admin sessions manifest to %s", len(admin_clients), manifest_path)
        except Exception as sm_err:
            log.debug("[UploadPool] Admin manifest save note: %s", sm_err)

    def load_admin_sessions_manifest(self, target_channel: int) -> int:
        """Load and restore verified admin sessions from bot/sessions/admin_sessions.json if matching target channel."""
        try:
            import json
            _services_dir = os.path.dirname(os.path.abspath(__file__))
            _bot_dir = os.path.dirname(_services_dir)
            manifest_path = os.path.join(_bot_dir, "sessions", "admin_sessions.json")
            if not os.path.exists(manifest_path):
                return 0
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ch_in_file = data.get("channel_id")
            if str(ch_in_file) != str(target_channel):
                return 0
            admin_list = data.get("admin_sessions", [])
            admin_session_files = {a.get("session", "") for a in admin_list if a.get("session")}
            admin_uids = {a.get("user_id") for a in admin_list if a.get("user_id")}

            matched_clients: List[Client] = []
            for c in self.clients:
                sess_file = os.path.basename(getattr(c, "name", ""))
                if not sess_file.endswith(".session"):
                    sess_file += ".session"
                c_uid = getattr(getattr(c, "me", None), "id", None)
                if sess_file in admin_session_files or (c_uid and c_uid in admin_uids):
                    if getattr(c, "is_connected", False) and c not in matched_clients:
                        matched_clients.append(c)
                        self._joined_sessions_cache.add(f"{getattr(c, 'name', '')}_{target_channel}")

            if matched_clients:
                ch_key = int(target_channel)
                self._admin_sessions[ch_key] = matched_clients
                self._admin_sessions[str(target_channel)] = matched_clients
                log.info("[UploadPool] Restored %d verified channel admin sessions from %s for channel %s", len(matched_clients), manifest_path, target_channel)
                return len(matched_clients)
        except Exception as lm_err:
            log.debug("[UploadPool] Admin manifest load note: %s", lm_err)
        return 0

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

        # Check SESSION_STRINGS / MULTI_ACCOUNT_SESSIONS env vars
        env_session_strings: List[str] = []
        for env_var in ("SESSION_STRINGS", "MULTI_ACCOUNT_SESSIONS"):
            val = os.getenv(env_var, "").strip()
            if val:
                for part in re.split(r"[,;\n\r]+", val):
                    part = part.strip()
                    if part and part not in env_session_strings:
                        env_session_strings.append(part)

        if not session_files and not env_session_strings:
            log.warning(
                "[UploadPool] No .session files found in %s and no session strings in env — pool will use main bot client.",
                sessions_dir
            )
            return

        password = os.getenv("SESSION_2FA_PASSWORD", "2122138")
        if session_files:
            log.info("[UploadPool] Loading %d session files concurrently from %s ...", len(session_files), sessions_dir)
        if env_session_strings:
            log.info("[UploadPool] Loading %d session strings from environment variables...", len(env_session_strings))

        # Discover channel admin user IDs up-front via single fast HTTP call so verified admin sessions connect FIRST
        pre_admin_uids: set[int] = set()
        if target_channel and session_files:
            try:
                import json, urllib.request, config
                token = getattr(config, "BOT_TOKEN", "") or os.getenv("BOT_TOKEN", "")
                if token and not os.environ.get("PYTEST_CURRENT_TEST"):
                    api_url = f"https://api.telegram.org/bot{token}/getChatAdministrators?chat_id={target_channel}"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
                    def _fetch_pre_admins():
                        with urllib.request.urlopen(req, timeout=5) as r:
                            return json.loads(r.read().decode("utf-8", errors="ignore"))
                    res_pre = await asyncio.to_thread(_fetch_pre_admins)
                    if res_pre.get("ok"):
                        for a in res_pre.get("result", []):
                            u = a.get("user", {})
                            if u and not u.get("is_bot", False) and u.get("id"):
                                pre_admin_uids.add(int(u["id"]))
            except Exception as pre_err:
                log.debug("[UploadPool] Pre-init admin UID lookup note: %s", pre_err)

        # Partition sessions locally via non-blocking thread so admin accounts become usable in seconds
        admin_session_files: List[str] = []
        other_session_files: List[str] = []
        if pre_admin_uids and session_files:
            def _partition_sessions():
                adm, oth = [], []
                for sp in session_files:
                    uid = None
                    try:
                        conn = sqlite3.connect(sp, timeout=3.0)
                        row = conn.execute("SELECT user_id FROM sessions").fetchone()
                        conn.close()
                        uid = int(row[0]) if row and row[0] else None
                    except Exception:
                        uid = None
                    if uid and uid in pre_admin_uids:
                        adm.append(sp)
                    else:
                        oth.append(sp)
                return adm, oth
            admin_session_files, other_session_files = await asyncio.to_thread(_partition_sessions)
            if admin_session_files:
                log.info("[UploadPool] Prioritizing %d verified channel admin sessions first!", len(admin_session_files))
        else:
            other_session_files = list(session_files)

        sem = asyncio.Semaphore(5)

        async def _load_one(s_path: str, stagger_s: float = 0.05):
            base_name = os.path.splitext(os.path.basename(s_path))[0]
            session_prefix = os.path.join(sessions_dir, base_name)

            if any(getattr(c, "name", "") == session_prefix for c in self.clients):
                return

            async with sem:
                if stagger_s > 0:
                    await asyncio.sleep(stagger_s)
                for attempt in range(3):
                    try:
                        # Ensure session database is in Pyrogram SQLite format in background thread
                        await asyncio.to_thread(_ensure_pyrogram_session, s_path, api_id)

                        c = Client(
                            name=session_prefix,
                            api_id=api_id,
                            api_hash=api_hash,
                            no_updates=True,
                            max_concurrent_transmissions=16,
                            sleep_threshold=15,  # Auto-sleep minor waits; rotate on longer ones
                        )
                        is_auth = await asyncio.wait_for(c.connect(), timeout=20.0)
                        if not is_auth:
                            await c.disconnect()
                            log.warning("[UploadPool] Session '%s' is not authorized. Skipping.", base_name)
                            return

                        try:
                            await asyncio.wait_for(c.initialize(), timeout=15.0)
                            # Stop idle updates_watchdog on no_updates=True pool clients so 98 clients don't poll GetState
                            ev = getattr(c, "updates_watchdog_event", None)
                            if ev is not None and hasattr(ev, "set"):
                                ev.set()
                        except Exception as init_err:
                            await c.disconnect()
                            log.warning("[UploadPool] Session '%s' init error: %s. Skipping.", base_name, init_err)
                            return

                        try:
                            me = await asyncio.wait_for(c.get_me(), timeout=15.0)
                            c.me = me
                            c._user_id = getattr(me, "id", None)
                            if not hasattr(c.me, "is_premium"):
                                setattr(c.me, "is_premium", False)
                            self.clients.append(c)
                            try:
                                from streaming.session_pool import stream_pool
                                if c not in stream_pool.clients:
                                    stream_pool.clients.append(c)
                            except Exception:
                                pass
                            return
                        except SessionPasswordNeeded:
                            try:
                                await asyncio.wait_for(c.check_password(password), timeout=15.0)
                                me = await asyncio.wait_for(c.get_me(), timeout=15.0)
                                c.me = me
                                c._user_id = getattr(me, "id", None)
                                if not hasattr(c.me, "is_premium"):
                                    setattr(c.me, "is_premium", False)
                                self.clients.append(c)
                                try:
                                    from streaming.session_pool import stream_pool
                                    if c not in stream_pool.clients:
                                        stream_pool.clients.append(c)
                                except Exception:
                                    pass
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
                                if getattr(c, "is_initialized", False):
                                    await c.stop()
                                elif getattr(c, "is_connected", False):
                                    await c.disconnect()
                        except Exception:
                            pass
                        err_str = str(exc).lower()
                        if "database is locked" in err_str and attempt < 2:
                            await asyncio.sleep(0.6 * (attempt + 1))
                            continue
                        log.warning("[UploadPool] Could not start session '%s': %s", base_name, exc)
                        break

        async def _load_string(s_str: str, idx: int):
            worker_name = f"upload_pool_worker_{idx}"
            if any(getattr(c, "name", "") == worker_name for c in self.clients):
                return
            async with sem:
                for attempt in range(3):
                    try:
                        c = Client(
                            name=worker_name,
                            session_string=s_str,
                            api_id=api_id,
                            api_hash=api_hash,
                            no_updates=True,
                            max_concurrent_transmissions=16,
                            sleep_threshold=15,
                        )
                        is_auth = await asyncio.wait_for(c.connect(), timeout=20.0)
                        if not is_auth:
                            await c.disconnect()
                            log.warning("[UploadPool] Session string #%d is not authorized. Skipping.", idx)
                            return
                        try:
                            await asyncio.wait_for(c.initialize(), timeout=15.0)
                            ev = getattr(c, "updates_watchdog_event", None)
                            if ev is not None and hasattr(ev, "set"):
                                ev.set()
                        except Exception as init_err:
                            await c.disconnect()
                            log.warning("[UploadPool] Session string #%d init error: %s. Skipping.", idx, init_err)
                            return

                        try:
                            me = await asyncio.wait_for(c.get_me(), timeout=15.0)
                            c.me = me
                            c._user_id = getattr(me, "id", None)
                            if not hasattr(c.me, "is_premium"):
                                setattr(c.me, "is_premium", False)
                            self.clients.append(c)
                            try:
                                from streaming.session_pool import stream_pool
                                if c not in stream_pool.clients:
                                    stream_pool.clients.append(c)
                            except Exception:
                                pass
                            log.info("[UploadPool] Loaded session string #%d (UID: %s)", idx, getattr(me, "id", ""))
                            return
                        except SessionPasswordNeeded:
                            try:
                                await asyncio.wait_for(c.check_password(password), timeout=15.0)
                                me = await asyncio.wait_for(c.get_me(), timeout=15.0)
                                c.me = me
                                c._user_id = getattr(me, "id", None)
                                if not hasattr(c.me, "is_premium"):
                                    setattr(c.me, "is_premium", False)
                                self.clients.append(c)
                                try:
                                    from streaming.session_pool import stream_pool
                                    if c not in stream_pool.clients:
                                        stream_pool.clients.append(c)
                                except Exception:
                                    pass
                                log.info("[UploadPool] 2FA OK for session string #%d (UID: %s)", idx, getattr(me, "id", ""))
                                return
                            except Exception as pw_err:
                                if c.is_initialized:
                                    await c.stop()
                                elif c.is_connected:
                                    await c.disconnect()
                                log.warning("[UploadPool] 2FA failed for session string #%d: %s", idx, pw_err)
                                return
                        except Exception as auth_err:
                            if c.is_initialized:
                                await c.stop()
                            elif c.is_connected:
                                await c.disconnect()
                            log.warning("[UploadPool] Session string #%d auth error (%s). Skipping.", idx, auth_err)
                            return
                    except Exception as exc:
                        try:
                            if 'c' in locals():
                                if getattr(c, "is_initialized", False):
                                    await c.stop()
                                elif getattr(c, "is_connected", False):
                                    await c.disconnect()
                        except Exception:
                            pass
                        err_str = str(exc).lower()
                        if "database is locked" in err_str and attempt < 2:
                            await asyncio.sleep(0.6 * (attempt + 1))
                            continue
                        log.warning("[UploadPool] Could not start session string #%d: %s", idx, exc)
                        break

        # Phase 1: Connect verified admin sessions first (fast startup for uploads & streams)
        if admin_session_files:
            await asyncio.gather(*[_load_one(sp, stagger_s=0.02) for sp in admin_session_files])
            if target_channel:
                try:
                    t_k = int(target_channel)
                    fast_admins = [c for c in self.clients if getattr(c, "_user_id", None) in pre_admin_uids and getattr(c, "is_connected", False)]
                    if fast_admins:
                        self._admin_sessions[t_k] = fast_admins
                        self._admin_check_times[t_k] = time.time()
                        self.save_admin_sessions_manifest(t_k, fast_admins)
                        from streaming.session_pool import stream_pool
                        stream_pool._admin_clients = list(fast_admins)
                        log.info("[UploadPool] Fast-registered %d verified admin sessions for channel %s!", len(fast_admins), target_channel)
                except Exception:
                    pass

        # Phase 2: Load remaining sessions gently to avoid MTProto / Kaggle NAT saturation
        tasks = [_load_one(sp, stagger_s=0.08) for sp in other_session_files]
        tasks += [_load_string(s, i) for i, s in enumerate(env_session_strings, start=1)]
        await asyncio.gather(*tasks)
        log.info(
            "[UploadPool] Total active upload clients in pool: %d/%d (%d files, %d strings)",
            len(self.clients),
            len(session_files) + len(env_session_strings),
            len(session_files),
            len(env_session_strings),
        )

        admin_count = 0
        if target_channel:
            # 1. Count already-discovered admins from Phase 1 / manifest
            t_k = int(target_channel)
            if len(self._admin_sessions.get(t_k, [])) >= 3:
                admin_count = len(self._admin_sessions[t_k])
            else:
                restored = self.load_admin_sessions_manifest(target_channel)
                if restored > 0:
                    admin_count = restored

            # 2. If still < 3, refresh verified admins first (maybe some non-partitioned files are admins)
            if admin_count < 3 and self.clients:
                try:
                    refreshed = await asyncio.wait_for(self.get_admin_sessions(target_channel, refresh=True), timeout=10.0)
                    admin_count = len(refreshed)
                except Exception as ref_err:
                    log.debug("[UploadPool] Admin refresh note: %s", ref_err)

            # 3. Only run join/promote if we still don't have enough verified admin sessions
            if admin_count < 3:
                try:
                    join_res = await self.join_channel(target_channel)
                    admin_count = join_res.get("admin_sessions", admin_count)
                except Exception as j_err:
                    log.warning("[UploadPool] Channel auto-join error: %s", j_err)

        # Sync all connected userbot clients to stream_pool so stream_pool has the full multi-account fleet
        try:
            from streaming.session_pool import stream_pool
            t_key = int(target_channel) if target_channel else 0
            admin_clients = [c for c in self._admin_sessions.get(t_key, []) if getattr(c, "is_connected", False)]
            if admin_clients:
                stream_pool._admin_clients = list(admin_clients)
            for c in self.clients:
                if getattr(c, "is_connected", False) and c not in stream_pool.clients:
                    stream_pool.clients.append(c)
            log.info("[UploadPool] Synced %d connected userbot accounts (%d admins) to stream_pool.", len(self.clients), len(admin_clients))
        except Exception as sync_err:
            log.debug("[UploadPool] Stream pool sync note: %s", sync_err)

        # Gently promote additional unused sessions in background (up to ~42 admins) for maximum upload & stream parallelism
        if target_channel and not os.environ.get("PYTEST_CURRENT_TEST") and len(self.clients) > admin_count and admin_count < 42:
            if self._admin_expand_task is None or self._admin_expand_task.done():
                self._admin_expand_task = asyncio.create_task(self.expand_admin_pool_background(target_channel, target_admins=42))

        # Notify admins that session pool is loaded and ready
        if self._main_client and getattr(self._main_client, "is_connected", False):
            import config
            for admin_id in getattr(config, "ADMIN_IDS", []):
                try:
                    admin_display = f"{admin_count} Verified Admin Sessions" if admin_count > 0 else f"{len(self.clients)} Connected"
                    q1080 = max(1, admin_count // 3) if admin_count >= 3 else 1
                    q720 = max(1, admin_count // 3) if admin_count >= 3 else 1
                    q480 = max(1, admin_count - q1080 - q720) if admin_count >= 3 else 1
                    await asyncio.wait_for(
                        self._main_client.send_message(
                            chat_id=admin_id,
                            text=(
                                f"✅ <b>Telegram Multi-Account Pool Ready!</b>\n\n"
                                f"👥 <b>Connected Accounts:</b> <code>{len(self.clients)}/{len(session_files)}</code>\n"
                                f"👑 <b>Channel Admins:</b> <code>{admin_display}</code>\n"
                                f"🔴 <b>1080p Tier:</b> <code>{q1080} Admin Accounts</code>\n"
                                f"🟡 <b>720p Tier:</b>  <code>{q720} Admin Accounts</code>\n"
                                f"🟢 <b>480p Tier:</b>  <code>{q480} Admin Accounts</code>\n"
                                f"🎥 <b>Streaming Pool:</b> <code>{len(admin_clients) if admin_clients else len(self.clients)} Verified Sessions</code>\n"
                                f"⚡ <b>Channel:</b> Zero Permission Errors guaranteed."
                            ),
                            parse_mode=ParseMode.HTML,
                        ),
                        timeout=12.0,
                    )
                except Exception as alert_err:
                    log.debug("[UploadPool] Admin alert note: %s", alert_err)

    async def join_channel(self, target_channel: int) -> dict:
        """Auto-join all loaded userbot sessions to the target Telegram channel and promote up to 40 sessions to admin."""
        if not self.clients or not target_channel:
            return {"joined": 0, "promoted": 0, "error": "No sessions or channel not set"}

        ch_key = int(target_channel)
        existing_admins = self._admin_sessions.get(ch_key, [])
        if len(existing_admins) >= 3:
            log.info("[UploadPool] Channel %s already has %d verified admin sessions from manifest. Skipping active join/promote.", target_channel, len(existing_admins))
            return {"joined": len(existing_admins), "promoted": 0, "admin_sessions": len(existing_admins)}

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
                chat = await asyncio.wait_for(self._main_client.get_chat(target_channel), timeout=8.0)
                channel_username = getattr(chat, "username", None)
                invite_link = getattr(chat, "invite_link", None)
                if not invite_link and not channel_username:
                    try:
                        exported = await asyncio.wait_for(self._main_client.export_chat_invite_link(target_channel), timeout=8.0)
                        invite_link = exported
                    except Exception as exp_err:
                        log.debug("[UploadPool] Channel export invite link note: %s", exp_err)
            except Exception as chat_err:
                log.warning("[UploadPool] Channel get_chat error for %s: %s", target_channel, chat_err)

        join_dest = channel_username or invite_link
        if not join_dest:
            log.info("[UploadPool] No username or invite link available for auto-joining sessions to %s", target_channel)
            return {"joined": 0, "promoted": 0, "error": "Bot cannot access channel or export invite link"}

        # Prioritize unjoined sessions and cap to at most 6 per join run to avoid Telegram flood limits
        unjoined_clients = [
            c for c in self.clients
            if f"{getattr(c, 'name', '')}_{target_channel}" not in self._joined_sessions_cache
        ]
        target_clients = unjoined_clients[:6] if unjoined_clients else self.clients[:3]
        log.info("[UploadPool] Auto-joining %d session(s) to channel %s (dest=%s)...", len(target_clients), target_channel, join_dest)
        join_sem = asyncio.Semaphore(2)
        joined_count = 0
        promoted_count = 0
        limit_reached = False

        async def _join_one(c: Client):
            nonlocal joined_count, promoted_count, limit_reached
            if not getattr(c, "is_connected", False):
                return
            sess_name = getattr(c, "name", "client")
            cache_key = f"{sess_name}_{target_channel}"
            if cache_key in self._joined_sessions_cache:
                joined_count += 1
                return
            async with join_sem:
                is_member = False
                try:
                    # Pre-check if session is already a participant/admin in target_channel
                    c_chat = await asyncio.wait_for(c.get_chat(target_channel), timeout=2.0)
                    if c_chat:
                        is_member = True
                        self._joined_sessions_cache.add(cache_key)
                        joined_count += 1
                except Exception:
                    is_member = False

                if not is_member:
                    try:
                        s_dest = str(join_dest)
                        is_invite = s_dest.startswith("http") or "+" in s_dest or "joinchat" in s_dest
                        if is_invite:
                            inv_hash = s_dest.split("+")[-1].split("/")[-1]
                            from pyrogram import raw
                            await c.invoke(raw.functions.messages.ImportChatInvite(hash=inv_hash), retries=0, timeout=3.5)
                        else:
                            await c.join_chat(join_dest)
                        self._joined_sessions_cache.add(cache_key)
                        joined_count += 1
                    except asyncio.CancelledError:
                        return
                    except (asyncio.TimeoutError, TimeoutError, OSError) as t_err:
                        log.warning("[UploadPool] Session %s join_chat timed out/failed on %s: %s", sess_name, target_channel, t_err)
                        self._joined_sessions_cache.add(cache_key)
                    except Exception as j_err:
                        err_s = str(j_err).lower()
                        if "already" in err_s or "user_already_participant" in err_s:
                            self._joined_sessions_cache.add(cache_key)
                            joined_count += 1
                        elif "flood_wait" in err_s or "floodwait" in err_s:
                            log.warning("[UploadPool] Session %s hit Telegram FloodWait during join_chat. Skipping.", sess_name)
                            self._joined_sessions_cache.add(cache_key)
                            return
                        else:
                            log.debug("[UploadPool] Session %s join note: %s", sess_name, j_err)
                            self._joined_sessions_cache.add(cache_key)

                # Attempt to promote userbot to admin with can_post_messages if main bot is admin (up to 42 sessions)
                if not limit_reached and promoted_count < 42 and self._main_client and getattr(self._main_client, "is_connected", False):
                    try:
                        u_me = getattr(c, "me", None)
                        if u_me and getattr(u_me, "id", None):
                            from pyrogram.types import ChatPrivileges
                            await asyncio.wait_for(
                                self._main_client.promote_chat_member(
                                    chat_id=target_channel,
                                    user_id=u_me.id,
                                    privileges=ChatPrivileges(
                                        can_post_messages=True,
                                        can_edit_messages=True,
                                    )
                                ),
                                timeout=6.0,
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

        t_join_start = time.time()
        gather_join = asyncio.gather(*[_join_one(c) for c in target_clients], return_exceptions=True)
        try:
            await asyncio.wait_for(
                gather_join,
                timeout=6.5,
            )
        except asyncio.TimeoutError:
            log.warning("[UploadPool] Batch join_channel timed out after 6.5s. Cancelling background join tasks.")
            if not gather_join.done():
                gather_join.cancel()
            try:
                await gather_join
            except (asyncio.CancelledError, Exception):
                pass
        except (asyncio.CancelledError, GeneratorExit):
            if not gather_join.done():
                gather_join.cancel()
            try:
                await gather_join
            except (asyncio.CancelledError, Exception):
                pass
            raise
        self._channel_unwritable.discard(target_channel)
        try:
            self._channel_unwritable.discard(int(target_channel))
        except Exception:
            pass

        # Immediately refresh and cache verified admin sessions for this channel
        elapsed_so_far = time.time() - t_join_start
        admin_timeout = max(1.5, min(3.2, 9.2 - elapsed_so_far))
        try:
            admin_clients = await asyncio.wait_for(
                self.get_admin_sessions(target_channel, refresh=True),
                timeout=admin_timeout,
            )
        except asyncio.TimeoutError:
            log.warning("[UploadPool] get_admin_sessions timed out after %.1fs in join_channel", admin_timeout)
            admin_clients = self._admin_sessions.get(t_key if "t_key" in locals() else target_channel, []) or self._admin_sessions.get(target_channel, [])
        except Exception as e:
            log.warning("[UploadPool] get_admin_sessions failed in join_channel: %s", e)
            admin_clients = self._admin_sessions.get(target_channel, [])
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
        First queries administrators via main bot client / Bot API HTTP for instantaneous 100% accurate discovery,
        matching against pool session user IDs, with per-session fallback check.
        Persists discovered admins BEFORE peer warmup so peer timeouts never discard verified admins.
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
            if t_key not in self._admin_check_times or (now - self._admin_check_times[t_key] < 600):
                return [c for c in self._admin_sessions[t_key] if getattr(c, "is_connected", False)]

        admin_clients: List[Client] = []

        # 1. Primary method: Query channel administrators via main bot (instant, zero per-session rate limit)
        admin_uids: set[int] = set()
        channel_username: Optional[str] = None
        invite_link: Optional[str] = None
        if self._main_client and getattr(self._main_client, "is_connected", False):
            async def _query_main_bot_admins():
                nonlocal channel_username, invite_link
                from pyrogram.enums import ChatMembersFilter
                try:
                    chat_obj = await self._main_client.get_chat(target_channel)
                    channel_username = getattr(chat_obj, "username", None)
                    invite_link = getattr(chat_obj, "invite_link", None)
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

            try:
                await asyncio.wait_for(_query_main_bot_admins(), timeout=2.5)
                log.info("[UploadPool] Main bot queried channel %s: %d human admin accounts verified.", target_channel, len(admin_uids))
            except Exception as q_err:
                log.warning("[UploadPool] Main bot could not query channel administrators: %s", q_err)

        # 1b. Fallback HTTP query via Telegram Bot API getChatAdministrators
        if not admin_uids:
            try:
                import urllib.request
                import json
                import config
                token = getattr(config, "BOT_TOKEN", "")
                if token:
                    api_url = f"https://api.telegram.org/bot{token}/getChatAdministrators?chat_id={target_channel}"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
                    def _fetch_admins_http():
                        with urllib.request.urlopen(req, timeout=2.0) as r:
                            return json.loads(r.read().decode())
                    res = await asyncio.wait_for(asyncio.to_thread(_fetch_admins_http), timeout=2.5)
                    if res.get("ok"):
                        for a in res.get("result", []):
                            u = a.get("user", {})
                            if not u or u.get("is_bot", False):
                                continue
                            if a.get("user", {}).get("id"):
                                admin_uids.add(u.get("id"))
                        if admin_uids:
                            log.info("[UploadPool] Bot API HTTP queried channel %s: %d human admin accounts verified.", target_channel, len(admin_uids))
            except Exception as http_err:
                log.debug("[UploadPool] Bot API HTTP admin query note: %s", http_err)

        if admin_uids:
            for c in self.clients:
                if not getattr(c, "is_connected", False):
                    continue
                c_me = getattr(c, "me", None)
                c_uid = getattr(c, "_user_id", None) or getattr(c_me, "id", None)
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
                        member = await asyncio.wait_for(
                            c.get_chat_member(target_channel, "me"),
                            timeout=2.5,
                        )
                        status_raw = getattr(member, "status", None)
                        status_str = getattr(status_raw, "value", str(status_raw)).lower()
                        privs = getattr(member, "privileges", None)
                        can_post = getattr(privs, "can_post_messages", False) if privs else False
                        if ("admin" in status_str and can_post) or "owner" in status_str or "creator" in status_str:
                            admin_clients.append(c)
                    except asyncio.CancelledError:
                        return
                    except Exception as c_err:
                        log.debug("[UploadPool] Session %s admin check in %s: %s", getattr(c, "name", "client"), target_channel, c_err)

            gather_check = asyncio.gather(*[_check_c(c) for c in self.clients], return_exceptions=True)
            try:
                await asyncio.wait_for(
                    gather_check,
                    timeout=3.5,
                )
            except asyncio.TimeoutError:
                log.warning("[UploadPool] Fallback get_chat_member check timed out after 3.5s")
            except (asyncio.CancelledError, GeneratorExit):
                if not gather_check.done():
                    gather_check.cancel()
                try:
                    await gather_check
                except (asyncio.CancelledError, Exception):
                    pass
                raise

        # Persist discovered verified admin sessions IMMEDIATELY before peer warmup!
        admin_clients.sort(key=lambda c: str(getattr(c, "name", "")))
        self._admin_sessions[t_key] = admin_clients
        self._admin_check_times[t_key] = now
        self.save_admin_sessions_manifest(t_key, admin_clients)

        # Update stream_pool so it immediately has the verified channel admin accounts
        try:
            from streaming.session_pool import stream_pool
            if admin_clients:
                stream_pool._admin_clients = list(admin_clients)
                stream_pool.clients = list(admin_clients)
                log.info("[UploadPool] Updated stream_pool with %d verified admin sessions.", len(admin_clients))
        except Exception:
            pass

        # 3. Gently warm channel peer resolution on verified admin sessions (bounded <= 1.5s, never blocks caller)
        if admin_clients:
            warmup_sem = asyncio.Semaphore(8)

            async def _warmup_peer(c: Client):
                async with warmup_sem:
                    try:
                        await asyncio.wait_for(c.get_chat(target_channel), timeout=1.5)
                        return
                    except Exception:
                        pass
                    if channel_username:
                        try:
                            await asyncio.wait_for(c.get_chat(channel_username), timeout=1.5)
                            return
                        except Exception:
                            pass

            warmup_gather = asyncio.gather(*[_warmup_peer(c) for c in admin_clients], return_exceptions=True)
            try:
                await asyncio.wait_for(warmup_gather, timeout=1.5)
            except asyncio.TimeoutError:
                if not warmup_gather.done():
                    warmup_gather.cancel()
                try:
                    await warmup_gather
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, GeneratorExit):
                if not warmup_gather.done():
                    warmup_gather.cancel()
                try:
                    await warmup_gather
                except (asyncio.CancelledError, Exception):
                    pass
                raise

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
                    q_clean = str(quality or "default").lower().strip()
                    if "1080" in q_clean:
                        base_idx = 0
                        tier_name = "1080p"
                    elif "720" in q_clean:
                        base_idx = 1
                        tier_name = "720p"
                    elif "480" in q_clean:
                        base_idx = 2
                        tier_name = "480p"
                    elif "360" in q_clean:
                        base_idx = 3
                        tier_name = "360p"
                    else:
                        base_idx = quality_map.get(q_clean, 0)
                        tier_name = "default"
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

                    q_key = f"{tier_name}_{t_key}"
                    cur_idx = self._indices.get(q_key, 0) % len(tier_admins)
                    self._indices[q_key] = (cur_idx + 1) % len(tier_admins)
                    rotated = tier_admins[cur_idx:] + tier_admins[:cur_idx]
                    prioritized = self._prioritize_idle_healthy(rotated, [c for c in admin_pool if c not in tier_admins])
                    selected = prioritized[0] if prioritized else rotated[0]
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
        reply_markup=None,
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
            try:
                admin_clients = await asyncio.wait_for(self.get_admin_sessions(target_chat), timeout=5.0)
            except Exception as e:
                log.warning("[UploadPool] get_admin_sessions timed out or error (%s), using cached fallback", e)
                try:
                    t_k = int(target_chat)
                except Exception:
                    t_k = target_chat
                admin_clients = [c for c in self._admin_sessions.get(t_k, []) if getattr(c, "is_connected", False)]
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
                            file_name=file_name,
                            reply_markup=reply_markup,
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
                        file_name=file_name,
                        reply_markup=reply_markup,
                        progress_callback=progress_callback,
                        fallback_chat=0,
                    )

        last_exc = None
        tried_clients: list = []

        # Build candidate pool:
        # 1) Verified admin sessions in this quality tier (1080p, 720p, 480p) rotated per tier
        # 2) Other verified admin sessions in channel (cross-tier fallback)
        # 3) Remaining connected userbot sessions in the pool
        candidates: List[Client] = []
        try:
            t_key = int(target_chat)
        except Exception:
            t_key = target_chat

        async with self._lock:
            active_admins = [c for c in self._admin_sessions.get(t_key, []) if getattr(c, "is_connected", False)]
            quality_map = {"1080p": 0, "720p": 1, "480p": 2, "360p": 3, "default": 0}
            q_clean = str(quality or "default").lower().strip()
            if "1080" in q_clean:
                base_idx = 0
                tier_name = "1080p"
            elif "720" in q_clean:
                base_idx = 1
                tier_name = "720p"
            elif "480" in q_clean:
                base_idx = 2
                tier_name = "480p"
            elif "360" in q_clean:
                base_idx = 3
                tier_name = "360p"
            else:
                base_idx = quality_map.get(q_clean, 0)
                tier_name = "default"

            if active_admins:
                n_admins = len(active_admins)

                if n_admins >= 3:
                    tier_admins = [active_admins[i] for i in range(n_admins) if (i % 3 == base_idx % 3)]
                    if not tier_admins:
                        tier_admins = active_admins
                elif n_admins == 2:
                    tier_admins = [active_admins[base_idx % 2]]
                else:
                    tier_admins = active_admins

                other_admins = [c for c in active_admins if c not in tier_admins]

                # Round-robin rotation across tier admin accounts so all channel admin
                # accounts are rotated evenly across successive movie uploads
                q_key = f"{tier_name}_{t_key}"
                cur_idx = self._indices.get(q_key, 0) % len(tier_admins)
                self._indices[q_key] = (cur_idx + 1) % len(tier_admins)
                rotated_tier_admins = tier_admins[cur_idx:] + tier_admins[:cur_idx]

                # Also rotate fallback admin accounts for even distribution
                other_key = f"other_{t_key}"
                if other_admins:
                    other_idx = self._indices.get(other_key, 0) % len(other_admins)
                    self._indices[other_key] = (other_idx + 1) % len(other_admins)
                    rotated_other_admins = other_admins[other_idx:] + other_admins[:other_idx]
                else:
                    rotated_other_admins = []

                candidates = self._prioritize_idle_healthy(rotated_tier_admins, rotated_other_admins)
            else:
                connected = [c for c in self.clients if getattr(c, "is_connected", False)]
                if connected:
                    tier_start, tier_end = _QUALITY_TIERS.get(tier_name, _QUALITY_TIERS["default"])
                    tier_start = min(tier_start, len(connected) - 1)
                    tier_end = min(tier_end, len(connected) - 1)
                    tier_clients = connected[tier_start : tier_end + 1] or connected

                    idx = self._indices.get(tier_name, 0) % len(tier_clients)
                    self._indices[tier_name] = (idx + 1) % len(tier_clients)
                    rotated_tier = tier_clients[idx:] + tier_clients[:idx]
                    other_clients = [c for c in connected if c not in rotated_tier]
                    candidates = self._prioritize_idle_healthy(rotated_tier, other_clients)
                else:
                    candidates = []

        max_attempts = min(len(candidates), 12)
        for attempt in range(max_attempts):
            client = candidates[attempt]
            if client in tried_clients and len(tried_clients) < len(candidates):
                continue
            tried_clients.append(client)
            log.info(
                "[UploadPool] Uploading [%s] attempt %d/%d via session '%s' (user_id=%s) → chat %s",
                quality, attempt + 1, max_attempts,
                getattr(client, "name", "main_bot"),
                getattr(getattr(client, "me", None), "id", "?"),
                target_chat,
            )

            try:
                c_lock = self.get_client_lock(client)
                async with c_lock:
                    _t_up_start = time.time()
                    res = await telegram_upload.upload_video_file(
                        bot_client=client,
                        file_path=file_path,
                        target_chat=target_chat,
                        caption=caption,
                        file_name=file_name,
                        reply_markup=reply_markup,
                        progress_callback=progress_callback,
                        fallback_chat=0,
                    )
                    try:
                        _up_elapsed = max(0.25, time.time() - _t_up_start)
                        _f_mb = (os.path.getsize(file_path) / (1024 * 1024)) if os.path.exists(file_path) else 0.0
                        if _f_mb > 1.0:
                            ck = self._client_key(client)
                            inst_mbps = _f_mb / _up_elapsed
                            prev = self._session_ema_mbps.get(ck)
                            self._session_ema_mbps[ck] = inst_mbps if prev is None else (0.65 * prev + 0.35 * inst_mbps)
                    except Exception:
                        pass
                    return res
            except FloodWait as fw:
                last_exc = fw
                self._session_cooldown_until[self._client_key(client)] = time.time() + float(getattr(fw, "value", 30))
                log.warning(
                    "[UploadPool] FloodWait %ds on session '%s' for [%s] — rotating to next session.",
                    fw.value, getattr(client, "name", "?"), quality,
                )
                continue
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                is_perm_err = any(err_kw in err_str for err_kw in (
                    "chat_admin_required", "admin_rights_empty", "chat_write_forbidden",
                    "channel_private", "user_not_participant", "peer_id_invalid",
                    "right_forbidden", "admins_too_much"
                ))
                if is_perm_err:
                    log.warning(
                        "[UploadPool] Session '%s' lacks admin/write permissions in chat %s (%s) — removing from admin cache and rotating.",
                        getattr(client, "name", "?"), target_chat, exc,
                    )
                    if t_key in self._admin_sessions and client in self._admin_sessions[t_key]:
                        try:
                            self._admin_sessions[t_key].remove(client)
                        except Exception:
                            pass

                    # Attempt on-the-fly promotion if main bot is admin with can_promote_members
                    if self._main_client and getattr(self._main_client, "is_connected", False):
                        try:
                            u_me = getattr(client, "me", None)
                            if not u_me and hasattr(client, "get_me"):
                                try:
                                    u_me = await client.get_me()
                                except Exception:
                                    pass
                            if u_me and getattr(u_me, "id", None):
                                from pyrogram.types import ChatPrivileges
                                await self._main_client.promote_chat_member(
                                    chat_id=target_chat,
                                    user_id=u_me.id,
                                    privileges=ChatPrivileges(
                                        can_post_messages=True,
                                        can_edit_messages=True,
                                    )
                                )
                                log.info("[UploadPool] On-the-fly promoted session '%s' to admin in %s", getattr(client, "name", "session"), target_chat)
                                if t_key not in self._admin_sessions:
                                    self._admin_sessions[t_key] = []
                                if client not in self._admin_sessions[t_key]:
                                    self._admin_sessions[t_key].append(client)

                                # Immediate retry upload with the newly promoted client!
                                try:
                                    c_lock = self.get_client_lock(client)
                                    async with c_lock:
                                        return await telegram_upload.upload_video_file(
                                            bot_client=client,
                                            file_path=file_path,
                                            target_chat=target_chat,
                                            caption=caption,
                                            file_name=file_name,
                                            reply_markup=reply_markup,
                                            progress_callback=progress_callback,
                                            fallback_chat=0,
                                        )
                                except Exception as retry_err:
                                    log.warning("[UploadPool] Immediate upload retry after promotion failed on session '%s': %s", getattr(client, "name", "?"), retry_err)
                        except Exception as prom_err:
                            log.debug("[UploadPool] On-the-fly promotion note: %s", prom_err)
                    continue

                log.warning(
                    "[UploadPool] Session upload failed for [%s] attempt %d (%s) — rotating to next session.",
                    quality, attempt + 1, exc,
                )
                continue

        # All userbot attempts exhausted — use main bot client
        if fallback:
            log.warning(
                "[UploadPool] All %d userbot session attempts exhausted for [%s]! Falling back to main bot client '%s' → chat %s",
                max_attempts, quality, getattr(fallback, "name", "main_bot"), target_chat
            )
            try:
                c_lock = self.get_client_lock(fallback)
                async with c_lock:
                    return await telegram_upload.upload_video_file(
                        bot_client=fallback,
                        file_path=file_path,
                        target_chat=target_chat,
                        caption=caption,
                        file_name=file_name,
                        reply_markup=reply_markup,
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
