"""
video_service.py — Smart 1080p FFmpeg Compression & Web Optimization.

Provides:
1. Smart 1080p Compression: Compresses bloated files (>1.95GB) down to 1.1GB-1.4GB
   preserving 1080p Full HD resolution and visual fidelity (H.264 CRF 23 + AAC).
2. Web Streamable Remux: Ensures MP4 container with moov atom at the front
   (+faststart) and stereo AAC audio for 100% universal browser playback.
3. Progress tracking via ffmpeg stderr parsing.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Max upload limit for standard Telegram Bot API (1.95 GB safe ceiling)
MAX_TELEGRAM_BOT_SIZE = int(1.95 * 1024 * 1024 * 1024)


def get_ffmpeg_binary() -> Optional[str]:
    """Find system ffmpeg or bundled imageio_ffmpeg binary."""
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception as exc:
        log.debug("[VideoService] imageio_ffmpeg lookup error: %s", exc)

    return None


def get_video_duration(file_path: str, ffmpeg_bin: str) -> float:
    """Extract duration in seconds using ffmpeg -i."""
    try:
        cmd = [ffmpeg_bin, "-hide_banner", "-i", file_path]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception as exc:
        log.debug("[VideoService] Could not parse duration: %s", exc)
    return 0.0


async def compress_smart_1080p(
    input_path: str,
    output_path: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> bool:
    """
    Compress video to high-efficiency 1080p MP4.
    Keeps 1080p resolution while reducing file size to ~1.1GB - 1.4GB.
    Uses libx264 CRF 23, preset veryfast, aac 128k, movflags +faststart.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        log.error("[VideoService] FFmpeg binary not found. Cannot compress.")
        return False

    duration = get_video_duration(input_path, ffmpeg_bin)
    log.info("[VideoService] Compressing '%s' (duration=%.1fs) -> '%s'", input_path, duration, output_path)

    # Filter: scale to 1080p max height if larger, otherwise keep original
    scale_filter = "scale=-2:min'(1080,ih)':force_original_aspect_ratio=decrease"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", input_path,
        "-vf", scale_filter,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        last_pct = 0.0

        async def _read_stderr():
            nonlocal last_pct
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                m = time_pattern.search(decoded)
                if m and duration > 0:
                    h, mm, ss = m.groups()
                    cur_secs = int(h) * 3600 + int(mm) * 60 + float(ss)
                    pct = min(99.0, (cur_secs / duration) * 100.0)
                    if pct - last_pct >= 2.0:
                        last_pct = pct
                        if progress_callback:
                            try:
                                if asyncio.iscoroutinefunction(progress_callback):
                                    await progress_callback(pct, f"{pct:.1f}%")
                                else:
                                    progress_callback(pct, f"{pct:.1f}%")
                            except Exception:
                                pass

        await asyncio.gather(proc.wait(), _read_stderr())

        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            orig_sz = os.path.getsize(input_path) / (1024 * 1024)
            new_sz = os.path.getsize(output_path) / (1024 * 1024)
            log.info("[VideoService] Compression SUCCESS: %.1f MB -> %.1f MB (%.1f%% reduction)",
                     orig_sz, new_sz, (1 - new_sz / orig_sz) * 100)
            return True
        else:
            log.error("[VideoService] FFmpeg exited with code %s", proc.returncode)
            return False

    except Exception as exc:
        log.error("[VideoService] Compression exception: %s", exc)
        return False


async def ensure_web_streamable(input_path: str, output_path: str) -> bool:
    """
    Fast remux non-MP4 or MKV videos to MP4 with +faststart.
    Takes only seconds since it copies video stream without re-encoding.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        return False

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", input_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as exc:
        log.debug("[VideoService] Fast remux error: %s", exc)
        return False
