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
from typing import AsyncGenerator, Dict, List, Optional, Union

from pyrogram import Client, types
from pyrogram.errors import FloodWait, PeerIdInvalid

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

    def set_main_client(self, client: Client) -> None:
        """Register the primary bot client running in main.py."""
        self._main_client = client
        if client not in self.clients:
            self.clients.insert(0, client)
            log.info("[StreamPool] Registered main bot client into streaming pool.")

    async def init_extra_sessions(self, api_id: int, api_hash: str, sessions_dir: str = "bot/sessions") -> None:
        """
        Detect and start any additional Pyrogram .session files in sessions_dir
        or SESSION_STRINGS env variable (e.g. for 20+ session scaling).
        """
        # Ensure sessions directory exists
        if not os.path.exists(sessions_dir):
            try:
                os.makedirs(sessions_dir, exist_ok=True)
            except Exception:
                pass

        # 1. Check directory for .session files
        if os.path.exists(sessions_dir):
            session_files = glob.glob(os.path.join(sessions_dir, "*.session"))
            for s_path in session_files:
                base_name = os.path.splitext(os.path.basename(s_path))[0]
                session_prefix = os.path.join(sessions_dir, base_name)
                # Skip if already loaded
                if any(getattr(c, "name", "") == session_prefix for c in self.clients):
                    continue
                try:
                    c = Client(
                        name=session_prefix,
                        api_id=api_id,
                        api_hash=api_hash,
                        no_updates=True,
                    )
                    await c.start()
                    self.clients.append(c)
                    log.info("[StreamPool] Loaded extra session file: %s", base_name)
                except Exception as exc:
                    log.warning("[StreamPool] Could not start session '%s': %s", base_name, exc)

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
                    )
                    await c.start()
                    self.clients.append(c)
                    log.info("[StreamPool] Loaded session string #%d into streaming pool.", i)
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

        if not msg:
            # Fallback to main client if different
            if self._main_client and self._main_client != client and getattr(self._main_client, "is_connected", False):
                try:
                    msg = await self._main_client.get_messages(chat_id_int, message_id)
                except Exception as mc_err:
                    log.warning("[StreamPool] Main client also failed to get message: %s", mc_err)

        if not msg:
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

    async def stream_media_chunks(
        self,
        media_source: Union[types.Message, str],
        start: int,
        end: int,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream exact byte range [start, end] from Telegram MTProto.
        Yields chunk by chunk for smooth browser playback.
        """
        client = await self.get_client()
        start_chunk = start // CHUNK_SIZE
        end_chunk = end // CHUNK_SIZE
        total_chunks = end_chunk - start_chunk + 1

        offset = start
        remaining = end - start + 1

        try:
            async for chunk in client.stream_media(
                media_source,
                offset=start_chunk,
                limit=total_chunks,
            ):
                if not chunk or remaining <= 0:
                    break

                # For the first chunk, slice if start was not aligned to CHUNK_SIZE boundary
                if offset == start:
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

        except asyncio.CancelledError:
            # Client closed the connection, paused, or scrubbed elsewhere
            log.debug("[StreamPool] Client cancelled or scrubbed stream range %d-%d", start, end)
            return
        except FloodWait as fw:
            log.warning("[StreamPool] FloodWait %ds on streaming client. Retrying with next client...", fw.value)
            if remaining > 0:
                client = await self.get_client()
                async for chunk in self.stream_media_chunks(media_source, offset, end):
                    yield chunk
        except Exception as exc:
            log.error("[StreamPool] Error streaming chunk from Telegram: %s", exc)

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
