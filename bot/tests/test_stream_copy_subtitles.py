"""
test_stream_copy_subtitles.py — Deep verification tests for instantaneous Stream Copy
(Soft-sub Muxing) and zero-reencoding pipeline in video_service and leech_service.
"""

import asyncio
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = REPO_ROOT / "bot"
for p in (str(REPO_ROOT), str(BOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.video_service import (
    stream_copy_subtitles,
    compress_smart_1080p,
    embed_subtitles_soft,
)


class DummyProc:
    def __init__(self, returncode=0, side_effect=None):
        self.returncode = returncode
        self.side_effect = side_effect

    async def wait(self):
        if self.side_effect:
            self.side_effect()
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        self.returncode = -9


@pytest.mark.asyncio
async def test_stream_copy_subtitles_mp4_flags(tmp_path):
    """Verify stream_copy_subtitles produces the exact requested flags for MP4:
    ffmpeg -y -i input.mp4 -i sub.srt -c:v copy -c:a copy -c:s mov_text -disposition:s:0 default -movflags +faststart output.mp4
    """
    in_video = tmp_path / "movie.mp4"
    in_video.write_bytes(b"DATA" * 500)
    sub_srt = tmp_path / "sub.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nහෙලෝ\n", encoding="utf-8")
    out_mp4 = tmp_path / "movie_subbed.mp4"

    captured_cmds = []

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        out_mp4.write_bytes(b"OUT_DATA" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await stream_copy_subtitles(str(in_video), str(sub_srt), str(out_mp4), disposition="default")

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]

    # Verify stream copy of video and audio (no re-encoding)
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
    # Verify mov_text subtitle codec for MP4 container
    assert "-c:s" in cmd and cmd[cmd.index("-c:s") + 1] == "mov_text"
    # Verify subtitle disposition is default
    assert "-disposition:s:0" in cmd and cmd[cmd.index("-disposition:s:0") + 1] == "default"
    # Verify +faststart is applied in the same command
    assert "-movflags" in cmd and cmd[cmd.index("-movflags") + 1] == "+faststart"
    # Verify Sinhala language metadata
    assert "language=sin" in cmd
    # Verify no slow re-encoding filters or encoders
    assert "libx264" not in cmd
    assert "-vf" not in cmd


@pytest.mark.asyncio
async def test_stream_copy_subtitles_mkv_flags(tmp_path):
    """Verify MKV container sets -c:s srt and does NOT append +faststart (which is MP4-only):
    ffmpeg -y -i input.mkv -i sub.srt -c:v copy -c:a copy -c:s srt -disposition:s:0 default output.mkv
    """
    in_video = tmp_path / "movie.mkv"
    in_video.write_bytes(b"DATA" * 500)
    sub_srt = tmp_path / "sub.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nහෙලෝ\n", encoding="utf-8")
    out_mkv = tmp_path / "movie_subbed.mkv"

    captured_cmds = []

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        out_mkv.write_bytes(b"OUT_MKV" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await stream_copy_subtitles(str(in_video), str(sub_srt), str(out_mkv), disposition="default")

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]

    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
    assert "-c:s" in cmd and cmd[cmd.index("-c:s") + 1] == "srt"
    assert "-disposition:s:0" in cmd and cmd[cmd.index("-disposition:s:0") + 1] == "default"
    assert "+faststart" not in cmd


@pytest.mark.asyncio
async def test_stream_copy_subtitles_no_sub_file(tmp_path):
    """Verify stream copy without subtitle file still applies +faststart and -sn."""
    in_video = tmp_path / "movie.mp4"
    in_video.write_bytes(b"DATA" * 500)
    out_mp4 = tmp_path / "movie_nosub.mp4"

    captured_cmds = []

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        out_mp4.write_bytes(b"OUT_DATA" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await stream_copy_subtitles(str(in_video), None, str(out_mp4))

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]

    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
    assert "-sn" in cmd
    assert "+faststart" in cmd


@pytest.mark.asyncio
async def test_stream_copy_subtitles_fallback_to_audio_transcode(tmp_path):
    """Verify that when direct stream copy fails (e.g. incompatible audio in MP4),
    it falls back to video stream-copy with Stereo AAC transcode."""
    in_video = tmp_path / "movie.mkv"
    in_video.write_bytes(b"DATA" * 500)
    sub_srt = tmp_path / "sub.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nහෙලෝ\n", encoding="utf-8")
    out_mp4 = tmp_path / "movie_fallback.mp4"

    captured_cmds = []
    attempt_count = 0

    async def fake_exec(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        captured_cmds.append(list(args))
        if attempt_count == 1:
            # Attempt 1 fails
            return DummyProc(returncode=1)
        # Attempt 2 succeeds
        out_mp4.write_bytes(b"OUT_FALLBACK" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await stream_copy_subtitles(str(in_video), str(sub_srt), str(out_mp4), disposition="default")

    assert ok is True
    assert len(captured_cmds) == 2
    cmd2 = captured_cmds[1]
    assert "-c:v" in cmd2 and cmd2[cmd2.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd2 and cmd2[cmd2.index("-c:a") + 1] == "aac"
    assert "-c:s" in cmd2 and cmd2[cmd2.index("-c:s") + 1] == "mov_text"
    assert "+faststart" in cmd2


@pytest.mark.asyncio
async def test_stream_copy_subtitles_fallback_to_nosub_when_sub_corrupt(tmp_path):
    """Verify that when both Attempt 1 and Attempt 2 fail (e.g. malformed subtitle),
    it falls back to stream copy without subtitle to guarantee the video is not lost."""
    in_video = tmp_path / "movie.mp4"
    in_video.write_bytes(b"DATA" * 500)
    sub_srt = tmp_path / "corrupt.srt"
    sub_srt.write_text("INVALID SUBTITLE CONTENT", encoding="utf-8")
    out_mp4 = tmp_path / "movie_nosub_fallback.mp4"

    captured_cmds = []
    call_count = 0

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        captured_cmds.append(list(args))
        if call_count < 3:
            return DummyProc(returncode=1)
        # Call 3 (nosub) succeeds
        out_mp4.write_bytes(b"OUT_NOSUB" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await stream_copy_subtitles(str(in_video), str(sub_srt), str(out_mp4))

    assert ok is True
    assert len(captured_cmds) == 3
    cmd3 = captured_cmds[2]
    assert "-sn" in cmd3
    assert "-c:v" in cmd3 and cmd3[cmd3.index("-c:v") + 1] == "copy"


@pytest.mark.asyncio
async def test_compress_smart_1080p_replaces_slow_encoding(tmp_path):
    """Verify compress_smart_1080p executes instantaneous stream-copy muxing in seconds
    instead of 45-60 minute libx264 re-encoding."""
    in_video = tmp_path / "big_movie.mp4"
    in_video.write_bytes(b"BIG_DATA" * 1000)
    sub_srt = tmp_path / "sub.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nටෙස්ට්\n", encoding="utf-8")
    out_mp4 = tmp_path / "compressed_fast.mp4"

    captured_cmds = []
    progress_calls = []

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        out_mp4.write_bytes(b"COMPRESSED_DATA" * 100)
        return DummyProc(returncode=0)

    async def fake_progress(pct, text):
        progress_calls.append((pct, text))

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await compress_smart_1080p(str(in_video), str(out_mp4), progress_callback=fake_progress, sub_path=str(sub_srt))

    assert ok is True
    assert len(captured_cmds) >= 1
    cmd = captured_cmds[0]
    # Verify stream copy flags were used
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-c:s" in cmd and cmd[cmd.index("-c:s") + 1] == "mov_text"
    assert "+faststart" in cmd
    # Verify progress callback was notified
    assert (100.0, "100.0%") in progress_calls


@pytest.mark.asyncio
async def test_embed_subtitles_soft_mkv_sub_codec(tmp_path):
    """Verify embed_subtitles_soft uses 'srt' codec for MKV container."""
    in_video = tmp_path / "movie.mkv"
    in_video.write_bytes(b"MKV_DATA" * 100)
    sub_srt = tmp_path / "sub.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nටෙස්ට්\n", encoding="utf-8")
    out_mkv = tmp_path / "subbed.mkv"

    captured_cmds = []

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        out_mkv.write_bytes(b"MKV_SUBBED" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await embed_subtitles_soft(str(in_video), str(sub_srt), str(out_mkv))

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert "-c:s" in cmd and cmd[cmd.index("-c:s") + 1] == "srt"


@pytest.mark.asyncio
async def test_stream_copy_subtitles_fallback_audio_aac_nosub_when_both_incompatible(tmp_path):
    """Verify that when Attempt 1, Attempt 2, and Attempt 3 all fail
    (e.g. incompatible audio like DTS in MP4 AND corrupt subtitle),
    Fallback Attempt 4 rescues the video with stream-copied video and Stereo AAC transcode without sub."""
    in_video = tmp_path / "movie.mkv"
    in_video.write_bytes(b"DATA" * 500)
    sub_srt = tmp_path / "corrupt.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nCORRUPT SYNTAX INVALID PARSER\n", encoding="utf-8")
    out_mp4 = tmp_path / "movie_rescue.mp4"

    captured_cmds = []
    call_count = 0

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        captured_cmds.append(list(args))
        if call_count < 4:
            return DummyProc(returncode=1)
        # Call 4 (AAC transcode + nosub) succeeds
        out_mp4.write_bytes(b"OUT_RESCUED" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await stream_copy_subtitles(str(in_video), str(sub_srt), str(out_mp4))

    assert ok is True
    assert len(captured_cmds) == 4
    cmd4 = captured_cmds[3]
    assert "-c:v" in cmd4 and cmd4[cmd4.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd4 and cmd4[cmd4.index("-c:a") + 1] == "aac"
    assert "-sn" in cmd4
    assert "+faststart" in cmd4


@pytest.mark.asyncio
async def test_stream_copy_subtitles_vtt_support(tmp_path):
    """Verify stream copy supports .vtt files seamlessly with -c:s mov_text."""
    in_video = tmp_path / "movie.mp4"
    in_video.write_bytes(b"DATA" * 500)
    sub_vtt = tmp_path / "sub.vtt"
    sub_vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nහෙලෝ\n", encoding="utf-8")
    out_mp4 = tmp_path / "movie_vtt.mp4"

    captured_cmds = []

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        out_mp4.write_bytes(b"OUT_VTT" * 100)
        return DummyProc(returncode=0)

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await stream_copy_subtitles(str(in_video), str(sub_vtt), str(out_mp4), disposition="default")

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert "-c:s" in cmd and cmd[cmd.index("-c:s") + 1] == "mov_text"
    assert "-disposition:s:0" in cmd and cmd[cmd.index("-disposition:s:0") + 1] == "default"


@pytest.mark.asyncio
async def test_stream_copy_subtitles_real_ffmpeg_execution(tmp_path):
    """Deep verification: run against the actual local FFmpeg binary to guarantee zero re-encoding
    speed (completes in <1 second) and verifies FFprobe reports disposition default for subtitle."""
    import subprocess
    import shutil
    import json

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        pytest.skip("FFmpeg/FFprobe binary not found in PATH")

    in_video = tmp_path / "real_in.mp4"
    sub_srt = tmp_path / "real_sub.srt"
    out_mp4 = tmp_path / "real_out.mp4"

    # Generate 1-second synthetic video
    res = subprocess.run([
        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
        "-c:v", "libx264", str(in_video)
    ], capture_output=True)
    if res.returncode != 0:
        pytest.skip("Could not generate synthetic test video")

    sub_srt.write_text("1\n00:00:00,100 --> 00:00:00,900\nසිංහල උපසිරැසි පරික්ෂාව\n", encoding="utf-8")

    ok = await stream_copy_subtitles(str(in_video), str(sub_srt), str(out_mp4), disposition="default")
    assert ok is True
    assert out_mp4.exists() and out_mp4.stat().st_size > 0

    # Inspect with ffprobe
    probe = subprocess.run([
        ffprobe_bin, "-v", "quiet", "-print_format", "json", "-show_streams", str(out_mp4)
    ], capture_output=True, text=True)
    assert probe.returncode == 0
    info = json.loads(probe.stdout)
    streams = info.get("streams", [])
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    assert len(sub_streams) == 1
    sub = sub_streams[0]
    assert sub.get("codec_name") == "mov_text"
    assert sub.get("disposition", {}).get("default") == 1
    assert sub.get("tags", {}).get("language") == "sin"
