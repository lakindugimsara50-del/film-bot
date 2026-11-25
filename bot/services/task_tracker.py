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
import os
import shutil
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
        self.upload_results: Optional[Dict[str, Any]] = None
        self.subtitle_url: Optional[str] = None

    def elapsed_seconds(self) -> int:
        end = self.end_time or datetime.datetime.now()
        return int((end - self.start_time).total_seconds())


class TaskTracker:
    def __init__(self):
        # user_id -> TaskInfo (latest active task for /status compatibility)
        self._user_tasks: Dict[int, TaskInfo] = {}
        # id(asyncio.Task) -> TaskInfo (isolates overlapping concurrent jobs cleanly)
        self._task_infos: Dict[int, TaskInfo] = {}
        # user_id -> last finished TaskInfo
        self._user_last_tasks: Dict[int, TaskInfo] = {}
        # last finished task for global status reference
        self._last_task: Optional[TaskInfo] = None

    @staticmethod
    def _cur_task_id() -> Optional[int]:
        try:
            t = asyncio.current_task()
            return id(t) if t is not None else None
        except Exception:
            return None

    def _resolve_info(self, user_id: int) -> Optional[TaskInfo]:
        tid = self._cur_task_id()
        if tid is not None and tid in self._task_infos:
            info = self._task_infos[tid]
            if info.user_id == user_id:
                return info
        return self._user_tasks.get(user_id)

    def start_task(self, user_id: int, title: str, task: Optional[asyncio.Task] = None) -> TaskInfo:
        task_id = f"task_{user_id}_{int(datetime.datetime.now().timestamp() * 1000)}"
        tid = id(task) if task is not None else self._cur_task_id()
        existing = self._task_infos.get(tid) if (tid is not None and tid in self._task_infos) else None
        if existing is None and task is None and tid is None:
            existing = self._user_tasks.get(user_id)
        task_handle = task or (existing.task if existing else None)
        info = TaskInfo(task_id, user_id, title, task_handle)
        if existing and existing.user_id == user_id:
            info.task_key = existing.task_key
            info.temp_dir = existing.temp_dir
            info.cleanup_callbacks = list(existing.cleanup_callbacks)
            for k, v in list(self._task_infos.items()):
                if v is existing:
                    self._task_infos[k] = info
        self._user_tasks[user_id] = info
        if tid is not None:
            self._task_infos[tid] = info
        if task_handle is not None:
            self._task_infos[id(task_handle)] = info
        log.info("Task started for user %s: %s (%s)", user_id, title, task_id)
        return info

    def set_task_handle(self, user_id: int, task: asyncio.Task) -> None:
        info = self._resolve_info(user_id)
        if info:
            info.task = task
            self._task_infos[id(task)] = info
            cur_tid = self._cur_task_id()
            if cur_tid is not None and cur_tid not in self._task_infos:
                self._task_infos[cur_tid] = info

    def set_metadata(self, user_id: int, task_key: Optional[str] = None, temp_dir: Optional[str] = None) -> None:
        info = self._resolve_info(user_id)
        if info:
            if task_key:
                info.task_key = task_key
            if temp_dir:
                info.temp_dir = temp_dir

    def register_cleanup(self, user_id: int, callback) -> None:
        info = self._resolve_info(user_id)
        if info:
            info.cleanup_callbacks.append(callback)

    def set_step(self, user_id: int, step: str) -> None:
        info = self._resolve_info(user_id)
        if info:
            info.step = step
            log.info("Task step for user %s [%s]: %s", user_id, info.title, step)

    def store_upload_results(self, user_id: int, results: Dict[str, Any], subtitle_url: Optional[str] = None) -> None:
        info = self._resolve_info(user_id)
        if info:
            info.upload_results = results
            if subtitle_url:
                info.subtitle_url = subtitle_url

    def get_upload_results(self, user_id: int) -> Optional[Dict[str, Any]]:
        info = self._resolve_info(user_id)
        if info:
            return info.upload_results
        if user_id in self._user_last_tasks:
            return self._user_last_tasks[user_id].upload_results
        return None

    def get_subtitle_url(self, user_id: int) -> Optional[str]:
        info = self._resolve_info(user_id)
        if info:
            return info.subtitle_url
        if user_id in self._user_last_tasks:
            return self._user_last_tasks[user_id].subtitle_url
        return None

    def _finalize_task_info(self, user_id: int, status: str, error: Optional[str] = None) -> None:
        tid = self._cur_task_id()
        info = None
        if tid is not None and tid in self._task_infos and self._task_infos[tid].user_id == user_id:
            info = self._task_infos.pop(tid)
        if info is None and user_id in self._user_tasks:
            info = self._user_tasks.get(user_id)
        if info is not None:
            # Remove any task_infos referencing this TaskInfo
            for k in [k for k, v in list(self._task_infos.items()) if v is info]:
                self._task_infos.pop(k, None)
            if self._user_tasks.get(user_id) is info:
                self._user_tasks.pop(user_id, None)
                # If another overlapping task for same user is still active, keep it in _user_tasks
                for rem_info in self._task_infos.values():
                    if rem_info.user_id == user_id and rem_info.status == TaskStatus.PROCESSING:
                        self._user_tasks[user_id] = rem_info
                        break
            info.status = status
            info.end_time = datetime.datetime.now()
            if error is not None:
                info.error = error
            self._last_task = info
            self._user_last_tasks[user_id] = info
            if status == TaskStatus.COMPLETED:
                log.info("Task completed for user %s: %s", user_id, info.title)
            else:
                log.error("Task failed for user %s: %s, error: %s", user_id, info.title, error)

    def complete_task(self, user_id: int) -> None:
        self._finalize_task_info(user_id, TaskStatus.COMPLETED)

    def fail_task(self, user_id: int, error: str) -> None:
        self._finalize_task_info(user_id, TaskStatus.FAILED, error=error)

    def cancel_task(self, user_id: Optional[int] = None) -> bool:
        """Cancel active task(s) for user_id (or any active user if user_id is None). Returns True if actively cancelled."""
        target_uid = user_id
        if target_uid is None:
            if self._user_tasks:
                target_uid = next(iter(self._user_tasks))
            elif self._task_infos:
                target_uid = next(iter(self._task_infos.values())).user_id

        targets_to_cancel: list[TaskInfo] = []
        seen_ids: set[int] = set()

        if target_uid is not None and target_uid in self._user_tasks:
            primary_info = self._user_tasks.pop(target_uid)
            seen_ids.add(id(primary_info))
            targets_to_cancel.append(primary_info)

        if target_uid is not None:
            for tid, info in list(self._task_infos.items()):
                if info.user_id == target_uid:
                    self._task_infos.pop(tid, None)
                    if id(info) not in seen_ids:
                        seen_ids.add(id(info))
                        targets_to_cancel.append(info)

        was_cancelled = False

        for target_info in targets_to_cancel:
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
                    loop = None
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        pass

                    if asyncio.iscoroutinefunction(cb):
                        if loop and loop.is_running():
                            loop.create_task(cb())
                    else:
                        res = cb()
                        if asyncio.iscoroutine(res):
                            if loop and loop.is_running():
                                loop.create_task(res)
                except Exception as cb_err:
                    log.warning("Task cleanup callback error: %s", cb_err)

            # Clean temporary folder if tracked
            if target_info.temp_dir and os.path.exists(target_info.temp_dir):
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

        try:
            loop = asyncio.get_running_loop()
            from services.pikpak_service import pikpak_service
            if pikpak_service.is_configured():
                loop.create_task(pikpak_service.clean_storage())
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

    def format_status_message(self, user_id: Optional[int] = None) -> str:
        """Alias for get_status_summary for backwards and cross-handler compatibility."""
        return self.get_status_summary(user_id)


# Global singleton instance
tracker = TaskTracker()


async def cancel_all_user_operations(user_id: Optional[int] = None) -> bool:
    """
    Comprehensively cancels all active tasks, queued items, download subprocesses,
    and purges Seedr cloud storage for the given user (or any active task if user_id is None).
    """
    # 1. Cancel in task_tracker
    was_cancelled = tracker.cancel_task(user_id)
    if not was_cancelled:
        import config
        is_admin = (user_id is None) or (user_id in getattr(config, "ADMIN_IDS", []))
        if is_admin and tracker.get_any_active_task():
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
