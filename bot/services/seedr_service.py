import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

import httpx

import config

log = logging.getLogger(__name__)

SEEDR_TOKEN_URL = "https://www.seedr.cc/oauth_test/token.php"
SEEDR_RESOURCE_URL = "https://www.seedr.cc/oauth_test/resource.php"
SEEDR_CLIENT_ID = "seedr_xbmc"


class SeedrService:
    """Async client for Seedr.cc cloud torrent debrid service."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.username = username or os.environ.get("SEEDR_USERNAME") or getattr(config, "SEEDR_USERNAME", "")
        self.password = password or os.environ.get("SEEDR_PASSWORD") or getattr(config, "SEEDR_PASSWORD", "")
        self.token = token or os.environ.get("SEEDR_TOKEN") or getattr(config, "SEEDR_TOKEN", "")
        self.token_expiry: float = 0.0

    def is_configured(self) -> bool:
        """Check if Seedr credentials or token are provided."""
        return bool(self.token or (self.username and self.password))

    async def get_token(self) -> Optional[str]:
        """Authenticate and retrieve valid Bearer token."""
        if self.token and time.time() < self.token_expiry:
            return self.token

        if not self.username or not self.password:
            if self.token:
                return self.token
            log.warning("[Seedr] Neither SEEDR_TOKEN nor (SEEDR_USERNAME, SEEDR_PASSWORD) configured.")
            return None

        data = {
            "grant_type": "password",
            "client_id": SEEDR_CLIENT_ID,
            "type": "login",
            "username": self.username,
            "password": self.password,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(SEEDR_TOKEN_URL, data=data)
                if resp.status_code != 200:
                    log.error("[Seedr] Authentication failed: %s %s", resp.status_code, resp.text)
                    return None

                res_json = resp.json()
                self.token = res_json.get("access_token")
                expires_in = int(res_json.get("expires_in", 3600))
                self.token_expiry = time.time() + expires_in - 60
                log.info("[Seedr] Authenticated successfully with Seedr.cc.")
                return self.token
        except Exception as exc:
            log.error("[Seedr] Auth request exception: %s", exc)
            return None

    async def convert_magnet_to_direct_url(
        self,
        magnet_url: str,
        timeout_seconds: int = 180,
        progress_callback: Optional[object] = None,
    ) -> Optional[dict]:
        """
        Add magnet to Seedr Cloud, wait for cloud download to finish,
        and return the direct HTTPS download link.
        """
        token = await self.get_token()
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}"}

        # Normalize magnet URL if it is a hash or contains a 40-char info_hash
        if not magnet_url.startswith("magnet:"):
            m = re.search(r"([0-9a-fA-F]{40})", magnet_url)
            if m:
                magnet_url = f"magnet:?xt=urn:btih:{m.group(1)}"
            else:
                log.warning("[Seedr] Invalid magnet URL provided: %s", magnet_url)
                return None

        # Step 1: Clean any existing files in Seedr to ensure 2GB quota is free
        # Step 1: Clean any existing files in Seedr to ensure 2GB quota is free
        if progress_callback:
            await progress_callback("☁️ <b>Seedr Cloud:</b> ගිණුමේ ඉඩ පරීක්ෂා කර පිරිසිදු කරමින් (Preparing Storage)...")
        await self.clean_storage(token)

        # Step 2: Add magnet to Seedr
        try:
            log.info("[Seedr] Adding magnet to Seedr cloud: %s", magnet_url[:60])
            if progress_callback:
                await progress_callback("☁️ <b>Seedr Cloud:</b> Magnet Torrent එක Seedr වෙත යවමින්...")

            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    SEEDR_RESOURCE_URL,
                    headers=headers,
                    data={"func": "add_torrent", "torrent_magnet": magnet_url},
                )
                res_data = resp.json()
                result_val = res_data.get("result")
                if result_val not in (True, "true", 1, "1"):
                    err_msg = res_data.get("result") or res_data.get("error") or "Unknown error"
                    log.warning("[Seedr] Failed to add torrent: %s", res_data)
                    if progress_callback:
                        await progress_callback(f"⚠️ <b>Seedr Cloud දෝෂය:</b> Torrent එක එක්කිරීම අසාර්ථක විය ({err_msg})")
                    return None

            log.info("[Seedr] Torrent added to Seedr cloud successfully.")
            if progress_callback:
                await progress_callback("☁️ <b>Seedr Cloud:</b> Torrent එක සාර්ථකව ලැබිණි. Cloud බාගත කිරීම ආරම්භ විය...")
        except Exception as exc:
            log.error("[Seedr] Error adding torrent: %s", exc)
            if progress_callback:
                await progress_callback(f"⚠️ <b>Seedr Cloud Connection Error:</b> {exc}")
            return None

        # Step 3: Poll folder until torrent is ready in cloud
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            await asyncio.sleep(2)
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        SEEDR_RESOURCE_URL,
                        headers=headers,
                        params={"func": "get_folder"},
                    )
                    folder_data = resp.json()

                    files = folder_data.get("files", [])
                    folders = folder_data.get("folders", [])

                    video_files = [
                        f for f in files
                        if any(f.get("name", "").lower().endswith(ext) for ext in (".mp4", ".mkv", ".avi", ".mov"))
                    ]

                    if not video_files and folders:
                        for folder in folders:
                            sub_id = folder.get("id")
                            if not sub_id:
                                continue
                            sub_resp = await client.get(
                                SEEDR_RESOURCE_URL,
                                headers=headers,
                                params={"func": "get_folder", "id": sub_id},
                            )
                            sub_data = sub_resp.json()
                            for f in sub_data.get("files", []):
                                if any(f.get("name", "").lower().endswith(ext) for ext in (".mp4", ".mkv", ".avi", ".mov")):
                                    video_files.append(f)

                    if video_files:
                        video_files.sort(key=lambda x: x.get("size", 0), reverse=True)
                        target_file = video_files[0]
                        file_id = target_file.get("folder_file_id") or target_file.get("id")
                        file_name = target_file.get("name", "movie.mp4")
                        file_size = target_file.get("size", 0)

                        if progress_callback:
                            await progress_callback(
                                f"☁️ <b>Seedr Cloud බාගත වීම සාර්ථකයි!</b>\n\n"
                                f"📁 <b>ගොනුව:</b> <code>{file_name}</code>\n"
                                f"📦 <b>ප්‍රමාණය:</b> {file_size / (1024 * 1024):.1f} MB\n"
                                f"⚡ Direct High-Speed Download Link එක සූදානම් කරමින්..."
                            )

                        link_resp = await client.get(
                            SEEDR_RESOURCE_URL,
                            headers=headers,
                            params={"func": "fetch_file", "folder_file_id": file_id},
                        )
                        link_data = link_resp.json()
                        direct_url = link_data.get("url")

                        if direct_url:
                            log.info(
                                "[Seedr] Cloud download complete! Direct URL obtained: %s (%d bytes)",
                                file_name, file_size
                            )
                            return {
                                "direct_url": direct_url,
                                "file_name": file_name,
                                "file_size": file_size,
                                "file_id": file_id,
                            }

                    torrents = folder_data.get("torrents", [])
                    if torrents:
                        t = torrents[0]
                        progress = t.get("progress", 0)
                        seeders = t.get("connected_to", 0) or t.get("seeders", 0)
                        rate_bytes = t.get("download_rate", 0)
                        rate_str = f"{rate_bytes / (1024 * 1024):.1f} MB/s" if rate_bytes > 0 else "Connecting"
                        filled = int(round(10 * (progress / 100.0)))
                        p_bar = f"[{'█' * filled}{'░' * (10 - filled)}]"
                        log.debug("[Seedr] Cloud downloading progress: %s%%", progress)
                        if progress_callback:
                            await progress_callback(
                                f"☁️ <b>Seedr Cloud එක බාගත කරමින් පවතී...</b>\n\n"
                                f"📊 <b>ප්‍රගතිය:</b> {p_bar} {progress}%\n"
                                f"⚡ <b>Cloud Speed:</b> {rate_str} | 👥 <b>Seeders:</b> {seeders}\n"
                                f"💡 <i>Seedr Cloud එකෙන් බාගත වූ පසු Render Server එකට කෙලින්ම Direct Link එක ලබාගනී.</i>"
                            )

            except Exception as poll_err:
                log.debug("[Seedr] Poll iteration error: %s", poll_err)

        log.warning("[Seedr] Timeout reached waiting for torrent cloud download.")
        return None

    async def clean_storage(self, token: Optional[str] = None) -> None:
        """Delete all items in Seedr cloud to maintain clean 2GB storage."""
        try:
            tok = token or await self.get_token()
            if not tok:
                return

            headers = {"Authorization": f"Bearer {tok}"}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    SEEDR_RESOURCE_URL,
                    headers=headers,
                    params={"func": "get_folder"},
                )
                data = resp.json()

                delete_arr = []
                for f in data.get("files", []):
                    delete_arr.append({"type": "file", "id": f["folder_file_id"]})
                for d in data.get("folders", []):
                    delete_arr.append({"type": "folder", "id": d["id"]})
                for t in data.get("torrents", []):
                    delete_arr.append({"type": "torrent", "id": t["id"]})

                if delete_arr:
                    import json
                    await client.post(
                        SEEDR_RESOURCE_URL,
                        headers=headers,
                        data={"func": "delete", "delete_arr": json.dumps(delete_arr)},
                    )
                    log.info("[Seedr] Storage cleaned (%d items deleted).", len(delete_arr))
        except Exception as exc:
            log.warning("[Seedr] Storage cleanup error: %s", exc)


class SeedrPool:
    """Manages a pool of multiple Seedr accounts for load balancing and increased storage."""

    def __init__(self, storage_file: str = "seedr_accounts.json"):
        self.storage_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", storage_file)
        self.services: list[SeedrService] = []
        self._current_idx = 0
        self._load_accounts()

    def _load_accounts(self) -> None:
        """Load accounts from JSON file or fallback to .env."""
        accounts = []
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    accounts = json.load(f)
            except Exception as e:
                log.warning("[SeedrPool] Error reading %s: %s", self.storage_file, e)

        # If empty, check .env default credentials
        if not accounts:
            u = os.environ.get("SEEDR_USERNAME") or getattr(config, "SEEDR_USERNAME", "")
            p = os.environ.get("SEEDR_PASSWORD") or getattr(config, "SEEDR_PASSWORD", "")
            if u and p:
                accounts.append({"username": u, "password": p})
                self._save_accounts(accounts)

        self.services = [SeedrService(username=acc["username"], password=acc["password"]) for acc in accounts]
        log.info("[SeedrPool] Initialized with %d Seedr account(s) in pool.", len(self.services))

    def _save_accounts(self, accounts: list[dict]) -> None:
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(accounts, f, indent=2)
        except Exception as e:
            log.error("[SeedrPool] Error saving accounts: %s", e)

    def is_configured(self) -> bool:
        return any(s.is_configured() for s in self.services)

    @property
    def username(self) -> str:
        return self.services[0].username if self.services else ""

    @property
    def password(self) -> str:
        return self.services[0].password if self.services else ""

    async def add_account(self, username: str, password: str) -> tuple[bool, str]:
        """Test and add a new Seedr account to the pool."""
        test_svc = SeedrService(username=username, password=password)
        tok = await test_svc.get_token()
        if not tok:
            return False, "Seedr.cc සමඟ සම්බන්ධ වීම අසාර්ථකයි. කරුණාකර Email සහ Password නිවැරදිදැයි පරීක්ෂා කරන්න."

        accounts = []
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    accounts = json.load(f)
            except Exception:
                pass

        existing = [acc for acc in accounts if acc.get("username", "").lower() == username.lower()]
        if existing:
            existing[0]["password"] = password
        else:
            accounts.append({"username": username, "password": password})

        self._save_accounts(accounts)
        self._load_accounts()
        return True, f"✅ ගිණුම සාර්ථකව Pool එකට එක් කරන ලදී!\n📧 {username}\n📊 මුළු සක්‍රීය ගිණුම් ගණන: {len(self.services)}"

    async def remove_account(self, username: str) -> bool:
        accounts = []
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    accounts = json.load(f)
            except Exception:
                pass

        new_accounts = [acc for acc in accounts if acc.get("username", "").lower() != username.lower()]
        if len(new_accounts) == len(accounts):
            return False

        self._save_accounts(new_accounts)
        self._load_accounts()
        return True

    async def list_accounts_status(self) -> list[dict]:
        """Check status and used storage for all accounts in pool."""
        results = []
        for idx, svc in enumerate(self.services, 1):
            tok = await svc.get_token()
            if not tok:
                results.append({
                    "index": idx,
                    "username": svc.username,
                    "status": "❌ දෝෂයකි (Auth Failed)",
                    "used_gb": 0.0,
                    "total_gb": 2.0,
                })
                continue

            try:
                headers = {"Authorization": f"Bearer {tok}"}
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(SEEDR_RESOURCE_URL, headers=headers, params={"func": "get_settings"})
                    if resp.status_code == 200:
                        settings = resp.json().get("account", {})
                        used_space = float(settings.get("space_used", 0)) / (1024 * 1024 * 1024)
                        max_space = float(settings.get("space_max", 2 * 1024 * 1024 * 1024)) / (1024 * 1024 * 1024)
                        results.append({
                            "index": idx,
                            "username": svc.username,
                            "status": "✅ සක්‍රීයයි (Active)",
                            "used_gb": round(used_space, 2),
                            "total_gb": round(max_space, 2),
                        })
                    else:
                        results.append({
                            "index": idx,
                            "username": svc.username,
                            "status": "✅ සක්‍රීයයි (Active)",
                            "used_gb": 0.0,
                            "total_gb": 2.0,
                        })
            except Exception:
                results.append({
                    "index": idx,
                    "username": svc.username,
                    "status": "✅ සක්‍රීයයි (Active)",
                    "used_gb": 0.0,
                    "total_gb": 2.0,
                })

        return results

    async def convert_magnet_to_direct_url(
        self,
        magnet_url: str,
        timeout_seconds: int = 180,
        progress_callback: Optional[object] = None,
    ) -> Optional[dict]:
        """
        Attempt conversion using accounts in pool with round-robin rotation.
        If account 1 fails (e.g. storage full), automatically rotates to account 2, 3, etc.
        """
        if not self.services:
            log.warning("[SeedrPool] No Seedr accounts available in pool.")
            return None

        total = len(self.services)
        for attempt in range(total):
            idx = (self._current_idx + attempt) % total
            svc = self.services[idx]
            log.info("[SeedrPool] Trying account %d/%d: %s", idx + 1, total, svc.username)

            if progress_callback:
                try:
                    await progress_callback(f"☁️ Seedr Pool ගිණුම {idx + 1}/{total} භාවිතා කරමින් ({svc.username})...")
                except Exception:
                    pass

            res = await svc.convert_magnet_to_direct_url(
                magnet_url=magnet_url,
                timeout_seconds=timeout_seconds,
                progress_callback=progress_callback,
            )

            if res and res.get("direct_url"):
                res["service"] = svc
                self._current_idx = (idx + 1) % total
                log.info("[SeedrPool] Download converted successfully via account: %s", svc.username)
                return res
            else:
                log.warning("[SeedrPool] Account %s failed. Trying next account in pool...", svc.username)

        log.error("[SeedrPool] All %d Seedr accounts in pool failed to convert torrent.", total)
        return None

    async def clean_storage(self, service: Optional[SeedrService] = None) -> None:
        """Clean storage of a specific service or all services in the pool."""
        if service:
            await service.clean_storage()
        else:
            for s in self.services:
                await s.clean_storage()


# Initialize global pool and client
seedr_pool = SeedrPool()
seedr_client = seedr_pool