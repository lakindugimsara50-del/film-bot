"""
queue_service.py — Async FIFO Movie Download & Upload Queue with Pipelined Pre-Download.

Ensures that:
1. Max 2 items in-flight at any time (1 item in GPU Encode/Upload + 1 item in Pre-Download).
2. Prevents Render container OOM, VPS disk overflow, and Seedr storage collisions.
3. Completely eliminates idle time: Movie N+1 downloads concurrently while Movie N is
   encoding (with Tesla T4 NVENC GPU) and uploading to Telegram/Cloud!
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

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
    movie_slug: str = ""
    imdb_id: str = ""
    is_cancelled: bool = False


class QueueService:
    def __init__(self):
        self._queue: Optional[asyncio.Queue] = None
        self._pending_items: List[QueueItem] = []
        self._is_worker_running = False
        self._active_item: Optional[QueueItem] = None
        self._active_tasks: Dict[asyncio.Task, QueueItem] = {}
        self._current_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None

    def _ensure_queue(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    def start_worker(self) -> None:
        """Start background queue consumer."""
        if self._worker_task is None or self._worker_task.done():
            self._is_worker_running = True
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
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
        movie_slug: str = "",
        imdb_id: str = "",
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
            movie_slug=movie_slug,
            imdb_id=imdb_id,
        )

        # Track in task tracker immediately so /cancel or /status knows about it
        task_tracker.tracker.start_task(user_id=user_id, title=title)

        self._pending_items.append(item)
        await self._ensure_queue().put(item)
        position = len(self._pending_items)

        log.info(
            "[QueueService] Added '%s' to queue (pos=%d, pending=%d)",
            title, position, len(self._pending_items)
        )
        return position

    def cancel_user(self, user_id: Optional[int] = None) -> bool:
        """Cancel active task(s) and remove all matching pending items from queue."""
        cancelled = False
        for task, it in list(self._active_tasks.items()):
            if user_id is None or it.user_id == user_id:
                it.is_cancelled = True
                if not task.done():
                    log.info("[QueueService] Cancelling active task for user %s (%s)", it.user_id, it.title)
                    task.cancel()
                    cancelled = True

        if self._active_item and (user_id is None or self._active_item.user_id == user_id):
            self._active_item.is_cancelled = True
            if self._current_task and not self._current_task.done():
                self._current_task.cancel()
                cancelled = True
            self._active_item = None

        # Remove matching pending items
        remaining: List[QueueItem] = []
        for it in self._pending_items:
            if user_id is None or it.user_id == user_id:
                it.is_cancelled = True
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

        # Purge cancelled items from asyncio.Queue without replacing the instance
        kept_items = []
        while not self._ensure_queue().empty():
            try:
                q_item = self._ensure_queue().get_nowait()
                if not q_item.is_cancelled and (user_id is not None and q_item.user_id != user_id):
                    kept_items.append(q_item)
                self._ensure_queue().task_done()
            except (asyncio.QueueEmpty, Exception):
                break
        for q_item in kept_items:
            self._ensure_queue().put_nowait(q_item)

        return cancelled

    def get_queue_status(self) -> List[dict]:
        """Return list of queued items."""
        result = []
        for task, it in self._active_tasks.items():
            if not it.is_cancelled:
                result.append({
                    "title": it.title,
                    "status": "ක්‍රියාත්මක වෙමින් පවතී (Active/Pipelined)",
                    "user_id": it.user_id,
                })
        for i, item in enumerate(self._pending_items, 1):
            if item not in self._active_tasks.values() and not item.is_cancelled:
                result.append({
                    "title": item.title,
                    "status": f"පෝලිමේ #{i} (Waiting)",
                    "user_id": item.user_id,
                })
        return result

    def is_idle(self) -> bool:
        """Check if queue is completely idle (0 active tasks and 0 pending items)."""
        return len(self._pending_items) == 0 and len(self._active_tasks) == 0

    def can_accept_next(self) -> bool:
        """
        Check if pipeline has capacity to accept another item for pre-downloading.
        Allows up to 2 items in-flight (1 in encode/upload + 1 downloading in background).
        """
        return len(self._pending_items) < 2

    async def _run_item_wrapper(self, item: QueueItem, leech_service) -> None:
        try:
            await leech_service.run_auto_leech(
                client=item.client,
                status_msg=item.status_msg,
                user_id=item.user_id,
                query_text=item.query_text,
                reply_media=item.reply_media,
                auto_publish=item.auto_publish,
            )
            if item.movie_slug:
                try:
                    from services.stage2_patcher import patch_movie_downloads
                    upload_results = task_tracker.tracker.get_upload_results(item.user_id)
                    sub_url = task_tracker.tracker.get_subtitle_url(item.user_id) or ""
                    if upload_results:
                        await patch_movie_downloads(item.movie_slug, upload_results, subtitle_url=sub_url, client=item.client)
                except Exception as patch_err:
                    log.error("[QueueService] Stage2 patch error: %s", patch_err)
        except asyncio.CancelledError:
            log.info("[QueueService] Task cancelled for user %s: %s", item.user_id, item.title)
        except Exception as exc:
            log.error("[QueueService] Error executing queued item '%s': %s", item.title, exc)
        finally:
            if item in self._pending_items:
                self._pending_items.remove(item)
            if self._active_item == item:
                self._active_item = None

    async def _worker_loop(self) -> None:
        """Pipelined FIFO queue consumer (max 2 in-flight: 1 encode/upload + 1 pre-download)."""
        log.info("[QueueService] Pipelined queue loop running.")
        self._active_tasks.clear()

        while self._is_worker_running:
            try:
                # 1. Prune finished tasks
                for t in list(self._active_tasks.keys()):
                    if t.done():
                        item = self._active_tasks.pop(t, None)
                        if item and item in self._pending_items:
                            self._pending_items.remove(item)
                        try:
                            self._ensure_queue().task_done()
                        except ValueError:
                            pass

                # 2. If pipeline has 2 active tasks running, wait until at least one finishes
                if len(self._active_tasks) >= 2:
                    done, _ = await asyncio.wait(
                        list(self._active_tasks.keys()),
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in done:
                        item = self._active_tasks.pop(t, None)
                        if item and item in self._pending_items:
                            self._pending_items.remove(item)
                        try:
                            self._ensure_queue().task_done()
                        except ValueError:
                            pass
                    continue

                # 3. Pull next item from queue with a short timeout
                try:
                    item: QueueItem = await asyncio.wait_for(self._ensure_queue().get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue

                if item.is_cancelled or item not in self._pending_items:
                    log.info("[QueueService] Discarding cancelled queue item: %s", item.title)
                    try:
                        self._ensure_queue().task_done()
                    except ValueError:
                        pass
                    continue

                self._active_item = item
                log.info(
                    "[QueueService] Launching pipelined queue item: %s for user %s (in-flight: %d)",
                    item.title, item.user_id, len(self._active_tasks) + 1
                )

                from services import leech_service
                leech_task = asyncio.create_task(
                    self._run_item_wrapper(item, leech_service)
                )
                self._active_tasks[leech_task] = item
                self._current_task = leech_task
                task_tracker.tracker.set_task_handle(item.user_id, leech_task)

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                log.error("[QueueService] Worker loop error: %s", loop_err)
                await asyncio.sleep(2)

        # Cancel remaining active tasks on termination
        for t in list(self._active_tasks.keys()):
            if not t.done():
                t.cancel()
        self._active_tasks.clear()
        self._is_worker_running = False
        log.info("[QueueService] Queue loop terminated.")


# Global singleton
queue_service = QueueService()


# Module-level alias exports
add_to_queue = queue_service.add_to_queue
is_idle = queue_service.is_idle
can_accept_next = queue_service.can_accept_next
cancel_user = queue_service.cancel_user
get_queue_status = queue_service.get_queue_status
start_worker = queue_service.start_worker
