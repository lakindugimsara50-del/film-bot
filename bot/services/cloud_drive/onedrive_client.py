"""
onedrive_client.py — High-performance Microsoft OneDrive & SharePoint uploader
using Microsoft Graph API v1.0.

Supports:
- Resumable chunked upload sessions for files > 4 MiB (up to 100 GB+).
- Automatic token refresh via OAuth2 refresh_token.
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

# Standard Microsoft public client ID for OneDrive/Office 365 (used by rclone and CLI tools)
DEFAULT_CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MiB upload chunk (optimal for Microsoft Graph)


class OneDriveClient:
    """Async client for uploading to and streaming from Microsoft OneDrive."""

    def __init__(
        self,
        drive_id: str,
        name: str,
        refresh_token: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        folder_name: str = "FilmSub_Movies",
    ) -> None:
        self.drive_id = drive_id
        self.name = name
        self.refresh_token = refresh_token
        self.client_id = client_id or DEFAULT_CLIENT_ID
        self.client_secret = client_secret or ""
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
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
            if self.client_secret:
                data["client_secret"] = self.client_secret

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(TOKEN_URL, data=data)
                if resp.status_code != 200:
                    self.is_active = False
                    self.last_error = f"Token refresh failed: {resp.text}"
                    log.error("[OneDrive:%s] %s", self.name, self.last_error)
                    raise RuntimeError(f"OneDrive token refresh failed: {resp.status_code}")

                payload = resp.json()
                self.access_token = payload.get("access_token")
                new_refresh = payload.get("refresh_token")
                if new_refresh:
                    self.refresh_token = new_refresh
                self.is_active = True
                self.last_error = ""
                return self.access_token

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    async def get_quota(self) -> Dict[str, Any]:
        """Fetch OneDrive storage quota (used, total, remaining)."""
        await self.get_access_token()
        url = f"{GRAPH_BASE}/me/drive"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=self._auth_headers())
            if resp.status_code != 200:
                self.is_active = False
                self.last_error = f"Failed to fetch quota: {resp.status_code}"
                raise RuntimeError(self.last_error)

            quota = resp.json().get("quota", {})
            total = quota.get("total", 0)
            used = quota.get("used", 0)
            remaining = quota.get("remaining", max(total - used, 0))
            self.is_active = True
            return {
                "total_bytes": total,
                "used_bytes": used,
                "remaining_bytes": remaining,
                "state": quota.get("state", "normal"),
            }

    async def ensure_folder(self) -> str:
        """Ensure the target root folder (e.g. FilmSub_Movies) exists."""
        await self.get_access_token()
        check_url = f"{GRAPH_BASE}/me/drive/root:/{self.folder_name}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(check_url, headers=self._auth_headers())
            if r.status_code == 200:
                return r.json().get("id", "")

            # Create if missing
            create_url = f"{GRAPH_BASE}/me/drive/root/children"
            body = {
                "name": self.folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            }
            create_resp = await client.post(create_url, json=body, headers=self._auth_headers())
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
        Uploads a local video file using a chunked resumable upload session.
        Returns:
            {
                "file_id": ...,
                "web_url": ...,
                "stream_url": ...,
                "download_url": ...,
                "size": ...,
                "drive_id": ...
            }
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"File not found: {local_path}")

        file_size = os.path.getsize(local_path)
        upload_name = filename or os.path.basename(local_path)

        await self.get_access_token()
        await self.ensure_folder()

        log.info("[OneDrive:%s] Creating upload session for '%s' (%d bytes)...", self.name, upload_name, file_size)

        # 1. Create upload session
        session_url = f"{GRAPH_BASE}/me/drive/root:/{self.folder_name}/{upload_name}:/createUploadSession"
        body = {
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": upload_name,
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            s_resp = await client.post(session_url, json=body, headers=self._auth_headers())
            if s_resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to create OneDrive upload session: {s_resp.status_code} - {s_resp.text}")
            upload_url = s_resp.json().get("uploadUrl")

        if not upload_url:
            raise RuntimeError("No uploadUrl returned by OneDrive.")

        # 2. Upload file in 10 MiB chunks
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

                # Upload chunk with retries
                for attempt in range(1, 4):
                    try:
                        async with httpx.AsyncClient(timeout=120.0) as u_client:
                            put_resp = await u_client.put(upload_url, content=chunk, headers=headers)
                            if put_resp.status_code in (200, 201):
                                # Completed
                                item_data = put_resp.json()
                                break
                            elif put_resp.status_code == 202:
                                # Chunk accepted
                                break
                            else:
                                if attempt == 3:
                                    raise RuntimeError(f"Chunk upload failed: {put_resp.status_code} - {put_resp.text}")
                                await asyncio.sleep(2 * attempt)
                    except Exception as exc:
                        if attempt == 3:
                            raise
                        log.warning("[OneDrive:%s] Retry chunk upload (%d/3): %s", self.name, attempt, exc)
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

        # 3. Create public/anonymous sharing link for direct CDN streaming
        direct_stream_url = ""
        web_link = item_data.get("webUrl", "")

        if file_item_id:
            try:
                link_url = f"{GRAPH_BASE}/me/drive/items/{file_item_id}/createLink"
                link_body = {"type": "view", "scope": "anonymous"}
                async with httpx.AsyncClient(timeout=20.0) as client:
                    link_resp = await client.post(link_url, json=link_body, headers=self._auth_headers())
                    if link_resp.status_code in (200, 201):
                        share_info = link_resp.json()
                        web_link = share_info.get("link", {}).get("webUrl", web_link)
            except Exception as link_err:
                log.warning("[OneDrive:%s] Could not create anonymous share link: %s", self.name, link_err)

        # Build direct streaming URL with ?download=1 for byte-range streaming
        if web_link:
            direct_stream_url = web_link + ("&" if "?" in web_link else "?") + "download=1"

        return {
            "file_id": file_item_id,
            "filename": upload_name,
            "size": file_size,
            "web_url": web_link,
            "stream_url": direct_stream_url or web_link,
            "download_url": direct_stream_url or web_link,
            "drive_id": self.drive_id,
            "provider": "onedrive",
        }
