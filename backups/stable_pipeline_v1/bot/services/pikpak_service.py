"""
pikpak_service.py — Async PikPak Cloud Torrent Debrid Service.

Provides high-speed cloud torrent caching, offline downloading (10GB+ free tier),
direct stream URL extraction, and storage cleanup.
Seamlessly complements Seedr for larger TV series episodes and 1080p movies.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

import config

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
CREDS_FILE = os.path.join(DATA_DIR, "pikpak_creds.json")


class PikPakService:
    """Async client for PikPak cloud torrent and direct download service."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self._username = username
        self._password = password
        self._client: Optional[Any] = None
        self._active_files: List[str] = []
        self._active_tasks: List[str] = []
        self._load_credentials()

    def _load_credentials(self) -> None:
        """Load credentials from local data file or config."""
        if not self._username or not self._password:
            if os.path.exists(CREDS_FILE):
                try:
                    with open(CREDS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._username = self._username or data.get("username")
                        self._password = self._password or data.get("password")
                except Exception as exc:
                    log.warning("[PikPak] Could not read %s: %s", CREDS_FILE, exc)

        self._username = self._username or getattr(config, "PIKPAK_USER", "")
        self._password = self._password or getattr(config, "PIKPAK_PASS", "")

    def is_configured(self) -> bool:
        """Check if PikPak credentials are provided."""
        self._load_credentials()
        return bool(self._username and self._password)

    def save_credentials(self, username: str, password: str) -> None:
        """Persist PikPak credentials to local data file."""
        self._username = username.strip()
        self._password = password.strip()
        self._client = None  # Reset client to re-authenticate
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(CREDS_FILE, "w", encoding="utf-8") as f:
                json.dump({"username": self._username, "password": self._password}, f, indent=2)
            log.info("[PikPak] Saved credentials for '%s'", self._username)
        except Exception as exc:
            log.error("[PikPak] Failed to save credentials: %s", exc)

    async def get_client(self) -> Any:
        """Initialize or return authenticated PikPakApi client."""
        if self._client:
            return self._client

        if not self.is_configured():
            raise RuntimeError(
                "PikPak is not configured. Set PIKPAK_USER and PIKPAK_PASS in .env or run /pikpak login."
            )

        try:
            from pikpakapi import PikPakApi

            client = PikPakApi(username=self._username, password=self._password)
            await client.login()
            self._client = client
            log.info("[PikPak] Logged in successfully as '%s'", self._username)
            return self._client
        except Exception as exc:
            self._client = None
            log.error("[PikPak] Login failed for '%s': %s", self._username, exc)
            raise

    async def get_quota_summary(self) -> Dict[str, Any]:
        """Fetch PikPak storage quota info."""
        client = await self.get_client()
        quota_data = await client.get_quota_info()
        quota = quota_data.get("quota", {})
        limit_bytes = int(quota.get("limit", 0))
        usage_bytes = int(quota.get("usage", 0))
        free_bytes = max(0, limit_bytes - usage_bytes)

        gb = 1024 * 1024 * 1024
        return {
            "limit_bytes": limit_bytes,
            "usage_bytes": usage_bytes,
            "free_bytes": free_bytes,
            "limit_gb": round(limit_bytes / gb, 2) if limit_bytes else 0.0,
            "usage_gb": round(usage_bytes / gb, 2) if usage_bytes else 0.0,
            "free_gb": round(free_bytes / gb, 2) if free_bytes else 0.0,
            "username": self._username,
        }

    async def convert_magnet_to_direct_url(
        self,
        magnet_link: str,
        progress_callback: Optional[Callable] = None,
        timeout: int = 500,
        episode_hint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Submit a torrent/magnet to PikPak, wait for cloud completion,
        and extract direct high-speed download link for the primary video.
        """
        client = await self.get_client()
        task_id: Optional[str] = None
        file_id: Optional[str] = None
        target_file_id: Optional[str] = None

        try:
            # Step 0: Ensure PikPak cloud storage is clean and has full 6GB free
            try:
                await self.clean_storage()
            except Exception as c_err:
                log.warning("[PikPak] Pre-conversion cleanup warning: %s", c_err)

            log.info("[PikPak] Submitting magnet to PikPak cloud...")
            res = await client.offline_download(file_url=magnet_link)
            log.info("[PikPak] Offline download response: %s", res)

            task = res.get("task", {})
            task_id = task.get("id")
            file_id = task.get("file_id")

            if task_id:
                self._active_tasks.append(task_id)
            if file_id:
                self._active_files.append(file_id)

            start_time = time.time()
            phase = task.get("phase")

            # If not immediately complete, poll until done
            while phase != "PHASE_TYPE_COMPLETE":
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"[PikPak] Offline task {task_id} timed out after {timeout}s.")

                await asyncio.sleep(2.5)

                try:
                    tasks_info = await client.offline_list(
                        size=20,
                        phase=["PHASE_TYPE_RUNNING", "PHASE_TYPE_PENDING", "PHASE_TYPE_COMPLETE", "PHASE_TYPE_ERROR"],
                    )
                    found_task = None
                    for t in tasks_info.get("tasks", []):
                        if t.get("id") == task_id:
                            found_task = t
                            break

                    if found_task:
                        phase = found_task.get("phase", phase)
                        progress = float(found_task.get("progress", 0))
                        file_id = found_task.get("file_id") or file_id
                        if file_id and file_id not in self._active_files:
                            self._active_files.append(file_id)

                        if phase == "PHASE_TYPE_ERROR":
                            msg = found_task.get("message", "Cloud download error")
                            raise RuntimeError(f"[PikPak] Task failed: {msg}")

                        if progress_callback:
                            file_size = int(found_task.get("file_size", 0))
                            done_bytes = int(file_size * (progress / 100.0))
                            done_str = f"{round(done_bytes / (1024 * 1024), 1)} MB"
                            total_str = f"{round(file_size / (1024 * 1024), 1)} MB" if file_size else "Cloud"
                            try:
                                if asyncio.iscoroutinefunction(progress_callback):
                                    await progress_callback(progress, done_str, total_str, "Cloud Cache", "Instant")
                                else:
                                    progress_callback(progress, done_str, total_str, "Cloud Cache", "Instant")
                            except Exception:
                                pass
                    else:
                        # Task might already be complete and removed from offline_list
                        log.info("[PikPak] Task %s not in active tasks, checking file info...", task_id)
                        phase = "PHASE_TYPE_COMPLETE"
                except Exception as poll_err:
                    if isinstance(poll_err, (RuntimeError, TimeoutError)):
                        raise
                    log.warning("[PikPak] Error during status polling: %s", poll_err)

            log.info("[PikPak] Task %s completed! Locating video file (file_id=%s)...", task_id, file_id)

            # Locate target episode or largest video file
            file_name = "video.mp4"
            file_size = 0

            # Check if file_id is a folder or file
            list_res = await client.file_list(parent_id=file_id, size=100)
            files = list_res.get("files", [])

            video_exts = (".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".ts")
            video_files = [f for f in files if f.get("name", "").lower().endswith(video_exts)]

            if video_files:
                if episode_hint:
                    from services import downloader
                    ep_matches = [f for f in video_files if downloader.matches_episode_filename(f.get("name", ""), episode_hint)]
                    if ep_matches:
                        video_files = ep_matches
                # Pick largest matching video file in the folder
                video_files.sort(key=lambda x: int(x.get("size", 0)), reverse=True)
                chosen = video_files[0]
                target_file_id = chosen["id"]
                file_name = chosen.get("name", "video.mp4")
                file_size = int(chosen.get("size", 0))
            elif files:
                # Pick largest file overall
                files.sort(key=lambda x: int(x.get("size", 0)), reverse=True)
                chosen = files[0]
                target_file_id = chosen["id"]
                file_name = chosen.get("name", "video.mp4")
                file_size = int(chosen.get("size", 0))
            else:
                # Direct single file
                target_file_id = file_id

            if target_file_id:
                self._active_files.append(target_file_id)

            # Retrieve direct high-speed download link
            dl_info = await client.get_download_url(target_file_id or file_id)
            direct_url = (
                dl_info.get("web_content_link")
                or (dl_info.get("medias", [{}])[0].get("link", {}).get("url") if dl_info.get("medias") else None)
            )

            if not direct_url:
                raise RuntimeError(f"[PikPak] Could not resolve direct download link: {dl_info}")

            if not file_size:
                file_size = int(dl_info.get("size", 0))
            if file_name == "video.mp4" and dl_info.get("name"):
                file_name = dl_info["name"]

            log.info("[PikPak] Resolved direct URL for '%s' (%s bytes)", file_name, file_size)

            return {
                "direct_url": direct_url,
                "file_name": file_name,
                "file_size": file_size,
                "file_id": target_file_id or file_id,
                "parent_id": file_id,
                "task_id": task_id,
                "service": "pikpak",
            }

        except asyncio.CancelledError:
            log.warning("[PikPak] CancelledError caught. Immediately purging PikPak cloud resources...")
            await self._cleanup_active_resources(task_id, [file_id, target_file_id])
            raise

        except Exception as exc:
            log.error("[PikPak] convert_magnet_to_direct_url failed: %s", exc)
            await self._cleanup_active_resources(task_id, [file_id, target_file_id])
            return None

    async def _cleanup_active_resources(
        self,
        task_id: Optional[str] = None,
        file_ids: Optional[List[Optional[str]]] = None,
    ) -> None:
        """Safely delete tasks and files from PikPak cloud."""
        if not self.is_configured() or not self._client:
            return

        try:
            client = self._client
            to_delete_tasks = [t for t in ([task_id] if task_id else self._active_tasks) if t]
            if to_delete_tasks:
                await client.delete_tasks(task_ids=to_delete_tasks, delete_files=True)
                for t in to_delete_tasks:
                    if t in self._active_tasks:
                        self._active_tasks.remove(t)

            to_delete_files = [f for f in (file_ids if file_ids else self._active_files) if f]
            if to_delete_files:
                await client.delete_forever(ids=to_delete_files)
                for f in to_delete_files:
                    if f in self._active_files:
                        self._active_files.remove(f)

            log.info("[PikPak] Cloud cleanup successful for tasks=%s files=%s", to_delete_tasks, to_delete_files)
        except Exception as err:
            log.warning("[PikPak] Error during cleanup: %s", err)

    async def clean_storage(self) -> bool:
        """Purge all files and tasks from PikPak cloud drive to restore 100% free space."""
        if not self.is_configured():
            return False

        try:
            client = await self.get_client()

            # Delete all offline tasks
            try:
                tasks_info = await client.offline_list(size=1000)
                task_ids = [t["id"] for t in tasks_info.get("tasks", []) if "id" in t]
                if task_ids:
                    await client.delete_tasks(task_ids=task_ids, delete_files=True)
                    log.info("[PikPak] Deleted %d cloud tasks.", len(task_ids))
            except Exception as e:
                log.warning("[PikPak] Error clearing tasks: %e", e)

            # Delete all root files & folders forever, including contents inside "My Pack"
            try:
                files_res = await client.file_list(parent_id=None, size=100)
                root_files = files_res.get("files", [])

                # Scan inside subfolders (specifically "My Pack" where cloud torrents reside)
                child_file_ids = []
                for rf in root_files:
                    if rf.get("kind") == "drive#folder" or "folder" in rf.get("mime_type", "").lower() or rf.get("name") == "My Pack":
                        try:
                            sub_res = await client.file_list(parent_id=rf["id"], size=100)
                            sub_ids = [sf["id"] for sf in sub_res.get("files", []) if "id" in sf]
                            if sub_ids:
                                child_file_ids.extend(sub_ids)
                        except Exception as sub_err:
                            log.debug("[PikPak] Error listing subfolder %s: %s", rf.get("id"), sub_err)

                if child_file_ids:
                    await client.delete_forever(ids=child_file_ids)
                    log.info("[PikPak] Deleted %d files inside My Pack/folders forever.", len(child_file_ids))

                file_ids = [f["id"] for f in root_files if "id" in f and f.get("name") != "My Pack"]
                if file_ids:
                    await client.delete_forever(ids=file_ids)
                    log.info("[PikPak] Deleted %d root files/folders forever.", len(file_ids))
            except Exception as e:
                log.warning("[PikPak] Error clearing root files: %s", e)

            self._active_files.clear()
            self._active_tasks.clear()
            return True
        except Exception as exc:
            log.error("[PikPak] clean_storage error: %s", exc)
            return False


# Global singleton
pikpak_service = PikPakService()
