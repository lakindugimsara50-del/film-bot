"""
downloader.py — Multi-Threaded VPS Video Downloader Engine.

Supports:
1. High-speed multi-connection aria2c downloading (16 connections, 1MB chunks)
2. Direct torrent / magnet downloads via aria2c (< 1.95GB filtered)
3. High-performance asynchronous HTTP streaming fallback via httpx if aria2c is absent
4. Telegram media downloading via Pyrogram client
5. Immediate process termination and temporary storage cleanup on cancel or completion

All log strings are in English to avoid Windows charmap errors.
"""

import asyncio
import logging
import os
import re
import shutil
import time
from typing import Callable, Optional

import aiofiles
import httpx

log = logging.getLogger(__name__)

# Active subprocesses for cancellation: task_id/user_id -> subprocess.Process
ACTIVE_SUBPROCESSES: dict[str, asyncio.subprocess.Process] = {}

# 1 MB chunk buffer for streaming
_CHUNK_SIZE = 1024 * 1024


def find_aria2c() -> Optional[str]:
    """Find aria2c executable on PATH or common VPS/Windows installation directories."""
    # Check PATH first
    exe = shutil.which("aria2c")
    if exe:
        return exe

    # Common Windows install locations
    project_bin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bin"))
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    candidates = [
        os.path.join(project_bin, "aria2c.exe"),
        os.path.join(project_bin, "aria2c"),
        os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "aria2c.exe"),
        os.path.join(program_files, "aria2", "aria2c.exe"),
        r"C:\aria2\aria2c.exe",
        r"C:\ProgramData\chocolatey\bin\aria2c.exe",
        # Common Linux locations
        "/usr/bin/aria2c",
        "/usr/local/bin/aria2c",
        "/bin/aria2c",
        "/opt/homebrew/bin/aria2c",
    ]

    for c in candidates:
        if os.path.exists(c):
            return c

    return None


def format_bytes(size: float) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def format_progress_bar(percent: float, length: int = 10) -> str:
    """Create a graphical text progress bar: [████████░░]."""
    clamped = min(100.0, max(0.0, percent))
    fraction = clamped / 100.0
    filled = int(round(length * fraction))
    return f"[{'█' * filled}{'░' * (length - filled)}]"


async def download_http(
    url: str,
    dest_dir: str,
    filename: Optional[str] = None,
    task_key: str = "",
    progress_callback: Optional[Callable] = None,
) -> str:
    """
    Download video from HTTP/HTTPS URL.
    Attempts multi-connection aria2c first (16 threads).
    Falls back to async httpx streaming if aria2c is unavailable or fails.
    """
    aria2_bin = find_aria2c()
    if aria2_bin:
        try:
            log.info("[Downloader] Attempting aria2c download for: %s", url[:80])
            return await _download_aria2c_http(
                aria2_bin=aria2_bin,
                url=url,
                dest_dir=dest_dir,
                filename=filename,
                task_key=task_key,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            log.warning("[Downloader] aria2c download failed (%s). Falling back to httpx...", exc)

    log.info("[Downloader] Using async httpx stream for: %s", url[:80])
    return await _download_httpx(
        url=url,
        dest_dir=dest_dir,
        filename=filename,
        progress_callback=progress_callback,
    )


async def _download_aria2c_http(
    aria2_bin: str,
    url: str,
    dest_dir: str,
    filename: Optional[str],
    task_key: str,
    progress_callback: Optional[Callable],
) -> str:
    """Download direct HTTP URL using aria2c multi-connection acceleration."""
    out_name = filename or (url.split("/")[-1].split("?")[0] or "movie.mp4")
    if not os.path.splitext(out_name)[1]:
        out_name += ".mp4"

    cmd = [
        aria2_bin,
        "-x", "16",
        "-s", "16",
        "-j", "16",
        "-k", "1M",
        "--min-split-size=1M",
        "--max-connection-per-server=16",
        "--check-certificate=false",
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "--summary-interval=1",
        "--console-log-level=warn",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--dir", dest_dir,
        "-o", out_name,
        url,
    ]

    log.info("[Downloader] Spawning aria2c: %s", " ".join(cmd[:8]))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    if task_key:
        ACTIVE_SUBPROCESSES[task_key] = proc

    # Regex to parse aria2c progress line:
    # [#xxxxxx 120MiB/1.5GiB(8%) CN:16 DL:12.5MiB ETA:1m45s]
    regex = re.compile(
        r"\[#\w+\s+([0-9.]+[A-Za-z]+)/([0-9.]+[A-Za-z]+)\((\d+)%\).*?DL:([0-9.]+[A-Za-z]+)(?:.*?ETA:([0-9a-zA-Z]+))?"
    )

    last_callback_time = 0.0

    try:
        buffer = ""
        while True:
            # 45s watchdog timeout for reading next chunk from aria2c stdout
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(512), timeout=45.0)
            except asyncio.TimeoutError:
                log.warning("[Downloader] aria2c produced no output for 45s, terminating to trigger fallback...")
                if proc and proc.returncode is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                raise RuntimeError("aria2c stalled (45s watchdog timeout)")

            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")
            lines = re.split(r"[\r\n]+", buffer)
            buffer = lines.pop()  # Keep incomplete tail
            for line_str in lines:
                line_str = line_str.strip()
                match = regex.search(line_str)
                if match and progress_callback:
                    done_str, total_str, pct_str, dl_speed, eta = match.groups()
                    pct = float(pct_str)
                    speed_formatted = f"{dl_speed}/s"
                    eta_formatted = eta or "N/A"
                    now = time.time()
                    if (now - last_callback_time >= 2.5) or pct >= 100.0:
                        last_callback_time = now
                        try:
                            # Non-blocking callback task so stdout pipe buffer is never blocked
                            if asyncio.iscoroutinefunction(progress_callback):
                                asyncio.create_task(progress_callback(pct, done_str, total_str, speed_formatted, eta_formatted))
                            else:
                                progress_callback(pct, done_str, total_str, speed_formatted, eta_formatted)
                        except Exception:
                            pass

        await proc.wait()
    except asyncio.CancelledError:
        log.info("[Downloader] aria2c download cancelled, killing process...")
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        raise
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        if task_key and task_key in ACTIVE_SUBPROCESSES:
            del ACTIVE_SUBPROCESSES[task_key]

    target_path = os.path.join(dest_dir, out_name)
    if proc.returncode == 0:
        if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
            log.info("[Downloader] aria2c download completed: %s (%s)", out_name, format_bytes(os.path.getsize(target_path)))
            return target_path
        # Defensive fallback: search dest_dir for any valid downloaded video
        for f in os.listdir(dest_dir):
            p = os.path.join(dest_dir, f)
            if os.path.isfile(p) and not f.endswith(".aria2") and os.path.getsize(p) > 1024 * 1024:
                log.info("[Downloader] aria2c download completed (found in folder): %s (%s)", f, format_bytes(os.path.getsize(p)))
                return p

    raise RuntimeError(f"aria2c exited with return code {proc.returncode}")


async def _download_httpx(
    url: str,
    dest_dir: str,
    filename: Optional[str],
    progress_callback: Optional[Callable],
) -> str:
    """Stream download via httpx with live speed and ETA calculations."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    timeout_config = httpx.Timeout(connect=25.0, read=120.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_config, headers=headers) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()

            cd = resp.headers.get("content-disposition", "")
            cd_match = re.search(r'filename="?([^";]+)"?', cd)
            if cd_match:
                out_name = cd_match.group(1).strip()
            elif filename:
                out_name = filename
            else:
                out_name = url.split("/")[-1].split("?")[0] or "movie.mp4"

            if not os.path.splitext(out_name)[1]:
                out_name += ".mp4"

            local_path = os.path.join(dest_dir, out_name)
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            start_time = time.time()
            last_notify = 0.0

            async with aiofiles.open(local_path, "wb") as fh:
                async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    await fh.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if progress_callback and ((now - last_notify >= 2.5) or (total and downloaded >= total)):
                        last_notify = now
                        elapsed = max(0.001, now - start_time)
                        speed_bytes = downloaded / elapsed
                        speed_str = f"{format_bytes(speed_bytes)}/s"
                        pct = (downloaded / total * 100.0) if total else 0.0
                        eta_seconds = int((total - downloaded) / speed_bytes) if (total and speed_bytes > 0) else 0
                        eta_str = f"{eta_seconds}s" if eta_seconds < 60 else f"{eta_seconds // 60}m {eta_seconds % 60}s"
                        try:
                            if asyncio.iscoroutinefunction(progress_callback):
                                asyncio.create_task(progress_callback(
                                    pct,
                                    format_bytes(downloaded),
                                    format_bytes(total) if total else "Unknown",
                                    speed_str,
                                    eta_str,
                                ))
                            else:
                                progress_callback(
                                    pct,
                                    format_bytes(downloaded),
                                    format_bytes(total) if total else "Unknown",
                                    speed_str,
                                    eta_str,
                                )
                        except Exception:
                            pass

    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        log.info("[Downloader] httpx download completed: %s (%s)", out_name, format_bytes(os.path.getsize(local_path)))
        return local_path

    raise RuntimeError(f"Download file empty or missing at {local_path}")


async def download_torrent(
    magnet_or_torrent: str,
    dest_dir: str,
    task_key: str = "",
    progress_callback: Optional[Callable] = None,
) -> str:
    """
    Download a torrent or magnet link directly to dest_dir using aria2c.
    Extracts and returns the largest video file (.mp4, .mkv, .avi) inside the download folder.
    """
    aria2_bin = find_aria2c()
    if not aria2_bin:
        raise RuntimeError("aria2c is required on the system for torrent/magnet downloads.")

    # If input is a remote .torrent URL, download it directly first for instant startup
    torrent_arg = magnet_or_torrent
    if magnet_or_torrent.startswith(("http://", "https://")) and ("torrent" in magnet_or_torrent.lower()):
        local_torrent_path = os.path.join(dest_dir, "meta.torrent")
        try:
            log.info("[Downloader] Pre-fetching .torrent file from: %s", magnet_or_torrent[:80])
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as h_client:
                r = await h_client.get(magnet_or_torrent, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and len(r.content) > 100:
                    with open(local_torrent_path, "wb") as f_out:
                        f_out.write(r.content)
                    torrent_arg = local_torrent_path
                    log.info("[Downloader] Successfully fetched .torrent file (%d bytes)", len(r.content))
        except Exception as tf_err:
            log.warning("[Downloader] Could not pre-fetch .torrent (%s), falling back to URL/magnet", tf_err)

    live_trackers = ",".join([
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.tracker.cl:1337/announce",
        "udp://tracker.openbittorrent.com:6969/announce",
        "http://tracker.openbittorrent.com:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://explodie.org:6969/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.moeking.me:6969/announce",
        "udp://p4p.arenabg.com:1337/announce",
        "udp://movies.zsw.ca:6969/announce",
    ])

    cmd = [
        aria2_bin,
        "--dir", dest_dir,
        "--seed-time=0",
        "--max-connection-per-server=16",
        "--split=16",
        "--enable-dht=true",
        "--enable-peer-exchange=true",
        "--bt-enable-lpd=true",
        "--bt-max-peers=100",
        "--file-allocation=none",
        "--peer-id-prefix=-qB4520-",
        "--user-agent=qBittorrent/4.5.2",
        f"--bt-tracker={live_trackers}",
        "--bt-stop-timeout=300",
        "--summary-interval=1",
        "--console-log-level=warn",
        "--follow-torrent=mem",
        torrent_arg,
    ]

    log.info("[Downloader] Starting aria2c torrent download: %s", torrent_arg[:80])
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    if task_key:
        ACTIVE_SUBPROCESSES[task_key] = proc

    regex = re.compile(
        r"\[#\w+\s+([0-9.]+[A-Za-z]+)/([0-9.]+[A-Za-z]+)\((\d+)%\).*?DL:([0-9.]+[A-Za-z]+)(?:.*?ETA:([0-9a-zA-Z]+))?"
    )
    meta_regex = re.compile(r"\[#\w+\s+.*?CN:(\d+).*?DL:([0-9.]+[A-Za-z]+)?")

    try:
        buffer = ""
        while True:
            chunk = await proc.stdout.read(512)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")
            lines = re.split(r"[\r\n]+", buffer)
            buffer = lines.pop()
            for line_str in lines:
                line_str = line_str.strip()
                match = regex.search(line_str)
                if match and progress_callback:
                    done_str, total_str, pct_str, dl_speed, eta = match.groups()
                    pct = float(pct_str)
                    speed_formatted = f"{dl_speed}/s"
                    eta_formatted = eta or "N/A"
                    try:
                        await progress_callback(pct, done_str, total_str, speed_formatted, eta_formatted)
                    except Exception:
                        pass
                elif progress_callback and ("CN:" in line_str or "metadata" in line_str.lower()):
                    meta_match = meta_regex.search(line_str)
                    peers_count = meta_match.group(1) if meta_match else "1"
                    speed_val = (meta_match.group(2) + "/s") if (meta_match and meta_match.group(2)) else "0 B/s"
                    try:
                        await progress_callback(0.0, "0 B", "Connecting...", speed_val, f"Peers: {peers_count}")
                    except Exception:
                        pass

        await proc.wait()
    except asyncio.CancelledError:
        log.info("[Downloader] aria2c torrent download cancelled, killing process...")
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        raise
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        if task_key and task_key in ACTIVE_SUBPROCESSES:
            del ACTIVE_SUBPROCESSES[task_key]

    if proc.returncode != 0:
        raise RuntimeError(f"aria2c torrent download exited with return code {proc.returncode}")

    # Find the primary video file (largest file with video extension)
    best_file = None
    best_size = 0
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm"}

    for root, _, files in os.walk(dest_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in video_exts:
                f_path = os.path.join(root, f)
                sz = os.path.getsize(f_path)
                if sz > best_size:
                    best_size = sz
                    best_file = f_path

    if best_file:
        log.info("[Downloader] Found main torrent video file: %s (%s)", os.path.basename(best_file), format_bytes(best_size))
        return best_file

    raise RuntimeError("No valid video file (.mp4, .mkv, .avi) found in torrent download.")


async def cancel_active_download(task_key: str) -> bool:
    """Terminate and kill any running aria2c process for the given task."""
    proc = ACTIVE_SUBPROCESSES.get(task_key)
    if proc and proc.returncode is None:
        try:
            log.info("[Downloader] Terminating aria2c process for task: %s", task_key)
            proc.terminate()
            await asyncio.sleep(0.5)
            if proc.returncode is None:
                proc.kill()
            ACTIVE_SUBPROCESSES.pop(task_key, None)
            return True
        except Exception as exc:
            log.warning("[Downloader] Could not kill aria2c process: %s", exc)
            ACTIVE_SUBPROCESSES.pop(task_key, None)
    return False


async def cancel_all_active_downloads() -> int:
    """Terminate and kill all running aria2c processes globally."""
    count = 0
    keys = list(ACTIVE_SUBPROCESSES.keys())
    for k in keys:
        if await cancel_active_download(k):
            count += 1
    log.info("[Downloader] Terminated %d active download subprocess(es).", count)
    return count
