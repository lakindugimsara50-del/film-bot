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
import urllib.parse
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


def parse_size_str(s: str) -> float:
    """Parse size string like '2.3GiB', '991.4MiB', '500KiB' to bytes."""
    m = re.match(r"^([0-9.]+)\s*([A-Za-z]+)$", s.strip())
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if "G" in unit:
        return val * 1024 * 1024 * 1024
    if "M" in unit:
        return val * 1024 * 1024
    if "K" in unit:
        return val * 1024
    return val


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
        "--split=16",
        "-j", "16",
        "-k", "1M",
        "--min-split-size=1M",
        "--max-connection-per-server=16",
        "--optimize-concurrent-downloads=true",
        "--file-allocation=none",
        "--disk-cache=128M" if (os.path.exists("/content") or os.path.isdir("/dev/shm")) else "--disk-cache=64M",
        "--continue=true",
        "--conditional-get=true",
        "--timeout=30",
        "--connect-timeout=15",
        "--max-tries=5",
        "--retry-wait=2",
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
    """
    High-speed HTTP download via httpx with multi-connection parallel chunk acceleration (8-16 workers)
    and graceful single-stream fallback.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    timeout_config = httpx.Timeout(connect=25.0, read=120.0, write=30.0, pool=30.0)

    # 1. Probe headers to detect filename and file size
    out_name = filename or ""
    total_size = 0
    accept_ranges = False

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_config, headers=headers) as client:
        try:
            head_resp = await client.head(url)
            if head_resp.status_code == 200:
                total_size = int(head_resp.headers.get("content-length", 0))
                accept_ranges = "bytes" in head_resp.headers.get("accept-ranges", "").lower()
                cd = head_resp.headers.get("content-disposition", "")
                cd_m = re.search(r'filename="?([^";]+)"?', cd)
                if cd_m and not out_name:
                    out_name = cd_m.group(1).strip()
        except Exception:
            pass

    if not out_name:
        out_name = url.split("/")[-1].split("?")[0] or "movie.mp4"
    if not os.path.splitext(out_name)[1]:
        out_name += ".mp4"

    local_path = os.path.join(dest_dir, out_name)

    # 2. If file size >= 20MB and server supports ranges, use 8-16 parallel range workers!
    if total_size >= 20 * 1024 * 1024 and accept_ranges:
        try:
            num_workers = 16 if (os.path.exists("/content") or os.path.isdir("/dev/shm")) else 8
            part_size = total_size // num_workers
            downloaded = [0] * num_workers
            start_time = time.time()
            last_notify = 0.0

            with open(local_path, "wb") as f_init:
                f_init.truncate(total_size)

            async def _worker(worker_idx: int, start_byte: int, end_byte: int):
                nonlocal last_notify
                w_headers = dict(headers)
                w_headers["Range"] = f"bytes={start_byte}-{end_byte}"
                async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_config, headers=w_headers) as w_client:
                    async with w_client.stream("GET", url) as w_resp:
                        if w_resp.status_code != 206:
                            raise RuntimeError(f"Range request returned status {w_resp.status_code} instead of 206")
                        cur_pos = start_byte
                        async with aiofiles.open(local_path, "r+b") as fh:
                            async for chunk in w_resp.aiter_bytes(_CHUNK_SIZE):
                                await fh.seek(cur_pos)
                                await fh.write(chunk)
                                cur_pos += len(chunk)
                                downloaded[worker_idx] += len(chunk)

                                now = time.time()
                                if progress_callback and (now - last_notify >= 2.5):
                                    last_notify = now
                                    tot_dl = sum(downloaded)
                                    elapsed = max(0.001, now - start_time)
                                    speed_bytes = tot_dl / elapsed
                                    speed_str = f"{format_bytes(speed_bytes)}/s"
                                    pct = min(100.0, (tot_dl / total_size * 100.0))
                                    eta_seconds = int((total_size - tot_dl) / speed_bytes) if speed_bytes > 0 else 0
                                    eta_str = f"{eta_seconds}s" if eta_seconds < 60 else f"{eta_seconds // 60}m {eta_seconds % 60}s"
                                    try:
                                        if asyncio.iscoroutinefunction(progress_callback):
                                            asyncio.create_task(progress_callback(
                                                pct, format_bytes(tot_dl), format_bytes(total_size), speed_str, eta_str
                                            ))
                                        else:
                                            progress_callback(pct, format_bytes(tot_dl), format_bytes(total_size), speed_str, eta_str)
                                    except Exception:
                                        pass

            tasks = []
            for i in range(num_workers):
                sb = i * part_size
                eb = (sb + part_size - 1) if (i < num_workers - 1) else (total_size - 1)
                tasks.append(_worker(i, sb, eb))

            log.info("[Downloader] Starting %d-worker parallel chunk HTTP download for %s (%s)",
                     num_workers, out_name, format_bytes(total_size))
            await asyncio.gather(*tasks)

            if os.path.exists(local_path) and os.path.getsize(local_path) >= total_size:
                log.info("[Downloader] Parallel HTTP download completed: %s (%s)", out_name, format_bytes(total_size))
                return local_path
        except Exception as p_err:
            log.warning("[Downloader] Parallel chunk download fallback to single stream: %s", p_err)
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass

    # 3. Fallback: single-stream GET
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_config, headers=headers) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()

            cd = resp.headers.get("content-disposition", "")
            cd_match = re.search(r'filename="?([^";]+)"?', cd)
            if cd_match and not out_name:
                out_name = cd_match.group(1).strip()

            local_path = os.path.join(dest_dir, out_name)
            total = int(resp.headers.get("content-length", 0)) or total_size
            downloaded_cnt = 0
            start_time = time.time()
            last_notify = 0.0

            async with aiofiles.open(local_path, "wb") as fh:
                async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    await fh.write(chunk)
                    downloaded_cnt += len(chunk)

                    now = time.time()
                    if progress_callback and ((now - last_notify >= 2.5) or (total and downloaded_cnt >= total)):
                        last_notify = now
                        elapsed = max(0.001, now - start_time)
                        speed_bytes = downloaded_cnt / elapsed
                        speed_str = f"{format_bytes(speed_bytes)}/s"
                        pct = (downloaded_cnt / total * 100.0) if total else 0.0
                        eta_seconds = int((total - downloaded_cnt) / speed_bytes) if (total and speed_bytes > 0) else 0
                        eta_str = f"{eta_seconds}s" if eta_seconds < 60 else f"{eta_seconds // 60}m {eta_seconds % 60}s"
                        try:
                            if asyncio.iscoroutinefunction(progress_callback):
                                asyncio.create_task(progress_callback(
                                    pct,
                                    format_bytes(downloaded_cnt),
                                    format_bytes(total) if total else "Unknown",
                                    speed_str,
                                    eta_str,
                                ))
                            else:
                                progress_callback(
                                    pct,
                                    format_bytes(downloaded_cnt),
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


def matches_episode_filename(filename: str, hint: str) -> bool:
    """Check if filename matches the episode hint (supports S01E02, 1x02, ranges, etc.)."""
    if not hint:
        return True
    fn_lower = filename.lower()
    hint_lower = hint.lower().strip()
    if hint_lower in fn_lower:
        return True

    # Try parsing SxxExx or xxXxx (e.g. S01E02, 1x02)
    m = re.search(r"s?(\d+)[ex](\d+)", hint_lower)
    if m:
        s_num = int(m.group(1))
        e_num = int(m.group(2))
        patterns = [
            rf"\bs0?{s_num}e0?{e_num}\b",
            rf"\bs0?{s_num}\s*ep?0?{e_num}\b",
            rf"\b0?{s_num}x0?{e_num}\b",
            rf"season\s*0?{s_num}.*?episode\s*0?{e_num}\b",
            rf"s0?{s_num}[\s._-]+e0?{e_num}\b",
        ]
        if any(re.search(p, fn_lower) for p in patterns):
            return True

        # Check multi-episode ranges: S01E01-E10, S01E01-02, 1x01-1x05, S01E01E02
        p1 = rf"\bs0?{s_num}\s*e(?:p)?(\d+)\s*(?:[-~]|[-~]?e(?:p)?)\s*0?(\d+)\b"
        m1 = re.search(p1, fn_lower)
        if m1:
            ep1, ep2 = int(m1.group(1)), int(m1.group(2))
            if ep1 > ep2:
                ep1, ep2 = ep2, ep1
            if ep1 <= e_num <= ep2:
                return True

        p2 = rf"\b0?{s_num}x0?(\d+)\s*(?:[-~]|[-~]?\d{{1,2}}x|[-~]?x)\s*0?(\d+)\b"
        m2 = re.search(p2, fn_lower)
        if m2:
            ep1, ep2 = int(m2.group(1)), int(m2.group(2))
            if ep1 > ep2:
                ep1, ep2 = ep2, ep1
            if ep1 <= e_num <= ep2:
                return True

        return False  # Hint explicitly specified season, so do not fall through to episode-only

    # Try episode only: e.g. "E02" or "ep02"
    m_ep = re.search(r"(?:e|ep|episode)\s*0?(\d+)", hint_lower)
    if m_ep:
        e_num = int(m_ep.group(1))
        patterns = [
            rf"(?:\b|s\d{{1,2}})e(?:p)?0?{e_num}\b",
            rf"episode\s*0?{e_num}\b",
            rf"\b\d{{1,2}}x0?{e_num}\b",
        ]
        if any(re.search(p, fn_lower) for p in patterns):
            return True

        p_ep_range = r"\be(?:p)?(\d+)\s*(?:[-~]|[-~]?e(?:p)?)\s*0?(\d+)\b"
        m_r = re.search(p_ep_range, fn_lower)
        if m_r:
            ep1, ep2 = int(m_r.group(1)), int(m_r.group(2))
            if ep1 > ep2:
                ep1, ep2 = ep2, ep1
            if ep1 <= e_num <= ep2:
                return True

    return False


async def find_episode_file_index_from_torrent(
    aria2_bin: str,
    torrent_file: str,
    episode_hint: str,
) -> Optional[int]:
    """Inspect .torrent file with aria2c --show-files to select the target episode index."""
    try:
        proc = await asyncio.create_subprocess_exec(
            aria2_bin,
            "--show-files=true",
            torrent_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        output = stdout.decode("utf-8", errors="replace")
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm"}

        for line in output.splitlines():
            # aria2c --show-files output: idx|path/to/file|length|completed%|selected
            parts = line.split("|")
            if len(parts) >= 2 and parts[0].strip().isdigit():
                idx = int(parts[0].strip())
                file_path = parts[1].strip()
                ext = os.path.splitext(file_path)[1].lower()
                base_name = os.path.basename(file_path.replace("\\", "/"))
                if ext in video_exts and (matches_episode_filename(base_name, episode_hint) or matches_episode_filename(file_path, episode_hint)):
                    log.info("[Downloader] Found target episode file in torrent: index %d -> %s", idx, file_path)
                    return idx
    except Exception as exc:
        log.warning("[Downloader] Error running aria2c --show-files on %s: %s", torrent_file, exc)
    return None


async def download_torrent(
    magnet_or_torrent: str,
    dest_dir: str,
    task_key: str = "",
    progress_callback: Optional[Callable] = None,
    episode_hint: Optional[str] = None,
    select_file_idx: Optional[int] = None,
) -> str:
    """
    Download a torrent or magnet link directly to dest_dir using aria2c.
    Extracts and returns the target episode or largest video file (.mp4, .mkv, .avi).
    """
    aria2_bin = find_aria2c()
    if not aria2_bin:
        raise RuntimeError("aria2c is required on the system for torrent/magnet downloads.")

    # Prevent container crash by checking available disk space
    try:
        free_space = shutil.disk_usage(dest_dir).free
        if free_space < 500 * 1024 * 1024:
            raise RuntimeError(f"Low server disk space ({format_bytes(free_space)} free). Aborting download to prevent crash.")
    except Exception as d_err:
        log.warning("[Downloader] Disk check warning: %s", d_err)

    # If input is a remote .torrent URL, download it directly first for instant startup
    torrent_arg = magnet_or_torrent
    if magnet_or_torrent.startswith(("http://", "https://")) and ("torrent" in magnet_or_torrent.lower()):
        local_torrent_path = os.path.join(dest_dir, "meta.torrent")
        try:
            log.info("[Downloader] Pre-fetching .torrent file from: %s", magnet_or_torrent[:80])
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as h_client:
                r = await h_client.get(magnet_or_torrent, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and len(r.content) > 100 and b"announce" in r.content[:2048]:
                    with open(local_torrent_path, "wb") as f_out:
                        f_out.write(r.content)
                    torrent_arg = local_torrent_path
                    log.info("[Downloader] Successfully fetched .torrent file (%d bytes)", len(r.content))
        except Exception as tf_err:
            log.warning("[Downloader] Could not pre-fetch .torrent (%s), falling back to URL/magnet", tf_err)

    tracker_list = [
        "udp://tracker.opentrackr.org:1337/announce",
        "http://tracker.opentrackr.org:1337/announce",
        "udp://open.tracker.cl:1337/announce",
        "udp://tracker.openbittorrent.com:6969/announce",
        "http://tracker.openbittorrent.com:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://explodie.org:6969/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.moeking.me:6969/announce",
        "udp://p4p.arenabg.com:1337/announce",
        "udp://movies.zsw.ca:6969/announce",
        "https://tracker.tamersunion.org:443/announce",
        "https://tracker.gbitt.info:443/announce",
    ]
    if magnet_or_torrent.startswith("magnet:"):
        for tr_m in re.findall(r"[?&]tr=([^&]+)", magnet_or_torrent):
            dec_tr = urllib.parse.unquote(tr_m).strip()
            if dec_tr and dec_tr not in tracker_list:
                tracker_list.append(dec_tr)
    live_trackers = ",".join(tracker_list)

    # Check if episode_hint or select_file_idx is provided and inspect file list via aria2c --show-files
    target_file_idx = select_file_idx
    if episode_hint or select_file_idx:
        if os.path.isfile(torrent_arg):
            detected_idx = await find_episode_file_index_from_torrent(aria2_bin, torrent_arg, episode_hint) if episode_hint else None
            target_file_idx = detected_idx or select_file_idx
        elif torrent_arg.startswith("magnet:"):
            # Fetch metadata (.torrent) quickly (up to 18s) with DHT, PEX & HTTPS tracker support to inspect file list
            meta_cmd = [
                aria2_bin,
                "--dir", dest_dir,
                "--check-certificate=false",
                "--bt-metadata-only=true",
                "--bt-save-metadata=true",
                "--enable-dht=true",
                "--enable-peer-exchange=true",
                "--bt-enable-lpd=true",
                "--bt-stop-timeout=18",
                "--console-log-level=warn",
                f"--bt-tracker={live_trackers}",
                torrent_arg,
            ]
            try:
                meta_proc = await asyncio.create_subprocess_exec(
                    *meta_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(meta_proc.communicate(), timeout=18.0)
                for f in os.listdir(dest_dir):
                    if f.endswith(".torrent"):
                        saved_torrent = os.path.join(dest_dir, f)
                        detected_idx = await find_episode_file_index_from_torrent(aria2_bin, saved_torrent, episode_hint) if episode_hint else None
                        target_file_idx = detected_idx or select_file_idx
                        if target_file_idx:
                            torrent_arg = saved_torrent
                            break
            except Exception as meta_err:
                log.debug("[Downloader] Magnet metadata fetch skipped/timed out: %s", meta_err)

    _high_ram_env = os.path.exists("/content") or os.path.isdir("/dev/shm")
    _disk_cache = "128M" if _high_ram_env else "16M"
    _safe_limit_gb = 4.5 if _high_ram_env else 1.85

    cmd = [
        aria2_bin,
        "--dir", dest_dir,
        "--check-certificate=false",
        "--seed-time=0",
        "-x", "16",
        "-s", "16",
        "-j", "16",
        "--min-split-size=1M",
        "--max-connection-per-server=16",
        "--split=16",
        "--enable-dht=true",
        "--enable-peer-exchange=true",
        "--bt-enable-lpd=true",
        "--bt-max-peers=120",
        "--bt-request-peer-speed-limit=10M",
        "--file-allocation=none",
        f"--disk-cache={_disk_cache}",
        "--peer-id-prefix=-qB4520-",
        "--user-agent=qBittorrent/4.5.2",
        f"--bt-tracker={live_trackers}",
        "--bt-stop-timeout=300",
        "--optimize-concurrent-downloads=true",
        "--summary-interval=1",
        "--console-log-level=warn",
        "--follow-torrent=mem",
    ]

    if target_file_idx:
        cmd.extend([
            f"--select-file={target_file_idx}",
            "--bt-remove-unselected-file=true",
        ])

    cmd.append(torrent_arg)

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

    start_wait_time = time.time()
    has_started_download = False

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
                if match:
                    done_str, total_str, pct_str, dl_speed, eta = match.groups()
                    pct = float(pct_str)
                    speed_formatted = f"{dl_speed}/s"
                    eta_formatted = eta or "N/A"

                    # Hard protection for container disk: abort if torrent total size exceeds safe limit
                    total_bytes = parse_size_str(total_str)
                    if not target_file_idx and total_bytes > int(_safe_limit_gb * 1024 * 1024 * 1024):
                        log.warning(
                            "[Downloader] Torrent total size %s (%s) exceeds safe limit (%.2f GB). Aborting to prevent crash.",
                            total_str, format_bytes(total_bytes), _safe_limit_gb
                        )
                        if proc and proc.returncode is None:
                            try:
                                proc.terminate()
                                proc.kill()
                            except Exception:
                                pass
                        raise RuntimeError(f"Torrent size ({total_str}) exceeds safe limit ({_safe_limit_gb} GB)")

                    if pct > 0 or parse_size_str(done_str) > 0 or parse_size_str(dl_speed) > 0:
                        has_started_download = True
                    if progress_callback:
                        try:
                            await progress_callback(pct, done_str, total_str, speed_formatted, eta_formatted)
                        except Exception:
                            pass
                elif "CN:" in line_str or "metadata" in line_str.lower():
                    meta_match = meta_regex.search(line_str)
                    peers_count = meta_match.group(1) if meta_match else "1"
                    speed_val = (meta_match.group(2) + "/s") if (meta_match and meta_match.group(2)) else "0 B/s"
                    if meta_match and meta_match.group(2) and parse_size_str(meta_match.group(2)) > 0:
                        has_started_download = True
                    if progress_callback:
                        try:
                            await progress_callback(0.0, "0 B", "Connecting...", speed_val, f"Peers: {peers_count}")
                        except Exception:
                            pass

            # Fail fast if torrent has 0 seeds and cannot start downloading within 40 seconds
            if not has_started_download and (time.time() - start_wait_time > 40.0):
                log.warning("[Downloader] Torrent stuck with 0 seeds for 40s. Terminating to try next candidate...")
                if proc and proc.returncode is None:
                    try:
                        proc.terminate()
                        proc.kill()
                    except Exception:
                        pass
                raise RuntimeError("Torrent stalled connecting to peers (40s seeder timeout)")

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

    # Find the primary video file (target episode or largest video file)
    best_file = None
    best_size = 0
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm"}

    if episode_hint:
        for root, _, files in os.walk(dest_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in video_exts and matches_episode_filename(f, episode_hint):
                    best_file = os.path.join(root, f)
                    log.info("[Downloader] Found matching episode file '%s' -> %s", episode_hint, f)
                    break
            if best_file:
                break

    if not best_file:
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
        log.info("[Downloader] Found main torrent video file: %s (%s)", os.path.basename(best_file), format_bytes(os.path.getsize(best_file)))
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
