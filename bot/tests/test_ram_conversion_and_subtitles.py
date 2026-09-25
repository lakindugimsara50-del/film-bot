"""
Unit and integration tests for:
1. 12GB RAM (/dev/shm) workspace allocation & single-pass MKV->MP4 conversion with Stereo AAC + Sinhala mov_text subtitles
2. Multi-quality (Auto, 1080p, 720p, 480p, 360p) generation & website schema validation
3. Sinhala subtitle acquisition, SRT<->VTT conversion, and soft subtitle embedding
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = REPO_ROOT / "bot"
for p in (str(REPO_ROOT), str(BOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.subtitle_service import (
    srt_to_vtt,
    vtt_to_srt,
    generate_fallback_sinhala_srt,
    auto_acquire_sinhala_subtitle,
)
from services.video_service import (
    get_optimal_work_dir,
    ensure_web_streamable,
    embed_subtitles_soft,
    generate_multi_quality_variants_ram,
)


def test_srt_vtt_roundtrip_and_edge_cases(tmp_path):
    # Missing file edge case
    with pytest.raises(FileNotFoundError):
        vtt_to_srt(str(tmp_path / "nonexistent.vtt"))

    # Standard Sinhala SRT -> VTT -> SRT
    sample_srt = (
        "1\n"
        "00:00:01,500 --> 00:00:05,250\n"
        "🎬 සිංහල උපසිරැසි පරීක්ෂාව\n\n"
        "2\n"
        "00:00:06,000 --> 00:00:10,000\n"
        "FilmSub.lk 1080p / 720p / 480p / 360p\n"
    )
    srt_file = tmp_path / "test_sub.srt"
    srt_file.write_text(sample_srt, encoding="utf-8")

    vtt_path = srt_to_vtt(str(srt_file))
    vtt_content = Path(vtt_path).read_text(encoding="utf-8")
    assert vtt_content.startswith("WEBVTT")
    assert "00:00:01.500 --> 00:00:05.250" in vtt_content
    assert "සිංහල උපසිරැසි පරීක්ෂාව" in vtt_content

    recovered_srt_path = vtt_to_srt(vtt_path)
    recovered_srt = Path(recovered_srt_path).read_text(encoding="utf-8")
    assert "00:00:01,500 --> 00:00:05,250" in recovered_srt
    assert "සිංහල උපසිරැසි පරීක්ෂාව" in recovered_srt


def test_generate_fallback_sinhala_srt_contains_title_and_year():
    srt = generate_fallback_sinhala_srt("Avatar: Fire and Ash", 2025)
    assert "Avatar: Fire and Ash (2025)" in srt
    assert "00:00:01,000 --> 00:00:08,000" in srt
    assert "සිංහල උපසිරැසි" in srt


def test_get_optimal_work_dir_allocates_and_cleans_up():
    work_dir = get_optimal_work_dir(min_free_gb=0.01, prefix="test_ram_")
    try:
        assert os.path.isdir(work_dir)
        assert "test_ram_" in os.path.basename(work_dir)
    finally:
        if os.path.isdir(work_dir):
            os.rmdir(work_dir)


@pytest.mark.asyncio
async def test_ensure_web_streamable_merges_subtitle_and_transcodes_ac3(tmp_path):
    in_video = tmp_path / "movie.mkv"
    in_video.write_bytes(b"fake_mkv_content" * 100)
    sub_file = tmp_path / "sinhala.srt"
    sub_file.write_text(generate_fallback_sinhala_srt("Test Movie", 2026), encoding="utf-8")
    out_video = tmp_path / "movie_out.mp4"

    captured_cmds = []

    class FakeProc:
        returncode = 0
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        # Write > 600 KB so size check (> 512 KB) passes
        out_path = Path(args[-1])
        out_path.write_bytes(b"X" * (600 * 1024))
        return FakeProc()

    class FakeProbe:
        stderr = "Stream #0:1: Audio: eac3, 48000 Hz, 5.1"
        stdout = ""

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.subprocess.run", return_value=FakeProbe()), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await ensure_web_streamable(str(in_video), str(out_video), sub_path=str(sub_file))

    assert ok is True
    assert out_video.exists()
    assert len(captured_cmds) == 1
    ffmpeg_cmd = captured_cmds[0]
    assert "aac" in ffmpeg_cmd
    assert "mov_text" in ffmpeg_cmd
    assert "default+forced" in ffmpeg_cmd
    assert "+faststart" in ffmpeg_cmd


@pytest.mark.asyncio
async def test_embed_subtitles_soft_sets_default_forced_and_faststart(tmp_path):
    in_mp4 = tmp_path / "source.mp4"
    in_mp4.write_bytes(b"mp4_data" * 100)
    sub_srt = tmp_path / "sub.srt"
    sub_srt.write_text(generate_fallback_sinhala_srt("Sample", 2025), encoding="utf-8")
    out_mp4 = tmp_path / "merged.mp4"

    captured_cmds = []

    class FakeProc:
        returncode = 0
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        Path(args[-1]).write_bytes(b"M" * (200 * 1024))
        return FakeProc()

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        ok = await embed_subtitles_soft(str(in_mp4), str(sub_srt), str(out_mp4))

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert "-c:s" in cmd and "mov_text" in cmd
    assert "default+forced" in cmd
    assert "+faststart" in cmd


@pytest.mark.asyncio
async def test_generate_multi_quality_variants_ram(tmp_path):
    in_mp4 = tmp_path / "source_1080p.mp4"
    in_mp4.write_bytes(b"1080p_content" * 200)

    class FakeStderr:
        async def readline(self):
            return b""

    class FakeProc:
        returncode = 0
        stderr = FakeStderr()
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        for arg in args:
            if str(arg).endswith(".mp4") and str(arg) != str(in_mp4):
                Path(arg).write_bytes(b"S" * (600 * 1024))
        return FakeProc()

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        variants = await generate_multi_quality_variants_ram(
            str(in_mp4),
            str(tmp_path),
            base_stem="test_film",
            target_qualities=("720p", "480p", "360p"),
        )

    assert set(variants.keys()) == {"720p", "480p", "360p"}
    for q, p in variants.items():
        assert os.path.exists(p), f"Variant {q} should exist at {p}"


def test_website_movies_json_and_movies_data_js_have_multi_quality_and_sinhala_subs():
    repo_root = Path(__file__).resolve().parents[2]
    movies_json_path = repo_root / "website" / "data" / "movies.json"
    movies_js_path = repo_root / "website" / "data" / "movies_data.js"

    assert movies_json_path.exists()
    assert movies_js_path.exists()

    data = json.loads(movies_json_path.read_text(encoding="utf-8"))
    movies = data["movies"] if isinstance(data, dict) and "movies" in data else data
    assert len(movies) > 0

    for m in movies:
        # 1. Qualities map check
        assert "qualities" in m, f"Missing qualities map in {m.get('title')}"
        for q_key in ("auto", "1080p", "720p", "480p", "360p"):
            assert q_key in m["qualities"], f"Missing {q_key} in {m.get('title')} qualities"

        # 2. Downloads multi-quality check
        dls = m.get("downloads") or []
        dl_qualities = {d.get("quality") for d in dls}
        for q_key in ("1080p", "720p", "480p", "360p"):
            assert q_key in dl_qualities, f"Missing {q_key} download in {m.get('title')}"
        assert all(d.get("subtitle_merged") is True for d in dls)

        # 3. Sinhala Subtitles check
        subs = m.get("subtitles") or []
        assert len(subs) >= 1, f"Missing subtitles in {m.get('title')}"
        assert subs[0].get("default") is True
        assert subs[0].get("url"), f"Empty subtitle URL in {m.get('title')}"


def test_task_tracker_temp_dir_cleanup_and_safety(tmp_path):
    from services.task_tracker import tracker, TaskStatus
    dummy_dir = tmp_path / "leech_test_temp"
    dummy_dir.mkdir()
    dummy_file = dummy_dir / "sample.txt"
    dummy_file.write_text("temp_data")
    assert dummy_dir.exists()

    user_id = 998877
    tracker.start_task(user_id=user_id, title="Test Cleanup Movie")
    tracker.set_metadata(user_id=user_id, temp_dir=str(dummy_dir))

    # Cancel task and verify temp_dir is deleted without NameError
    cancelled = tracker.cancel_task(user_id=user_id)
    assert cancelled is True
    assert not dummy_dir.exists(), "temp_dir should be cleanly deleted on task cancellation"


def test_telegram_channel_id_parsing_accuracy():
    from services.scrapers.method1_telegram import parse_telegram_message_link
    # Standard 10-digit channel ID
    chat_id, msg_id = parse_telegram_message_link("https://t.me/c/1234567890/42")
    assert chat_id == -1001234567890
    assert msg_id == 42

    # 10-digit channel ID like private channel -1004325759505
    chat_id2, msg_id2 = parse_telegram_message_link("https://t.me/c/4325759505/14")
    assert chat_id2 == -1004325759505
    assert msg_id2 == 14

    # Public username link
    user, pub_msg_id = parse_telegram_message_link("https://t.me/my_channel/99")
    assert user == "my_channel"
    assert pub_msg_id == 99

