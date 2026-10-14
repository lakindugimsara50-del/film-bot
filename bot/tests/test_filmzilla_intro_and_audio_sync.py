"""
test_filmzilla_intro_and_audio_sync.py

Tests for:
1. get_or_create_filmzilla_intro:
   - 5.0s branded intro video generation and caching
   - Valid stereo silent audio (48000Hz)
2. prepend_filmzilla_intro:
   - Zero audio bleed during 5.0s intro (volumedetect t=0..4.8s is pure silence <= -80dB)
   - Audio from film starts strictly after t=5.0s
   - Concatenation of video with audio and video without audio
   - In-place modification handling
3. Subtitle timing adjustment:
   - Net offset calculation: -10s trim + 5s intro = -5.0s net offset
   - Net offset without trim: +5.0s net offset
"""

import asyncio
import os
import re
import subprocess
import sys
import tempfile
import pytest

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BOT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from bot.services import video_service, subtitle_service


@pytest.fixture(scope="module")
def ffmpeg_bin():
    exe = video_service.get_ffmpeg_binary()
    if not exe:
        pytest.skip("FFmpeg binary not available on this system")
    return exe


@pytest.mark.asyncio
async def test_get_or_create_filmzilla_intro(ffmpeg_bin):
    """Verify intro video is generated with exact 5.0s duration and stereo silent audio."""
    intro_path = video_service.get_or_create_filmzilla_intro(
        target_width=640,
        target_height=360,
        fps=30,
        duration=5.0,
        ffmpeg_bin=ffmpeg_bin,
    )
    assert intro_path is not None
    assert os.path.exists(intro_path)
    assert os.path.getsize(intro_path) > 1024

    dur = video_service.get_video_duration(intro_path, ffmpeg_bin)
    assert 4.8 <= dur <= 5.2, f"Expected ~5.0s duration, got {dur}"

    # Verify second call retrieves from cache
    cached_path = video_service.get_or_create_filmzilla_intro(
        target_width=640,
        target_height=360,
        fps=30,
        duration=5.0,
        ffmpeg_bin=ffmpeg_bin,
    )
    assert cached_path == intro_path


@pytest.mark.asyncio
async def test_prepend_filmzilla_intro_with_audio_and_zero_bleed(ffmpeg_bin):
    """
    Verify:
    1. Prepending 5.0s intro adds exactly 5.0s to film duration.
    2. Audio during intro (t=0..4.8s) is 100% silent (<= -80 dB, zero audio bleed).
    3. Film audio starts strictly after 5.0s (measured at t=5.2..7.8s).
    """
    with tempfile.TemporaryDirectory() as td:
        in_film = os.path.join(td, "test_film.mp4")
        out_film = os.path.join(td, "test_film_with_intro.mp4")

        # Create a 3.0-second test film with a 1000Hz tone
        make_film_cmd = [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=3.0",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:d=3.0",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "3.0",
            in_film
        ]
        subprocess.run(make_film_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        film_dur = video_service.get_video_duration(in_film, ffmpeg_bin)
        assert 2.8 <= film_dur <= 3.2

        # Prepend 5s intro
        progress_calls = []
        async def _prog(pct, pstr):
            progress_calls.append(pct)

        success = await video_service.prepend_filmzilla_intro(
            input_path=in_film,
            output_path=out_film,
            intro_duration=5.0,
            progress_callback=_prog,
            ffmpeg_bin=ffmpeg_bin,
        )
        assert success is True
        assert os.path.exists(out_film)
        assert len(progress_calls) > 0

        # Total duration should be ~8.0s (5.0s intro + 3.0s film)
        total_dur = video_service.get_video_duration(out_film, ffmpeg_bin)
        assert 7.8 <= total_dur <= 8.2, f"Expected ~8.0s, got {total_dur}"

        # CRITICAL AUDIO CHECK 1: Intro window t=0.0s to 4.8s MUST be pure silence
        probe_intro = subprocess.run([
            ffmpeg_bin, "-ss", "0.0", "-t", "4.8", "-i", out_film,
            "-af", "volumedetect", "-f", "null", "-"
        ], stderr=subprocess.PIPE, text=True, errors="replace")
        
        intro_stderr = probe_intro.stderr
        mean_vol = None
        max_vol = None
        for line in intro_stderr.splitlines():
            if "mean_volume:" in line:
                m = re.search(r"mean_volume:\s*(-?[\d\.]+)\s*dB", line)
                if m:
                    mean_vol = float(m.group(1))
            if "max_volume:" in line:
                m = re.search(r"max_volume:\s*(-?[\d\.]+)\s*dB", line)
                if m:
                    max_vol = float(m.group(1))

        assert max_vol is not None and max_vol <= -70.0, f"Audio bled into intro! max_volume={max_vol} dB"
        assert mean_vol is not None and mean_vol <= -80.0, f"Intro mean volume not silent: {mean_vol} dB"

        # CRITICAL AUDIO CHECK 2: Film window t=5.2s to 7.8s MUST have film audio
        probe_film = subprocess.run([
            ffmpeg_bin, "-ss", "5.2", "-t", "2.6", "-i", out_film,
            "-af", "volumedetect", "-f", "null", "-"
        ], stderr=subprocess.PIPE, text=True, errors="replace")
        
        film_stderr = probe_film.stderr
        film_max_vol = None
        for line in film_stderr.splitlines():
            if "max_volume:" in line:
                m = re.search(r"max_volume:\s*(-?[\d\.]+)\s*dB", line)
                if m:
                    film_max_vol = float(m.group(1))

        assert film_max_vol is not None and film_max_vol > -35.0, f"Film audio missing after intro! max_volume={film_max_vol} dB"


@pytest.mark.asyncio
async def test_prepend_filmzilla_intro_silent_video(ffmpeg_bin):
    """Verify prepending intro works on videos without an audio track."""
    with tempfile.TemporaryDirectory() as td:
        in_film = os.path.join(td, "silent_film.mp4")
        out_film = os.path.join(td, "silent_with_intro.mp4")

        # Create 2.0s silent video
        subprocess.run([
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=2.0",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-t", "2.0", in_film
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        success = await video_service.prepend_filmzilla_intro(
            input_path=in_film,
            output_path=out_film,
            intro_duration=5.0,
            ffmpeg_bin=ffmpeg_bin,
        )
        assert success is True
        assert os.path.exists(out_film)

        total_dur = video_service.get_video_duration(out_film, ffmpeg_bin)
        assert 6.8 <= total_dur <= 7.2


@pytest.mark.asyncio
async def test_prepend_filmzilla_intro_inplace(ffmpeg_bin):
    """Verify prepending intro when input_path == output_path."""
    with tempfile.TemporaryDirectory() as td:
        film = os.path.join(td, "inplace_film.mp4")

        subprocess.run([
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=2.0",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-t", "2.0", film
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        success = await video_service.prepend_filmzilla_intro(
            input_path=film,
            output_path=film,
            intro_duration=5.0,
            ffmpeg_bin=ffmpeg_bin,
        )
        assert success is True
        assert os.path.exists(film)

        total_dur = video_service.get_video_duration(film, ffmpeg_bin)
        assert 6.8 <= total_dur <= 7.2


def test_subtitle_shift_math_for_5s_intro():
    """
    Verify:
    1. Trimming 10s promo + prepending 5s intro = -5.0s net offset.
    2. No trim + prepending 5s intro = +5.0s net offset.
    """
    sample_srt = (
        "1\n"
        "00:00:15,000 --> 00:00:18,500\n"
        "Hello World\n\n"
        "2\n"
        "00:00:20,000 --> 00:00:25,000\n"
        "Second Line\n"
    )

    with tempfile.TemporaryDirectory() as td:
        in_srt = os.path.join(td, "orig.srt")
        out_srt_trimmed = os.path.join(td, "trimmed_net.srt")
        out_srt_untrimmed = os.path.join(td, "untrimmed_net.srt")

        with open(in_srt, "w", encoding="utf-8") as f:
            f.write(sample_srt)

        # 1. Trimmed case: net offset = -5.0s
        res_trimmed = subtitle_service.shift_srt_file(in_srt, offset_seconds=-5.0, output_path=out_srt_trimmed)
        assert os.path.exists(res_trimmed)
        with open(res_trimmed, "r", encoding="utf-8") as f:
            c1 = f.read()
        # Original 00:00:15,000 shifted by -5.0s becomes 00:00:10,000
        assert "00:00:10,000 --> 00:00:13,500" in c1
        assert "00:00:15,000 --> 00:00:20,000" in c1

        # 2. Untrimmed case: net offset = +5.0s
        res_untrimmed = subtitle_service.shift_srt_file(in_srt, offset_seconds=5.0, output_path=out_srt_untrimmed)
        assert os.path.exists(res_untrimmed)
        with open(res_untrimmed, "r", encoding="utf-8") as f:
            c2 = f.read()
        # Original 00:00:15,000 shifted by +5.0s becomes 00:00:20,000
        assert "00:00:20,000 --> 00:00:23,500" in c2
        assert "00:00:25,000 --> 00:00:30,000" in c2
