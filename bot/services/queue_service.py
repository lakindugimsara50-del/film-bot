"""
queue_service.py — Async FIFO Movie Download & Upload Queue.

Ensures that multiple movie requests are executed strictly one by one (sequentially).
Prevents Render container out-of-memory errors, VPS disk overflow, and Seedr storage collisions.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from services import task_tracker

log = logging.getLogger(__name__)


@dataclass
class QueueItem:
    item_id: str
    user_id: int
    query_text: str
    reply_media: Optional[dict]
    status_msg: Message
    client: Client
    title: str
    auto_publish: bool = False


class QueueService:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pending_items: List[QueueItem] = []
        self._is_worker_running = False
        self._active_item: Optional[QueueItem] = None
        self._current_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None

    def start_worker(self) -> None:
        """Start background queue consumer."""
        if not self._is_worker_running:
            self._is_worker_running = True
            loop = asyncio.get_event_loop()
            self._worker_task = loop.create_task(self._worker_loop())
            log.info("[QueueService] Background worker started.")

    async def add_to_queue(
        self,
        client: Client,
        status_msg: Message,
        user_id: int,
        query_text: str,
        reply_media: Optional[dict] = None,
        title_hint: str = "",
        auto_publish: bool = False,
    ) -> int:
        """
        Add a movie request to the queue.
        Returns the queue position (1-based index).
        """
        self.start_worker()

        item_id = f"q_{user_id}_{int(__import__('time').time())}_{len(self._pending_items)}"
        title = title_hint or query_text.strip() or "Movie"
        item = QueueItem(
            item_id=item_id,
            user_id=user_id,
            query_text=query_text,
            reply_media=reply_media,
            status_msg=status_msg,
            client=client,
            title=title,
            auto_publish=auto_publish,
        )

        # Track in task tracker immediately so /cancel or /status knows about it
        task_tracker.tracker.start_task(user_id=user_id, title=title)

        self._pending_items.append(item)
        await self._queue.put(item)
        position = len(self._pending_items)

        log.info(
            "[QueueService] Added '%s' to queue (pos=%d, pending=%d)",
            title, position, len(self._pending_items)
        )
        return position

    def cancel_user(self, user_id: Optional[int] = None) -> bool:
        """Cancel current active task and remove all matching pending items from queue."""
        cancelled = False
        if self._active_item and (user_id is None or self._active_item.user_id == user_id):
            if self._current_task and not self._current_task.done():
                log.info("[QueueService] Cancelling active task for user %s (%s)", self._active_item.user_id, self._active_item.title)
                self._current_task.cancel()
                cancelled = True

        # Remove matching pending items
        remaining: List[QueueItem] = []
        for it in self._pending_items:
            if it == self._active_item:
                remaining.append(it)
                continue
            if user_id is None or it.user_id == user_id:
                cancelled = True
                try:
                    asyncio.create_task(
                        it.status_msg.edit_text(
                            "❌ <b>බාගත කිරීමේ පෝලිමෙන් ඉවත් කරන ලදී (Cancelled from queue).</b>",
                            parse_mode=ParseMode.HTML,
                        )
                    )
                except Exception:
                    pass
            else:
                remaining.append(it)

        self._pending_items = remaining
        return cancelled

    def get_queue_status(self) -> List[dict]:
        """Return list of queued items."""
        result = []
        if self._active_item:
            result.append({
                "title": self._active_item.title,
                "status": "ක්‍රියාත්මක වෙමින් පවතී (Active)",
                "user_id": self._active_item.user_id,
            })
        for i, item in enumerate(self._pending_items, 1):
            if item != self._active_item:
                result.append({
                    "title": item.title,
                    "status": f"පෝලිමේ #{i} (Waiting)",
                    "user_id": item.user_id,
                })
        return result

    def is_idle(self) -> bool:
        """Check if queue is currently processing anything."""
        return self._active_item is None and len(self._pending_items) == 0

    async def _worker_loop(self) -> None:
        """Sequential FIFO queue consumer."""
        log.info("[QueueService] Queue loop running.")
        while self._is_worker_running:
            try:
                item: QueueItem = await self._queue.get()
                self._active_item = item
                log.info("[QueueService] Processing queue item: %s for user %s", item.title, item.user_id)

                from services import leech_service
                leech_task = asyncio.create_task(
                    leech_service.run_auto_leech(
                        client=item.client,
                        status_msg=item.status_msg,
                        user_id=item.user_id,
                        query_text=item.query_text,
                        reply_media=item.reply_media,
                        auto_publish=item.auto_publish,
                    )
                )
                self._current_task = leech_task
                task_tracker.tracker.set_task_handle(item.user_id, leech_task)

                try:
                    await leech_task
                except asyncio.CancelledError:
                    log.info("[QueueService] Task cancelled for user %s: %s", item.user_id, item.title)
                except Exception as exc:
                    log.error("[QueueService] Error executing queued item '%s': %s", item.title, exc)
                finally:
                    self._current_task = None
                    if item in self._pending_items:
                        self._pending_items.remove(item)
                    self._active_item = None
                    self._queue.task_done()

                    # Pause 2 seconds between jobs to allow system memory GC
                    await asyncio.sleep(2)

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                log.error("[QueueService] Worker loop error: %s", loop_err)
                await asyncio.sleep(3)


# Global singleton
queue_service = QueueService()
