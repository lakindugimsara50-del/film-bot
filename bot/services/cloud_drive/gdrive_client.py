"""
gdrive_client.py — High-performance Google Drive uploader using Drive API v3.

Supports:
- Resumable chunked upload sessions for large files (1GB - 5GB+).
- Automatic token refresh via OAuth2 refresh_token or Service Account.
- Dedicated folder creation: FilmSub_Movies/
- Direct CDN streaming & anonymous download link generation.
- Quota inspection (used, total, remaining).
"""

import asyncio
import logging
import os
from typing import Any, Callable, Dict, Optional

import aiofiles
import httpx

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MiB upload chunk


class GoogleDriveClient:
    """Async client for uploading to and streaming from Google Drive."""

    def __init__(
        self,
        drive_id: str,
        name: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        folder_name: str = "FilmSub_Movies",
    ) -> None:
        self.drive_id = drive_id
        self.name = name
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.folder_name = folder_name

        self.access_token: Optional[str] = None
        self._lock = asyncio.Lock()
        self.is_active = True
        self.last_error = ""

    async def get_access_token(self) -> str:
        """Refresh and return a valid access token."""
        async with self._lock:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(TOKEN_URL, data=data)
                if resp.status_code != 200:
                    self.is_active = False
                    self.last_error = f"Google token refresh failed: {resp.text}"
                    log.error("[GDrive:%s] %s", self.name, self.last_error)
                    raise RuntimeError(f"Google Drive token refresh failed: {resp.status_code}")

                payload = resp.json()
                self.access_token = payload.get("access_token")
                self.is_active = True
                self.last_error = ""
                return self.access_token

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    async def get_quota(self) -> Dict[str, Any]:
        """Fetch Google Drive storage quota."""
        await self.get_access_token()
        url = f"{DRIVE_API_BASE}/about?fields=storageQuota"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=self._auth_headers())
            if resp.status_code != 200:
                self.is_active = False
                self.last_error = f"Failed to fetch quota: {resp.status_code}"
                raise RuntimeError(self.last_error)

            quota = resp.json().get("storageQuota", {})
            total = int(quota.get("limit", 0))
            used = int(quota.get("usage", 0))
            remaining = max(total - used, 0) if total > 0 else 107374182400  # Default 100GB if unlimited
            self.is_active = True
            return {
                "total_bytes": total,
                "used_bytes": used,
                "remaining_bytes": remaining,
                "state": "normal",
            }

    async def ensure_folder(self) -> str:
        """Ensure the target root folder exists and return its folder_id."""
        await self.get_access_token()
        query = f"name = '{self.folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        url = f"{DRIVE_API_BASE}/files?q={httpx.URL('', params={'q': query}).params['q']}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=self._auth_headers())
            if r.status_code == 200:
                files = r.json().get("files", [])
                if files:
                    return files[0]["id"]

            # Create folder
            body = {
                "name": self.folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            create_resp = await client.post(f"{DRIVE_API_BASE}/files", json=body, headers=self._auth_headers())
            if create_resp.status_code in (200, 201):
                return create_resp.json().get("id", "")
            return ""

    async def upload_file(
        self,
        local_path: str,
        filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], Any]] = None,
    ) -> Dict[str, Any]:
        """
        Uploads a local file using a resumable session.
        Makes file publicly readable and returns direct stream link.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"File not found: {local_path}")

        file_size = os.path.getsize(local_path)
        upload_name = filename or os.path.basename(local_path)

        await self.get_access_token()
        folder_id = await self.ensure_folder()

        log.info("[GDrive:%s] Creating resumable upload session for '%s' (%d bytes)...", self.name, upload_name, file_size)

        metadata = {"name": upload_name}
        if folder_id:
            metadata["parents"] = [folder_id]

        init_headers = {
            **self._auth_headers(),
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_size),
            "Content-Type": "application/json; charset=UTF-8",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            init_resp = await client.post(UPLOAD_API_BASE, json=metadata, headers=init_headers)
            if init_resp.status_code != 200:
                raise RuntimeError(f"Failed to start GDrive upload session: {init_resp.status_code} - {init_resp.text}")
            upload_url = init_resp.headers.get("Location")

        if not upload_url:
            raise RuntimeError("No upload Location returned by Google Drive.")

        # Chunked upload
        item_data: Dict[str, Any] = {}
        uploaded_bytes = 0

        async with aiofiles.open(local_path, "rb") as f:
            while uploaded_bytes < file_size:
                chunk = await f.read(CHUNK_SIZE)
                if not chunk:
                    break

                chunk_len = len(chunk)
                start_byte = uploaded_bytes
                end_byte = uploaded_bytes + chunk_len - 1

                headers = {
                    "Content-Length": str(chunk_len),
                    "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                }

                for attempt in range(1, 4):
                    try:
                        async with httpx.AsyncClient(timeout=120.0) as u_client:
                            put_resp = await u_client.put(upload_url, content=chunk, headers=headers)
                            if put_resp.status_code in (200, 201):
                                item_data = put_resp.json()
                                break
                            elif put_resp.status_code == 308:
                                # Incomplete, chunk saved
                                break
                            else:
                                if attempt == 3:
                                    raise RuntimeError(f"GDrive chunk upload failed: {put_resp.status_code} - {put_resp.text}")
                                await asyncio.sleep(2 * attempt)
                    except Exception as exc:
                        if attempt == 3:
                            raise
                        log.warning("[GDrive:%s] Retry chunk upload (%d/3): %s", self.name, attempt, exc)
                        await asyncio.sleep(2 * attempt)

                uploaded_bytes += chunk_len
                if progress_callback:
                    try:
                        res = progress_callback(uploaded_bytes, file_size)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass

        file_item_id = item_data.get("id", "")

        # Set public permission
        if file_item_id:
            try:
                perm_url = f"{DRIVE_API_BASE}/files/{file_item_id}/permissions"
                perm_body = {"role": "reader", "type": "anyone"}
                async with httpx.AsyncClient(timeout=20.0) as client:
                    await client.post(perm_url, json=perm_body, headers=self._auth_headers())
            except Exception as p_err:
                log.warning("[GDrive:%s] Could not set public permission: %s", self.name, p_err)

        direct_stream_url = f"https://drive.google.com/uc?export=download&id={file_item_id}" if file_item_id else ""

        return {
            "file_id": file_item_id,
            "filename": upload_name,
            "size": file_size,
            "web_url": f"https://drive.google.com/file/d/{file_item_id}/view" if file_item_id else "",
            "stream_url": direct_stream_url,
            "download_url": direct_stream_url,
            "drive_id": self.drive_id,
            "provider": "gdrive",
        }
