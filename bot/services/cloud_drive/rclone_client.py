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


_CACHED_RCLONE_BIN: Optional[str] = None


def find_rclone_binary() -> str:
    """Find the path to rclone binary across Windows, Linux, and custom download paths."""
    global _CACHED_RCLONE_BIN
    if _CACHED_RCLONE_BIN:
        return _CACHED_RCLONE_BIN

    # 1. Project bin/ directory
    project_bin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "bin"))
    for cand in (os.path.join(project_bin, "rclone.exe"), os.path.join(project_bin, "rclone")):
        if os.path.exists(cand):
            _CACHED_RCLONE_BIN = cand
            return cand

    # 2. System PATH
    found = shutil.which("rclone")
    if found:
        _CACHED_RCLONE_BIN = found
        return found

    # 3. Windows Downloads directory fallback (shallow check)
    win_dl = os.path.expanduser(r"~\Downloads")
    if os.path.exists(win_dl):
        try:
            for entry in os.scandir(win_dl):
                if entry.is_file() and entry.name.lower() == "rclone.exe":
                    _CACHED_RCLONE_BIN = entry.path
                    return entry.path
                elif entry.is_dir() and "rclone" in entry.name.lower():
                    sub_exe = os.path.join(entry.path, "rclone.exe")
                    if os.path.exists(sub_exe):
                        _CACHED_RCLONE_BIN = sub_exe
                        return sub_exe
        except Exception:
            pass

    _CACHED_RCLONE_BIN = "rclone"
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
        Upload local file to `<remote>:FilmSub_Movies/<filename>` with real-time progress.
        Parses rclone --stats stderr to fire progress_callback(bytes_done, bytes_total).
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        upload_name = filename or os.path.basename(local_path)
        remote_dest = f"{self.remote_name}:{self.folder_name}/{upload_name}"

        await self.ensure_folder()
        log.info("[Rclone:%s] Uploading '%s' (%d bytes) -> %s", self.remote_name, local_path, file_size, remote_dest)

        # Build rclone copyto with real-time stats reporting every 3s
        cmd = self._cmd_prefix() + [
            "copyto",
            local_path,
            remote_dest,
            "--transfers", "8",
            "--checkers", "8",
            "--drive-chunk-size", "128M",
            "--buffer-size", "64M",
            "--stats", "3s",
            "--stats-one-line",
            "-v",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Parse rclone stats lines from stderr in real-time
        # rclone --stats-one-line format: "Transferred: 1.234 GiB / 2.100 GiB, 59%, 45.2 MiB/s, ETA 20s"
        import time as _time
        _last_cb = 0.0

        async def _read_progress() -> None:
            nonlocal _last_cb
            assert proc.stderr is not None
            buf = bytearray()
            while proc.returncode is None:
                try:
                    chunk = await asyncio.wait_for(proc.stderr.read(256), timeout=2.0)
                    if not chunk:
                        if proc.returncode is not None:
                            break
                        await asyncio.sleep(0.2)
                        continue
                    buf.extend(chunk)
                    while b"\n" in buf or b"\r" in buf:
                        idx_n = buf.find(b"\n")
                        idx_r = buf.find(b"\r")
                        if idx_n != -1 and idx_r != -1:
                            idx = min(idx_n, idx_r)
                        elif idx_n != -1:
                            idx = idx_n
                        else:
                            idx = idx_r
                        raw_line = bytes(buf[:idx])
                        del buf[:idx + 1]
                        line = raw_line.decode(errors="ignore").strip()
                        if not line:
                            continue
                        log.debug("[Rclone:%s] %s", self.remote_name, line)
                        if progress_callback is None:
                            continue

                        # Match: Transferred: 1.234 GiB / 2.100 GiB, 59%, 45.2 MiB/s, ETA 20s
                        m = re.search(
                            r"Transferred:\s+([\d.]+)\s*(\w+)\s*/\s*([\d.]+)\s*(\w+)(?:,\s*([\d.]+)%)?(?:,\s*([\d.]+\s*\w+/s))?(?:,\s*ETA\s*([^\s,]+))?",
                            line,
                        )
                        if not m:
                            continue
                        try:
                            unit_map = {
                                "B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3,
                                "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TIB": 1024**4,
                            }
                            done_val = float(m.group(1))
                            done_mult = unit_map.get(m.group(2).upper(), 1)
                            bytes_done = int(done_val * done_mult)
                            speed_str = m.group(6) or "--"
                            eta_str = m.group(7) or "--"

                            now = _time.monotonic()
                            if now - _last_cb >= 2.0:
                                _last_cb = now
                                try:
                                    await progress_callback(bytes_done, file_size, speed_str, eta_str)
                                except TypeError:
                                    await progress_callback(bytes_done, file_size)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    continue
                except Exception:
                    break

        # Wait for upload + progress reader concurrently
        try:
            await asyncio.gather(proc.wait(), _read_progress())
        except Exception as gather_err:
            log.warning("[Rclone:%s] Upload gather error: %s", self.remote_name, gather_err)

        if proc.returncode != 0:
            try:
                leftover = await proc.stderr.read() if proc.stderr else b""
            except Exception:
                leftover = b""
            err = leftover.decode(errors="ignore").strip() or f"rclone exited {proc.returncode}"
            self.is_active = False
            self.last_error = err
            raise RuntimeError(f"Rclone copyto failed: {err}")

        # ── Get shareable public link ──────────────────────────────────────────
        link_cmd = self._cmd_prefix() + ["link", remote_dest]
        link_proc = await asyncio.create_subprocess_exec(
            *link_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        link_out, _ = await link_proc.communicate()
        web_link = link_out.decode().strip()

        # Build proper streaming URLs from GDrive file ID
        # Share:    https://drive.google.com/file/d/<ID>/view?usp=sharing
        # Embed:    https://drive.google.com/file/d/<ID>/preview
        # Download: https://drive.google.com/uc?export=download&id=<ID>
        file_id = ""
        direct_stream_url = web_link
        direct_download_url = web_link

        m_id = re.search(r"/d/([a-zA-Z0-9_-]+)", web_link)
        if not m_id:
            m_id = re.search(r"id=([a-zA-Z0-9_-]+)", web_link)
        if m_id:
            file_id = m_id.group(1)
            direct_stream_url = f"https://drive.google.com/file/d/{file_id}/preview"
            direct_download_url = f"https://drive.google.com/uc?export=download&id={file_id}"

        log.info("[Rclone:%s] Upload complete: %s → %s", self.remote_name, upload_name, direct_stream_url)

        return {
            "file_id": file_id or upload_name,
            "filename": upload_name,
            "size": file_size,
            "web_url": web_link,
            "stream_url": direct_stream_url or web_link,
            "download_url": direct_download_url or web_link,
            "drive_id": self.drive_id,
            "provider": "gdrive",
        }

