"""
drive_manager.py — Multi-account Cloud Drive Pool & Health Monitoring Manager.

Responsibilities:
1. Pool management: Register multiple Google Drive & OneDrive accounts.
2. Auto-selection: Select the active drive with the highest remaining free space.
3. Health monitoring: Verify tokens and detect inactive/disconnected drives.
4. Movie-to-Drive tracking: Index which movie is stored on which drive.
5. Inactive audit: Report all movies that become inaccessible when a drive goes down.
"""

import asyncio
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Union

from .gdrive_client import GoogleDriveClient
from .onedrive_client import OneDriveClient
from .rclone_client import RcloneDriveClient, DEFAULT_CONF_FILE

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DRIVES_CONFIG_FILE = os.path.join(DATA_DIR, "drives.json")
DRIVE_MOVIES_FILE = os.path.join(DATA_DIR, "drive_movies_index.json")


class DriveManager:
    """Central manager for multi-account OneDrive & Google Drive storage."""

    def __init__(self) -> None:
        self.drives: Dict[str, Union[OneDriveClient, GoogleDriveClient]] = {}
        self.movie_index: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    def initialize(self) -> None:
        """Load configured drives and movie indices from persistent storage."""
        if self._initialized:
            return
        self._initialized = True

        os.makedirs(DATA_DIR, exist_ok=True)

        # 1. Load movie index from disk (merge with any existing)
        if os.path.exists(DRIVE_MOVIES_FILE):
            try:
                with open(DRIVE_MOVIES_FILE, "r", encoding="utf-8") as f:
                    disk_index = json.load(f)
                    self.movie_index = {**disk_index, **self.movie_index}
            except Exception as e:
                log.warning("[DriveManager] Error loading movie index: %s", e)

        # 2. Load drives configuration from JSON
        if os.path.exists(DRIVES_CONFIG_FILE):
            try:
                with open(DRIVES_CONFIG_FILE, "r", encoding="utf-8") as f:
                    drives_data = json.load(f)
                    for d in drives_data:
                        self._register_client_from_dict(d)
            except Exception as e:
                log.warning("[DriveManager] Error loading drives.json: %s", e)

        # 3. Load drives from environment variables if present
        # Format: ONEDRIVE_ACCOUNTS=id:name:refresh_token:client_id:client_secret;...
        raw_onedrive = os.getenv("ONEDRIVE_ACCOUNTS", "").strip()
        if raw_onedrive:
            for item in raw_onedrive.split(";"):
                parts = item.strip().split(":")
                if len(parts) >= 3:
                    d_id = parts[0]
                    d_name = parts[1]
                    d_refresh = parts[2]
                    d_client_id = parts[3] if len(parts) > 3 else None
                    d_secret = parts[4] if len(parts) > 4 else None
                    if d_id not in self.drives:
                        self.drives[d_id] = OneDriveClient(
                            drive_id=d_id,
                            name=d_name,
                            refresh_token=d_refresh,
                            client_id=d_client_id,
                            client_secret=d_secret,
                        )

        # 4. Auto-populate rclone.conf from environment variable on Render/cloud
        gdrive_token_env = os.getenv("RCLONE_CONFIG_GDRIVE_TOKEN") or os.getenv("GDRIVE_TOKEN")
        if gdrive_token_env:
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                existing = ""
                if os.path.exists(DEFAULT_CONF_FILE):
                    with open(DEFAULT_CONF_FILE, "r", encoding="utf-8") as f:
                        existing = f.read()
                if "[gdrive]" not in existing:
                    with open(DEFAULT_CONF_FILE, "a+", encoding="utf-8") as f:
                        f.write(f"\n[gdrive]\ntype = drive\nscope = drive\ntoken = {gdrive_token_env}\n")
                    log.info("[DriveManager] Wrote gdrive token from environment variable to %s", DEFAULT_CONF_FILE)
            except Exception as w_err:
                log.warning("[DriveManager] Could not write rclone env token: %s", w_err)

        # 5. Load remotes from rclone.conf (e.g. 5TB Google Drive / OneDrive)
        if os.path.exists(DEFAULT_CONF_FILE):
            try:
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read(DEFAULT_CONF_FILE)
                for section in cfg.sections():
                    if section not in self.drives:
                        self.drives[section] = RcloneDriveClient(remote_name=section, config_file=DEFAULT_CONF_FILE)
                        log.info("[DriveManager] Auto-registered Rclone remote '%s' into cloud pool.", section)
            except Exception as rc_err:
                log.warning("[DriveManager] Error reading rclone.conf: %s", rc_err)

        self._initialized = True
        log.info("[DriveManager] Initialized with %d configured drive(s).", len(self.drives))

    def _register_client_from_dict(self, d: Dict[str, Any]) -> None:
        provider = d.get("provider", "").lower()
        d_id = d.get("id") or d.get("drive_id")
        name = d.get("name", d_id)
        if not d_id or not provider:
            return

        if provider == "onedrive":
            self.drives[d_id] = OneDriveClient(
                drive_id=d_id,
                name=name,
                refresh_token=d.get("refresh_token", ""),
                client_id=d.get("client_id"),
                client_secret=d.get("client_secret"),
                folder_name=d.get("folder_name", "FilmSub_Movies"),
            )
        elif provider in ("gdrive", "googledrive"):
            self.drives[d_id] = GoogleDriveClient(
                drive_id=d_id,
                name=name,
                refresh_token=d.get("refresh_token", ""),
                client_id=d.get("client_id", ""),
                client_secret=d.get("client_secret", ""),
                folder_name=d.get("folder_name", "FilmSub_Movies"),
            )

    def save_state(self) -> None:
        """Persist movie index and configured drives."""
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(DRIVE_MOVIES_FILE, "w", encoding="utf-8") as f:
                json.dump(self.movie_index, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error("[DriveManager] Failed to save movie index: %s", e)

    def add_drive(
        self,
        drive_id: str,
        name: str,
        provider: str,
        refresh_token: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        folder_name: str = "FilmSub_Movies",
    ) -> Dict[str, Any]:
        """Dynamically add and persist a new cloud drive."""
        self.initialize()
        d_dict = {
            "id": drive_id,
            "name": name,
            "provider": provider.lower(),
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "folder_name": folder_name,
        }
        self._register_client_from_dict(d_dict)

        # Update drives.json
        drives_list: List[Dict[str, Any]] = []
        if os.path.exists(DRIVES_CONFIG_FILE):
            try:
                with open(DRIVES_CONFIG_FILE, "r", encoding="utf-8") as f:
                    drives_list = json.load(f)
            except Exception:
                drives_list = []

        drives_list = [d for d in drives_list if d.get("id") != drive_id]
        drives_list.append(d_dict)

        with open(DRIVES_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(drives_list, f, indent=2)

        return {"status": "success", "drive_id": drive_id, "name": name, "provider": provider}

    def remove_drive(self, drive_id: str) -> bool:
        """Remove a drive from active management."""
        self.initialize()
        if drive_id in self.drives:
            del self.drives[drive_id]

        if os.path.exists(DRIVES_CONFIG_FILE):
            try:
                with open(DRIVES_CONFIG_FILE, "r", encoding="utf-8") as f:
                    drives_list = json.load(f)
                drives_list = [d for d in drives_list if d.get("id") != drive_id]
                with open(DRIVES_CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(drives_list, f, indent=2)
                return True
            except Exception as e:
                log.error("[DriveManager] Error removing drive: %s", e)
        return False

    async def get_drives_status(self) -> List[Dict[str, Any]]:
        """
        Check health and remaining space of all registered drives.
        Returns detailed status for each drive.
        """
        self.initialize()
        results: List[Dict[str, Any]] = []

        for d_id, client in self.drives.items():
            status_entry: Dict[str, Any] = {
                "drive_id": d_id,
                "name": client.name,
                "provider": "onedrive" if isinstance(client, OneDriveClient) else "gdrive",
                "is_active": False,
                "total_gb": 0.0,
                "used_gb": 0.0,
                "remaining_gb": 0.0,
                "movie_count": sum(1 for m in self.movie_index.values() if m.get("drive_id") == d_id),
                "error": "",
            }

            try:
                quota = await client.get_quota()
                total_b = quota.get("total_bytes", 0)
                used_b = quota.get("used_bytes", 0)
                rem_b = quota.get("remaining_bytes", 0)

                status_entry["is_active"] = client.is_active
                status_entry["total_gb"] = round(total_b / (1024**3), 2)
                status_entry["used_gb"] = round(used_b / (1024**3), 2)
                status_entry["remaining_gb"] = round(rem_b / (1024**3), 2)
            except Exception as exc:
                status_entry["is_active"] = False
                status_entry["error"] = str(exc)

            results.append(status_entry)

        return results

    async def pick_best_drive(self, required_bytes: int = 0) -> Optional[Union[OneDriveClient, GoogleDriveClient]]:
        """Select the active drive with the most remaining free space."""
        self.initialize()
        best_client = None
        max_remaining = -1

        for d_id, client in self.drives.items():
            try:
                quota = await client.get_quota()
                rem = quota.get("remaining_bytes", 0)
                if client.is_active and rem >= required_bytes:
                    if rem > max_remaining:
                        max_remaining = rem
                        best_client = client
            except Exception as exc:
                log.warning("[DriveManager] Health check failed for %s: %s", d_id, exc)

        return best_client

    async def upload_movie(
        self,
        local_path: str,
        movie_slug: str,
        movie_title: str,
        filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Uploads a video to the best available active drive, registers it in the
        movie index, and returns the direct CDN streaming payload.
        """
        self.initialize()
        if not self.drives:
            log.info("[DriveManager] No cloud drives configured. Skipping cloud upload.")
            return None

        file_size = os.path.getsize(local_path)
        client = await self.pick_best_drive(required_bytes=file_size)

        if not client:
            log.warning("[DriveManager] No active drive with enough space for %d bytes.", file_size)
            return None

        upload_res = await client.upload_file(
            local_path=local_path,
            filename=filename or f"{movie_slug}.mp4",
            progress_callback=progress_callback,
        )

        # Record in index
        self.movie_index[movie_slug] = {
            "movie_slug": movie_slug,
            "title": movie_title,
            "drive_id": client.drive_id,
            "provider": upload_res.get("provider", "onedrive"),
            "file_id": upload_res.get("file_id", ""),
            "filename": upload_res.get("filename", ""),
            "size": upload_res.get("size", file_size),
            "stream_url": upload_res.get("stream_url", ""),
            "download_url": upload_res.get("download_url", ""),
            "web_url": upload_res.get("web_url", ""),
        }
        self.save_state()

        log.info("[DriveManager] Successfully stored '%s' on %s (%s).", movie_slug, client.name, client.drive_id)
        return upload_res

    def get_movies_on_drive(self, drive_id: str) -> List[Dict[str, Any]]:
        """Return list of all movies stored on a given drive ID."""
        self.initialize()
        return [m for m in self.movie_index.values() if m.get("drive_id") == drive_id]

    async def get_offline_movies(self) -> Dict[str, Any]:
        """
        Inspect all drives. If any drive is inactive, returns the list
        of movies that are affected.
        """
        statuses = await self.get_drives_status()
        offline_drives = [s for s in statuses if not s["is_active"]]

        affected_movies: List[Dict[str, Any]] = []
        for d in offline_drives:
            d_id = d["drive_id"]
            movies = self.get_movies_on_drive(d_id)
            for m in movies:
                affected_movies.append({
                    **m,
                    "drive_name": d["name"],
                    "error": d.get("error", "Drive inactive/disconnected"),
                })

        return {
            "total_offline_drives": len(offline_drives),
            "offline_drives": offline_drives,
            "total_affected_movies": len(affected_movies),
            "affected_movies": affected_movies,
        }


# Singleton instance
drive_manager = DriveManager()
