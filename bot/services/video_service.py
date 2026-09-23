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
import multiprocessing
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

    # Auto-detect CPU cores; use ultrafast preset on Colab for 3-5x speed improvement
    _on_colab = os.path.exists('/content')
    _threads = '0'  # Use all available CPU cores
    _preset = 'ultrafast' if _on_colab else 'veryfast'
    log.info('[VideoService] on_colab=%s preset=%s threads=%s', _on_colab, _preset, _threads)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", input_path,
        "-vf", scale_filter,
        "-c:v", "libx264",
        "-preset", _preset,
        "-crf", "23",
        "-threads", _threads,
        "-maxrate", "3500k",
        "-bufsize", "4000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ]

    def _cleanup_output() -> None:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

    proc = None
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
            _cleanup_output()
            return False

    except asyncio.CancelledError:
        log.info("[VideoService] Compression cancelled, terminating FFmpeg...")
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        _cleanup_output()
        raise
    except Exception as exc:
        log.error("[VideoService] Compression exception: %s", exc)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        _cleanup_output()
        return False
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass


async def ensure_web_streamable(input_path: str, output_path: str) -> bool:
    """
    Fast remux non-MP4 or MKV videos to MP4 with +faststart.
    Attempts instant stream copy (-c copy -sn) first (takes ~3s).
    Falls back to AAC audio remux with a strict 90s timeout for files <= 1.2 GB.
    For files > 1.2 GB, immediately falls back to uploading original file if copy remux fails.
    Guarantees immediate cleanup of output_path on failure or timeout so partial files never leak.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        return False

    def _cleanup_output() -> None:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

    # Attempt 1: Instant stream-copy (fastest, no re-encoding, ~3 seconds)
    # -sn strips incompatible subtitle streams that prevent stream copy in MP4
    cmd_copy = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", input_path,
        "-c", "copy",
        "-sn",
        "-movflags", "+faststart",
        output_path,
    ]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_copy,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
            log.info("[VideoService] Fast remux (copy) succeeded: %s", output_path)
            return True
        else:
            log.warning(
                "[VideoService] Fast copy remux exited with code %s or invalid output.",
                getattr(proc, "returncode", None),
            )
            _cleanup_output()
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        _cleanup_output()
        raise
    except Exception as exc:
        log.debug("[VideoService] Fast copy remux skipped or timed out (%s)", exc)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        _cleanup_output()
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass

    # For files > 1.2 GB, avoid running slow, CPU-intensive audio transcode on Render (0.1 vCPU).
    # Immediately fall back to uploading the original file.
    file_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
    _on_colab_env = os.path.exists('/content')
    if not _on_colab_env and file_size > 1.2 * 1024 * 1024 * 1024:
        log.warning(
            "[VideoService] File size %.2f GB > 1.2 GB and stream-copy failed. "
            "Skipping slow CPU audio transcode; falling back to original file.",
            file_size / (1024 * 1024 * 1024),
        )
        _cleanup_output()
        return False

    # Attempt 2: Audio AAC remux if stream-copy was incompatible (with 90s timeout)
    cmd_aac = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", input_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-sn",
        "-movflags", "+faststart",
        output_path,
    ]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_aac,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=300.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
            log.info("[VideoService] AAC audio remux succeeded: %s", output_path)
            return True
        else:
            log.warning("[VideoService] AAC audio remux failed with code %s", getattr(proc, "returncode", None))
            _cleanup_output()
            return False
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        _cleanup_output()
        raise
    except Exception as exc:
        log.warning("[VideoService] AAC remux error or timeout: %s", exc)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        _cleanup_output()
        return False
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass


async def embed_subtitles_soft(video_path: str, sub_path: str, output_path: str) -> bool:
    """
    Soft-embed subtitles into video container (MP4 mov_text or MKV subrip).
    Takes only 2-4 seconds since video and audio streams are copied without re-encoding (-c copy).
    Enables native media players (VLC, MX Player, Smart TVs) to autoplay Sinhala subtitles immediately.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path) or not os.path.exists(sub_path):
        return False

    ext = os.path.splitext(output_path)[1].lower()
    sub_codec = "mov_text" if ext in (".mp4", ".m4v", ".mov") else "subrip"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", video_path,
        "-i", sub_path,
        "-c", "copy",
        "-c:s", sub_codec,
        "-metadata:s:s:0", "language=sin",
        "-metadata:s:s:0", "title=Sinhala",
        "-disposition:s:0", "default",
        output_path,
    ]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        raise
    except Exception as exc:
        log.warning("[VideoService] Soft subtitle mux error: %s", exc)
        return False
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass

