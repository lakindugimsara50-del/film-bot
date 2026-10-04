"""
session_pool.py — Multi-client Telegram streaming pool.

Manages the primary bot client and optional secondary userbot session files
for parallel, high-throughput media streaming without hitting Telegram FloodWait.
Supports up to 20+ session files placed in the `bot/sessions/` directory or
via SESSION_STRINGS environment variable.
"""

import asyncio
import glob
import logging
import math
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from pyrogram import Client, types
from pyrogram.errors import FloodWait, PeerIdInvalid, SessionPasswordNeeded

log = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1 MiB — must match Pyrogram's internal MTProto block size (DO NOT change)
STREAM_BATCH = 8           # fetch 8 × 1 MiB = 8 MiB per batch → 2× throughput, fewer Telegram round-trips


class TelegramStreamPool:
    """Manages one or more Pyrogram clients for streaming media from Telegram."""

    def __init__(self) -> None:
        self.clients: List[Client] = []
        self._index: int = 0
        self._lock = asyncio.Lock()
        self._main_client: Optional[Client] = None
        self._client_cooldowns: Dict[Any, float] = {}
        self._msg_cache: Dict[str, types.Message] = {}

    def set_main_client(self, client: Client) -> None:
        """Register the primary bot client running in main.py."""
        self._main_client = client
        if hasattr(client, "max_concurrent_transmissions"):
            client.max_concurrent_transmissions = 10
        if client not in self.clients:
            self.clients.insert(0, client)
            log.info("[StreamPool] Registered main bot client into streaming pool (10x transmission enabled).")

    async def init_extra_sessions(self, api_id: int, api_hash: str, sessions_dir: str = None) -> None:
        """
        Detect and start any additional Pyrogram .session files in sessions_dir
        or SESSION_STRINGS env variable (e.g. for 20+ to 100 session scaling).
        """
        if not sessions_dir or sessions_dir == "bot/sessions":
            _streaming_dir = os.path.dirname(os.path.abspath(__file__))
            _bot_dir = os.path.dirname(_streaming_dir)
            sessions_dir = os.path.join(_bot_dir, "sessions")

        # Ensure sessions directory exists
        if not os.path.exists(sessions_dir):
            try:
                os.makedirs(sessions_dir, exist_ok=True)
            except Exception:
                pass

        # If upload_pool already has clients, reuse verified channel admin sessions directly to avoid SQLite 'database is locked' errors!
        try:
            import config
            from services.upload_pool import upload_pool
            if upload_pool.clients:
                target_ch = getattr(config, "PRIVATE_CHANNEL_ID", 0)
                admin_sessions = upload_pool.get_admin_sessions_cached(target_ch) if target_ch else []
                source_clients = admin_sessions if admin_sessions else upload_pool.clients
                for c in source_clients:
                    if c not in self.clients:
                        self.clients.append(c)
                log.info("[StreamPool] Reused %d clients from upload_pool (admin prioritized: %s).", len(self.clients), bool(admin_sessions))
                return
        except Exception:
            pass

        password = os.getenv("SESSION_2FA_PASSWORD", "2122138")

        # 1. Check directory for .session files
        if os.path.exists(sessions_dir):
            session_files = sorted(glob.glob(os.path.join(sessions_dir, "*.session")))
            if session_files:
                log.info("[StreamPool] Concurrently loading %d streaming sessions from %s...", len(session_files), sessions_dir)
                sem = asyncio.Semaphore(10)

                async def _load_one(s_path: str):
                    base_name = os.path.splitext(os.path.basename(s_path))[0]
                    session_prefix = os.path.join(sessions_dir, base_name)
                    # Skip if already loaded
                    if any(getattr(c, "name", "") == session_prefix for c in self.clients):
                        return
                    async with sem:
                        try:
                            try:
                                from services.upload_pool import _ensure_pyrogram_session
                                _ensure_pyrogram_session(s_path, api_id)
                            except Exception:
                                pass

                            c = Client(
                                name=session_prefix,
                                api_id=api_id,
                                api_hash=api_hash,
                                no_updates=True,
                                max_concurrent_transmissions=10,
                            )
                            is_auth = await c.connect()
                            if not is_auth:
                                await c.disconnect()
                                return
                            try:
                                await c.initialize()
                            except Exception as init_e:
                                await c.disconnect()
                                log.warning("[StreamPool] Session '%s' init error: %s", base_name, init_e)
                                return

                            try:
                                await c.get_me()
                                self.clients.append(c)
                                log.info("[StreamPool] Loaded extra session file: %s (10x transmission enabled)", base_name)
                            except SessionPasswordNeeded:
                                try:
                                    await c.check_password(password)
                                    self.clients.append(c)
                                    log.info("[StreamPool] 2FA OK for stream session: %s", base_name)
                                except Exception as pw_e:
                                    if c.is_initialized:
                                        await c.stop()
                                    elif c.is_connected:
                                        await c.disconnect()
                                    log.warning("[StreamPool] 2FA failed for stream session '%s': %s", base_name, pw_e)
                            except Exception as auth_e:
                                if c.is_initialized:
                                    await c.stop()
                                elif c.is_connected:
                                    await c.disconnect()
                                log.warning("[StreamPool] Could not start session '%s': %s", base_name, auth_e)
                        except Exception as exc:
                            log.warning("[StreamPool] Could not start session '%s': %s", base_name, exc)

                await asyncio.gather(*[_load_one(sp) for sp in session_files])

        # 2. Check SESSION_STRINGS env (comma-separated session strings)
        raw_strings = os.getenv("SESSION_STRINGS", "").strip()
        if raw_strings:
            for i, s_str in enumerate(raw_strings.split(","), start=1):
                s_str = s_str.strip()
                if not s_str:
                    continue
                worker_name = f"stream_pool_worker_{i}"
                if any(getattr(c, "name", "") == worker_name for c in self.clients):
                    continue
                try:
                    c = Client(
                        name=worker_name,
                        session_string=s_str,
                        api_id=api_id,
                        api_hash=api_hash,
                        no_updates=True,
                        max_concurrent_transmissions=10,
                    )
                    await c.start()
                    self.clients.append(c)
                    log.info("[StreamPool] Loaded session string #%d into streaming pool (10x transmission enabled).", i)
                except Exception as exc:
                    log.warning("[StreamPool] Could not start session string #%d: %s", i, exc)

        log.info("[StreamPool] Total active streaming clients in pool: %d", len(self.clients))

    @staticmethod
    def normalize_chat_id(chat_id: Union[int, str]) -> int:
        """Normalize chat ID from string, positive channel ID, or special keyword."""
        s = str(chat_id).strip()
        if not s or s.lower() in ("0", "private", "default", "channel"):
            import config
            return config.PRIVATE_CHANNEL_ID
        if s.startswith("-100"):
            return int(s)
        if s.startswith("-"):
            return int(s)
        if len(s) >= 10:
            return int(f"-100{s}")
        return int(s)

    async def get_client(self, chat_id: Optional[Union[int, str]] = None) -> Client:
        """Round-robin through active, connected clients suitable for the target chat."""
        async with self._lock:
            candidates = self.clients
            if chat_id is not None:
                chat_id_int = self.normalize_chat_id(chat_id)
                if chat_id_int < 0:
                    # Target is a channel: prioritize verified channel admin sessions or main client
                    try:
                        from services.upload_pool import upload_pool
                        admin_pool = upload_pool.get_admin_sessions_cached(chat_id_int)
                        if admin_pool:
                            candidates = [c for c in admin_pool if getattr(c, "is_connected", False)]
                    except Exception:
                        pass
                    if not candidates and self._main_client and getattr(self._main_client, "is_connected", False):
                        return self._main_client

            connected = [c for c in candidates if getattr(c, "is_connected", False)]
            if not connected:
                if self._main_client and getattr(self._main_client, "is_connected", False):
                    return self._main_client
                if self.clients:
                    return self.clients[0]
                raise RuntimeError("No active Telegram clients in streaming pool.")

            self._index = (self._index + 1) % len(connected)
            return connected[self._index]

    async def get_media_info(self, chat_id: Union[int, str], message_id: int) -> dict:
        """Fetch message from Telegram and extract media details."""
        chat_id_int = self.normalize_chat_id(chat_id)
        is_channel = chat_id_int < 0
        msg = None

        # 1. Primary: Use main bot client directly for channel messages
        # Main bot is a guaranteed channel admin with valid MTProto channel access & message peers.
        if is_channel and self._main_client and getattr(self._main_client, "is_connected", False):
            try:
                msg = await self._main_client.get_messages(chat_id_int, message_id)
            except Exception as mc_err:
                log.warning("[StreamPool] Main client get_messages error for channel %s/%s: %s", chat_id_int, message_id, mc_err)

        # 2. If message was not fetched by main client, try candidate clients
        if not msg or getattr(msg, "empty", False):
            candidates: List[Client] = []
            if is_channel:
                try:
                    from services.upload_pool import upload_pool
                    admin_pool = upload_pool.get_admin_sessions_cached(chat_id_int)
                    candidates.extend([c for c in admin_pool if getattr(c, "is_connected", False)])
                except Exception:
                    pass
            for c in self.clients:
                if c not in candidates and getattr(c, "is_connected", False):
                    candidates.append(c)

            for client in candidates:
                try:
                    msg = await client.get_messages(chat_id_int, message_id)
                    if msg and not getattr(msg, "empty", False):
                        break
                except PeerIdInvalid:
                    try:
                        await client.get_chat(chat_id_int)
                        msg = await client.get_messages(chat_id_int, message_id)
                        if msg and not getattr(msg, "empty", False):
                            break
                    except Exception:
                        pass
                except Exception as c_err:
                    log.debug("[StreamPool] Client %s get_messages error: %s", getattr(client, "name", ""), c_err)
                    continue

        # 3. Final fallback to main client
        if not msg or getattr(msg, "empty", False):
            if self._main_client and getattr(self._main_client, "is_connected", False):
                try:
                    msg = await self._main_client.get_messages(chat_id_int, message_id)
                except Exception as mc_err:
                    log.warning("[StreamPool] Final fallback to main client failed: %s", mc_err)

        if not msg or getattr(msg, "empty", False):
            raise ValueError(f"Message {message_id} not found in chat {chat_id}")

        media = msg.video or msg.document
        if not media:
            raise ValueError(f"Message {message_id} has no downloadable video/document media.")

        file_size = getattr(media, "file_size", 0)
        file_name = getattr(media, "file_name", f"video_{message_id}.mp4")
        mime_type = getattr(media, "mime_type", "video/mp4") or "video/mp4"

        return {
            "message": msg,
            "media": media,
            "file_size": file_size,
            "file_name": file_name,
            "mime_type": mime_type,
        }

    async def get_channel_admin_uids(self, channel_id: int) -> set[int]:
        """Fetch and cache verified channel admin user IDs (via Bot API or upload_pool)."""
        now = time.time()
        if not hasattr(self, "_admin_uids_cache"):
            self._admin_uids_cache: Dict[int, tuple[float, set[int]]] = {}

        try:
            ch_key = int(channel_id)
        except Exception:
            ch_key = channel_id

        if ch_key in self._admin_uids_cache:
            cache_time, uids = self._admin_uids_cache[ch_key]
            if now - cache_time < 900 and uids:
                return uids

        admin_uids: set[int] = set()

        # 1. Query Telegram Bot API (instant and authoritative)
        try:
            import config, urllib.request, json
            token = getattr(config, "BOT_TOKEN", "") or os.getenv("BOT_TOKEN", "")
            if token:
                api_url = f"https://api.telegram.org/bot{token}/getChatAdministrators?chat_id={channel_id}"
                req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = json.loads(r.read().decode("utf-8", errors="ignore"))
                    if data.get("ok"):
                        for a in data.get("result", []):
                            u = a.get("user", {})
                            if u and not u.get("is_bot", False):
                                admin_uids.add(u.get("id"))
        except Exception as api_err:
            log.debug("[StreamPool] Bot API admin query note: %s", api_err)

        # 2. Fallback to upload_pool cache
        if not admin_uids:
            try:
                from services.upload_pool import upload_pool
                for c in upload_pool.get_admin_sessions_cached(channel_id):
                    c_uid = getattr(getattr(c, "me", None), "id", None)
                    if c_uid:
                        admin_uids.add(c_uid)
            except Exception:
                pass

        if admin_uids:
            self._admin_uids_cache[ch_key] = (now, admin_uids)
            log.info("[StreamPool] Verified %d human channel administrators for channel %s", len(admin_uids), channel_id)

        return admin_uids

    async def _get_client_media(
        self,
        client: Client,
        media_source: Union[types.Message, str],
    ) -> Union[types.Message, str]:
        """Ensure media_source is bound to the target client with a valid file_reference."""
        if not isinstance(media_source, types.Message):
            return media_source

        # If already bound to this client
        if getattr(media_source, "_client", None) is client:
            return media_source

        if client is self._main_client and getattr(media_source, "_client", None) is self._main_client:
            return media_source

        chat_id = getattr(getattr(media_source, "chat", None), "id", None)
        msg_id = getattr(media_source, "id", None)
        if not chat_id or not msg_id:
            return media_source

        cache_key = f"{id(client)}:{chat_id}:{msg_id}"
        if cache_key in self._msg_cache:
            return self._msg_cache[cache_key]

        try:
            client_msg = await client.get_messages(chat_id, msg_id)
            if client_msg and not getattr(client_msg, "empty", False):
                self._msg_cache[cache_key] = client_msg
                if len(self._msg_cache) > 200:
                    self._msg_cache.pop(next(iter(self._msg_cache)))
                return client_msg
        except PeerIdInvalid:
            try:
                await client.get_chat(chat_id)
                client_msg = await client.get_messages(chat_id, msg_id)
                if client_msg and not getattr(client_msg, "empty", False):
                    self._msg_cache[cache_key] = client_msg
                    return client_msg
            except Exception:
                pass
        except Exception as ref_err:
            log.debug("[StreamPool] Client %s cannot fetch message %s/%s for file_ref: %s", getattr(client, "name", ""), chat_id, msg_id, ref_err)
            raise ref_err

        return media_source

    async def _fetch_chunk(
        self,
        media_source: Union[types.Message, str],
        chunk_idx: int,
    ) -> bytes:
        """
        Fetch a single 1 MiB chunk (offset=chunk_idx, limit=1) from Telegram MTProto.
        Balances concurrent chunk requests across available connected pool clients via
        session rotation to maximize throughput without hitting FloodWait.
        Filters out clients currently on rate-limit cooldown and falls back to primary client.
        """
        primary_client = getattr(media_source, "_client", None)
        if not primary_client or not getattr(primary_client, "is_connected", False):
            primary_client = self._main_client

        connected_clients = [c for c in self.clients if getattr(c, "is_connected", False)]
        if not connected_clients:
            if primary_client and getattr(primary_client, "is_connected", False):
                connected_clients = [primary_client]
            else:
                try:
                    cl = await self.get_client()
                    if cl and getattr(cl, "is_connected", False):
                        connected_clients = [cl]
                except Exception:
                    pass

        if not connected_clients:
            if primary_client:
                connected_clients = [primary_client]
            else:
                raise RuntimeError("No active Telegram clients in streaming pool.")

        # Prioritize clients not currently on rate-limit or error cooldown
        now = time.time()
        active_candidates = [c for c in connected_clients if self._client_cooldowns.get(c, 0.0) <= now]
        if not active_candidates:
            # All clients on cooldown; use all connected clients as fallback
            active_candidates = connected_clients

        # Round-robin rotate candidate starting client by chunk_idx across available clients
        n = len(active_candidates)
        rotated: List[Client] = []
        if n > 0:
            start_rot = chunk_idx % n
            rotated = active_candidates[start_rot:] + active_candidates[:start_rot]

        # For Telegram channel messages (types.Message in -100 channels), primary_client (main bot)
        # and verified channel admin accounts are the ONLY clients that can access the private channel.
        # For public file_id / string media, rotate across all pool clients for load balancing.
        is_channel_msg = (
            isinstance(media_source, types.Message) and
            getattr(media_source, "chat", None) and
            (
                str(getattr(media_source.chat, "id", "")).startswith("-100") or
                int(getattr(media_source.chat, "id", 0) or 0) < 0
            )
        )

        candidates: List[Client] = []
        if is_channel_msg:
            ch_id = getattr(media_source.chat, "id")
            admin_uids = await self.get_channel_admin_uids(ch_id)

            # 1. TOP PRIORITY: Any connected client whose user_id is a verified channel administrator!
            # Userbot accounts stream at unthrottled wire speed (20-30 MB/s)!
            for c in self.clients:
                if not getattr(c, "is_connected", False):
                    continue
                if self._client_cooldowns.get(c, 0.0) > now:
                    continue
                c_me = getattr(c, "me", None)
                c_uid = getattr(c_me, "id", None)
                if c_uid and c_uid in admin_uids and c is not self._main_client and c is not primary_client:
                    if c not in candidates:
                        candidates.append(c)

            # Also check upload_pool clients if any
            try:
                from services.upload_pool import upload_pool
                for c in upload_pool.clients:
                    if not getattr(c, "is_connected", False):
                        continue
                    if self._client_cooldowns.get(c, 0.0) > now:
                        continue
                    c_me = getattr(c, "me", None)
                    c_uid = getattr(c_me, "id", None)
                    if c_uid and c_uid in admin_uids and c is not self._main_client and c is not primary_client:
                        if c not in candidates:
                            candidates.append(c)
            except Exception:
                pass

            # Rotate verified admin userbots across chunk_idx to balance MTProto streaming load
            if len(candidates) > 1:
                rot_idx = chunk_idx % len(candidates)
                candidates = candidates[rot_idx:] + candidates[:rot_idx]

            # 2. Main Bot Client as Fallback
            bot_fallbacks = []
            if primary_client and getattr(primary_client, "is_connected", False) and primary_client not in candidates:
                bot_fallbacks.append(primary_client)
            if self._main_client and getattr(self._main_client, "is_connected", False) and self._main_client not in candidates and self._main_client not in bot_fallbacks:
                bot_fallbacks.append(self._main_client)

            if not candidates:
                candidates = bot_fallbacks
            else:
                candidates.extend(bot_fallbacks)
        else:
            for c in rotated:
                if c not in candidates:
                    candidates.append(c)
            if primary_client and primary_client not in candidates and getattr(primary_client, "is_connected", False):
                candidates.append(primary_client)

        last_exc = None
        max_attempts = 2

        for attempt in range(max_attempts):
            for client in candidates:
                buf = bytearray()
                stream_gen = None
                try:
                    target_media = await self._get_client_media(client, media_source)
                    stream_gen = client.stream_media(target_media, offset=chunk_idx, limit=1)

                    async def _collect():
                        async for piece in stream_gen:
                            if piece:
                                buf.extend(piece)

                    # 6-second timeout avoids blocking video playback if a secondary socket stalls
                    await asyncio.wait_for(_collect(), timeout=6.0)
                    if buf:
                        # Client succeeded: clear cooldown
                        self._client_cooldowns.pop(client, None)
                        return bytes(buf)
                except FloodWait as fw:
                    wait_sec = min(fw.value, 1.5)
                    log.warning("[StreamPool] FloodWait %ds on chunk %d with client %s, pausing %0.1fs", fw.value, chunk_idx, getattr(client, "name", ""), wait_sec)
                    self._client_cooldowns[client] = time.time() + min(max(float(fw.value), 2.0), 60.0)
                    last_exc = fw
                    await asyncio.sleep(wait_sec)
                    continue
                except (asyncio.CancelledError, ConnectionResetError):
                    # Clean cancellation during timeline seek / scrubbing
                    raise
                except asyncio.TimeoutError:
                    log.warning("[StreamPool] Timeout (6s) fetching chunk %d with client %s, trying next candidate", chunk_idx, getattr(client, "name", ""))
                    self._client_cooldowns[client] = time.time() + 10.0
                    last_exc = TimeoutError(f"Timeout fetching chunk {chunk_idx}")
                    continue
                except Exception as exc:
                    last_exc = exc
                    self._client_cooldowns[client] = time.time() + 5.0
                    log.debug("[StreamPool] Client %s cannot stream chunk %d: %s", getattr(client, "name", ""), chunk_idx, exc)
                    continue
                finally:
                    if stream_gen is not None:
                        try:
                            await stream_gen.aclose()
                        except Exception:
                            pass

            if attempt < max_attempts - 1:
                await asyncio.sleep(0.3)

        if last_exc:
            log.warning("[StreamPool] All candidate clients failed for chunk %d: %s", chunk_idx, last_exc)
            raise last_exc
        return b""

    async def stream_media_chunks(
        self,
        media_source: Union[types.Message, str],
        start: int,
        end: int,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream exact byte range [start, end] from Telegram MTProto using
        multi-connection parallel chunk pipelining for 6-10 MB/s throughput.
        Fetches 1 MiB chunks concurrently across available clients in a sliding window.
        """
        if start > end or start < 0:
            return

        start_chunk = start // CHUNK_SIZE
        end_chunk = end // CHUNK_SIZE
        total_chunks = end_chunk - start_chunk + 1

        offset = start
        remaining = end - start + 1
        if remaining <= 0:
            return

        pending_tasks: Dict[int, asyncio.Task] = {}

        try:
            if total_chunks <= 1:
                # Single-chunk fast-path: fetch single 1MB block directly
                chunk = await self._fetch_chunk(media_source, start_chunk)
                if chunk:
                    local_start = start % CHUNK_SIZE
                    slice_chunk = chunk[local_start:local_start + remaining]
                    if slice_chunk:
                        yield slice_chunk
                return

            # Concurrency tuning:
            # If only 1 client is available (e.g. main bot), use concurrency 2 (lean pipeline, zero flood throttle).
            # If multiple verified admin userbots exist, scale concurrency up to 6 workers for 20-30 MB/s.
            is_ch = (
                isinstance(media_source, types.Message) and
                getattr(media_source, "chat", None) and
                (
                    str(getattr(media_source.chat, "id", "")).startswith("-100") or
                    int(getattr(media_source.chat, "id", 0) or 0) < 0
                )
            )
            active_workers = 1
            if is_ch:
                try:
                    from services.upload_pool import upload_pool
                    ch_id = getattr(media_source.chat, "id")
                    active_workers = len(upload_pool.get_admin_sessions_cached(ch_id)) or 1
                except Exception:
                    active_workers = 1
            else:
                active_workers = len([c for c in self.clients if getattr(c, "is_connected", False)]) or 1

            concurrency = min(total_chunks, max(2, min(active_workers * 2, 6)))

            # Pre-launch initial batch of concurrent chunk fetches
            for c in range(start_chunk, min(start_chunk + concurrency, end_chunk + 1)):
                pending_tasks[c] = asyncio.create_task(self._fetch_chunk(media_source, c))

            next_to_schedule = start_chunk + concurrency

            for curr_chunk in range(start_chunk, end_chunk + 1):
                task = pending_tasks.get(curr_chunk)
                if not task:
                    task = asyncio.create_task(self._fetch_chunk(media_source, curr_chunk))
                    pending_tasks[curr_chunk] = task

                # Schedule next chunk into sliding window pipeline
                if next_to_schedule <= end_chunk:
                    pending_tasks[next_to_schedule] = asyncio.create_task(
                        self._fetch_chunk(media_source, next_to_schedule)
                    )
                    next_to_schedule += 1

                chunk = await task
                # Pop after await completes so that cancellation during await task will cleanly cancel task in finally:
                pending_tasks.pop(curr_chunk, None)

                if not chunk or remaining <= 0:
                    break

                # Slicing: adjust start chunk boundary
                if curr_chunk == start_chunk:
                    local_start = start % CHUNK_SIZE
                    slice_chunk = chunk[local_start:]
                else:
                    slice_chunk = chunk

                if len(slice_chunk) > remaining:
                    slice_chunk = slice_chunk[:remaining]

                yield slice_chunk
                remaining -= len(slice_chunk)
                offset += len(slice_chunk)
                if remaining <= 0:
                    break

        except (asyncio.CancelledError, ConnectionResetError) as abort_exc:
            log.debug("[StreamPool] Stream range %d-%d cancelled/aborted (%s)", start, end, type(abort_exc).__name__)
            return
        except Exception as exc:
            if exc.__class__.__name__ in ("ClientDisconnect", "EndOfStream"):
                log.debug("[StreamPool] Stream range %d-%d client disconnected", start, end)
                return
            log.error("[StreamPool] Parallel chunk streaming pipeline error: %s", exc)
        finally:
            # Cleanly cancel and await all in-flight prefetch tasks to free MTProto workers immediately
            for t in list(pending_tasks.values()):
                if not t.done():
                    t.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks.values(), return_exceptions=True)
            pending_tasks.clear()

    def get_status(self) -> Dict[str, Union[int, str, List[str]]]:
        """Get pool diagnostic status."""
        connected = [c for c in self.clients if getattr(c, "is_connected", False)]
        return {
            "total_clients": len(self.clients),
            "connected_clients": len(connected),
            "sessions_supported": "up_to_20+",
            "chunk_size_bytes": CHUNK_SIZE,
        }


stream_pool = TelegramStreamPool()
