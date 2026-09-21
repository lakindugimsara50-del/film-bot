"""
task_tracker.py — Centralized singleton to track active background movie processing tasks.

Tracks:
- active asyncio.Task objects
- user_id, movie title, current step, start time
- status (IDLE, PROCESSING, CANCELLED, FAILED, COMPLETED)
- error message if failed
"""

import asyncio
import datetime
import logging
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)


class TaskStatus:
    IDLE = "IDLE"
    PROCESSING = "PROCESSING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class TaskInfo:
    def __init__(self, task_id: str, user_id: int, title: str, task: Optional[asyncio.Task] = None):
        self.task_id = task_id
        self.user_id = user_id
        self.title = title
        self.step = "ආරම්භ කරමින් පවතී..."
        self.status = TaskStatus.PROCESSING
        self.start_time = datetime.datetime.now()
        self.end_time: Optional[datetime.datetime] = None
        self.error: Optional[str] = None
        self.task: Optional[asyncio.Task] = task
        self.task_key: Optional[str] = None
        self.temp_dir: Optional[str] = None
        self.cleanup_callbacks: list = []

    def elapsed_seconds(self) -> int:
        end = self.end_time or datetime.datetime.now()
        return int((end - self.start_time).total_seconds())


class TaskTracker:
    def __init__(self):
        # user_id -> TaskInfo
        self._user_tasks: Dict[int, TaskInfo] = {}
        # user_id -> last finished TaskInfo
        self._user_last_tasks: Dict[int, TaskInfo] = {}
        # last finished task for global status reference
        self._last_task: Optional[TaskInfo] = None

    def start_task(self, user_id: int, title: str, task: Optional[asyncio.Task] = None) -> TaskInfo:
        task_id = f"task_{user_id}_{int(datetime.datetime.now().timestamp())}"
        existing = self._user_tasks.get(user_id)
        task_handle = task or (existing.task if existing else None)
        info = TaskInfo(task_id, user_id, title, task_handle)
        if existing:
            info.task_key = existing.task_key
            info.temp_dir = existing.temp_dir
            info.cleanup_callbacks = list(existing.cleanup_callbacks)
        self._user_tasks[user_id] = info
        log.info("Task started for user %s: %s (%s)", user_id, title, task_id)
        return info

    def set_task_handle(self, user_id: int, task: asyncio.Task) -> None:
        if user_id in self._user_tasks:
            self._user_tasks[user_id].task = task

    def set_metadata(self, user_id: int, task_key: Optional[str] = None, temp_dir: Optional[str] = None) -> None:
        if user_id in self._user_tasks:
            if task_key:
                self._user_tasks[user_id].task_key = task_key
            if temp_dir:
                self._user_tasks[user_id].temp_dir = temp_dir

    def register_cleanup(self, user_id: int, callback) -> None:
        if user_id in self._user_tasks:
            self._user_tasks[user_id].cleanup_callbacks.append(callback)

    def set_step(self, user_id: int, step: str) -> None:
        if user_id in self._user_tasks:
            self._user_tasks[user_id].step = step
            log.info("Task step for user %s [%s]: %s", user_id, self._user_tasks[user_id].title, step)

    def complete_task(self, user_id: int) -> None:
        if user_id in self._user_tasks:
            info = self._user_tasks.pop(user_id)
            info.status = TaskStatus.COMPLETED
            info.end_time = datetime.datetime.now()
            self._last_task = info
            self._user_last_tasks[user_id] = info
            log.info("Task completed for user %s: %s", user_id, info.title)

    def fail_task(self, user_id: int, error: str) -> None:
        if user_id in self._user_tasks:
            info = self._user_tasks.pop(user_id)
            info.status = TaskStatus.FAILED
            info.end_time = datetime.datetime.now()
            info.error = error
            self._last_task = info
            self._user_last_tasks[user_id] = info
            log.error("Task failed for user %s: %s, error: %s", user_id, info.title, error)

    def cancel_task(self, user_id: Optional[int] = None) -> bool:
        """Cancel active task for user_id (or any active task if user_id is None). Returns True if a task was actively cancelled."""
        target_info = None
        if user_id is not None and user_id in self._user_tasks:
            target_info = self._user_tasks.pop(user_id)
        elif user_id is None and self._user_tasks:
            target_info = self._user_tasks.pop(next(iter(self._user_tasks)))

        was_cancelled = False

        if target_info:
            target_info.status = TaskStatus.CANCELLED
            target_info.end_time = datetime.datetime.now()
            self._last_task = target_info
            self._user_last_tasks[target_info.user_id] = target_info

            if target_info.task and not target_info.task.done():
                target_info.task.cancel()
                log.info("asyncio.Task cancelled for user %s: %s", target_info.user_id, target_info.title)

            # Execute all registered cleanup callbacks
            for cb in target_info.cleanup_callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(cb())
                        except RuntimeError:
                            pass
                    else:
                        cb()
                except Exception as cb_err:
                    log.warning("Task cleanup callback error: %s", cb_err)

            # Clean temporary folder if tracked
            if target_info.temp_dir and os.path.exists(target_info.temp_dir):
                import shutil
                shutil.rmtree(target_info.temp_dir, ignore_errors=True)
                log.info("Temp dir %s deleted on cancellation.", target_info.temp_dir)

            was_cancelled = True

        # Always trigger downloader subprocess cancellation and Seedr cleanup if loop is running
        try:
            loop = asyncio.get_running_loop()
            from services import downloader
            loop.create_task(downloader.cancel_all_active_downloads())
        except (RuntimeError, Exception):
            pass

        try:
            loop = asyncio.get_running_loop()
            from services import seedr_service
            loop.create_task(seedr_service.seedr_pool.clean_storage())
        except (RuntimeError, Exception):
            pass

        # Also cancel corresponding items in queue_service
        try:
            from services.queue_service import queue_service
            if queue_service.cancel_user(user_id):
                was_cancelled = True
        except Exception:
            pass

        return was_cancelled

    def get_active_task(self, user_id: Optional[int] = None) -> Optional[TaskInfo]:
        if user_id is not None:
            return self._user_tasks.get(user_id)
        if self._user_tasks:
            return next(iter(self._user_tasks.values()))
        return None

    def get_any_active_task(self) -> Optional[TaskInfo]:
        if self._user_tasks:
            return next(iter(self._user_tasks.values()))
        return None

    def get_last_task(self, user_id: Optional[int] = None) -> Optional[TaskInfo]:
        if user_id is not None and user_id in self._user_last_tasks:
            return self._user_last_tasks[user_id]
        return self._last_task

    def get_status_summary(self, user_id: Optional[int] = None) -> str:
        """Return human-friendly Sinhala summary of current/last task status."""
        active = self.get_active_task(user_id) if user_id is not None else self.get_any_active_task()
        if active:
            elapsed = active.elapsed_seconds()
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
            return (
                f"⏳ <b>දැනට ක්‍රියාත්මකයි (Processing):</b>\n"
                f"🎬 <b>චිත්‍රපටය:</b> {active.title}\n"
                f"📍 <b>පියවර:</b> {active.step}\n"
                f"⏱ <b>ගතවූ කාලය:</b> {time_str}"
            )

        last = self.get_last_task(user_id)
        if last:
            elapsed = last.elapsed_seconds()
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
            if last.status == TaskStatus.CANCELLED:
                return (
                    f"❌ <b>අවලංගු විය (Cancelled):</b> {last.title}\n"
                    f"⏱ <b>අවලංගු වූයේ:</b> මීට {time_str} පෙර"
                )
            elif last.status == TaskStatus.FAILED:
                err_msg = (last.error or "Unknown error")[:120]
                return (
                    f"⚠️ <b>අසාර්ථක විය (Failed):</b> {last.title}\n"
                    f"❌ <b>දෝෂය:</b> <code>{err_msg}</code>\n"
                    f"⏱ <b>සිදුවූයේ:</b> මීට {time_str} පෙර"
                )
            elif last.status == TaskStatus.COMPLETED:
                return (
                    f"✅ <b>අවසන් වූ කාර්යය:</b> {last.title} සාර්ථකව Web එකට එක් කරන ලදී.\n"
                    f"✅ <b>කිසිදු වැඩක් නැත (Idle)</b>"
                )

        return "✅ <b>කිසිදු වැඩක් නැත (Idle)</b> — සියලුම කාර්යයන් සාර්ථකව අවසන් හෝ නව කාර්යයක් බලාපොරොත්තුවෙන්."


# Global singleton instance
tracker = TaskTracker()


async def cancel_all_user_operations(user_id: Optional[int] = None) -> bool:
    """
    Comprehensively cancels all active tasks, queued items, download subprocesses,
    and purges Seedr cloud storage for the given user (or any active task if user_id is None).
    """
    # 1. Cancel in task_tracker
    was_cancelled = tracker.cancel_task(user_id)
    if not was_cancelled and tracker.get_any_active_task():
        was_cancelled = tracker.cancel_task(None)

    # 2. Cancel in queue_service
    try:
        from services.queue_service import queue_service
        if queue_service.cancel_user(user_id):
            was_cancelled = True
    except Exception as e:
        log.warning("queue_service cancel error: %s", e)

    # 3. Kill all active aria2c / downloader subprocesses
    try:
        from services import downloader
        await downloader.cancel_all_active_downloads()
    except Exception as e:
        log.warning("downloader cancellation error: %s", e)

    # 4. Clean Seedr storage completely
    try:
        from services import seedr_service
        await seedr_service.seedr_pool.clean_storage()
    except Exception as e:
        log.warning("seedr storage clean error: %s", e)

    return was_cancelled
