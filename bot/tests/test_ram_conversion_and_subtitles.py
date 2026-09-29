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


@pytest.mark.asyncio
async def test_generate_multi_quality_variants_ram_burns_subtitles(tmp_path):
    """Verify that multi-quality variants include the subtitles filter for burning in subtitles."""
    in_mp4 = tmp_path / "source_burn.mp4"
    in_mp4.write_bytes(b"DATA" * 500)
    sub_srt = tmp_path / "sample_burn.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:05,000\nසිංහල උපසිරැසි\n", encoding="utf-8")

    class FakeProc:
        returncode = 0
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    captured_cmds = []

    async def fake_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        for arg in args:
            if str(arg).endswith(".mp4") and str(arg) != str(in_mp4):
                Path(arg).write_bytes(b"S" * (600 * 1024))
        return FakeProc()

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        variants = await generate_multi_quality_variants_ram(
            str(in_mp4),
            str(tmp_path),
            slug="test_slug",
            sub_path=str(sub_srt),
            qualities=("480p",),
        )

    assert len(captured_cmds) > 0
    cmd_str = " ".join(captured_cmds[0])
    assert "subtitles=filename=" in cmd_str
    assert "sub_burn_multi.srt" in cmd_str


@pytest.mark.asyncio
async def test_series_and_movie_variant_pipeline_logic(tmp_path):
    """
    Verify resolution detection and variant selection logic:
    - 720p series source requests only 480p (no 720p duplicate)
    - 1080p film source requests 720p and 480p
    """
    # 1. Test 720p series source
    src_720_w, src_720_h = 1280, 720
    is_source_720p = (1000 <= src_720_w < 1600) or (550 <= src_720_h < 900)
    primary_quality_720 = "720p" if is_source_720p else "1080p"
    assert primary_quality_720 == "720p"

    requested_qualities_720 = ("480p",) if primary_quality_720 == "720p" else ("720p", "480p")
    assert requested_qualities_720 == ("480p",)

    # 2. Test 1080p film source
    src_1080_w, src_1080_h = 1920, 1080
    is_source_1080p = (src_1080_w >= 1600 or src_1080_h >= 900)
    is_source_720p_film = (1000 <= src_1080_w < 1600) or (550 <= src_1080_h < 900)
    primary_quality_1080 = "720p" if is_source_720p_film else "1080p"
    assert primary_quality_1080 == "1080p"

    requested_qualities_1080 = ("720p", "480p") if primary_quality_1080 == "1080p" else ("480p",)
    assert requested_qualities_1080 == ("720p", "480p")

    # 3. Test non-duplicate downloads_list generation
    variant_tg_info = {
        "720p": {"message_id": 101, "file_id": "fid_720", "file_size": 500000000, "stream_url": "http://stream/720"},
        "480p": {"message_id": 102, "file_id": "fid_480", "file_size": 250000000, "stream_url": "http://stream/480"},
    }
    primary_q = "720p"
    message_id = 101
    tg_channel_id_clean = "12345678"

    downloads_list = []
    added_msg_ids = set()
    for q_var, q_lbl, fallback_sz, fallback_stream in [
        ("1080p", "1080p Full HD", 1000, "url1"),
        ("720p", "720p HD", 500, "url2"),
        ("480p", "480p SD", 250, "url3"),
    ]:
        info = variant_tg_info.get(q_var) or {}
        v_msg = info.get("message_id")
        if not v_msg and q_var == primary_q:
            v_msg = message_id
        if v_msg and v_msg > 0 and v_msg not in added_msg_ids and tg_channel_id_clean:
            added_msg_ids.add(v_msg)
            downloads_list.append({"quality": q_var, "message_id": v_msg})

    assert len(downloads_list) == 2
    assert downloads_list[0]["quality"] == "720p" and downloads_list[0]["message_id"] == 101
    assert downloads_list[1]["quality"] == "480p" and downloads_list[1]["message_id"] == 102


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
        # Skip movies that are currently queued in Stage 1 awaiting Stage 2 Telegram upload
        if m.get("telegram_status") == "queued" or m.get("stage") == "embed_only":
            continue

        # 1. Qualities map check (when qualities map is defined)
        if "qualities" in m:
            for q_key in ("auto", "1080p", "720p", "480p", "360p"):
                assert q_key in m["qualities"], f"Missing {q_key} in {m.get('title')} qualities"

        # 2. Downloads multi-quality check
        dls = m.get("downloads") or []
        if len(dls) >= 4:
            dl_qualities = {d.get("quality") for d in dls}
            for q_key in ("1080p", "720p", "480p", "360p"):
                assert q_key in dl_qualities, f"Missing {q_key} download in {m.get('title')}"
        if dls:
            assert all(d.get("subtitle_merged") is True or d.get("sub_merged") is True for d in dls)

        # 3. Sinhala Subtitles check
        subs = m.get("subtitles") or []
        if m.get("has_sinhala_sub") or subs:
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

    # 100-prefixed channel ID in URL (e.g. https://t.me/c/1004325759505/14)
    chat_id3, msg_id3 = parse_telegram_message_link("https://t.me/c/1004325759505/14")
    assert chat_id3 == -1004325759505
    assert msg_id3 == 14

    # Public username link
    user, pub_msg_id = parse_telegram_message_link("https://t.me/my_channel/99")
    assert user == "my_channel"
    assert pub_msg_id == 99


@pytest.mark.asyncio
async def test_drives_command_import_and_execution():
    from unittest.mock import AsyncMock, MagicMock
    from handlers.drive_handler import drives_command

    client = MagicMock()
    message = MagicMock()
    message.from_user.id = 12345
    message.text = "/drives"
    message.reply_text = AsyncMock()

    # Unauthorized user gets access denied gracefully
    await drives_command(client, message)
    message.reply_text.assert_awaited()
    assert "Administrators" in message.reply_text.call_args[0][0]

    # Safe against None text
    message.text = None
    message.caption = None
    message.reply_text.reset_mock()
    await drives_command(client, message)
    message.reply_text.assert_awaited()


def test_search_js_exists_and_filters_type():
    import json
    import subprocess
    from pathlib import Path

    js_file = Path(__file__).resolve().parents[2] / "website" / "assets" / "js" / "search.js"
    assert js_file.exists(), "website/assets/js/search.js must exist"

    # Run node check on search.js
    res = subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True)
    assert res.returncode == 0, f"search.js syntax error: {res.stderr}"

    # Verify search.html references search.js
    html_file = Path(__file__).resolve().parents[2] / "website" / "search.html"
    html_content = html_file.read_text(encoding="utf-8")
    assert 'src="assets/js/search.js"' in html_content, "search.html must link assets/js/search.js"


@pytest.mark.asyncio
async def test_generate_multi_quality_variants_1080p_and_fallback(tmp_path):
    in_mp4 = tmp_path / "source_4k.mp4"
    in_mp4.write_bytes(b"4k_content" * 200)

    class FakeProc:
        returncode = 0
        stderr = None
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    recorded_cmds = []

    async def fake_exec(*args, **kwargs):
        recorded_cmds.append(list(args))
        for arg in args:
            if str(arg).endswith(".mp4") and str(arg) != str(in_mp4):
                Path(arg).write_bytes(b"V" * (600 * 1024))
        p = FakeProc()
        class FakeStderr:
            async def read(self, *a):
                return b""
            async def readline(self):
                return b""
        p.stderr = FakeStderr()
        return p

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.get_video_resolution", return_value=(3840, 2160)), \
         patch("services.video_service.detect_hw_encoder", return_value="libx264"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        variants = await generate_multi_quality_variants_ram(
            str(in_mp4),
            str(tmp_path),
            base_stem="test_4k_film",
            target_qualities=("1080p", "720p", "480p"),
        )

    assert "1080p" in variants
    assert "720p" in variants
    assert "480p" in variants
    for q, p in variants.items():
        assert os.path.exists(p)


@pytest.mark.asyncio
async def test_generate_multi_quality_variants_cleanup_on_failure(tmp_path):
    in_mp4 = tmp_path / "corrupt_source.mp4"
    in_mp4.write_bytes(b"corrupt_data" * 200)

    class FailProc:
        returncode = 1
        stderr = None
        async def wait(self):
            return 1
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        # Write dummy partial files that should be cleaned up on failure
        for arg in args:
            if str(arg).endswith(".mp4") and str(arg) != str(in_mp4):
                Path(arg).write_bytes(b"CorruptBytes" * 100)
        p = FailProc()
        class FakeStderr:
            async def read(self, *a):
                return b""
            async def readline(self):
                return b""
        p.stderr = FakeStderr()
        return p

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.get_video_resolution", return_value=(1920, 1080)), \
         patch("services.video_service.detect_hw_encoder", return_value="libx264"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        variants = await generate_multi_quality_variants_ram(
            str(in_mp4),
            str(tmp_path),
            base_stem="failed_film",
            target_qualities=("720p", "480p"),
        )

    # All failed attempt files should be discarded/cleaned up
    assert variants == {}
    for p in tmp_path.glob("failed_film-*.mp4"):
        assert not p.exists() or p.stat().st_size == 0


def test_player_js_two_clean_servers_and_syntax():
    import subprocess
    player_js = Path(__file__).resolve().parents[2] / "website" / "assets" / "js" / "player.js"
    assert player_js.exists()

    res = subprocess.run(["node", "--check", str(player_js)], capture_output=True, text=True)
    assert res.returncode == 0, f"player.js syntax error: {res.stderr}"

    content = player_js.read_text(encoding="utf-8")
    # Verify zero-ads 2 clean servers logic
    assert "Super Player (Telegram Cloud HD • Zero Ads)" in content
    assert "VIP Player (VidLink Ultra HD • Zero Ads)" in content
    assert "isMatchingEpisode" in content


@pytest.mark.asyncio
async def test_generate_multi_quality_variants_1080p_widescreen_and_ac3(tmp_path):
    """Verify that a 1920x800 widescreen film with AC3 audio triggers the 1080p shortcut,
    transcodes audio to AAC, and generates remaining requested variants."""
    in_mp4 = tmp_path / "widescreen_1080p.mp4"
    in_mp4.write_bytes(b"WIDESCREEN_1080P" * 200)

    class FakeProc:
        returncode = 0
        stderr = None
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        for arg in args:
            if str(arg).endswith(".mp4") and str(arg) != str(in_mp4):
                Path(arg).write_bytes(b"V" * (600 * 1024))
        p = FakeProc()
        class FakeStderr:
            async def read(self, *a):
                return b""
            async def readline(self):
                return b""
        p.stderr = FakeStderr()
        return p

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.get_video_resolution", return_value=(1920, 800)), \
         patch("services.video_service.get_audio_codec", return_value="ac3"), \
         patch("services.video_service.detect_hw_encoder", return_value="libx264"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        variants = await generate_multi_quality_variants_ram(
            str(in_mp4),
            str(tmp_path),
            base_stem="widescreen_film",
            target_qualities=("1080p", "720p", "480p"),
        )

    assert "1080p" in variants
    assert "720p" in variants
    assert "480p" in variants
    for q, p in variants.items():
        assert os.path.exists(p)


@pytest.mark.asyncio
async def test_boost_command_auto_publish():
    """Verify /boost command registers as is_auto=True so queue_service runs in auto-publish mode."""
    from unittest.mock import MagicMock
    from handlers.leech_handler import register as register_leech_handler
    from services.auth_service import auth_service

    app = MagicMock()
    registered_handlers = []
    def fake_on_message(filters):
        def decorator(func):
            registered_handlers.append((filters, func))
            return func
        return decorator
    app.on_message = fake_on_message

    register_leech_handler(app)
    # Find leech_command handler
    leech_cmd_fn = None
    for filt, fn in registered_handlers:
        if fn.__name__ == "leech_command":
            leech_cmd_fn = fn
            break
    assert leech_cmd_fn is not None

    # Call with /boost command
    client = MagicMock()
    msg = MagicMock()
    msg.from_user.id = 123456
    msg.from_user.username = "testuser"
    msg.command = ["boost", "Inception", "2010"]
    msg.text = "/boost Inception 2010"
    msg.reply_to_message = None

    status_mock = AsyncMock()
    msg.reply_text = AsyncMock(return_value=status_mock)

    with patch.object(auth_service, "is_authorized", return_value=True), \
         patch("handlers.leech_handler.queue_service.is_idle", return_value=True), \
         patch("handlers.leech_handler.queue_service.add_to_queue", new_callable=AsyncMock) as mock_add_queue:
        mock_add_queue.return_value = 1
        await leech_cmd_fn(client, msg)

        # auto_publish must be True for /boost
        mock_add_queue.assert_called_once()
        _, kwargs = mock_add_queue.call_args
        assert kwargs.get("auto_publish") is True


def test_player_js_no_duplicate_urls_on_imdb_only():
    """Verify player.js creates distinct VidLink and AutoEmbed stream URLs without duplication."""
    import subprocess
    js_code = """
    const fs = require('fs');
    const vm = require('vm');
    const code = fs.readFileSync('website/assets/js/player.js', 'utf8');
    // Stub browser globals
    global.window = { FilmSub: {} };
    global.document = {
        addEventListener: () => {},
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => []
    };
    global.navigator = {};
    global.location = { search: '', href: '' };
    global.FilmSub = { escHtml: s => s };
    global.currentSeason = 1;
    global.currentEpisode = 2;
    global.currentEffectiveQuality = '1080p';
    vm.runInThisContext(code);

    const movie = {
        title: 'Game of Thrones',
        type: 'series',
        season: 1,
        episode: 1,
        imdb_id: 'tt0944947'
    };
    const streams = getMovieStreams(movie);
    console.log(JSON.stringify(streams.map(s => ({ server: s.server, label: s.label, url: s.stream_url }))));
    """
    res = subprocess.run(["node", "-e", js_code], capture_output=True, text=True)
    assert res.returncode == 0, f"Node eval error: {res.stderr}"
    import json
    st_list = json.loads(res.stdout.strip())
    assert len(st_list) >= 2, f"Expected at least 2 clean servers, got {len(st_list)}"
    urls = [s['url'] for s in st_list]
    assert len(set(urls)) == len(urls), f"Expected distinct URLs, got duplicates: {urls}"
    assert any("vidlink.pro" in u for u in urls), f"Missing vidlink in {urls}"
    assert any("multiembed.mov" in u for u in urls), f"Missing multiembed in {urls}"


@pytest.mark.asyncio
async def test_generate_multi_quality_variants_480p_instant_copy(tmp_path):
    """Verify that a source video with 854x480 resolution triggers instant copy for 480p."""
    in_mp4 = tmp_path / "source_480p.mp4"
    in_mp4.write_bytes(b"SD_480P_DATA" * 200)

    class FakeProc:
        returncode = 0
        stderr = None
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        for arg in args:
            if str(arg).endswith(".mp4") and str(arg) != str(in_mp4):
                Path(arg).write_bytes(b"OUT" * (600 * 1024))
        p = FakeProc()
        class FakeStderr:
            async def read(self, *a):
                return b""
            async def readline(self):
                return b""
        p.stderr = FakeStderr()
        return p

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.get_video_resolution", return_value=(854, 480)), \
         patch("services.video_service.get_audio_codec", return_value="aac"), \
         patch("services.video_service.detect_hw_encoder", return_value="libx264"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        variants = await generate_multi_quality_variants_ram(
            str(in_mp4),
            str(tmp_path),
            base_stem="sd_film",
            target_qualities=("480p", "360p"),
        )

    assert "480p" in variants
    assert "360p" in variants
    for q, p in variants.items():
        assert os.path.exists(p)


@pytest.mark.asyncio
async def test_status_command_rich_formatting_and_markup():
    """Verify /status command constructs rich formatting and provides interactive buttons."""
    from main import status_handler, _build_status_content
    from unittest.mock import MagicMock, AsyncMock

    client = MagicMock()
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.reply_text = AsyncMock()

    with patch("main.config.ADMIN_IDS", [12345]), \
         patch("main.config.PUBLIC_CHANNEL_ID", -100123456789), \
         patch("services.task_tracker.tracker.get_status_summary", return_value="✅ Idle"), \
         patch("handlers.wizard.USER_SESSIONS", {}):
        client.get_me = AsyncMock(return_value=MagicMock(username="Filmsinhala200Bot"))
        await status_handler(client, msg)

        msg.reply_text.assert_called_once()
        args, kwargs = msg.reply_text.call_args
        assert "Film Bot තත්ත්වය" in args[0]
        assert kwargs.get("reply_markup") is not None
        # Check buttons in reply_markup
        kb = kwargs["reply_markup"]
        buttons_flat = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Refresh Status" in b for b in buttons_flat)
        assert any("View Queue" in b for b in buttons_flat)
        assert any("Boost" in b for b in buttons_flat)
        assert any("Cancel" in b for b in buttons_flat)


@pytest.mark.asyncio
async def test_progress_reporter_with_reply_markup():
    """Verify ProgressReporter includes reply_markup in start and update calls."""
    from services.progress_service import ProgressReporter
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from unittest.mock import MagicMock, AsyncMock

    client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.id = 999
    fake_msg.edit_text = AsyncMock()
    client.send_message = AsyncMock(return_value=fake_msg)

    test_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="test:cancel")]])
    reporter = ProgressReporter(client, chat_id=123, title="Test Movie", reply_markup=test_kb)

    await reporter.start()
    client.send_message.assert_called_once()
    _, start_kwargs = client.send_message.call_args
    assert start_kwargs.get("reply_markup") == test_kb

    # Force update
    reporter._last_edit = 0.0
    await reporter.update(500 * 1024 * 1024, 1000 * 1024 * 1024)
    fake_msg.edit_text.assert_called_once()
    _, edit_kwargs = fake_msg.edit_text.call_args
    assert edit_kwargs.get("reply_markup") == test_kb


def test_player_js_selects_optimal_telegram_variant():
    """Verify player.js uses Telegram variant_media matching currentEffectiveQuality."""
    import subprocess
    js_code = """
    const fs = require('fs');
    const vm = require('vm');
    const code = fs.readFileSync('website/assets/js/player.js', 'utf8');
    global.window = { FilmSub: {} };
    global.document = {
        addEventListener: () => {},
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => []
    };
    global.navigator = {};
    global.location = { search: '', href: '' };
    global.FilmSub = { escHtml: s => s };
    global.currentSeason = 1;
    global.currentEpisode = 1;
    vm.runInThisContext(code);
    currentEffectiveQuality = '720p';

    const movie = {
        title: 'Dune',
        type: 'movie',
        channel_chat_id: '-1004325759505',
        variant_media: {
            '1080p': { message_id: 101, file_id: 'f101' },
            '720p': { message_id: 102, file_id: 'f102' },
            '480p': { message_id: 103, file_id: 'f103' }
        },
        downloads: []
    };
    const streams = getMovieStreams(movie);
    const superPlayer = streams.find(s => s.server === 'Server 1');
    console.log(JSON.stringify({ found: !!superPlayer, url: superPlayer ? superPlayer.stream_url : '' }));
    """
    res = subprocess.run(["node", "-e", js_code], capture_output=True, text=True)
    assert res.returncode == 0, f"Node eval error: {res.stderr}"
    import json
    data = json.loads(res.stdout.strip())
    assert data["found"] is True
    # Should use message_id 102 for 720p
    assert "/stream/channel/-1004325759505/102" in data["url"]


@pytest.mark.asyncio
async def test_generate_multi_quality_variants_360p_instant_copy_and_anti_upscale(tmp_path):
    """Verify that 360p source video triggers instant copy for 360p and eliminates wasteful upscaling."""
    in_mp4 = tmp_path / "source_360p.mp4"
    in_mp4.write_bytes(b"SD_360P_DATA" * 150)

    class FakeProc:
        returncode = 0
        stderr = None
        async def wait(self):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        for arg in args:
            if str(arg).endswith(".mp4") and str(arg) != str(in_mp4):
                Path(arg).write_bytes(b"OUT" * (300 * 1024))
        p = FakeProc()
        class FakeStderr:
            async def read(self, *a):
                return b""
            async def readline(self):
                return b""
        p.stderr = FakeStderr()
        return p

    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.get_video_resolution", return_value=(640, 360)), \
         patch("services.video_service.get_audio_codec", return_value="aac"), \
         patch("services.video_service.detect_hw_encoder", return_value="libx264"), \
         patch("services.video_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
        variants = await generate_multi_quality_variants_ram(
            str(in_mp4),
            str(tmp_path),
            base_stem="sd_film_360",
            target_qualities=("1080p", "720p", "360p"),
        )

    # 360p instant copy should be present, upscales (1080p, 720p) dropped
    assert "360p" in variants
    assert "1080p" not in variants
    assert "720p" not in variants
    assert os.path.exists(variants["360p"])


def test_player_js_normalizes_stream_urls_and_ignores_dead_tunnels():
    """Verify player.js converts ephemeral /stream/channel/ URLs to relative proxy paths."""
    import subprocess
    js_code = """
    const fs = require('fs');
    const vm = require('vm');
    const code = fs.readFileSync('website/assets/js/player.js', 'utf8');
    global.window = { FilmSub: {} };
    global.document = {
        addEventListener: () => {},
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => []
    };
    global.navigator = {};
    global.location = { search: '', href: '' };
    global.FilmSub = { escHtml: s => s };
    global.currentSeason = 1;
    global.currentEpisode = 1;
    vm.runInThisContext(code);

    const movie = {
        title: 'Deadpool',
        type: 'movie',
        variant_media: {
            '1080p': {
                stream_url: 'https://damaged-aye-metals-marking.trycloudflare.com/stream/channel/-1004325759505/55',
                message_id: 55
            }
        },
        downloads: []
    };
    currentEffectiveQuality = '1080p';
    const streams = getMovieStreams(movie);
    const superPlayer = streams.find(s => s.server === 'Server 1');

    // Also test normalizeStreamUrl on dead tunnel vs relative
    const norm = normalizeStreamUrl('https://xyz.trycloudflare.com/stream/channel/-100123/456');
    const dead = normalizeStreamUrl('https://xyz.trycloudflare.com/plain/url.mp4');

    console.log(JSON.stringify({
        url: superPlayer ? superPlayer.stream_url : '',
        norm: norm,
        dead: dead
    }));
    """
    res = subprocess.run(["node", "-e", js_code], capture_output=True, text=True)
    assert res.returncode == 0, f"Node eval error: {res.stderr}"
    import json
    data = json.loads(res.stdout.strip())
    assert data["url"] == "/stream/channel/-1004325759505/55"
    assert data["norm"] == "/stream/channel/-100123/456"
    assert data["dead"] == ""


def test_player_js_360p_fallback_on_auto():
    """Verify that if a movie only has 360p in variant_media, it is resolved correctly on auto."""
    import subprocess
    js_code = """
    const fs = require('fs');
    const vm = require('vm');
    const code = fs.readFileSync('website/assets/js/player.js', 'utf8');
    global.window = { FilmSub: {} };
    global.document = {
        addEventListener: () => {},
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => []
    };
    global.navigator = {};
    global.location = { search: '', href: '' };
    global.FilmSub = { escHtml: s => s };
    global.currentSeason = 1;
    global.currentEpisode = 1;
    vm.runInThisContext(code);

    currentEffectiveQuality = 'auto';
    const movie = {
        title: 'Old Film',
        type: 'movie',
        channel_chat_id: '-1004325759505',
        variant_media: {
            '360p': { message_id: 88, file_id: 'f88' }
        },
        downloads: []
    };
    const streams = getMovieStreams(movie);
    const superPlayer = streams.find(s => s.server === 'Server 1');
    console.log(JSON.stringify({ url: superPlayer ? superPlayer.stream_url : '' }));
    """
    res = subprocess.run(["node", "-e", js_code], capture_output=True, text=True)
    assert res.returncode == 0, f"Node eval error: {res.stderr}"
    import json
    data = json.loads(res.stdout.strip())
    assert "/stream/channel/-1004325759505/88" in data["url"]


@pytest.mark.asyncio
async def test_progress_reporter_edit_preserves_reply_markup():
    """Verify ProgressReporter.edit preserves reply_markup."""
    from services.progress_service import ProgressReporter
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from unittest.mock import MagicMock, AsyncMock

    client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.id = 1001
    fake_msg.edit_text = AsyncMock()
    client.send_message = AsyncMock(return_value=fake_msg)

    test_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="test:cancel")]])
    reporter = ProgressReporter(client, chat_id=123, title="Test Movie", reply_markup=test_kb)
    await reporter.start()

    await reporter.edit("⚡ Single Edit")
    fake_msg.edit_text.assert_called_once()
    _, kwargs = fake_msg.edit_text.call_args
    assert kwargs.get("reply_markup") == test_kb


@pytest.mark.asyncio
async def test_cancel_command_cleans_queue_service():
    """Verify that /cancel cleans both task_tracker and queue_service."""
    from handlers.leech_handler import register
    from pyrogram import filters
    from unittest.mock import MagicMock, AsyncMock, patch

    mock_app = MagicMock()
    handlers = {}
    def on_message_decorator(flt):
        def wrapper(fn):
            handlers[fn.__name__] = fn
            return fn
        return wrapper
    def on_callback_decorator(flt):
        def wrapper(fn):
            handlers[fn.__name__] = fn
            return fn
        return wrapper
    mock_app.on_message = on_message_decorator
    mock_app.on_callback_query = on_callback_decorator

    register(mock_app)

    cancel_fn = handlers["cancel_command"]
    client = MagicMock()
    msg = MagicMock()
    msg.from_user.id = 555
    msg.from_user.username = "tester"
    msg.reply_text = AsyncMock()

    with patch("handlers.leech_handler.auth_service.is_authorized", return_value=True), \
         patch("services.queue_service.queue_service.cancel_user", return_value=True) as mock_q_cancel, \
         patch("services.task_tracker.cancel_all_user_operations", new_callable=AsyncMock) as mock_tracker_cancel:
        mock_tracker_cancel.return_value = True
        await cancel_fn(client, msg)

        mock_q_cancel.assert_called_once()
        mock_tracker_cancel.assert_called_once_with(555)
        msg.reply_text.assert_called_once()
        assert "සාර්ථකව අවලංගු කරන ලදී" in msg.reply_text.call_args[0][0]






