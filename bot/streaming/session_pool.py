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
STREAM_BATCH = 4           # fetch 4 × 1 MiB = 4 MiB per browser request for fast buffering


class TelegramStreamPool:
    """Manages one or more Pyrogram clients for streaming media from Telegram."""

    def __init__(self) -> None:
        self.clients: List[Client] = []
        self._index: int = 0
        self._lock = asyncio.Lock()
        self._main_client: Optional[Client] = None
        self._client_cooldowns: Dict[Any, float] = {}

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

        # If upload_pool already has clients, reuse them directly to avoid SQLite 'database is locked' errors!
        try:
            from services.upload_pool import upload_pool
            if upload_pool.clients:
                for c in upload_pool.clients:
                    if c not in self.clients:
                        self.clients.append(c)
                log.info("[StreamPool] Reused %d clients from upload_pool (zero lock contention).", len(self.clients))
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

    async def get_client(self) -> Client:
        """Round-robin through active, connected clients."""
        async with self._lock:
            if not self.clients:
                if self._main_client:
                    return self._main_client
                raise RuntimeError("No active Telegram clients in streaming pool.")

            connected = [c for c in self.clients if getattr(c, "is_connected", False)]
            if not connected:
                if self._main_client and getattr(self._main_client, "is_connected", False):
                    return self._main_client
                return self.clients[0]

            self._index = (self._index + 1) % len(connected)
            return connected[self._index]

    async def get_media_info(self, chat_id: Union[int, str], message_id: int) -> dict:
        """Fetch message from Telegram and extract media details."""
        chat_id_int = self.normalize_chat_id(chat_id)
        client = await self.get_client()

        msg = None
        try:
            msg = await client.get_messages(chat_id_int, message_id)
        except PeerIdInvalid:
            # Attempt to prime chat peer on client
            try:
                await client.get_chat(chat_id_int)
                msg = await client.get_messages(chat_id_int, message_id)
            except Exception as prime_err:
                log.warning("[StreamPool] Failed priming peer %s: %s", chat_id_int, prime_err)

        if not msg or getattr(msg, "empty", False):
            # Fallback to main client if different
            if self._main_client and self._main_client != client and getattr(self._main_client, "is_connected", False):
                try:
                    msg = await self._main_client.get_messages(chat_id_int, message_id)
                except Exception as mc_err:
                    log.warning("[StreamPool] Main client also failed to get message: %s", mc_err)

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
        candidates: List[Client] = []
        n = len(active_candidates)
        if n > 0:
            start_rot = chunk_idx % n
            rotated = active_candidates[start_rot:] + active_candidates[:start_rot]
            for c in rotated:
                if c not in candidates:
                    candidates.append(c)

        # Ensure primary_client is always present as ultimate fallback (guaranteed channel access)
        if primary_client and primary_client not in candidates and getattr(primary_client, "is_connected", False):
            candidates.append(primary_client)

        last_exc = None
        max_attempts = 2

        for attempt in range(max_attempts):
            for client in candidates:
                buf = bytearray()
                stream_gen = None
                try:
                    stream_gen = client.stream_media(media_source, offset=chunk_idx, limit=1)

                    async def _collect():
                        async for piece in stream_gen:
                            if piece:
                                buf.extend(piece)

                    # 20-second timeout avoids hanging forever if a TCP socket stalls
                    await asyncio.wait_for(_collect(), timeout=20.0)
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
                    log.warning("[StreamPool] Timeout (20s) fetching chunk %d with client %s, trying next candidate", chunk_idx, getattr(client, "name", ""))
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
                # Single-chunk fast-path
                chunk = await self._fetch_chunk(media_source, start_chunk)
                if chunk:
                    local_start = start % CHUNK_SIZE
                    slice_chunk = chunk[local_start:local_start + remaining]
                    if slice_chunk:
                        yield slice_chunk
                return

            # Pipelined concurrent chunk fetching across clients
            # Sliding window concurrency: 4-6 parallel workers
            concurrency = min(total_chunks, max(4, len(self.clients) * 2), 6)

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
