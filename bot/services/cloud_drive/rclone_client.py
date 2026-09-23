"""
rclone_client.py — High-speed Rclone Cloud Storage Client for Google Drive & OneDrive.

Utilizes the rclone binary with persistent configuration (bot/data/rclone.conf)
for rock-solid, resumable, multi-threaded cloud uploads and direct link generation.
"""

import asyncio
import json
import logging
import os
import re
import shutil
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DEFAULT_CONF_FILE = os.path.join(DATA_DIR, "rclone.conf")


def find_rclone_binary() -> str:
    """Find the path to rclone binary across Windows, Linux, and custom download paths."""
    # 1. System PATH
    found = shutil.which("rclone")
    if found:
        return found

    # 2. Windows Downloads directory fallback
    win_dl = os.path.expanduser(r"~\Downloads")
    if os.path.exists(win_dl):
        for root, _, files in os.walk(win_dl):
            if "rclone.exe" in files:
                return os.path.join(root, "rclone.exe")

    return "rclone"


class RcloneDriveClient:
    """Async wrapper around rclone for Google Drive & OneDrive."""

    def __init__(
        self,
        remote_name: str = "gdrive",
        folder_name: str = "FilmSub_Movies",
        config_file: Optional[str] = None,
    ) -> None:
        self.remote_name = remote_name
        self.drive_id = remote_name
        self.name = f"Rclone_{remote_name}"
        self.folder_name = folder_name
        self.config_file = config_file or DEFAULT_CONF_FILE
        self.rclone_bin = find_rclone_binary()
        self.is_active = True
        self.last_error = ""

    def _cmd_prefix(self) -> list[str]:
        cmd = [self.rclone_bin]
        if os.path.exists(self.config_file):
            cmd.extend(["--config", self.config_file])
        return cmd

    async def get_quota(self) -> Dict[str, Any]:
        """Fetch remote storage quota via `rclone about <remote>: --json`."""
        cmd = self._cmd_prefix() + ["about", f"{self.remote_name}:", "--json"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                self.is_active = False
                self.last_error = stderr.decode(errors="ignore").strip()
                raise RuntimeError(f"Rclone about failed: {self.last_error}")

            data = json.loads(stdout.decode())
            total = data.get("total", 0)
            used = data.get("used", 0)
            free = data.get("free", max(total - used, 0))
            self.is_active = True
            return {
                "total_bytes": total,
                "used_bytes": used,
                "remaining_bytes": free,
                "state": "normal",
            }
        except Exception as exc:
            self.is_active = False
            self.last_error = str(exc)
            log.warning("[Rclone:%s] Quota check failed: %s", self.remote_name, exc)
            raise

    async def ensure_folder(self) -> bool:
        """Create target folder on remote."""
        cmd = self._cmd_prefix() + ["mkdir", f"{self.remote_name}:{self.folder_name}"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await proc.communicate()
        return proc.returncode == 0

    async def upload_file(
        self,
        local_path: str,
        filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], Any]] = None,
    ) -> Dict[str, Any]:
        """
        Upload local file to `<remote>:FilmSub_Movies/<filename>` and retrieve public direct link.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        upload_name = filename or os.path.basename(local_path)
        remote_dest = f"{self.remote_name}:{self.folder_name}/{upload_name}"

        await self.ensure_folder()
        log.info("[Rclone:%s] Uploading '%s' (%d bytes) to %s...", self.remote_name, local_path, file_size, remote_dest)

        # 1. Copy file
        cmd = self._cmd_prefix() + [
            "copyto",
            local_path,
            remote_dest,
            "--transfers", "4",
            "--drive-chunk-size", "64M",
            "-v",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode(errors="ignore").strip()
            self.is_active = False
            self.last_error = err
            raise RuntimeError(f"Rclone copyto failed: {err}")

        # 2. Get public link
        link_cmd = self._cmd_prefix() + ["link", remote_dest]
        link_proc = await asyncio.create_subprocess_exec(
            *link_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        link_out, _ = await link_proc.communicate()
        web_link = link_out.decode().strip()

        # Extract file ID for direct streaming URL
        file_id = ""
        direct_stream_url = web_link

        if "id=" in web_link:
            m = re.search(r"id=([a-zA-Z0-9_-]+)", web_link)
            if m:
                file_id = m.group(1)
                direct_stream_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        elif "/d/" in web_link:
            m = re.search(r"/d/([a-zA-Z0-9_-]+)", web_link)
            if m:
                file_id = m.group(1)
                direct_stream_url = f"https://drive.google.com/uc?export=download&id={file_id}"

        log.info("[Rclone:%s] Upload completed: %s -> %s", self.remote_name, upload_name, direct_stream_url)

        return {
            "file_id": file_id or upload_name,
            "filename": upload_name,
            "size": file_size,
            "web_url": web_link,
            "stream_url": direct_stream_url or web_link,
            "download_url": direct_stream_url or web_link,
            "drive_id": self.drive_id,
            "provider": "gdrive",
        }
