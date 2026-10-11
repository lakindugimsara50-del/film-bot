import os
import sys
import tempfile
import subprocess
import pytest

sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.video_service import trim_video_start, get_ffmpeg_binary
from services.subtitle_service import shift_srt_file


@pytest.mark.asyncio
async def test_trim_video_start_execution():
    """Verify that trim_video_start trims exactly 10s from video without error."""
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        pytest.skip("FFmpeg not installed in test environment")

    with tempfile.TemporaryDirectory() as tmpdir:
        in_mp4 = os.path.join(tmpdir, "in_25s.mp4")
        out_mp4 = os.path.join(tmpdir, "out_15s.mp4")

        # Generate a 25-second synthetic test video
        cmd_gen = [
            ffmpeg_bin,
            "-y",
            "-f", "lavfi", "-i", "testsrc=duration=25:size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=25",
            "-c:v", "libx264", "-c:a", "aac",
            in_mp4
        ]
        subprocess.run(cmd_gen, check=True, capture_output=True)
        assert os.path.exists(in_mp4) and os.path.getsize(in_mp4) > 1024

        ok = await trim_video_start(in_mp4, out_mp4, start_seconds=10.0)
        assert ok is True
        assert os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 1024

        ffprobe_bin = ffmpeg_bin.replace("ffmpeg.EXE", "ffprobe.EXE").replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
        if os.path.exists(ffprobe_bin):
            cmd_probe = [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                out_mp4
            ]
            dur = float(subprocess.check_output(cmd_probe, text=True).strip())
            assert 14.0 <= dur <= 16.0, f"Expected ~15s duration, got {dur}"


@pytest.mark.asyncio
async def test_trim_video_start_skips_short_video():
    """Verify that trim_video_start refuses to trim videos shorter than start_seconds + 5s."""
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        pytest.skip("FFmpeg not installed in test environment")

    with tempfile.TemporaryDirectory() as tmpdir:
        short_mp4 = os.path.join(tmpdir, "short_5s.mp4")
        out_mp4 = os.path.join(tmpdir, "out_short.mp4")

        cmd_gen = [
            ffmpeg_bin,
            "-y",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-c:v", "libx264", "-c:a", "aac",
            short_mp4
        ]
        subprocess.run(cmd_gen, check=True, capture_output=True)

        ok = await trim_video_start(short_mp4, out_mp4, start_seconds=10.0)
        assert ok is False


def test_shift_srt_file():
    """Verify that shift_srt_file shifts all subtitle timestamps by -10.0 seconds and drops cues ending before 0s."""
    sample_srt = """1
00:00:01,000 --> 00:00:07,500
Site Intro / Watermark logo

2
00:00:09,000 --> 00:00:15,000
Spanning across the 10 second cut boundary

3
00:00:20,000 --> 00:00:25,000
First actual dialogue line
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        in_srt = os.path.join(tmpdir, "test.srt")
        out_srt = os.path.join(tmpdir, "test_shifted.srt")

        with open(in_srt, "w", encoding="utf-8") as f:
            f.write(sample_srt)

        res_path = shift_srt_file(in_srt, offset_seconds=-10.0, output_path=out_srt)
        assert os.path.exists(res_path)

        with open(res_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Cue 1 (ending at 7.5s) was dropped because it ends <= 0s
        assert "Site Intro / Watermark logo" not in content

        # Cue 2 starts at 00:00:00,000 and ends at 00:00:05,000
        assert "00:00:00,000 --> 00:00:05,000" in content
        assert "Spanning across the 10 second cut boundary" in content

        # Cue 3 was shifted from 20s-25s to 10s-15s
        assert "00:00:10,000 --> 00:00:15,000" in content
        assert "First actual dialogue line" in content
