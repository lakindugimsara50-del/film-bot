"""
progress_service.py — Live upload/download progress messages for Telegram.

Provides a single ProgressReporter that:
  • Sends an initial status message and stores its message_id.
  • Edits that same message every 3 seconds (no duplicate messages).
  • Formats:  ⏳ 42.5%  (450 MB / 1 050 MB)  @  8.3 MB/s  ETA 72 s
"""

import asyncio
import logging
import time
from typing import Any, Optional

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

log = logging.getLogger(__name__)

# Minimum seconds between two consecutive edits to avoid Telegram rate-limits
_EDIT_INTERVAL = 3.0


class ProgressReporter:
    """
    Single-message progress reporter.

    Usage::

        reporter = ProgressReporter(client, chat_id, title="Inception (2010)")
        await reporter.start()
        # … inside your upload loop:
        await reporter.update(transferred=current_bytes, total=total_bytes)
        await reporter.finish("✅ Upload complete!")
    """

    def __init__(
        self,
        client: Client,
        chat_id: int,
        title: str = "",
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Any] = None,
    ) -> None:
        self._client = client
        self._chat_id = chat_id
        self._title = title
        self._reply_id = reply_to_message_id
        self._reply_markup = reply_markup

        self._msg: Optional[Message] = None
        self._last_edit: float = 0.0
        self._start_time: float = time.time()
        self._lock = asyncio.Lock()

    def set_reply_markup(self, reply_markup: Optional[Any]) -> None:
        """Dynamically update or attach reply_markup (e.g. Cancel button)."""
        self._reply_markup = reply_markup

    async def start(self, text: Optional[str] = None) -> None:
        """Send the initial progress message."""
        body = text or (
            f"⏳ <b>ආරම්භ කරමින් පවතී…</b>\n"
            f"🎬 {self._title}"
        )
        try:
            kwargs = {}
            if self._reply_markup:
                kwargs["reply_markup"] = self._reply_markup
            self._msg = await self._client.send_message(
                chat_id=self._chat_id,
                text=body,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=self._reply_id,
                **kwargs,
            )
            self._start_time = time.time()
            self._last_edit = time.time()
        except Exception as exc:
            log.warning("[ProgressService] Could not send start message: %s", exc)

    async def update(self, transferred: int, total: int) -> None:
        """
        Update the progress message if _EDIT_INTERVAL seconds have passed.
        Safe to call from any asyncio task.
        """
        now = time.time()
        if now - self._last_edit < _EDIT_INTERVAL:
            return
        if not self._msg:
            return

        async with self._lock:
            # Re-check after acquiring lock
            if time.time() - self._last_edit < _EDIT_INTERVAL:
                return

            percent = (transferred / total * 100) if total else 0.0
            elapsed = max(now - self._start_time, 0.001)
            speed_bps = transferred / elapsed          # bytes / sec
            speed_mb  = speed_bps / (1024 * 1024)     # MB / s
            remaining = (total - transferred) / speed_bps if speed_bps > 0 else 0

            xfr_mb  = transferred / (1024 * 1024)
            tot_mb  = total       / (1024 * 1024)

            bar_filled = int(percent / 5)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)

            eta_str = f"{int(remaining)} s" if remaining < 3600 else f"{remaining/3600:.1f} h"

            text = (
                f"⏳ <b>{percent:.1f}%</b>\n"
                f"<code>[{bar}]</code>\n\n"
                f"🎬 {self._title}\n"
                f"📦 <b>{xfr_mb:.1f} MB</b> / {tot_mb:.1f} MB\n"
                f"⚡ <b>{speed_mb:.1f} MB/s</b>  •  ETA {eta_str}"
            )

            try:
                kwargs = {}
                if self._reply_markup:
                    kwargs["reply_markup"] = self._reply_markup
                await self._msg.edit_text(text, parse_mode=ParseMode.HTML, **kwargs)
                self._last_edit = time.time()
            except Exception as exc:
                log.debug("[ProgressService] Edit skipped: %s", exc)

    async def finish(self, text: str, reply_markup: Optional[Any] = None) -> None:
        """Replace the progress message with a final status message."""
        if not self._msg:
            return
        try:
            kwargs = {}
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            await self._msg.edit_text(text, parse_mode=ParseMode.HTML, **kwargs)
        except Exception as exc:
            log.warning("[ProgressService] Could not finish message: %s", exc)

    async def edit(self, text: str) -> None:
        """Unconditionally edit the progress message (ignores rate-limit guard)."""
        if not self._msg:
            return
        try:
            await self._msg.edit_text(text, parse_mode=ParseMode.HTML)
            self._last_edit = time.time()
        except Exception as exc:
            log.debug("[ProgressService] edit() skipped: %s", exc)

    @property
    def message_id(self) -> Optional[int]:
        return self._msg.id if self._msg else None


def make_pyrogram_progress_callback(reporter: ProgressReporter):
    """
    Return an async callable compatible with Pyrogram's progress callback
    signature: callback(current, total).
    """
    async def _cb(current: int, total: int) -> None:
        await reporter.update(current, total)

    return _cb
