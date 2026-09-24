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
    sub_path: Optional[str] = None,
) -> bool:
    """
    Compress video to high-efficiency 1080p MP4 in 12GB RAM (/dev/shm).
    Keeps 1080p resolution while reducing file size to ~1.1GB - 1.4GB.
    When sub_path is provided, burns Sinhala subtitles into the video frames AND
    embeds dual default+forced mov_text tracks in the same single FFmpeg pass.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        log.error("[VideoService] FFmpeg binary not found. Cannot compress.")
        return False

    duration = get_video_duration(input_path, ffmpeg_bin)
    log.info("[VideoService] Compressing '%s' (duration=%.1fs) -> '%s'", input_path, duration, output_path)

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    has_sub = bool(sub_path and os.path.exists(sub_path) and os.path.getsize(sub_path) > 16)
    local_burn_srt = os.path.join(out_dir, "sub_burn_1080.srt")
    if has_sub:
        try:
            shutil.copyfile(sub_path, local_burn_srt)
        except Exception:
            has_sub = False

    # Filter: scale to 1080p max height if larger, plus burn-in Sinhala subtitles if available
    scale_base = "scale=-2:'min(1080,ih)'"
    scale_filter = f"subtitles=sub_burn_1080.srt,{scale_base}" if has_sub else scale_base

    # Auto-detect CPU cores; use ultrafast preset on Colab for 3-5x speed improvement
    _on_colab = os.path.exists('/content') or os.path.isdir('/dev/shm')
    _threads = '0'  # Use all available CPU cores
    _preset = 'ultrafast' if _on_colab else 'veryfast'
    log.info('[VideoService] on_colab=%s preset=%s threads=%s has_sub=%s', _on_colab, _preset, _threads, has_sub)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", os.path.abspath(input_path),
    ]
    if has_sub:
        cmd.extend([
            "-i", os.path.abspath(sub_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-map", "1:0",
            "-map", "1:0",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=eng",
            "-metadata:s:s:0", "title=Sinhala (සිංහල) [Auto]",
            "-disposition:s:0", "default+forced",
            "-metadata:s:s:1", "language=sin",
            "-metadata:s:s:1", "title=සිංහල උපසිරැසි (Sinhala)",
            "-disposition:s:1", "default+forced",
        ])
    cmd.extend([
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
        os.path.abspath(output_path),
    ])

    def _cleanup_output() -> None:
        for p in (output_path, local_burn_srt):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=out_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        last_pct = 0.0

        async def _read_stderr():
            nonlocal last_pct
            while True:
                if hasattr(proc.stderr, "read"):
                    chunk = await proc.stderr.read(4096)
                else:
                    chunk = await proc.stderr.readline()
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
                matches = time_pattern.findall(decoded)
                if matches and duration > 0:
                    h, mm, ss = matches[-1]
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
        if os.path.exists(local_burn_srt):
            try:
                os.remove(local_burn_srt)
            except Exception:
                pass

        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            orig_sz = max(1, os.path.getsize(input_path)) / (1024 * 1024)
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


def get_optimal_work_dir(min_free_gb: float = 2.0, prefix: str = "leech_ram_") -> str:
    """
    Allocate a working directory in the 12GB RAM disk (/dev/shm) on Google Colab / Linux
    when sufficient free RAM space is available, eliminating disk I/O bottlenecks
    during downloading, MKV-to-MP4 remuxing, subtitle muxing, and quality conversion.
    Falls back to /content or system temp directory if /dev/shm is unavailable or low on space.
    """
    import tempfile

    ram_disk = "/dev/shm"
    min_bytes = int(min_free_gb * 1024 * 1024 * 1024)
    if os.path.isdir(ram_disk) and os.access(ram_disk, os.W_OK):
        try:
            free_ram = shutil.disk_usage(ram_disk).free
            if free_ram >= min_bytes:
                work_dir = tempfile.mkdtemp(prefix=prefix, dir=ram_disk)
                log.info(
                    "[VideoService] Allocated 12GB RAM Disk workspace: %s (%.2f GB free in /dev/shm)",
                    work_dir,
                    free_ram / (1024 ** 3),
                )
                return work_dir
        except Exception as exc:
            log.debug("[VideoService] /dev/shm check skipped: %s", exc)

    if os.path.isdir("/content") and os.access("/content", os.W_OK):
        try:
            return tempfile.mkdtemp(prefix=prefix, dir="/content")
        except Exception:
            pass

    return tempfile.mkdtemp(prefix=prefix)


async def extract_embedded_subtitle(video_path: str, output_srt_path: str) -> bool:
    """
    Extract the primary text subtitle track (SRT/ASS/WebVTT) from an MKV/MP4 video container
    before remuxing, ensuring internal subtitles are never lost.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path):
        return False

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-i", video_path,
        "-map", "0:s:0",
        "-c:s", "srt",
        output_srt_path,
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=25.0)
        if proc.returncode == 0 and os.path.exists(output_srt_path) and os.path.getsize(output_srt_path) > 64:
            log.info("[VideoService] Extracted embedded subtitle from container: %s", output_srt_path)
            return True
    except Exception as exc:
        log.debug("[VideoService] No extractable text subtitle in container (%s)", exc)
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
    return False


async def ensure_web_streamable(
    input_path: str,
    output_path: str,
    sub_path: Optional[str] = None,
) -> bool:
    """
    Ultra-fast 12GB RAM-optimized MKV/AVI/WebM -> MP4 converter with +faststart
    and optional single-pass Sinhala subtitle merging (mov_text default+forced).
    Ensures stereo AAC audio so browsers (Chrome, Safari, Mobile) never play silent video.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(input_path):
        return False

    def _cleanup_output() -> None:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

    has_sub = bool(sub_path and os.path.exists(sub_path) and os.path.getsize(sub_path) > 16)
    _threads = "0"  # Use all CPU threads & RAM buffer
    input_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
    min_valid_size = min(512 * 1024, max(1024, int(input_size * 0.25)))

    # Inspect input audio codec quickly so we don't copy AC3/EAC3/DTS into MP4 (which causes silent playback in browsers)
    needs_aac_transcode = False
    try:
        probe = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-i", input_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        audio_matches = re.findall(r"Stream #\d+:\d+.*?: Audio:\s*([a-zA-Z0-9_]+)", probe.stderr)
        if audio_matches:
            primary_audio = audio_matches[0].lower()
            if primary_audio not in ("aac", "mp3"):
                needs_aac_transcode = True
                log.info("[VideoService] Audio codec '%s' detected — will transcode to Stereo AAC for 100%% browser compatibility.", primary_audio)
    except Exception:
        pass

    # Attempt 1: Instant stream-copy (only if audio is already browser-compatible AAC/MP3)
    if not needs_aac_transcode:
        cmd_copy = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-threads", _threads,
            "-i", input_path,
        ]
        if has_sub:
            cmd_copy.extend([
                "-i", sub_path,
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-map", "1:0",
                "-map", "1:0",
                "-c:v", "copy",
                "-c:a", "copy",
                "-c:s", "mov_text",
                "-metadata:s:s:0", "language=eng",
                "-metadata:s:s:0", "title=Sinhala (සිංහල) [Auto]",
                "-disposition:s:0", "default+forced",
                "-metadata:s:s:1", "language=sin",
                "-metadata:s:s:1", "title=සිංහල උපසිරැසි (Sinhala)",
                "-disposition:s:1", "default+forced",
            ])
        else:
            cmd_copy.extend([
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-c:v", "copy",
                "-c:a", "copy",
                "-sn",
            ])
        cmd_copy.extend([
            "-max_muxing_queue_size", "9999",
            "-movflags", "+faststart",
            output_path,
        ])

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_copy,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=180.0)
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) >= min_valid_size:
                log.info("[VideoService] Fast RAM remux (copy + sub=%s) succeeded: %s", has_sub, output_path)
                return True
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
            log.debug("[VideoService] Fast copy remux fallback (%s)", exc)
            _cleanup_output()
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    proc.kill()
                except Exception:
                    pass

    # For files > 1.2 GB on low-CPU non-Colab environments without /dev/shm, skip CPU audio transcode
    file_size = input_size
    _has_ram_disk = os.path.isdir("/dev/shm") or os.path.exists("/content")
    if not _has_ram_disk and file_size > 1.2 * 1024 * 1024 * 1024:
        log.warning(
            "[VideoService] File size %.2f GB > 1.2 GB on constrained host. Skipping audio transcode.",
            file_size / (1024 ** 3),
        )
        _cleanup_output()
        return False

    # Attempt 2: Video stream-copy + Multi-threaded Stereo AAC 160k audio + Dual Sinhala subtitle + faststart
    cmd_aac = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", _threads,
        "-i", input_path,
    ]
    if has_sub:
        cmd_aac.extend([
            "-i", sub_path,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-map", "1:0",
            "-map", "1:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-ac", "2",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=eng",
            "-metadata:s:s:0", "title=Sinhala (සිංහල) [Auto]",
            "-disposition:s:0", "default+forced",
            "-metadata:s:s:1", "language=sin",
            "-metadata:s:s:1", "title=සිංහල උපසිරැසි (Sinhala)",
            "-disposition:s:1", "default+forced",
        ])
    else:
        cmd_aac.extend([
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-ac", "2",
            "-sn",
        ])
    cmd_aac.extend([
        "-max_muxing_queue_size", "9999",
        "-movflags", "+faststart",
        output_path,
    ])

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_aac,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=360.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) >= min_valid_size:
            log.info("[VideoService] AAC audio + MP4 remux (sub=%s) succeeded: %s", has_sub, output_path)
            return True
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
    Soft-embed Sinhala subtitles into video container (MP4 mov_text or MKV subrip)
    with dual eng+sin default+forced tracks and +faststart so ALL web browsers and
    native media players (English-locale Android/iOS, VLC, MX Player, Smart TVs)
    automatically display Sinhala subtitles immediately upon playback.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path) or not os.path.exists(sub_path):
        return False

    ext = os.path.splitext(output_path)[1].lower()
    is_mp4 = ext in (".mp4", ".m4v", ".mov")
    sub_codec = "mov_text" if is_mp4 else "subrip"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
        "-i", video_path,
        "-i", sub_path,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-map", "1:0",
        "-map", "1:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", sub_codec,
        "-metadata:s:s:0", "language=eng",
        "-metadata:s:s:0", "title=Sinhala (සිංහල) [Auto]",
        "-disposition:s:0", "default+forced",
        "-metadata:s:s:1", "language=sin",
        "-metadata:s:s:1", "title=සිංහල උපසිරැසි (Sinhala)",
        "-disposition:s:1", "default+forced",
    ]
    if is_mp4:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(output_path)

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180.0)
        return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        raise
    except asyncio.TimeoutError:
        log.warning("[VideoService] Soft subtitle mux timed out (180s) — skipping soft embed.")
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        return False
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


async def generate_multi_quality_variants_ram(
    input_path: str,
    output_dir: str,
    slug: str = "movie",
    sub_path: Optional[str] = None,
    qualities: tuple[str, ...] = ("720p", "480p", "360p"),
    progress_callback: Optional[Callable[[float, str], None]] = None,
    base_stem: Optional[str] = None,
    target_qualities: Optional[tuple[str, ...]] = None,
) -> dict[str, str]:
    """
    Leverage 12GB RAM (/dev/shm) and all CPU cores (-threads 0 -preset ultrafast)
    to generate multi-quality MP4 streams/downloads (720p, 480p, 360p) in a single
    FFmpeg multi-output pass with BOTH burned-in Sinhala subtitles AND dual default+forced
    mov_text tracks plus +faststart.
    Returns a dict mapping quality label (e.g. '720p') -> generated local file path.
    """
    if base_stem:
        slug = base_stem
    if target_qualities:
        qualities = target_qualities
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(input_path):
        return {}

    abs_out_dir = os.path.abspath(output_dir)
    os.makedirs(abs_out_dir, exist_ok=True)
    duration = get_video_duration(input_path, ffmpeg_bin)
    input_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
    min_valid_size = min(256 * 1024, max(1024, int(input_size * 0.05)))

    has_sub = bool(sub_path and os.path.exists(sub_path) and os.path.getsize(sub_path) > 16)
    local_burn_srt = os.path.join(abs_out_dir, "sub_burn_multi.srt")
    if has_sub:
        try:
            shutil.copyfile(sub_path, local_burn_srt)
        except Exception:
            has_sub = False

    # Resolution & bitrate profile per quality tier
    profiles = {
        "720p": {"height": 720, "crf": "25", "maxrate": "1800k", "bufsize": "2400k", "abitrate": "128k"},
        "480p": {"height": 480, "crf": "27", "maxrate": "950k",  "bufsize": "1400k", "abitrate": "96k"},
        "360p": {"height": 360, "crf": "28", "maxrate": "550k",  "bufsize": "900k",  "abitrate": "64k"},
    }

    target_q_list = [q for q in qualities if q in profiles]
    if not target_q_list:
        return {}

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
        "-i", os.path.abspath(input_path),
    ]
    if has_sub:
        cmd.extend(["-i", os.path.abspath(sub_path)])

    out_paths: dict[str, str] = {}
    for q in target_q_list:
        prof = profiles[q]
        out_file = os.path.join(abs_out_dir, f"{slug}-{q}.mp4")
        out_paths[q] = out_file
        scale_base = f"scale=-2:'min({prof['height']},ih)'"
        scale_f = f"subtitles=sub_burn_multi.srt,{scale_base}" if has_sub else scale_base
        cmd.extend([
            "-map", "0:v:0",
            "-map", "0:a:0?",
        ])
        if has_sub:
            cmd.extend([
                "-map", "1:0",
                "-map", "1:0",
                "-c:s", "mov_text",
                "-metadata:s:s:0", "language=eng",
                "-metadata:s:s:0", "title=Sinhala (සිංහල) [Auto]",
                "-disposition:s:0", "default+forced",
                "-metadata:s:s:1", "language=sin",
                "-metadata:s:s:1", "title=සිංහල උපසිරැසි (Sinhala)",
                "-disposition:s:1", "default+forced",
            ])
        cmd.extend([
            "-vf", scale_f,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", prof["crf"],
            "-maxrate", prof["maxrate"],
            "-bufsize", prof["bufsize"],
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", prof["abitrate"],
            "-ac", "2",
            "-movflags", "+faststart",
            out_file,
        ])

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=abs_out_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        last_pct = 0.0

        async def _read_stderr():
            nonlocal last_pct
            while True:
                if hasattr(proc.stderr, "read"):
                    chunk = await proc.stderr.read(4096)
                else:
                    chunk = await proc.stderr.readline()
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
                matches = time_pattern.findall(decoded)
                if matches and duration > 0:
                    h, mm, ss = matches[-1]
                    cur_secs = int(h) * 3600 + int(mm) * 60 + float(ss)
                    pct = min(99.0, (cur_secs / duration) * 100.0)
                    if pct - last_pct >= 3.0:
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
        if os.path.exists(local_burn_srt):
            try:
                os.remove(local_burn_srt)
            except Exception:
                pass
        valid_outputs = {
            q: p for q, p in out_paths.items()
            if os.path.exists(p) and os.path.getsize(p) >= min_valid_size
        }
        log.info("[VideoService] Multi-quality RAM generation complete: %s", list(valid_outputs.keys()))
        return valid_outputs
    except Exception as exc:
        log.warning("[VideoService] Multi-quality generation skipped/failed: %s", exc)
        return {}
    finally:
        if os.path.exists(local_burn_srt):
            try:
                os.remove(local_burn_srt)
            except Exception:
                pass
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass


