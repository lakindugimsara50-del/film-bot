import asyncio
import glob
import logging
import os
from typing import Dict, List, Optional

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
from services import telegram_upload

log = logging.getLogger(__name__)

_QUALITY_TIERS = {
    "1080p": (0, 33),
    "720p":  (34, 66),
    "480p":  (67, 99),
    "360p":  (67, 99),  # same as 480p
    "default": (0, 99),
}

class TelegramUploadPool:
    def __init__(self) -> None:
        self.clients: List[Client] = []
        self._main_client: Optional[Client] = None
        self._indices: Dict[str, int] = {k: 0 for k in _QUALITY_TIERS}
        self._lock = asyncio.Lock()

    def set_main_client(self, client: Client) -> None:
        self._main_client = client

    async def init(self, api_id: int, api_hash: str, sessions_dir: str = "bot/sessions") -> None:
        if not os.path.exists(sessions_dir):
            try:
                os.makedirs(sessions_dir, exist_ok=True)
            except Exception:
                pass
        
        session_files = sorted(glob.glob(os.path.join(sessions_dir, "*.session")))
        
        password = os.getenv("SESSION_2FA_PASSWORD", "2122138")
        
        for s_path in session_files:
            base_name = os.path.splitext(os.path.basename(s_path))[0]
            session_prefix = os.path.join(sessions_dir, base_name)
            if any(getattr(c, "name", "") == session_prefix for c in self.clients):
                continue
            
            try:
                c = Client(
                    name=session_prefix,
                    api_id=api_id,
                    api_hash=api_hash,
                    no_updates=True,
                    max_concurrent_transmissions=10,
                )
                await c.connect()
                
                try:
                    # check if authorized
                    await c.get_me()
                except SessionPasswordNeeded:
                    await c.check_password(password)
                    
                self.clients.append(c)
                log.info("[UploadPool] Loaded extra session file for upload: %s", base_name)
            except Exception as exc:
                log.warning("[UploadPool] Could not start session '%s': %s", base_name, exc)
                
        log.info("[UploadPool] Total active upload clients in pool: %d", len(self.clients))

    async def get_client_for_quality(self, quality: str = "default", fallback_client: Optional[Client] = None) -> Client:
        async with self._lock:
            if not self.clients:
                if self._main_client:
                    return self._main_client
                if fallback_client:
                    return fallback_client
                raise RuntimeError("No active Telegram clients in upload pool.")
            
            tier_range = _QUALITY_TIERS.get(quality, _QUALITY_TIERS["default"])
            
            # Sublist for the tier
            start_idx = tier_range[0]
            end_idx = tier_range[1] + 1
            
            available_clients = self.clients[start_idx:end_idx]
            if not available_clients:
                available_clients = self.clients

            connected = [c for c in available_clients if getattr(c, "is_connected", False)]
            if not connected:
                if self._main_client and getattr(self._main_client, "is_connected", False):
                    return self._main_client
                if fallback_client:
                    return fallback_client
                return available_clients[0]
                
            idx = self._indices.get(quality, 0)
            idx = (idx + 1) % len(connected)
            self._indices[quality] = idx
            return connected[idx]

    async def upload_with_pool(self, file_path: str, target_chat: int, quality: str, caption: str, file_name: str, progress_callback=None, fallback_client: Optional[Client] = None) -> dict:
        client = await self.get_client_for_quality(quality, fallback_client)
        
        # Call upload_video_file from telegram_upload module
        return await telegram_upload.upload_video_file(
            bot_client=client,
            file_path=file_path,
            target_chat=target_chat,
            caption=caption,
            progress_callback=progress_callback,
            fallback_chat=0
        )
        
    def get_status(self) -> dict:
        connected = [c for c in self.clients if getattr(c, "is_connected", False)]
        return {
            "total_clients": len(self.clients),
            "connected_clients": len(connected)
        }

upload_pool = TelegramUploadPool()
