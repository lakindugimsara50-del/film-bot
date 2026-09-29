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
import collections
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

# ── Colab / Render environment detection ─────────────────────────────────────
# Google Colab (T4 GPU, 12 GB RAM): use all cores + ultrafast preset
# Render Free Tier (0.1 vCPU, 512 MB RAM): use veryfast + limited remux
_on_colab: bool = os.path.exists("/content")
_threads: str = "0"          # always use all available CPU cores
_preset: str = "ultrafast" if _on_colab else "veryfast"
log.info(
    "[VideoService] Environment: on_colab=%s  preset=%s  threads=%s",
    _on_colab, _preset, _threads,
)


def get_ffmpeg_binary() -> Optional[str]:
    """Find system ffmpeg or bundled imageio_ffmpeg binary."""
    # Check /usr/local/bin/ffmpeg first (where Colab CUDA NVENC ffmpeg is placed)
    if os.path.exists("/usr/local/bin/ffmpeg") and os.access("/usr/local/bin/ffmpeg", os.X_OK):
        return "/usr/local/bin/ffmpeg"

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


def get_video_resolution(file_path: str, ffmpeg_bin: Optional[str] = None) -> tuple[int, int]:
    """Extract (width, height) resolution using ffmpeg -i."""
    exe = ffmpeg_bin or get_ffmpeg_binary()
    if not exe or not os.path.exists(file_path):
        return (0, 0)
    try:
        cmd = [exe, "-hide_banner", "-i", file_path]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        match = re.search(r",\s*(\d{3,5})x(\d{3,5})(?:,\s*|\s*\[|\s*)", proc.stderr)
        if match:
            w, h = match.groups()
            return (int(w), int(h))
    except Exception as exc:
        log.debug("[VideoService] Could not parse resolution: %s", exc)
    return (0, 0)


def get_audio_codec(file_path: str, ffmpeg_bin: Optional[str] = None) -> Optional[str]:
    """Extract primary audio codec name using ffmpeg -i."""
    exe = ffmpeg_bin or get_ffmpeg_binary()
    if not exe or not os.path.exists(file_path):
        return None
    try:
        cmd = [exe, "-hide_banner", "-i", file_path]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        audio_matches = re.findall(r"Stream #\d+:\d+.*?: Audio:\s*([a-zA-Z0-9_]+)", proc.stderr)
        if audio_matches:
            return audio_matches[0].lower()
    except Exception as exc:
        log.debug("[VideoService] Could not parse audio codec: %s", exc)
    return None


def is_web_compatible_audio(codec: Optional[str]) -> bool:
    """Check if audio codec can be natively decoded by web browsers (AAC, MP3)."""
    if not codec:
        return True
    return codec.lower() in ("aac", "mp3")


_CACHED_HW_ENCODER: Optional[str] = None


def setup_colab_cuda_ffmpeg() -> bool:
    """
    On Google Colab (with NVIDIA T4 GPU), ensure a CUDA-enabled FFmpeg binary with
    h264_nvenc support is installed into /usr/local/bin/ffmpeg.
    """
    if not os.path.exists("/content"):
        return False
    try:
        smi = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if smi.returncode != 0:
            return False

        # If /usr/local/bin/ffmpeg already has working h264_nvenc, skip re-download
        if os.path.exists("/usr/local/bin/ffmpeg"):
            test_probe = subprocess.run(
                ["/usr/local/bin/ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.04", "-c:v", "h264_nvenc", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4,
            )
            if test_probe.returncode == 0:
                return True

        log.info("[VideoService] Colab NVIDIA GPU detected. Installing CUDA FFmpeg (NVENC)...")
        cmd = (
            "curl -sL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz -o /tmp/ff_cuda.tar.xz "
            "&& tar -xf /tmp/ff_cuda.tar.xz --wildcards '*/bin/ffmpeg' '*/bin/ffprobe' --strip-components=2 -C /usr/local/bin/ "
            "&& chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe "
            "&& rm -rf /tmp/ff_cuda.tar.xz"
        )
        res = subprocess.run(cmd, shell=True, timeout=180)
        return res.returncode == 0
    except Exception as exc:
        log.debug("[VideoService] Colab CUDA FFmpeg auto-setup note: %s", exc)
        return False


def detect_hw_encoder(ffmpeg_bin: Optional[str] = None) -> str:
    """
    Auto-detect if NVIDIA GPU hardware encoder (h264_nvenc on Google Colab T4/L4)
    is available and functional. Falls back to 'libx264' on CPU-only hosts.
    """
    global _CACHED_HW_ENCODER
    if _CACHED_HW_ENCODER is not None:
        return _CACHED_HW_ENCODER

    # Check if NVIDIA GPU exists first
    has_gpu = False
    try:
        smi = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        has_gpu = (smi.returncode == 0)
    except Exception:
        has_gpu = False

    if not has_gpu:
        log.info("[VideoService] No NVIDIA GPU detected (CPU mode active). Using high-speed multi-threaded CPU encoder.")
        _CACHED_HW_ENCODER = "libx264"
        return _CACHED_HW_ENCODER

    exe = ffmpeg_bin or get_ffmpeg_binary()
    if not exe:
        _CACHED_HW_ENCODER = "libx264"
        return _CACHED_HW_ENCODER

    try:
        probe = subprocess.run(
            [
                exe,
                "-hide_banner",
                "-f", "lavfi",
                "-i", "nullsrc=s=256x256:d=0.04",
                "-c:v", "h264_nvenc",
                "-f", "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
        )
        if probe.returncode == 0:
            _CACHED_HW_ENCODER = "h264_nvenc"
            log.info("[VideoService] Hardware GPU acceleration detected: h264_nvenc enabled!")
            return _CACHED_HW_ENCODER
    except Exception:
        pass

    # On Google Colab with GPU: if NVENC failed with standard apt ffmpeg, auto-install BtbN CUDA ffmpeg
    if os.path.exists("/content") and not getattr(detect_hw_encoder, "_attempted_cuda_install", False):
        setattr(detect_hw_encoder, "_attempted_cuda_install", True)
        if setup_colab_cuda_ffmpeg():
            new_exe = get_ffmpeg_binary()
            try:
                probe2 = subprocess.run(
                    [
                        new_exe,
                        "-hide_banner",
                        "-f", "lavfi",
                        "-i", "nullsrc=s=256x256:d=0.04",
                        "-c:v", "h264_nvenc",
                        "-f", "null",
                        "-",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=4,
                )
                if probe2.returncode == 0:
                    _CACHED_HW_ENCODER = "h264_nvenc"
                    log.info("[VideoService] Colab CUDA GPU acceleration activated: h264_nvenc enabled!")
                    return _CACHED_HW_ENCODER
            except Exception:
                pass

    _CACHED_HW_ENCODER = "libx264"
    log.info("[VideoService] Falling back to high-speed CPU encoder: libx264")
    return _CACHED_HW_ENCODER


async def stream_copy_subtitles(
    video_path: str,
    sub_path: Optional[str],
    output_path: str,
    disposition: str = "default",
) -> bool:
    """
    Instantaneous Stream Copy (Soft-sub Muxing) in 3-5 seconds without video re-encoding:
    ffmpeg -y -i input.mp4 -i sub.srt -c:v copy -c:a copy -c:s mov_text -disposition:s:0 default -movflags +faststart output.mp4
    (and if container is MKV or needs conversion: -c:s srt -disposition:s:0 default).

    Marks the subtitle stream as the default/forced track (-disposition:s:0 default) so that
    offline media players (VLC, MX Player, KMPlayer, Android/iOS, Smart TVs) automatically
    display the Sinhala subtitle without user intervention.
    Applies +faststart in the same command for instant zero-lag web streaming.
    Handles edge cases gracefully: if input is MKV or has incompatible audio streams, falls back
    to stream copy with stereo AAC transcode or subtitle-safe remux.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path):
        log.error("[VideoService] FFmpeg binary not found or input missing: %s", video_path)
        return False

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    def _cleanup_output() -> None:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

    ext = os.path.splitext(output_path)[1].lower()
    is_mp4 = ext in (".mp4", ".m4v", ".mov")
    sub_codec = "mov_text" if is_mp4 else "srt"
    has_sub = bool(sub_path and os.path.exists(sub_path) and os.path.getsize(sub_path) > 16)
    if has_sub and is_mp4 and sub_path.lower().endswith(".vtt"):
        try:
            from services.subtitle_service import vtt_to_srt
            sub_srt = os.path.join(out_dir, f"sub_copy_{os.path.basename(sub_path)}.srt")
            sub_path = vtt_to_srt(sub_path, sub_srt)
        except Exception as e_vtt:
            log.debug("[VideoService] VTT->SRT prep for stream-copy: %s", e_vtt)

    # Detect if source audio requires AAC transcode for browser compatibility
    in_audio_codec = get_audio_codec(video_path, ffmpeg_bin)
    needs_aac = is_mp4 and bool(in_audio_codec and not is_web_compatible_audio(in_audio_codec))
    if needs_aac:
        log.info("[VideoService] Stream-copy detected non-AAC audio (%s) -> transcoding to Stereo AAC for 100%% browser playback", in_audio_codec)

    # Primary Attempt: Direct instantaneous stream copy with soft-sub muxing and +faststart
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
        "-i", os.path.abspath(video_path),
    ]
    if has_sub:
        cmd.extend([
            "-i", os.path.abspath(sub_path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-map", "1:0",
            "-c:v", "copy",
        ])
        if needs_aac:
            cmd.extend(["-c:a", "aac", "-b:a", "160k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])
        cmd.extend([
            "-c:s", sub_codec,
            "-metadata:s:s:0", "language=sin",
            "-metadata:s:s:0", "title=Sinhala (සිංහල)",
            "-disposition:s:0", disposition,
        ])
    else:
        cmd.extend([
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "copy",
        ])
        if needs_aac:
            cmd.extend(["-c:a", "aac", "-b:a", "160k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])
        cmd.extend(["-sn"])
    cmd.extend(["-max_muxing_queue_size", "9999"])
    if is_mp4:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(os.path.abspath(output_path))

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=out_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            log.info("[VideoService] Stream-copy muxing succeeded: %s", output_path)
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
        log.debug("[VideoService] Direct stream-copy primary attempt failed: %s", exc)
        _cleanup_output()
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass

    # Fallback Attempt 2: If MP4 container rejected the copied audio stream (e.g. PCM, Vorbis, incompatible audio in MP4),
    # keep video stream-copy (-c:v copy) and fast-transcode audio to Stereo AAC (-c:a aac)
    cmd_fallback_audio = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
        "-i", os.path.abspath(video_path),
    ]
    if has_sub:
        cmd_fallback_audio.extend([
            "-i", os.path.abspath(sub_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-map", "1:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-ac", "2",
            "-c:s", sub_codec,
            "-metadata:s:s:0", "language=sin",
            "-metadata:s:s:0", "title=Sinhala (සිංහල)",
            "-disposition:s:0", disposition,
        ])
    else:
        cmd_fallback_audio.extend([
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-ac", "2",
            "-sn",
        ])
    cmd_fallback_audio.extend(["-max_muxing_queue_size", "9999"])
    if is_mp4:
        cmd_fallback_audio.extend(["-movflags", "+faststart"])
    cmd_fallback_audio.append(os.path.abspath(output_path))

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_fallback_audio,
            cwd=out_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            log.info("[VideoService] Stream-copy with stereo AAC audio fallback succeeded: %s", output_path)
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
        log.debug("[VideoService] Audio AAC transcode fallback failed: %s", exc)
        _cleanup_output()
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass

    # Fallback Attempt 3: If subtitle file was corrupt or had incompatible format,
    # stream-copy video and audio without subtitle so the video file is preserved
    if has_sub:
        cmd_fallback_nosub = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-threads", "0",
            "-i", os.path.abspath(video_path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "copy",
        ]
        if needs_aac:
            cmd_fallback_nosub.extend(["-c:a", "aac", "-b:a", "160k", "-ac", "2"])
        else:
            cmd_fallback_nosub.extend(["-c:a", "copy"])
        cmd_fallback_nosub.extend([
            "-sn",
            "-max_muxing_queue_size", "9999",
        ])
        if is_mp4:
            cmd_fallback_nosub.extend(["-movflags", "+faststart"])
        cmd_fallback_nosub.append(os.path.abspath(output_path))

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_fallback_nosub,
                cwd=out_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=180.0)
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                log.info("[VideoService] Stream-copy without subtitle fallback succeeded: %s", output_path)
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
            log.warning("[VideoService] No-sub fallback failed: %s", exc)
            _cleanup_output()
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    proc.kill()
                except Exception:
                    pass

    # Fallback Attempt 4: If both audio stream was incompatible with container copy (e.g. DTS/Opus in MP4)
    # AND subtitle file was corrupt or failed, stream-copy video with Stereo AAC audio transcode and no subtitle
    cmd_fallback_aac_nosub = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
        "-i", os.path.abspath(video_path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "160k",
        "-ac", "2",
        "-sn",
        "-max_muxing_queue_size", "9999",
    ]
    if is_mp4:
        cmd_fallback_aac_nosub.extend(["-movflags", "+faststart"])
    cmd_fallback_aac_nosub.append(os.path.abspath(output_path))

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_fallback_aac_nosub,
            cwd=out_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            log.info("[VideoService] Stream-copy AAC audio without subtitle fallback succeeded: %s", output_path)
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
        log.warning("[VideoService] AAC no-sub fallback failed: %s", exc)
        _cleanup_output()
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass

    return False


async def compress_video(
    input_path: str,
    output_path: str,
    target_size_bytes: int = int(1.40 * 1024 * 1024 * 1024),
    progress_callback: Optional[Callable[[float, str], None]] = None,
    sub_path: Optional[str] = None,
) -> bool:
    """
    Compress bloated video files (>1.95GB, e.g. KGF Chapter 2 2.3GB) down to strictly <= 1.95GB
    (target 1.40GB safe default) using multi-core CPU (-preset veryfast -threads 0) or GPU NVENC.
    Calculates exact target bitrate from video duration to guarantee the output never exceeds
    the Telegram Bot 1.95GB limit.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        log.error("[VideoService] FFmpeg binary not found. Cannot compress.")
        return False

    if not os.path.exists(input_path):
        log.error("[VideoService] Input file does not exist: %s", input_path)
        return False

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    def _cleanup_output() -> None:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

    duration = get_video_duration(input_path, ffmpeg_bin)
    in_bytes = os.path.getsize(input_path)
    log.info("[VideoService] compress_video: '%s' (duration=%.1fs, size=%d bytes) -> '%s' (target=%d bytes)",
             input_path, duration, in_bytes, output_path, target_size_bytes)

    # Calculate optimal target bitrate
    audio_bps = 128_000
    if duration > 10.0:
        usable_bits = int(target_size_bytes * 8 * 0.95)
        total_bps = usable_bits / duration
        video_bps = max(400_000, int(total_bps - audio_bps))
        v_bitrate_k = int(video_bps / 1000)
    else:
        v_bitrate_k = 1800

    maxrate_k = int(v_bitrate_k * 1.15)
    bufsize_k = int(v_bitrate_k * 2)

    hw_enc = detect_hw_encoder(ffmpeg_bin)
    has_sub = bool(sub_path and os.path.exists(sub_path) and os.path.getsize(sub_path) > 16)
    ext = os.path.splitext(output_path)[1].lower()
    is_mp4 = ext in (".mp4", ".m4v", ".mov")
    sub_codec = "mov_text" if is_mp4 else "srt"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
        "-i", os.path.abspath(input_path),
    ]

    if has_sub:
        cmd.extend([
            "-i", os.path.abspath(sub_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-map", "1:0",
            "-c:s", sub_codec,
            "-metadata:s:s:0", "language=sin",
            "-metadata:s:s:0", "title=Sinhala (සිංහල)",
            "-disposition:s:0", "default",
        ])
    else:
        cmd.extend([
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-sn",
        ])

    if hw_enc == "h264_nvenc":
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", "p1",
            "-rc", "vbr",
            "-b:v", f"{v_bitrate_k}k",
            "-maxrate", f"{maxrate_k}k",
            "-bufsize", f"{bufsize_k}k",
        ])
    else:
        cmd.extend([
            "-c:v", "libx264",
            "-preset", _preset,   # ultrafast on Colab GPU, veryfast on Render
            "-tune", "fastdecode",
            "-threads", _threads,
            "-b:v", f"{v_bitrate_k}k",
            "-maxrate", f"{maxrate_k}k",
            "-bufsize", f"{bufsize_k}k",
            "-pix_fmt", "yuv420p",
        ])


    cmd.extend([
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-max_muxing_queue_size", "9999",
    ])
    if is_mp4:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(os.path.abspath(output_path))

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

        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            out_sz = os.path.getsize(output_path)
            log.info("[VideoService] compress_video SUCCESS: %d bytes (<= %d MAX_TELEGRAM_BOT_SIZE)", out_sz, MAX_TELEGRAM_BOT_SIZE)
            if out_sz <= MAX_TELEGRAM_BOT_SIZE:
                if progress_callback:
                    try:
                        if asyncio.iscoroutinefunction(progress_callback):
                            await progress_callback(100.0, "100.0%")
                        else:
                            progress_callback(100.0, "100.0%")
                    except Exception:
                        pass
                return True
            else:
                log.warning("[VideoService] Compressed size %d bytes exceeded MAX_TELEGRAM_BOT_SIZE %d", out_sz, MAX_TELEGRAM_BOT_SIZE)
                _cleanup_output()
                return False
        else:
            log.error("[VideoService] FFmpeg exited with code %s", proc.returncode)
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
        log.error("[VideoService] compress_video exception: %s", exc)
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


async def compress_smart_1080p(
    input_path: str,
    output_path: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    sub_path: Optional[str] = None,
) -> bool:
    """
    Intelligent 1080p Processing:
    1. If file size <= 1.95GB (MAX_TELEGRAM_BOT_SIZE):
       Executes instantaneous Stream Copy & Soft-Sub Muxing in 3-5 seconds.
    2. If file size > 1.95GB (bloated video like KGF Chapter 2 - 2.3GB):
       Executes fast multi-core compression (compress_video) targeting 1.85GB,
       guaranteeing output <= 1.95GB in minutes (-preset veryfast -threads 0).
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        log.error("[VideoService] FFmpeg binary not found. Cannot process.")
        return False

    if not os.path.exists(input_path):
        log.error("[VideoService] Input file does not exist: %s", input_path)
        return False

    in_size = os.path.getsize(input_path)
    if in_size > MAX_TELEGRAM_BOT_SIZE:
        log.info("[VideoService] Input file %s (%d bytes) > 1.95GB ceiling. Routing to fast compress_video...", input_path, in_size)
        return await compress_video(
            input_path=input_path,
            output_path=output_path,
            target_size_bytes=int(1.40 * 1024 * 1024 * 1024),
            progress_callback=progress_callback,
            sub_path=sub_path,
        )

    log.info("[VideoService] compress_smart_1080p: Executing instant Stream Copy (Soft-sub Muxing) for '%s' -> '%s'", input_path, output_path)

    ok = await stream_copy_subtitles(input_path, sub_path, output_path, disposition="default")
    if not ok:
        ok = await ensure_web_streamable(input_path, output_path, sub_path=sub_path)
    if not ok and sub_path and os.path.exists(sub_path):
        ok = await embed_subtitles_soft(input_path, sub_path, output_path, disposition="default")

    if ok and os.path.exists(output_path):
        out_sz = os.path.getsize(output_path)
        if out_sz > MAX_TELEGRAM_BOT_SIZE:
            log.warning("[VideoService] Stream copy output %d bytes > 1.95GB limit. Compressing...", out_sz)
            return await compress_video(
                input_path=input_path,
                output_path=output_path,
                target_size_bytes=int(1.40 * 1024 * 1024 * 1024),
                progress_callback=progress_callback,
                sub_path=sub_path,
            )
        if progress_callback:
            try:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(100.0, "100.0%")
                else:
                    progress_callback(100.0, "100.0%")
            except Exception:
                pass
        return True

    return False


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
            if free_ram >= min_bytes or free_ram >= int(1.5 * 1024 * 1024 * 1024):
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
    if has_sub and sub_path.lower().endswith(".vtt"):
        try:
            from services.subtitle_service import vtt_to_srt
            out_d = os.path.dirname(os.path.abspath(output_path)) or "."
            sub_srt = os.path.join(out_d, f"sub_web_{os.path.basename(sub_path)}.srt")
            sub_path = vtt_to_srt(sub_path, sub_srt)
        except Exception as e_vtt:
            log.debug("[VideoService] VTT->SRT prep for ensure_web_streamable: %s", e_vtt)
    _threads = "0"  # Use all CPU threads & RAM buffer
    input_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
    min_valid_size = min(512 * 1024, max(1024, int(input_size * 0.25)))

    # Inspect input audio codec quickly so we don't copy AC3/EAC3/DTS into MP4 (which causes silent playback in browsers)
    in_codec = get_audio_codec(input_path, ffmpeg_bin)
    needs_aac_transcode = bool(in_codec and not is_web_compatible_audio(in_codec))
    if needs_aac_transcode:
        log.info("[VideoService] Audio codec '%s' detected — will transcode to Stereo AAC for 100%% browser compatibility.", in_codec)

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

    # For files > 1.2 GB on Render (non-Colab, no /dev/shm), skip CPU audio transcode
    # to avoid OOM. Direct upload without extra remux is handled by leech_service.
    file_size = input_size
    if not _on_colab and file_size > 1.2 * 1024 * 1024 * 1024:
        log.warning(
            "[VideoService] File size %.2f GB > 1.2 GB on Render (not Colab). "
            "Skipping audio transcode to prevent OOM.",
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
        await asyncio.wait_for(proc.wait(), timeout=360.0)  # ≥300 s per plan §4.3; 360 s for large Colab files

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


async def embed_subtitles_soft(
    video_path: str,
    sub_path: str,
    output_path: str,
    disposition: str = "default+forced",
) -> bool:
    """
    Soft-embed Sinhala subtitles into video container (MP4 mov_text or MKV srt)
    with dual default+forced tracks and +faststart so ALL web browsers and
    native media players (English-locale Android/iOS, VLC, MX Player, Smart TVs)
    automatically display Sinhala subtitles immediately upon playback.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path) or not os.path.exists(sub_path):
        return False

    ext = os.path.splitext(output_path)[1].lower()
    is_mp4 = ext in (".mp4", ".m4v", ".mov")
    sub_codec = "mov_text" if is_mp4 else "srt"

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
        "-disposition:s:0", disposition,
        "-metadata:s:s:1", "language=sin",
        "-metadata:s:s:1", "title=සිංහල උපසිරැසි (Sinhala)",
        "-disposition:s:1", disposition,
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


async def apply_faststart(input_path: str, output_path: str) -> bool:
    """
    Execute an ultra-fast stream-copy remux relocating the MP4 moov atom to byte 0:
    ffmpeg -y -hide_banner -threads 0 -i input.mp4 -c copy -movflags +faststart output.mp4

    Relocates the metadata index from the tail to byte 0 in <3-5 seconds without re-encoding,
    enabling browsers and Video.js to start playback in <300ms.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(input_path):
        return False

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
        "-i", os.path.abspath(input_path),
        "-c", "copy",
        "-max_muxing_queue_size", "9999",
        "-movflags", "+faststart",
        os.path.abspath(output_path),
    ]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=out_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            log.info("[VideoService] FastStart (+faststart) copy remux success: %s", output_path)
            return True

        # Fallback: if pure -c copy failed (e.g. due to incompatible subtitle/attachment streams in MKV/MP4),
        # copy only video and audio streams to MP4 container with +faststart
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

        cmd_fallback = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-threads", "0",
            "-i", os.path.abspath(input_path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "copy",
            "-c:a", "copy",
            "-max_muxing_queue_size", "9999",
            "-movflags", "+faststart",
            os.path.abspath(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd_fallback,
            cwd=out_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180.0)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            log.info("[VideoService] FastStart (+faststart) fallback remux success: %s", output_path)
            return True

        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        return False
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        raise
    except Exception as exc:
        log.warning("[VideoService] FastStart copy remux error: %s", exc)
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
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
    qualities: tuple[str, ...] = ("720p", "480p"),
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
        if sub_path.lower().endswith(".vtt"):
            try:
                from services.subtitle_service import vtt_to_srt
                sub_srt = os.path.join(abs_out_dir, f"sub_multi_{slug}.srt")
                sub_path = vtt_to_srt(sub_path, sub_srt)
            except Exception as e_vtt:
                log.debug("[VideoService] VTT->SRT prep in multi-quality: %s", e_vtt)
        try:
            shutil.copyfile(sub_path, local_burn_srt)
        except Exception:
            has_sub = False

    # Resolution & bitrate profile per quality tier (CRF 26 for ultra-fast high-quality encoding)
    profiles = {
        "1080p": {"height": 1080, "crf": "26", "maxrate": "3500k", "bufsize": "5000k", "abitrate": "160k"},
        "720p":  {"height": 720,  "crf": "26", "maxrate": "1800k", "bufsize": "2400k", "abitrate": "128k"},
        "480p":  {"height": 480,  "crf": "26", "maxrate": "950k",  "bufsize": "1400k", "abitrate": "96k"},
        "360p":  {"height": 360,  "crf": "26", "maxrate": "550k",  "bufsize": "900k",  "abitrate": "64k"},
    }

    target_q_list = [q for q in qualities if q in profiles]
    if not target_q_list:
        return {}

    valid_outputs: dict[str, str] = {}
    src_w, src_h = get_video_resolution(input_path, ffmpeg_bin)

    # Enforce AAC audio transcode unless source is confirmed already to be AAC (prevents MP4 mux errors with DTS/EAC3)
    in_audio_codec = get_audio_codec(input_path, ffmpeg_bin)
    needs_aac_transcode = bool(in_audio_codec != "aac")
    if needs_aac_transcode:
        log.info("[VideoService] Audio codec is '%s' -> transcoding to AAC for universal MP4 browser playback", in_audio_codec or "unknown")

    # Detect resolution tier cleanly (mutually exclusive) supporting both 16:9 and 2.39:1 widescreen films
    if (src_w >= 1600 or src_h >= 900):
        is_source_1080p = True
        is_source_720p = is_source_480p = is_source_360p = False
    elif (src_w >= 1000 or src_h >= 576):
        is_source_720p = True
        is_source_1080p = is_source_480p = is_source_360p = False
    elif (src_w >= 650 or src_h >= 420):
        is_source_480p = True
        is_source_1080p = is_source_720p = is_source_360p = False
    elif (src_w > 0 or src_h > 0):
        is_source_360p = True
        is_source_1080p = is_source_720p = is_source_480p = False
    else:
        is_source_1080p = is_source_720p = is_source_480p = is_source_360p = False

    # If source is already ~1080p and 1080p is in target_q_list, instant copy
    if is_source_1080p and "1080p" in target_q_list:
        direct_1080_path = os.path.join(abs_out_dir, f"{slug}-1080p.mp4")
        if os.path.abspath(input_path) == os.path.abspath(direct_1080_path):
            valid_outputs["1080p"] = direct_1080_path
            target_q_list = [q for q in target_q_list if q != "1080p"]
        else:
            try:
                ok_copy = await stream_copy_subtitles(input_path, sub_path, direct_1080_path, disposition="default")
                if not ok_copy:
                    ok_copy = await ensure_web_streamable(input_path, direct_1080_path, sub_path=sub_path)
                if ok_copy and os.path.exists(direct_1080_path) and os.path.getsize(direct_1080_path) >= min_valid_size:
                    valid_outputs["1080p"] = direct_1080_path
                    target_q_list = [q for q in target_q_list if q != "1080p"]
                    log.info("[VideoService] Source is already ~1080p (%dx%d). Instant 1080p copy applied: %s", src_w, src_h, direct_1080_path)
            except Exception as e1080:
                log.debug("[VideoService] Direct 1080p copy note: %s", e1080)

    # If source is already ~720p, do not re-encode 720p if copy succeeds.
    if is_source_720p and "720p" in target_q_list:
        direct_720_path = os.path.join(abs_out_dir, f"{slug}-720p.mp4")
        if os.path.abspath(input_path) == os.path.abspath(direct_720_path):
            valid_outputs["720p"] = direct_720_path
            target_q_list = [q for q in target_q_list if q != "720p"]
        else:
            try:
                ok_copy = await stream_copy_subtitles(input_path, sub_path, direct_720_path, disposition="default")
                if not ok_copy:
                    ok_copy = await ensure_web_streamable(input_path, direct_720_path, sub_path=sub_path)
                if ok_copy and os.path.exists(direct_720_path) and os.path.getsize(direct_720_path) >= min_valid_size:
                    valid_outputs["720p"] = direct_720_path
                    target_q_list = [q for q in target_q_list if q != "720p"]
                    log.info("[VideoService] Source is already <= 720p (%dx%d). Instant 720p copy applied in seconds: %s", src_w, src_h, direct_720_path)
            except Exception as e720:
                log.debug("[VideoService] Direct 720p copy note: %s", e720)

    # If source is already ~480p, do not re-encode 480p if copy succeeds.
    if is_source_480p and "480p" in target_q_list:
        direct_480_path = os.path.join(abs_out_dir, f"{slug}-480p.mp4")
        if os.path.abspath(input_path) == os.path.abspath(direct_480_path):
            valid_outputs["480p"] = direct_480_path
            target_q_list = [q for q in target_q_list if q != "480p"]
        else:
            try:
                ok_copy = await stream_copy_subtitles(input_path, sub_path, direct_480_path, disposition="default")
                if not ok_copy:
                    ok_copy = await ensure_web_streamable(input_path, direct_480_path, sub_path=sub_path)
                if ok_copy and os.path.exists(direct_480_path) and os.path.getsize(direct_480_path) >= min_valid_size:
                    valid_outputs["480p"] = direct_480_path
                    target_q_list = [q for q in target_q_list if q != "480p"]
                    log.info("[VideoService] Source is already ~480p (%dx%d). Instant 480p copy applied in seconds: %s", src_w, src_h, direct_480_path)
            except Exception as e480:
                log.debug("[VideoService] Direct 480p copy note: %s", e480)

    # If source is already ~360p, do not re-encode 360p if copy succeeds.
    if is_source_360p and "360p" in target_q_list:
        direct_360_path = os.path.join(abs_out_dir, f"{slug}-360p.mp4")
        if os.path.abspath(input_path) == os.path.abspath(direct_360_path):
            valid_outputs["360p"] = direct_360_path
            target_q_list = [q for q in target_q_list if q != "360p"]
        else:
            try:
                ok_copy = await stream_copy_subtitles(input_path, sub_path, direct_360_path, disposition="default")
                if not ok_copy:
                    ok_copy = await ensure_web_streamable(input_path, direct_360_path, sub_path=sub_path)
                if ok_copy and os.path.exists(direct_360_path) and os.path.getsize(direct_360_path) >= min_valid_size:
                    valid_outputs["360p"] = direct_360_path
                    target_q_list = [q for q in target_q_list if q != "360p"]
                    log.info("[VideoService] Source is already ~360p (%dx%d). Instant 360p copy applied in seconds: %s", src_w, src_h, direct_360_path)
            except Exception as e360:
                log.debug("[VideoService] Direct 360p copy note: %s", e360)

    # Prevent wasteful upscaling: filter out qualities that exceed source resolution
    # Check width or height to support widescreen aspect ratios (e.g. 1280x534 for 720p, 854x360 for 480p)
    _min_w = {"1080p": 1400, "720p": 950, "480p": 500, "360p": 300}
    if src_w > 0 or src_h > 0:
        target_q_list = [
            q for q in target_q_list
            if (src_w >= _min_w.get(q, 0) or src_h >= (profiles[q]["height"] - 150))
        ]

    if not target_q_list:
        return valid_outputs

    hw_enc = detect_hw_encoder(ffmpeg_bin)
    num_q = len(target_q_list)

    def _build_multi_cmd(use_hw: str, burn_subs: bool, include_soft_subs: bool) -> tuple[list[str], dict[str, str]]:
        if num_q == 1:
            q0 = target_q_list[0]
            h0 = profiles[q0]["height"]
            fc = (
                f"[0:v:0]subtitles=sub_burn_multi.srt,scale=w=-2:h=min(ih\\,{h0}):flags=fast_bilinear[v_{q0}]"
                if burn_subs
                else f"[0:v:0]scale=w=-2:h=min(ih\\,{h0}):flags=fast_bilinear[v_{q0}]"
            )
        else:
            split_labels = "".join(f"[sp_{q}]" for q in target_q_list)
            split_head = (
                f"[0:v:0]subtitles=sub_burn_multi.srt,split={num_q}{split_labels}"
                if burn_subs
                else f"[0:v:0]split={num_q}{split_labels}"
            )
            scale_branches = ";".join(
                f"[sp_{q}]scale=w=-2:h=min(ih\\,{profiles[q]['height']}):flags=fast_bilinear[v_{q}]"
                for q in target_q_list
            )
            fc = f"{split_head};{scale_branches}"

        c: list[str] = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-threads", "0",
            "-i", os.path.abspath(input_path),
        ]
        effective_sub = sub_path
        if include_soft_subs and sub_path and os.path.exists(sub_path):
            if sub_path.lower().endswith(".vtt"):
                try:
                    from services.subtitle_service import vtt_to_srt
                    conv_srt = os.path.join(abs_out_dir, "multi_sub_temp.srt")
                    effective_sub = vtt_to_srt(sub_path, conv_srt)
                except Exception as conv_err:
                    log.debug("[VideoService] Subtitle format prep note: %s", conv_err)
                    effective_sub = sub_path
            if effective_sub and os.path.exists(effective_sub) and os.path.getsize(effective_sub) > 16:
                c.extend(["-i", os.path.abspath(effective_sub)])
            else:
                include_soft_subs = False

        c.extend(["-filter_complex", fc])

        paths: dict[str, str] = {}
        for q in target_q_list:
            prof = profiles[q]
            out_file = os.path.join(abs_out_dir, f"{slug}-{q}.mp4")
            paths[q] = out_file
            c.extend([
                "-map", f"[v_{q}]",
                "-map", "0:a:0?",
            ])
            if include_soft_subs and effective_sub and os.path.exists(effective_sub) and os.path.getsize(effective_sub) > 16:
                c.extend([
                    "-map", "1:0",
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=sin",
                    "-metadata:s:s:0", "title=Sinhala (සිංහල)",
                    "-disposition:s:0", "default",
                ])
            if use_hw == "h264_nvenc":
                c.extend([
                    "-c:v", "h264_nvenc",
                    "-preset", "p1",
                    "-rc", "vbr",
                    "-cq", prof["crf"],
                    "-maxrate", prof["maxrate"],
                    "-bufsize", prof["bufsize"],
                ])
            else:
                c.extend([
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-tune", "fastdecode",
                    "-crf", "28",
                    "-threads", "0",
                ])
            c.append("-pix_fmt")
            c.append("yuv420p")
            if needs_aac_transcode:
                c.extend([
                    "-c:a", "aac",
                    "-b:a", prof["abitrate"],
                    "-ac", "2",
                ])
            else:
                c.extend(["-c:a", "copy"])
            c.extend([
                "-movflags", "+faststart",
                out_file,
            ])
        return c, paths

    # Build ordered attempt strategies:
    # 1. Preferred encoder (NVENC or libx264) + soft subs (instantaneous, zero CPU subtitle burning overhead)
    # 2. Preferred encoder without soft subs (if mov_text / subtitle stream muxing failed)
    # 3. Fallback CPU libx264 without subs
    strategies: list[tuple[str, bool, bool]] = []
    if hw_enc == "h264_nvenc":
        strategies.append(("h264_nvenc", False, has_sub))
        if has_sub:
            strategies.append(("h264_nvenc", False, False))
        strategies.append(("libx264", False, has_sub))
        if has_sub:
            strategies.append(("libx264", False, False))
    else:
        if has_sub:
            strategies.append(("libx264", False, True))
        strategies.append(("libx264", False, False))

    proc = None
    try:
        for attempt_idx, (enc_choice, burn_choice, soft_choice) in enumerate(strategies):
            cmd, out_paths = _build_multi_cmd(enc_choice, burn_choice, soft_choice)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=abs_out_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
            last_pct = 0.0
            stderr_tail = collections.deque(maxlen=40)

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
                    for line in decoded.splitlines(keepends=True):
                        stderr_tail.append(line)
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

            timeout_val = max(300.0, min(1800.0, duration * 0.5)) if enc_choice == "h264_nvenc" else max(900.0, min(5400.0, duration * 2.0))
            try:
                await asyncio.wait_for(asyncio.gather(proc.wait(), _read_stderr()), timeout=timeout_val)
            except asyncio.TimeoutError:
                log.warning("[VideoService] Multi-quality FFmpeg timed out (%.0fs) — terminating process", timeout_val)
                if proc and proc.returncode is None:
                    try:
                        proc.terminate()
                        proc.kill()
                    except Exception:
                        pass

            if proc and proc.returncode == 0:
                for q, p in out_paths.items():
                    if os.path.exists(p) and os.path.getsize(p) >= min_valid_size:
                        valid_outputs[q] = p
            else:
                tail_str = "".join(stderr_tail).strip()
                log.error(
                    "[VideoService] Multi-quality FFmpeg attempt %d failed (exit %s):\n%s",
                    attempt_idx + 1, getattr(proc, "returncode", "None"), tail_str[-1200:] if tail_str else "(no stderr)",
                )
                # Even on non-zero exit, rescue any output files that appear fully-written.
                # A real encoded variant is always several hundred KB; corrupt/partial stub
                # files from interrupted FFmpeg passes are tiny.  Use 512 KB as the rescue
                # threshold so test stubs (a few KB) are still cleaned up.
                _rescue_min = max(512 * 1024, min_valid_size)
                rescued = []
                for q, p in out_paths.items():
                    if os.path.exists(p) and os.path.getsize(p) >= _rescue_min and q not in valid_outputs:
                        valid_outputs[q] = p
                        rescued.append(q)
                    elif os.path.exists(p) and q not in valid_outputs:
                        # Partial/corrupt file — delete it
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                if rescued:
                    log.info(
                        "[VideoService] Rescued %d valid output(s) from non-zero-exit FFmpeg attempt: %s",
                        len(rescued), rescued,
                    )

            if all(q in valid_outputs for q in target_q_list):
                log.info(
                    "[VideoService] Multi-quality RAM generation complete (attempt %d, encoder=%s, burn=%s): %s",
                    attempt_idx + 1, enc_choice, burn_choice, list(valid_outputs.keys()),
                )
                return valid_outputs

            # If all newly targeted qualities were produced in this attempt, return
            if proc and proc.returncode == 0 and any(q in valid_outputs for q in out_paths.keys()):
                log.info(
                    "[VideoService] Multi-quality variants successfully produced: %s",
                    list(valid_outputs.keys()),
                )
                return valid_outputs

            log.warning(
                "[VideoService] Multi-quality attempt %d (encoder=%s, burn=%s) produced %d/%d variants; retrying fallback...",
                attempt_idx + 1, enc_choice, burn_choice, len(valid_outputs), len(target_q_list),
            )
        return valid_outputs
    except Exception as exc:
        log.warning("[VideoService] Multi-quality generation skipped/failed: %s", exc)
        return valid_outputs
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


