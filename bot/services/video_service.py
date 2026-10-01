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
import tempfile
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Max upload limit for standard Telegram Bot API (1.95 GB safe ceiling)
MAX_TELEGRAM_BOT_SIZE = int(1.95 * 1024 * 1024 * 1024)
# Target compression size for 1080p: ~1.90 GB (close to 2GB to maximize visual fidelity without hitting 1.95GB limit)
TARGET_COMPRESS_SIZE = int(1.90 * 1024 * 1024 * 1024)

# ── Colab / Render environment detection ─────────────────────────────────────
# Google Colab (T4 GPU, 12 GB RAM): use all cores + ultrafast preset
# Render Free Tier (0.1 vCPU, 512 MB RAM): use veryfast + limited remux
_on_colab: bool = os.path.exists("/content")
_threads: str = "0"          # always use all available CPU cores
_preset: str = "ultrafast" if _on_colab else "veryfast"

# Ensure NVIDIA CUDA / NVENC driver libraries (/usr/local/nvidia/lib64) are in LD_LIBRARY_PATH on Colab/Linux
if os.name != "nt":
    _nv_lib_dirs = [
        d for d in ("/usr/local/nvidia/lib64", "/usr/local/cuda/lib64", "/usr/lib/x86_64-linux-gnu")
        if os.path.isdir(d)
    ]
    if _nv_lib_dirs:
        _cur_ld = os.environ.get("LD_LIBRARY_PATH", "")
        _missing_ld = [d for d in _nv_lib_dirs if d not in _cur_ld.split(":")]
        if _missing_ld:
            os.environ["LD_LIBRARY_PATH"] = ":".join(_missing_ld + ([_cur_ld] if _cur_ld else []))

log.info(
    "[VideoService] Environment: on_colab=%s  preset=%s  threads=%s",
    _on_colab, _preset, _threads,
)


_VALIDATED_FFMPEG: dict[str, bool] = {}
_PREFERRED_FFMPEG_BIN: Optional[str] = None
_COLAB_FFMPEG_REPAIR_ATTEMPTED: bool = False


def _is_working_ffmpeg(exe_path: Optional[str]) -> bool:
    """Verify that an ffmpeg candidate binary actually executes without missing-library errors (exit code 127)."""
    if not exe_path:
        return False
    if exe_path in _VALIDATED_FFMPEG:
        return _VALIDATED_FFMPEG[exe_path]
    try:
        proc = subprocess.run(
            [exe_path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        ok = getattr(proc, "returncode", 0) == 0
        _VALIDATED_FFMPEG[exe_path] = ok
        if not ok:
            log.warning(
                "[VideoService] Rejecting broken ffmpeg binary '%s' (exit=%s): %s",
                exe_path,
                getattr(proc, "returncode", "N/A"),
                (getattr(proc, "stderr", "") or "")[:200].strip(),
            )
        return ok
    except Exception as exc:
        log.debug("[VideoService] ffmpeg candidate '%s' check failed: %s", exe_path, exc)
        _VALIDATED_FFMPEG[exe_path] = False
        return False


def _test_nvenc_binary(exe_path: Optional[str]) -> tuple[bool, str]:
    """Test whether exe_path can initialize NVIDIA h264_nvenc hardware encoder on the host GPU."""
    if not exe_path:
        return False, "No ffmpeg binary"
    try:
        probe = subprocess.run(
            [
                exe_path,
                "-hide_banner",
                "-f", "lavfi",
                "-i", "color=c=black:s=640x360:r=25:d=0.2",
                "-pix_fmt", "yuv420p",
                "-c:v", "h264_nvenc",
                "-f", "null",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if getattr(probe, "returncode", 1) == 0:
            return True, ""
        err_txt = (getattr(probe, "stderr", "") or "").strip()
        return False, err_txt[-300:] if err_txt else f"exit={getattr(probe, 'returncode', 1)}"
    except Exception as exc:
        return False, str(exc)


def ensure_colab_working_ffmpeg() -> Optional[str]:
    """
    Ensure a fully functional FFmpeg binary (with h264_nvenc compatible with Colab T4 Driver 535/550,
    libx264, libass, aac, and mov_text) is available on Google Colab.
    Note: BtbN 'ffmpeg-master-latest' requires NVENC API 13.0 (Driver >= 570), which fails on Colab T4
    (Driver 535/550, NVENC API 12.1/12.2). Ubuntu's native apt ffmpeg and BtbN n6.1 work 100% with Colab T4.
    """
    global _COLAB_FFMPEG_REPAIR_ATTEMPTED, _PREFERRED_FFMPEG_BIN
    if not os.path.exists("/content"):
        return None

    local_bin = "/usr/local/bin/ffmpeg"
    sys_bin = "/usr/bin/ffmpeg"

    if os.path.exists(sys_bin) and _is_working_ffmpeg(sys_bin):
        return sys_bin
    if os.path.exists(local_bin) and _is_working_ffmpeg(local_bin):
        return local_bin

    if not _COLAB_FFMPEG_REPAIR_ATTEMPTED:
        _COLAB_FFMPEG_REPAIR_ATTEMPTED = True
        log.info("[VideoService] Installing/repairing Colab FFmpeg (Ubuntu apt + BtbN n6.1 NVENC 12.1)...")
        try:
            subprocess.run(
                "apt-get update -qq && apt-get install --reinstall -y -qq ffmpeg fonts-noto-core fontconfig",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90,
            )
            _VALIDATED_FFMPEG.pop(sys_bin, None)
            if os.path.exists(sys_bin) and _is_working_ffmpeg(sys_bin):
                return sys_bin
        except Exception as apt_exc:
            log.debug("[VideoService] apt ffmpeg reinstall note: %s", apt_exc)

        try:
            os.makedirs("/usr/local/bin", exist_ok=True)
            cmd = (
                "curl -sL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n6.1-latest-linux64-gpl-6.1.tar.xz -o /tmp/ff_static.tar.xz "
                "&& tar -xf /tmp/ff_static.tar.xz --wildcards '*/bin/ffmpeg' '*/bin/ffprobe' --strip-components=2 -C /usr/local/bin/ "
                "&& chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe "
                "&& rm -rf /tmp/ff_static.tar.xz"
            )
            res = subprocess.run(cmd, shell=True, timeout=180)
            _VALIDATED_FFMPEG.pop(local_bin, None)
            if getattr(res, "returncode", 1) == 0 and os.path.exists(local_bin) and _is_working_ffmpeg(local_bin):
                log.info("[VideoService] Static BtbN n6.1 FFmpeg installed at /usr/local/bin/ffmpeg")
                return local_bin
        except Exception as exc:
            log.warning("[VideoService] Static BtbN FFmpeg install error: %s", exc)

    # Fallback: copy bundled imageio_ffmpeg static binary if available
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe) and _is_working_ffmpeg(exe):
            return exe
    except Exception:
        pass

    return None


def get_ffmpeg_binary() -> Optional[str]:
    """Find and validate a working system ffmpeg or bundled imageio_ffmpeg binary."""
    if _PREFERRED_FFMPEG_BIN and _VALIDATED_FFMPEG.get(_PREFERRED_FFMPEG_BIN) and os.path.exists(_PREFERRED_FFMPEG_BIN):
        return _PREFERRED_FFMPEG_BIN

    # 1. Check /usr/local/bin/ffmpeg first
    if os.path.exists("/usr/local/bin/ffmpeg") and os.access("/usr/local/bin/ffmpeg", os.X_OK):
        if _is_working_ffmpeg("/usr/local/bin/ffmpeg"):
            return "/usr/local/bin/ffmpeg"

    # 2. Check system PATH ffmpeg and verify it actually runs (protects against broken colab-ffmpeg-cuda exit 127)
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg and _is_working_ffmpeg(sys_ffmpeg):
        return sys_ffmpeg

    # 3. On Google Colab, if system ffmpeg is missing or corrupted, self-heal immediately
    if os.path.exists("/content"):
        repaired = ensure_colab_working_ffmpeg()
        if repaired and _is_working_ffmpeg(repaired):
            return repaired

    # 4. Fallback to bundled imageio_ffmpeg binary
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe) and _is_working_ffmpeg(exe):
            return exe
    except Exception as exc:
        log.debug("[VideoService] imageio_ffmpeg lookup error: %s", exc)

    return None


def get_video_duration(file_path: str, ffmpeg_bin: Optional[str] = None) -> float:
    """Extract duration in seconds using ffmpeg -i (supports both container Duration and MKV stream DURATION tags)."""
    exe = ffmpeg_bin or get_ffmpeg_binary()
    if not exe or not os.path.exists(file_path):
        return 0.0
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
        stderr_text = getattr(proc, "stderr", "") or ""
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text)
        if match:
            h, m, s = match.groups()
            dur = int(h) * 3600 + int(m) * 60 + float(s)
            if dur > 0:
                return dur
        # Fallback for MKV files where container Duration is N/A but stream tag has DURATION: HH:MM:SS.nnnnnnnnn
        tag_match = re.search(r"DURATION\s*:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text, re.IGNORECASE)
        if tag_match:
            h, m, s = tag_match.groups()
            dur = int(h) * 3600 + int(m) * 60 + float(s)
            if dur > 0:
                return dur
    except Exception as exc:
        log.debug("[VideoService] Could not parse duration: %s", exc)
    return 0.0


def get_video_resolution(file_path: str, ffmpeg_bin: Optional[str] = None) -> tuple[int, int]:
    """Extract (width, height) resolution using ffmpeg -i, accounting for anamorphic display aspect ratio (DAR)."""
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
        stderr_text = getattr(proc, "stderr", "") or ""
        w, h = 0, 0
        # Prefer matching directly on the Video stream line
        v_match = re.search(r"Stream #.*?Video:.*?,\s*(\d{2,5})x(\d{2,5})(?:,\s*|\s*\[|\s*|$)", stderr_text)
        if v_match:
            w, h = int(v_match.group(1)), int(v_match.group(2))
        else:
            match = re.search(r",\s*(\d{2,5})x(\d{2,5})(?:,\s*|\s*\[|\s*)", stderr_text)
            if match:
                w, h = int(match.group(1)), int(match.group(2))

        # Check for Display Aspect Ratio (DAR) in case of anamorphic video (e.g. 720x576 or 1440x1080 with DAR 16:9)
        if w > 0 and h > 0:
            dar_match = re.search(r"DAR\s*(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", stderr_text)
            if dar_match:
                try:
                    dar_num = float(dar_match.group(1))
                    dar_den = float(dar_match.group(2))
                    if dar_den > 0:
                        dar = dar_num / dar_den
                        sar_ratio = w / h
                        if abs(dar - sar_ratio) > 0.1 and dar >= 1.3:
                            display_w = int(round(h * dar))
                            if display_w % 2 != 0:
                                display_w += 1
                            return (display_w, h)
                except Exception:
                    pass
            return (w, h)
    except Exception as exc:
        log.debug("[VideoService] Could not parse resolution: %s", exc)
    return (0, 0)


def get_video_metadata(file_path: str, ffmpeg_bin: Optional[str] = None) -> dict:
    """
    Extract video width, height, and duration.
    Guarantees valid widescreen (16:9) dimensions (e.g. 1920x1080, 1280x720, 854x480, 640x360)
    for Telegram native widescreen video cards without square pillarboxing ("කොටු size").
    """
    exe = ffmpeg_bin or get_ffmpeg_binary()
    w, h = get_video_resolution(file_path, exe)
    duration = get_video_duration(file_path, exe)

    # If resolution was not detected or invalid, infer from filename or fallback to standard 16:9 HD
    if w <= 0 or h <= 0:
        fn_lower = os.path.basename(file_path).lower()
        if "1080" in fn_lower:
            w, h = 1920, 1080
        elif "720" in fn_lower:
            w, h = 1280, 720
        elif "480" in fn_lower:
            w, h = 854, 480
        elif "360" in fn_lower:
            w, h = 640, 360
        else:
            w, h = 1280, 720

    # Ensure valid even numbers for video encoder / player compatibility
    w = int(w)
    h = int(h)
    if w % 2 != 0:
        w += 1
    if h % 2 != 0:
        h += 1

    dur_sec = max(0, int(round(duration)))
    return {
        "width": w,
        "height": h,
        "duration": dur_sec,
    }


def generate_video_thumbnail(
    file_path: str,
    thumb_path: Optional[str] = None,
    duration: float = 0.0,
    ffmpeg_bin: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a crisp 16:9 widescreen thumbnail image (.jpg) from the video.
    Uses 640x360 resolution with high JPEG quality so Telegram renders
    a native full-width 16:9 widescreen video preview card across Mobile,
    Laptop, and Smart TV screens without square pillarboxing.
    """
    exe = ffmpeg_bin or get_ffmpeg_binary()
    if not exe or not os.path.exists(file_path):
        return None

    if not thumb_path:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        thumb_path = tmp.name
        tmp.close()

    if duration <= 0:
        duration = get_video_duration(file_path, exe)

    # Pick a scene timestamp avoiding black screen at the start
    if duration > 30:
        seek_time = max(5.0, min(duration * 0.12, 120.0))
    elif duration > 5:
        seek_time = 2.0
    else:
        seek_time = 0.0

    # 16:9 widescreen thumbnail filter: scale to 640x360 keeping aspect ratio and pad with black if cinematic
    vf = "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2:black"

    cmd = [
        exe,
        "-y",
        "-hide_banner",
        "-ss", f"{seek_time:.2f}",
        "-i", file_path,
        "-vframes", "1",
        "-vf", vf,
        "-q:v", "2",
        thumb_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if proc.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 500:
            return thumb_path
    except Exception as exc:
        log.debug("[VideoService] First attempt to generate thumbnail failed: %s", exc)

    # Fallback attempt at timestamp 00:00:01 if seek_time failed
    try:
        cmd_fallback = [
            exe,
            "-y",
            "-hide_banner",
            "-ss", "00:00:01",
            "-i", file_path,
            "-vframes", "1",
            "-vf", vf,
            "-q:v", "2",
            thumb_path,
        ]
        proc = subprocess.run(
            cmd_fallback,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if proc.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 500:
            return thumb_path
    except Exception as exc:
        log.debug("[VideoService] Fallback thumbnail generation failed: %s", exc)

    return None


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
        stderr_text = getattr(proc, "stderr", "") or ""
        audio_matches = re.findall(r"Stream #\d+:\d+.*?: Audio:\s*([a-zA-Z0-9_]+)", stderr_text)
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
    On Google Colab GPU runtimes, heal FFmpeg if /usr/local/bin/ffmpeg or /usr/bin/ffmpeg
    was overwritten by BtbN 'ffmpeg-master-latest' (which requires NVENC API 13.0 / Driver 570+,
    failing on Colab's T4 Driver 535/550). Restores Ubuntu's native NVENC-compatible /usr/bin/ffmpeg
    or BtbN n6.1 so h264_nvenc works 100%.
    """
    global _PREFERRED_FFMPEG_BIN
    if not os.path.exists("/content"):
        return False

    local_bin = "/usr/local/bin/ffmpeg"
    sys_bin = "/usr/bin/ffmpeg"

    # 1. If /usr/local/bin/ffmpeg exists but fails NVENC (e.g. NVENC API 13.0 mismatch), remove it first
    if os.path.exists(local_bin):
        ok_local, err_local = _test_nvenc_binary(local_bin)
        if ok_local:
            _VALIDATED_FFMPEG[local_bin] = True
            _PREFERRED_FFMPEG_BIN = local_bin
            return True
        log.info("[VideoService] Removing NVENC-incompatible %s (%s)", local_bin, err_local[:120])
        try:
            os.remove(local_bin)
            _VALIDATED_FFMPEG.pop(local_bin, None)
        except Exception:
            pass

    # 2. Check if /usr/bin/ffmpeg already works with NVENC
    if os.path.exists(sys_bin):
        ok_sys, _ = _test_nvenc_binary(sys_bin)
        if ok_sys:
            _VALIDATED_FFMPEG[sys_bin] = True
            _PREFERRED_FFMPEG_BIN = sys_bin
            return True

    # 3. Reinstall Ubuntu's native ffmpeg package (NVENC 11.x — 100% compatible with Colab T4 Driver 535/550)
    if shutil.which("apt-get"):
        try:
            log.info("[VideoService] Restoring Ubuntu native NVENC FFmpeg via apt-get reinstall...")
            subprocess.run(
                "apt-get update -qq && apt-get install --reinstall -y -qq ffmpeg fonts-noto-core fontconfig",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90,
            )
            _VALIDATED_FFMPEG.pop(sys_bin, None)
            if os.path.exists(sys_bin) and _is_working_ffmpeg(sys_bin):
                ok_sys, _ = _test_nvenc_binary(sys_bin)
                if ok_sys:
                    _PREFERRED_FFMPEG_BIN = sys_bin
                    try:
                        shutil.copyfile(sys_bin, local_bin)
                        os.chmod(local_bin, 0o755)
                        _VALIDATED_FFMPEG[local_bin] = True
                    except Exception:
                        pass
                    return True
        except Exception as apt_err:
            log.debug("[VideoService] apt reinstall ffmpeg note: %s", apt_err)

    # 4. Fallback: install BtbN n6.1 (NVENC API 12.1 — compatible with Driver 535/550)
    try:
        os.makedirs("/usr/local/bin", exist_ok=True)
        cmd = (
            "curl -sL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n6.1-latest-linux64-gpl-6.1.tar.xz -o /tmp/ff_n61.tar.xz "
            "&& tar -xf /tmp/ff_n61.tar.xz --wildcards '*/bin/ffmpeg' '*/bin/ffprobe' --strip-components=2 -C /usr/local/bin/ "
            "&& chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe "
            "&& rm -rf /tmp/ff_n61.tar.xz"
        )
        subprocess.run(cmd, shell=True, timeout=180)
        _VALIDATED_FFMPEG.pop(local_bin, None)
        if os.path.exists(local_bin) and _is_working_ffmpeg(local_bin):
            ok_b, _ = _test_nvenc_binary(local_bin)
            if ok_b:
                _PREFERRED_FFMPEG_BIN = local_bin
                return True
    except Exception as exc:
        log.debug("[VideoService] BtbN n6.1 install note: %s", exc)

    return False


def detect_hw_encoder(ffmpeg_bin: Optional[str] = None) -> str:
    """
    Auto-detect if NVIDIA GPU hardware encoder (h264_nvenc on Google Colab T4/L4)
    is available and functional. Falls back to 'libx264' on CPU-only hosts.
    """
    global _CACHED_HW_ENCODER, _PREFERRED_FFMPEG_BIN
    if _CACHED_HW_ENCODER is not None:
        return _CACHED_HW_ENCODER

    # Check if NVIDIA GPU exists first
    has_gpu = False
    try:
        smi = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        has_gpu = (getattr(smi, "returncode", 1) == 0)
    except Exception:
        has_gpu = False

    if not has_gpu:
        log.info("[VideoService] No NVIDIA GPU detected (CPU mode active). Using high-speed multi-threaded CPU encoder.")
        _CACHED_HW_ENCODER = "libx264"
        return _CACHED_HW_ENCODER

    # Test primary and system FFmpeg candidates for NVENC support
    candidates = []
    if ffmpeg_bin:
        candidates.append(ffmpeg_bin)
    else:
        exe = get_ffmpeg_binary()
        if exe:
            candidates.append(exe)
    for extra in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if extra not in candidates and os.path.exists(extra):
            candidates.append(extra)

    last_err = ""
    for cand in candidates:
        ok, err_msg = _test_nvenc_binary(cand)
        if ok:
            _VALIDATED_FFMPEG[cand] = True
            _PREFERRED_FFMPEG_BIN = cand
            _CACHED_HW_ENCODER = "h264_nvenc"
            log.info("[VideoService] Hardware GPU acceleration detected: h264_nvenc enabled (binary=%s)!", cand)
            return _CACHED_HW_ENCODER
        last_err = err_msg

    # On Google Colab with GPU: if NVENC failed (e.g. due to BtbN master NVENC 13.0 driver mismatch), heal FFmpeg
    if os.path.exists("/content") and not getattr(detect_hw_encoder, "_attempted_cuda_install", False):
        setattr(detect_hw_encoder, "_attempted_cuda_install", True)
        log.warning("[VideoService] Initial h264_nvenc probe failed (%s) — repairing Colab CUDA FFmpeg...", last_err[:160])
        if setup_colab_cuda_ffmpeg():
            new_exe = _PREFERRED_FFMPEG_BIN or get_ffmpeg_binary()
            ok2, err2 = _test_nvenc_binary(new_exe)
            if ok2:
                _CACHED_HW_ENCODER = "h264_nvenc"
                log.info("[VideoService] Colab CUDA GPU acceleration activated: h264_nvenc enabled (binary=%s)!", new_exe)
                return _CACHED_HW_ENCODER
            last_err = err2

    _CACHED_HW_ENCODER = "libx264"
    log.warning("[VideoService] h264_nvenc unavailable (%s); falling back to high-speed CPU encoder: libx264", last_err[:160])
    return _CACHED_HW_ENCODER


_CUDA_CTX_HANDLE = None
_CUDA_MEM_PTR = None


def warm_up_colab_gpu() -> str:
    """
    Ensure FFmpeg h264_nvenc is healed/verified on Google Colab and hold a lightweight
    persistent CUDA context (~256 MB VRAM) via libcuda.so.1 so:
      1. The Tesla T4 GPU stays initialized (zero cold-start latency when FFmpeg launches).
      2. Colab's GPU RAM monitor shows active GPU utilization and never triggers idle GPU warnings.
    Returns the active hardware encoder name ('h264_nvenc' or 'libx264').
    """
    global _CUDA_CTX_HANDLE, _CUDA_MEM_PTR
    hw_enc = detect_hw_encoder()
    if hw_enc == "h264_nvenc" and _CUDA_CTX_HANDLE is None and os.name != "nt":
        try:
            import ctypes
            cuda = ctypes.CDLL("libcuda.so.1")
            if cuda.cuInit(0) == 0:
                dev = ctypes.c_int()
                if cuda.cuDeviceGet(ctypes.byref(dev), 0) == 0:
                    ctx = ctypes.c_void_p()
                    if cuda.cuCtxCreate_v2(ctypes.byref(ctx), 0, dev) == 0:
                        _CUDA_CTX_HANDLE = ctx
                        dptr = ctypes.c_uint64()
                        if cuda.cuMemAlloc_v2(ctypes.byref(dptr), ctypes.c_size_t(256 * 1024 * 1024)) == 0:
                            _CUDA_MEM_PTR = dptr
                            log.info("[VideoService] Tesla T4 CUDA context warmed up (256 MB VRAM pinned for instant NVENC).")
        except Exception as exc:
            log.debug("[VideoService] CUDA context warmup note: %s", exc)
    return hw_enc


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
    clean_sub_temp: Optional[str] = None
    if has_sub:
        if is_mp4 and sub_path.lower().endswith(".vtt"):
            try:
                from services.subtitle_service import vtt_to_srt
                sub_srt = os.path.join(out_dir, f"sub_copy_{os.path.basename(sub_path)}.srt")
                sub_path = vtt_to_srt(sub_path, sub_srt)
            except Exception as e_vtt:
                log.debug("[VideoService] VTT->SRT prep for stream-copy: %s", e_vtt)
        # Normalize UTF-8/UTF-16/BOM subtitle file so mov_text muxing never fails at EOF
        try:
            clean_sub_temp = os.path.join(out_dir, f"clean_copy_{os.path.basename(output_path)}.srt")
            if prepare_clean_srt_for_burn(sub_path, clean_sub_temp):
                sub_path = clean_sub_temp
        except Exception:
            pass

    def _cleanup_sub_temp() -> None:
        if clean_sub_temp and os.path.exists(clean_sub_temp):
            try:
                os.remove(clean_sub_temp)
            except Exception:
                pass

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
            "-map", "0:a:0?",
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
            "-map", "0:a:0?",
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

    _attempt_timeout = 180.0 if needs_aac else 60.0
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=out_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=_attempt_timeout)
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            log.info("[VideoService] Stream-copy muxing succeeded: %s", output_path)
            _cleanup_sub_temp()
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
        _cleanup_sub_temp()
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

    # Fallback Attempt 2: Only run if Attempt 1 did NOT already transcode audio to AAC
    if not needs_aac:
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
                _cleanup_sub_temp()
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
            _cleanup_sub_temp()
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

    # Fallback Attempt 3: Only when has_sub and not needs_aac, try stream-copy without subtitle (-c:a copy -sn)
    if has_sub and not needs_aac:
        cmd_fallback_nosub = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-threads", "0",
            "-i", os.path.abspath(video_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "copy",
            "-c:a", "copy",
            "-sn",
            "-max_muxing_queue_size", "9999",
        ]
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
            await asyncio.wait_for(proc.wait(), timeout=60.0)
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                log.info("[VideoService] Stream-copy without subtitle fallback succeeded: %s", output_path)
                _cleanup_sub_temp()
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
            _cleanup_sub_temp()
            raise
        except Exception as exc:
            log.debug("[VideoService] Stream-copy no-sub fallback failed: %s", exc)
            _cleanup_output()
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    proc.kill()
                except Exception:
                    pass

    # Fallback Attempt 4: Stream-copy video + stereo AAC without subtitle
    if has_sub:
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
                log.info("[VideoService] Stream-copy AAC without subtitle fallback succeeded: %s", output_path)
                _cleanup_sub_temp()
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
            _cleanup_sub_temp()
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
    _cleanup_sub_temp()

    return False


async def compress_video(
    input_path: str,
    output_path: str,
    target_size_bytes: int = TARGET_COMPRESS_SIZE,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    sub_path: Optional[str] = None,
    is_hardsub: bool = False,
) -> bool:
    """
    Compress bloated video files (>1.95GB, e.g. KGF Chapter 2 2.3GB, DC 3.5GB) down to strictly <= 1.95GB
    (target ~1.90GB safe ceiling to maximize 1080p visual fidelity) using multi-core CPU or GPU NVENC.
    Calculates exact target bitrate from video duration to guarantee the output never exceeds
    the Telegram Bot 1.95GB limit while preserving sharp resolution, audio fidelity, and crisp detail.
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

    # Disk space safety check: ensure disk/RAM has sufficient space for input + output
    try:
        needed_bytes = target_size_bytes + 200 * 1024 * 1024
        free_space = shutil.disk_usage(out_dir).free
        if free_space < needed_bytes:
            log.warning(
                "[VideoService] Work dir '%s' has only %.2f GB free (target needs %.2f GB). "
                "Checking alternative storage...",
                out_dir, free_space / (1024 ** 3), needed_bytes / (1024 ** 3)
            )
            fallback_dirs = ["/content", tempfile.gettempdir(), "."]
            for fb in fallback_dirs:
                if os.path.isdir(fb) and os.access(fb, os.W_OK):
                    fb_free = shutil.disk_usage(fb).free
                    if fb_free >= needed_bytes:
                        orig_name = os.path.basename(output_path)
                        output_path = os.path.join(fb, orig_name)
                        out_dir = fb
                        log.info("[VideoService] Redirected compression output to disk '%s' (%.2f GB free)", output_path, fb_free / (1024 ** 3))
                        break
    except Exception as d_err:
        log.debug("[VideoService] Disk space check note: %s", d_err)

    def _cleanup_output() -> None:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

    duration = get_video_duration(input_path, ffmpeg_bin)
    in_bytes = os.path.getsize(input_path)
    log.info("[VideoService] compress_video: '%s' (duration=%.1fs, size=%d bytes) -> '%s' (target=%d bytes, is_hardsub=%s)",
             input_path, duration, in_bytes, output_path, target_size_bytes, is_hardsub)

    # Calculate optimal target bitrate with ~3% margin reserved for container/muxing overhead
    audio_bps = 128_000
    usable_bits = int(target_size_bytes * 8 * 0.97)
    if duration > 10.0:
        total_bps = usable_bits / duration
        video_bps = max(500_000, int(total_bps - audio_bps))
        v_bitrate_k = int(video_bps / 1000)
    else:
        # Fallback if duration is unknown/unparsed: assume 3 hours (10800s) to guarantee staying strictly under target_size_bytes
        log.warning("[VideoService] Duration unparsed or <= 10s (%.1fs). Using safe 3-hour fallback bitrate.", duration)
        fallback_dur = 10800.0
        total_bps = usable_bits / fallback_dur
        video_bps = max(500_000, int(total_bps - audio_bps))
        v_bitrate_k = int(video_bps / 1000)

    maxrate_k = int(v_bitrate_k * 1.15)
    bufsize_k = int(v_bitrate_k * 2)

    hw_enc = detect_hw_encoder(ffmpeg_bin)
    if is_hardsub:
        log.info("[VideoService] Video is already hardsubbed (is_hardsub=True). Skipping subtitle burn/mux.")
        sub_path = None
        has_sub = False
    else:
        has_sub = bool(sub_path and os.path.exists(sub_path) and os.path.getsize(sub_path) > 16)
    ext = os.path.splitext(output_path)[1].lower()
    is_mp4 = ext in (".mp4", ".m4v", ".mov")
    sub_codec = "mov_text" if is_mp4 else "srt"

    burn_vf = None
    if has_sub:
        try:
            comp_sub_srt = os.path.join(out_dir, "sub_burn_comp.srt")
            if prepare_clean_srt_for_burn(sub_path, comp_sub_srt):
                sub_path = comp_sub_srt
            ass_path = os.path.splitext(sub_path)[0] + ".ass"
            shaped_ass = srt_to_ass_sinhala_shaped(sub_path, ass_path)
            sub_to_use = shaped_ass if (shaped_ass and os.path.exists(shaped_ass)) else sub_path
            burn_vf = build_subtitles_burn_filter(sub_to_use)
        except Exception as b_err:
            log.debug("[VideoService] compress_video hard-burn prep note: %s", b_err)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-threads", "0",
    ]
    if hw_enc == "h264_nvenc":
        cmd.extend(["-hwaccel", "auto"])
    cmd.extend(["-i", os.path.abspath(input_path)])

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
        if burn_vf:
            cmd.extend(["-vf", burn_vf])
    else:
        cmd.extend([
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-sn",
        ])

    if hw_enc == "h264_nvenc":
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", "fast",
            "-rc", "vbr",
            "-b:v", f"{v_bitrate_k}k",
            "-maxrate", f"{maxrate_k}k",
            "-bufsize", f"{bufsize_k}k",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
        ])
    else:
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",   # veryfast preserves B-frames, CABAC, and crisp 1080p visual fidelity
            "-threads", "0",
            "-b:v", f"{v_bitrate_k}k",
            "-maxrate", f"{maxrate_k}k",
            "-bufsize", f"{bufsize_k}k",
            "-profile:v", "high",
            "-level:v", "4.1",
            "-pix_fmt", "yuv420p",
        ])

    cmd.extend([
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-af", "aresample=async=1",
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

        # Generous 1800s (30 minute) timeout to ensure long 3-hour movie compression finishes
        await asyncio.wait_for(asyncio.gather(proc.wait(), _read_stderr()), timeout=1800.0)

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
    except asyncio.TimeoutError:
        log.error("[VideoService] compress_video timed out after 1800s for: %s", input_path)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
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
    is_hardsub: bool = False,
) -> bool:
    """
    Intelligent 1080p Processing:
    1. If file size <= 1.95GB (MAX_TELEGRAM_BOT_SIZE):
       Executes instantaneous Stream Copy & Soft-Sub Muxing in 3-5 seconds.
    2. If file size > 1.95GB (bloated video like KGF Chapter 2 - 2.3GB, DC 3.5GB):
       Executes fast multi-core compression (compress_video) targeting ~1.90GB,
       guaranteeing output <= 1.95GB while preserving maximal 1080p visual fidelity.
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
            target_size_bytes=TARGET_COMPRESS_SIZE,
            progress_callback=progress_callback,
            sub_path=sub_path,
            is_hardsub=is_hardsub,
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
                target_size_bytes=TARGET_COMPRESS_SIZE,
                progress_callback=progress_callback,
                sub_path=sub_path,
                is_hardsub=is_hardsub,
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
    # On Colab, downloading + remuxing + multi-quality encoding needs at least 4.5 GB headroom in /dev/shm
    effective_min_gb = max(min_free_gb, 4.5) if (min_free_gb >= 1.0 and os.path.exists("/content")) else min_free_gb
    min_bytes = int(effective_min_gb * 1024 * 1024 * 1024)
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
            work_dir = tempfile.mkdtemp(prefix=prefix, dir="/content")
            log.info("[VideoService] Allocated /content high-capacity workspace: %s", work_dir)
            return work_dir
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


def ensure_sinhala_font_dir() -> Optional[str]:
    """
    Ensure 'Noto Sans Sinhala' TrueType font (.ttf) is available for FFmpeg libass subtitle burning.
    Checks bundled bot/assets/fonts first, then /usr/share/fonts/truetype/noto, and auto-installs
    fonts-noto-core on Colab/Linux if missing.
    """
    _svc_dir = os.path.dirname(os.path.abspath(__file__))
    _bundled_dir = os.path.join(os.path.dirname(_svc_dir), "assets", "fonts")
    _candidate_dirs = [
        _bundled_dir,
        "/usr/share/fonts/truetype/noto",
        "/usr/local/share/fonts/noto",
    ]
    for d in _candidate_dirs:
        if os.path.isdir(d):
            try:
                if any("sinhala" in fn.lower() and fn.lower().endswith((".ttf", ".otf")) for fn in os.listdir(d)):
                    return d
            except Exception:
                pass

    if not os.environ.get("PYTEST_CURRENT_TEST"):
        if os.path.exists("/content") and shutil.which("apt-get"):
            try:
                subprocess.run(
                    "apt-get update -qq && apt-get install -y -qq fonts-noto-core fontconfig p7zip-full unrar && fc-cache -f",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=90,
                )
                if os.path.isdir("/usr/share/fonts/truetype/noto"):
                    return "/usr/share/fonts/truetype/noto"
            except Exception as apt_err:
                log.debug("[VideoService] Colab fonts-noto-core install note: %s", apt_err)

    return _bundled_dir if os.path.isdir(_bundled_dir) else None


def prepare_clean_srt_for_burn(src_srt_path: str, dest_srt_path: str) -> bool:
    """
    Normalize a subtitle .srt file for FFmpeg libass hard-burning:
    - Decodes UTF-8-SIG (strips BOM \\ufeff), UTF-16, or CP1252
    - Converts CRLF to LF
    - Strips malformed HTML font tags while preserving Sinhala Unicode (U+0D80..U+0DFF)
    """
    if not src_srt_path or not os.path.exists(src_srt_path):
        return False
    try:
        raw = open(src_srt_path, "rb").read()
        text = None
        for enc in ("utf-8-sig", "utf-16", "utf-8", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")
        text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        with open(dest_srt_path, "w", encoding="utf-8", newline="\n") as out_f:
            out_f.write(text)
        return os.path.exists(dest_srt_path) and os.path.getsize(dest_srt_path) > 16
    except Exception as prep_err:
        log.debug("[VideoService] Clean SRT prep fallback copy: %s", prep_err)
        try:
            shutil.copyfile(src_srt_path, dest_srt_path)
            return os.path.exists(dest_srt_path)
        except Exception:
            return False


def srt_to_ass_sinhala_shaped(src_srt_path: str, dest_ass_path: Optional[str] = None) -> Optional[str]:
    """
    Convert an .srt file to Advanced SubStation Alpha (.ass) format with OpenType HarfBuzz
    text shaping and NFC normalization, preserving Zero-Width Joiner (ZWJ U+200D) and
    Zero-Width Non-Joiner (ZWNJ U+200C).
    Fixes broken Sinhala ligatures (kombuwa, yansaya, rakaransaya, rephaya).
    """
    import unicodedata

    if not src_srt_path or not os.path.exists(src_srt_path):
        return None

    if not dest_ass_path:
        dest_ass_path = os.path.splitext(src_srt_path)[0] + ".ass"

    try:
        raw = open(src_srt_path, "rb").read()
        text = None
        for enc in ("utf-8-sig", "utf-16", "utf-8", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")

        text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        # Normalize Unicode NFC while strictly preserving ZWJ (\u200d) and ZWNJ (\u200c)
        text = unicodedata.normalize("NFC", text)

        def _srt_to_ass_ts(ts: str) -> str:
            ts = ts.strip().replace(",", ".")
            m = re.match(r"(\d+):(\d+):(\d+)(?:\.(\d+))?", ts)
            if not m:
                return "0:00:00.00"
            h, mi, s, ms = m.groups()
            ms_val = (ms or "0")[:2].ljust(2, "0")
            return f"{int(h)}:{mi}:{s}.{ms_val}"

        ass_header = (
            "[Script Info]\n"
            "Title: FilmSub Sinhala Subtitle\n"
            "ScriptType: v4.00+\n"
            "WrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n"
            "YCbCr Matrix: TV.709\n"
            "PlayResX: 1920\n"
            "PlayResY: 1080\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Noto Sans Sinhala,44,&H0000FFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2.2,1.2,2,30,30,42,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        dialogues = []
        blocks = re.split(r"\n\s*\n", text.strip())
        for blk in blocks:
            lines = [ln.strip() for ln in blk.strip().split("\n") if ln.strip()]
            if not lines:
                continue
            ts_idx = -1
            for idx, ln in enumerate(lines[:3]):
                if "-->" in ln:
                    ts_idx = idx
                    break
            if ts_idx == -1:
                continue
            ts_line = lines[ts_idx]
            parts = ts_line.split("-->")
            if len(parts) != 2:
                continue
            start_ts = _srt_to_ass_ts(parts[0])
            end_ts = _srt_to_ass_ts(parts[1])

            content_lines = lines[ts_idx + 1:]
            if not content_lines:
                continue
            cue_text = "\\N".join(content_lines)
            cue_text = re.sub(r"<\s*i\s*>", r"{\\i1}", cue_text, flags=re.IGNORECASE)
            cue_text = re.sub(r"<\s*/\s*i\s*>", r"{\\i0}", cue_text, flags=re.IGNORECASE)
            cue_text = re.sub(r"<\s*b\s*>", r"{\\b1}", cue_text, flags=re.IGNORECASE)
            cue_text = re.sub(r"<\s*/\s*b\s*>", r"{\\b0}", cue_text, flags=re.IGNORECASE)
            cue_text = re.sub(r"<[^>]+>", "", cue_text)
            dialogues.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{cue_text}")

        os.makedirs(os.path.dirname(os.path.abspath(dest_ass_path)) or ".", exist_ok=True)
        with open(dest_ass_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(ass_header)
            f.write("\n".join(dialogues))
            f.write("\n")

        if os.path.exists(dest_ass_path) and os.path.getsize(dest_ass_path) > 32:
            return dest_ass_path
        return None
    except Exception as exc:
        log.warning("[VideoService] srt_to_ass_sinhala_shaped error: %s", exc)
        return None


def build_subtitles_burn_filter(escaped_sub_file: str = "sub_burn_multi.srt", include_style: Optional[bool] = None) -> str:
    """
    Construct the FFmpeg libass subtitles/ass filter expression with Noto Sans Sinhala font directory
    and CineSubz / SinhalaSub cinema styling (yellow/white high-contrast text with dark outline).
    Supports both .ass (HarfBuzz OpenType shaping) and .srt subtitles.
    Properly escapes colons, backslashes, and quotes for FFmpeg filter parser cross-platform.
    """
    is_ass = escaped_sub_file.lower().endswith(".ass")
    # Convert path to forward slashes and escape colon for FFmpeg filter syntax (e.g. C\: -> C\:)
    safe_sub = os.path.abspath(escaped_sub_file).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    font_dir = ensure_sinhala_font_dir()
    fdir_opt = ""
    if font_dir:
        safe_fdir = os.path.abspath(font_dir).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        fdir_opt = f":fontsdir='{safe_fdir}'"

    if is_ass:
        return f"ass=filename='{safe_sub}'{fdir_opt}"

    base = f"subtitles=filename='{safe_sub}'"
    if include_style is None:
        include_style = bool(os.name != "nt" and not os.environ.get("PYTEST_CURRENT_TEST"))
    if not include_style:
        return base

    style_str = (
        "FontName=Noto Sans Sinhala,FontSize=18,Bold=1,"
        "PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BackColour=&H64000000,"
        "BorderStyle=1,Outline=1.8,Shadow=1.2,MarginV=24,Alignment=2"
    )
    return f"{base}:charenc=UTF-8{fdir_opt}:force_style='{style_str}'"


async def burn_subtitles_to_video(
    video_path: str,
    sub_path: Optional[str],
    output_path: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> bool:
    """
    Burn Sinhala subtitles directly into video frames with libass / HarfBuzz OpenType shaping
    and Noto Sans Sinhala font.
    If sub_path is None or missing, executes instant stream copy remux.
    Falls back gracefully to stream_copy_subtitles if burning encounters an error.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path):
        log.error("[VideoService] burn_subtitles_to_video: ffmpeg or video missing: %s", video_path)
        return False

    if not sub_path or not os.path.exists(sub_path) or os.path.getsize(sub_path) < 16:
        log.info("[VideoService] No subtitle to burn; delegating to stream_copy_subtitles.")
        return await stream_copy_subtitles(video_path, sub_path, output_path, disposition="default")

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    try:
        clean_srt = os.path.join(out_dir, f"sub_burn_{os.path.splitext(os.path.basename(video_path))[0]}.srt")
        working_sub = sub_path
        if working_sub.lower().endswith(".vtt"):
            try:
                from services.subtitle_service import vtt_to_srt
                working_sub = vtt_to_srt(working_sub, clean_srt)
            except Exception as e_vtt:
                log.debug("[VideoService] VTT->SRT note in burn: %s", e_vtt)

        if not prepare_clean_srt_for_burn(working_sub, clean_srt):
            clean_srt = working_sub

        ass_path = os.path.splitext(clean_srt)[0] + ".ass"
        shaped_ass = srt_to_ass_sinhala_shaped(clean_srt, ass_path)
        sub_to_use = shaped_ass if (shaped_ass and os.path.exists(shaped_ass)) else clean_srt

        burn_vf = build_subtitles_burn_filter(sub_to_use)
        hw_enc = detect_hw_encoder(ffmpeg_bin)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-threads", "0",
        ]
        if hw_enc == "h264_nvenc":
            cmd.extend(["-hwaccel", "auto"])
        cmd.extend(["-i", os.path.abspath(video_path)])
        cmd.extend(["-vf", burn_vf])

        if hw_enc == "h264_nvenc":
            cmd.extend([
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-rc", "vbr",
                "-cq", "23",
                "-maxrate", "3000k",
                "-bufsize", "6000k",
                "-pix_fmt", "yuv420p",
            ])
        else:
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-tune", "fastdecode",
                "-threads", "0",
                "-pix_fmt", "yuv420p",
            ])

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-af", "aresample=async=1",
            "-sn",
            "-max_muxing_queue_size", "9999",
            "-movflags", "+faststart",
            os.path.abspath(output_path),
        ])

        log.info("[VideoService] Executing subtitle burn: %s + %s -> %s", os.path.basename(video_path), os.path.basename(sub_to_use), os.path.basename(output_path))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=600.0)
        except asyncio.TimeoutError:
            log.warning("[VideoService] Subtitle burn timed out after 600s: %s", video_path)
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return await stream_copy_subtitles(video_path, sub_path, output_path, disposition="default")
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise

        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            log.info("[VideoService] Subtitle burn succeeded: %s (%d bytes)", output_path, os.path.getsize(output_path))
            return True
        else:
            err_msg = stderr_bytes.decode("utf-8", errors="replace")[-300:] if stderr_bytes else ""
            log.warning("[VideoService] Subtitle burn failed (exit=%s, stderr=%s). Falling back to stream_copy_subtitles.", proc.returncode, err_msg)
    except Exception as exc:
        log.warning("[VideoService] burn_subtitles_to_video exception: %s. Falling back to stream_copy_subtitles.", exc)

    return await stream_copy_subtitles(video_path, sub_path, output_path, disposition="default")


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
    Leverage 12GB RAM (/dev/shm) or /content high-capacity workspace and all CPU/GPU cores
    (-threads 0 -preset ultrafast) to generate multi-quality MP4 streams/downloads (1080p, 720p, 480p, 360p)
    in a single FFmpeg pass with Sinhala subtitles (burn-in + mov_text soft-sub) and +faststart.
    Returns a dict mapping quality label (e.g. '720p') -> generated local file path.
    """
    if base_stem:
        slug = base_stem
    if target_qualities:
        qualities = target_qualities
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(input_path):
        log.error("[VideoService] generate_multi_quality_variants_ram aborted: ffmpeg_bin=%s, input_exists=%s (%s)",
                  ffmpeg_bin, os.path.exists(input_path) if input_path else False, input_path)
        return {}

    abs_out_dir = os.path.abspath(output_dir)
    # Guard against /dev/shm running out of space mid-encode when input_path is already occupying RAM disk
    if abs_out_dir.startswith("/dev/shm") and os.path.isdir("/content") and os.access("/content", os.W_OK):
        try:
            free_shm = shutil.disk_usage(abs_out_dir).free
            if free_shm < int(2.2 * 1024 * 1024 * 1024):
                alt_dir = os.path.join("/content", os.path.basename(abs_out_dir) + "_variants")
                os.makedirs(alt_dir, exist_ok=True)
                log.info(
                    "[VideoService] /dev/shm free space low (%.2f GB) -> redirecting variant output to %s",
                    free_shm / (1024 ** 3), alt_dir,
                )
                abs_out_dir = alt_dir
        except Exception:
            pass

    os.makedirs(abs_out_dir, exist_ok=True)
    duration = get_video_duration(input_path, ffmpeg_bin)
    input_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
    effective_duration = duration if duration > 0 else max(1200.0, (input_size / (1024 * 1024)) * 2.5)
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
        if not prepare_clean_srt_for_burn(sub_path, local_burn_srt):
            has_sub = False
        else:
            local_burn_ass = os.path.join(abs_out_dir, "sub_burn_multi.ass")
            try:
                srt_to_ass_sinhala_shaped(local_burn_srt, local_burn_ass)
            except Exception as ass_err:
                log.debug("[VideoService] ASS shaping note: %s", ass_err)
            ensure_sinhala_font_dir()

    # CineSubz / SinhalaSub WebRip reference bitrate & resolution profiles per quality tier
    profiles = {
        "1080p": {"height": 1080, "crf": "23", "maxrate": "2600k", "bufsize": "5200k", "abitrate": "160k"},
        "720p":  {"height": 720,  "crf": "24", "maxrate": "1400k", "bufsize": "2800k", "abitrate": "128k"},
        "480p":  {"height": 480,  "crf": "25", "maxrate": "800k",  "bufsize": "1600k", "abitrate": "96k"},
        "360p":  {"height": 360,  "crf": "26", "maxrate": "500k",  "bufsize": "1000k", "abitrate": "64k"},
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
    elif (src_w >= 1000 or src_h >= 540):
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

    # If no subtitle hard-burn is requested and source matches target resolution, use instant stream-copy shortcut.
    # When has_sub is True, all requested qualities go through the Single-Decode libass Hard-Burn + split=N engine!
    if not has_sub and is_source_1080p and "1080p" in target_q_list:
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

    # If source is already ~720p and no hard-burn is needed, do not re-encode 720p if copy succeeds.
    if not has_sub and is_source_720p and "720p" in target_q_list:
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

    # If source is already ~480p or smaller and no hard-burn is needed, do not re-encode 480p if copy succeeds.
    if not has_sub and (is_source_480p or (src_h > 0 and src_h <= 500)) and "480p" in target_q_list:
        direct_480_path = os.path.join(abs_out_dir, f"{slug}-480p.mp4")
        if os.path.abspath(input_path) == os.path.abspath(direct_480_path):
            valid_outputs["480p"] = direct_480_path
            target_q_list = [q for q in target_q_list if q != "480p"]
        else:
            try:
                ok_copy = await stream_copy_subtitles(input_path, sub_path, direct_480_path, disposition="default")
                if not ok_copy:
                    ok_copy = await ensure_web_streamable(input_path, direct_480_path, sub_path=sub_path)
                if not ok_copy and os.path.exists(input_path):
                    shutil.copyfile(input_path, direct_480_path)
                    ok_copy = True
                if ok_copy and os.path.exists(direct_480_path) and os.path.getsize(direct_480_path) >= min_valid_size:
                    valid_outputs["480p"] = direct_480_path
                    target_q_list = [q for q in target_q_list if q != "480p"]
                    log.info("[VideoService] Source is already ~480p (%dx%d). Instant 480p copy applied in seconds: %s", src_w, src_h, direct_480_path)
            except Exception as e480:
                log.debug("[VideoService] Direct 480p copy note: %s", e480)

    # If source is already ~360p and no hard-burn is needed, do not re-encode 360p if copy succeeds.
    if not has_sub and is_source_360p and "360p" in target_q_list:
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
        escaped_sub_file = "sub_burn_multi.ass" if os.path.exists(os.path.join(abs_out_dir, "sub_burn_multi.ass")) else "sub_burn_multi.srt"
        sub_burn_expr = build_subtitles_burn_filter(escaped_sub_file)
        if num_q == 1:
            q0 = target_q_list[0]
            h0 = profiles[q0]["height"]
            if burn_subs:
                fc = f"[0:v:0]{sub_burn_expr},scale=w=-2:h={h0}:flags=fast_bilinear[v_{q0}]"
            else:
                fc = f"[0:v:0]scale=w=-2:h={h0}:flags=fast_bilinear[v_{q0}]"
        else:
            split_labels = "".join(f"[sp_{q}]" for q in target_q_list)
            split_head = (
                f"[0:v:0]{sub_burn_expr},split={num_q}{split_labels}"
                if burn_subs
                else f"[0:v:0]split={num_q}{split_labels}"
            )
            scale_branches = ";".join(
                f"[sp_{q}]scale=w=-2:h={profiles[q]['height']}:flags=fast_bilinear[v_{q}]"
                for q in target_q_list
            )
            fc = f"{split_head};{scale_branches}"

        c: list[str] = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-threads", "0",
        ]
        if use_hw == "h264_nvenc":
            c.extend(["-hwaccel", "auto"])
        c.extend(["-i", os.path.abspath(input_path)])
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
                    "-preset", "fast",
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
                    "-crf", prof["crf"],
                    "-maxrate", prof["maxrate"],
                    "-bufsize", prof["bufsize"],
                    "-threads", "0",
                ])
            c.extend([
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", prof["abitrate"],
                "-ac", "2",
                "-movflags", "+faststart",
                out_file,
            ])
        return c, paths

    # Build ordered attempt strategies:
    # 1. Burn + soft-sub (works on BtbN static FFmpeg with libass)
    # 2. Soft-sub mov_text without burn (works on imageio_ffmpeg and builds without libass)
    # 3. Clean video + audio without subtitle (guaranteed fallback if subtitle file is malformed)
    strategies: list[tuple[str, bool, bool]] = []
    if hw_enc == "h264_nvenc":
        if has_sub:
            strategies.append(("h264_nvenc", True, True))
            strategies.append(("h264_nvenc", False, True))
        strategies.append(("h264_nvenc", False, False))
        if has_sub:
            strategies.append(("libx264", False, True))
        strategies.append(("libx264", False, False))
    else:
        if has_sub:
            strategies.append(("libx264", True, True))
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
                stderr_stream = getattr(proc, "stderr", None)
                if stderr_stream is None:
                    return
                while True:
                    if hasattr(stderr_stream, "read"):
                        chunk = await stderr_stream.read(4096)
                    elif hasattr(stderr_stream, "readline"):
                        chunk = await stderr_stream.readline()
                    else:
                        break
                    if not chunk:
                        break
                    decoded = chunk.decode("utf-8", errors="replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
                    for line in decoded.splitlines(keepends=True):
                        stderr_tail.append(line)
                    matches = time_pattern.findall(decoded)
                    if matches and effective_duration > 0:
                        h, mm, ss = matches[-1]
                        cur_secs = int(h) * 3600 + int(mm) * 60 + float(ss)
                        pct = min(99.0, (cur_secs / effective_duration) * 100.0)
                        if pct - last_pct >= 2.5:
                            last_pct = pct
                            if progress_callback:
                                try:
                                    if asyncio.iscoroutinefunction(progress_callback):
                                        await progress_callback(pct, f"{pct:.1f}%")
                                    else:
                                        progress_callback(pct, f"{pct:.1f}%")
                                except Exception:
                                    pass

            timeout_val = (
                max(360.0, min(2400.0, effective_duration * 0.8))
                if enc_choice == "h264_nvenc"
                else max(900.0, min(5400.0, effective_duration * 2.2))
            )
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
                _rescue_min = max(512 * 1024, min_valid_size)
                rescued = []
                for q, p in out_paths.items():
                    if os.path.exists(p) and os.path.getsize(p) >= _rescue_min and q not in valid_outputs:
                        valid_outputs[q] = p
                        rescued.append(q)
                    elif os.path.exists(p) and q not in valid_outputs:
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
                if progress_callback:
                    try:
                        if asyncio.iscoroutinefunction(progress_callback):
                            await progress_callback(100.0, "100.0%")
                        else:
                            progress_callback(100.0, "100.0%")
                    except Exception:
                        pass
                log.info(
                    "[VideoService] Multi-quality RAM generation complete (attempt %d, encoder=%s, burn=%s): %s",
                    attempt_idx + 1, enc_choice, burn_choice, list(valid_outputs.keys()),
                )
                return valid_outputs

            # If all newly targeted qualities were produced in this attempt, return
            if proc and proc.returncode == 0 and any(q in valid_outputs for q in out_paths.keys()):
                if progress_callback:
                    try:
                        if asyncio.iscoroutinefunction(progress_callback):
                            await progress_callback(100.0, "100.0%")
                        else:
                            progress_callback(100.0, "100.0%")
                    except Exception:
                        pass
                log.info(
                    "[VideoService] Multi-quality variants successfully produced: %s",
                    list(valid_outputs.keys()),
                )
                return valid_outputs

            log.warning(
                "[VideoService] Multi-quality attempt %d (encoder=%s, burn=%s) produced %d/%d variants; retrying fallback...",
                attempt_idx + 1, enc_choice, burn_choice, len(valid_outputs), len(target_q_list),
            )

        # Guaranteed Fallback: If any requested quality is still missing (e.g. 480p),
        # run a direct, bulletproof single-pass transcode with stereo AAC and soft-subs.
        for q in target_q_list:
            if q in valid_outputs:
                continue
            prof = profiles.get(q)
            if not prof:
                continue
            fallback_out = os.path.join(abs_out_dir, f"{slug}-{q}.mp4")
            log.info("[VideoService] Running guaranteed single-pass fallback encode for variant %s: %s", q, fallback_out)
            h_val = prof["height"]
            fb_cmd = [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-threads", "0",
            ]
            if hw_enc == "h264_nvenc":
                fb_cmd.extend(["-hwaccel", "auto"])
            fb_cmd.extend(["-i", os.path.abspath(input_path)])
            has_fb_sub = bool(sub_path and os.path.exists(sub_path) and os.path.getsize(sub_path) > 16)
            fb_effective_sub = sub_path
            if has_fb_sub:
                if sub_path.lower().endswith(".vtt"):
                    try:
                        from services.subtitle_service import vtt_to_srt
                        conv_srt = os.path.join(abs_out_dir, f"multi_fb_sub_{q}.srt")
                        fb_effective_sub = vtt_to_srt(sub_path, conv_srt)
                    except Exception:
                        fb_effective_sub = sub_path
                if fb_effective_sub and os.path.exists(fb_effective_sub) and os.path.getsize(fb_effective_sub) > 16:
                    fb_cmd.extend(["-i", os.path.abspath(fb_effective_sub)])
                else:
                    has_fb_sub = False

            fb_cmd.extend([
                "-vf", f"scale=-2:{h_val}",
                "-map", "0:v:0",
                "-map", "0:a:0?",
            ])
            if has_fb_sub and fb_effective_sub:
                fb_cmd.extend([
                    "-map", "1:0",
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=sin",
                    "-metadata:s:s:0", "title=Sinhala (සිංහල)",
                    "-disposition:s:0", "default",
                ])
            if hw_enc == "h264_nvenc":
                fb_cmd.extend([
                    "-c:v", "h264_nvenc",
                    "-preset", "fast",
                    "-rc", "vbr",
                    "-cq", prof["crf"],
                    "-maxrate", prof["maxrate"],
                    "-bufsize", prof["bufsize"],
                ])
            else:
                fb_cmd.extend([
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "28",
                    "-threads", "0",
                ])
            fb_cmd.extend([
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", prof["abitrate"],
                "-ac", "2",
                "-movflags", "+faststart",
                fallback_out,
            ])
            try:
                fb_proc = await asyncio.create_subprocess_exec(
                    *fb_cmd,
                    cwd=abs_out_dir,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(fb_proc.wait(), timeout=max(300.0, min(1800.0, effective_duration * 1.5)))
                if fb_proc.returncode == 0 and os.path.exists(fallback_out) and os.path.getsize(fallback_out) >= min_valid_size:
                    valid_outputs[q] = fallback_out
                    log.info("[VideoService] Guaranteed single-pass fallback succeeded for %s: %s", q, fallback_out)
                else:
                    log.warning("[VideoService] Fallback encode for %s returned code %s; trying simple transcode...", q, getattr(fb_proc, "returncode", "N/A"))
                    simple_cmd = [
                        ffmpeg_bin, "-y", "-hide_banner", "-threads", "0",
                        "-i", os.path.abspath(input_path),
                        "-vf", f"scale=-2:{h_val}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-threads", "0", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", prof["abitrate"], "-ac", "2",
                        "-movflags", "+faststart",
                        fallback_out,
                    ]
                    p_simple = await asyncio.create_subprocess_exec(
                        *simple_cmd, cwd=abs_out_dir,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(p_simple.wait(), timeout=max(300.0, min(1800.0, effective_duration * 1.5)))
                    if p_simple.returncode == 0 and os.path.exists(fallback_out) and os.path.getsize(fallback_out) >= min_valid_size:
                        valid_outputs[q] = fallback_out
                        log.info("[VideoService] Simple single-pass fallback succeeded for %s: %s", q, fallback_out)
                    elif os.path.exists(fallback_out) and q not in valid_outputs:
                        try:
                            os.remove(fallback_out)
                        except Exception:
                            pass
            except Exception as fb_err:
                log.warning("[VideoService] Fallback encode for %s failed: %s", q, fb_err)
                if os.path.exists(fallback_out) and q not in valid_outputs:
                    try:
                        os.remove(fallback_out)
                    except Exception:
                        pass

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


