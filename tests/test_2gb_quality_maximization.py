"""
tests/test_2gb_quality_maximization.py

Verifies FilmZilla's 2 GB (< 1.95 GB) quality-maximization upgrades:
1. _calc_safe_target_video_kbps fills available < 2 GB Telegram budget dynamically by duration.
2. _build_nvenc_encode_opts supports fullres/qres multipass, 32-frame lookahead, AQ strength 10,
   and B-frame middle reference mode while keeping backward-compatible test defaults.
3. burn_subtitles_to_video preserves Sinhala subtitle hard-burning and upgrades bitrates/preset.
4. generate_multi_quality_variants_ram uses lanczos scaling, dynamic bitrates, and NVENC p5 / x264 film.
"""

import asyncio
import os
import sys

_bot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
if _bot_dir not in sys.path:
    sys.path.insert(0, _bot_dir)

import services.video_service as video_service


def test_calc_safe_target_video_kbps_scales_with_duration():
    short_ep_kbps = video_service._calc_safe_target_video_kbps(2700.0, audio_kbps=160, min_kbps=1400, max_kbps=4800)
    standard_movie_kbps = video_service._calc_safe_target_video_kbps(5400.0, audio_kbps=160, min_kbps=1400, max_kbps=4800)
    long_epic_kbps = video_service._calc_safe_target_video_kbps(10800.0, audio_kbps=160, min_kbps=800, max_kbps=4800)

    # 45-min episode utilizes full high bitrate headroom (4800 kbps instead of old 2600k cap)
    assert short_ep_kbps == 4800
    # 90-min movie gets > 2600 kbps while staying inside TARGET_COMPRESS_SIZE
    assert standard_movie_kbps >= 2600
    # 3-hour movie gracefully fits < 1.90 GB without exceeding ceiling
    est_bytes_long = ((long_epic_kbps + 160) * 1000 / 8.0) * 10800.0
    assert est_bytes_long < video_service.MAX_TELEGRAM_BOT_SIZE


def test_build_nvenc_encode_opts_fullres_bref_and_compat():
    opts_auto_full = video_service._build_nvenc_encode_opts(
        v_bitrate_k=3200,
        maxrate_k=4320,
        bufsize_k=6400,
        cq=18,
        preset="p5",
        caps={"nvenc_hq": True, "nvenc_fullres": True, "nvenc_qres": True, "nvenc_bref_middle": True},
        multipass="auto",
        lookahead=32,
        aq_strength=10,
        b_ref_mode="middle",
    )
    assert opts_auto_full[opts_auto_full.index("-preset") + 1] == "p5"
    assert opts_auto_full[opts_auto_full.index("-multipass") + 1] == "fullres"
    assert opts_auto_full[opts_auto_full.index("-rc-lookahead") + 1] == "32"
    assert opts_auto_full[opts_auto_full.index("-aq-strength") + 1] == "10"
    assert opts_auto_full[opts_auto_full.index("-b_ref_mode") + 1] == "middle"

    # Graceful fallback when fullres / b_ref_mode unsupported
    opts_auto_qres = video_service._build_nvenc_encode_opts(
        cq=19,
        preset="p5",
        caps={"nvenc_hq": True, "nvenc_fullres": False, "nvenc_qres": True, "nvenc_bref_middle": False},
        multipass="auto",
    )
    assert opts_auto_qres[opts_auto_qres.index("-multipass") + 1] == "qres"
    assert "-b_ref_mode" not in opts_auto_qres


def test_burn_subtitles_to_video_preserves_burn_and_uses_hq_2gb_budget(tmp_path, monkeypatch):
    in_vid = tmp_path / "movie.mp4"
    in_vid.write_bytes(b"\x00" * 4096)
    sub_srt = tmp_path / "si.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nසිංහල\n", encoding="utf-8")
    out_vid = tmp_path / "burned.mp4"

    captured_cmds = []

    class _FakeProc:
        def __init__(self, target):
            self.returncode = 0
            self.stderr = self
            self._target = target
            self._done = False

        async def read(self, n=4096):
            if not self._done:
                self._done = True
                return b"frame=100 fps=240.0 time=00:00:04.00\n"
            return b""

        async def wait(self):
            with open(self._target, "wb") as f:
                f.write(b"\x00" * 8192)
            return 0

        async def communicate(self):
            await self.wait()
            return b"", b""

    async def _fake_exec(*cmd, **kwargs):
        captured_cmds.append(list(cmd))
        return _FakeProc(cmd[-1])

    monkeypatch.setattr(video_service, "get_ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(video_service, "detect_hw_encoder", lambda *a: "h264_nvenc")
    monkeypatch.setattr(video_service, "detect_cuda_pipeline_caps", lambda *a: {
        "ffmpeg_bin": "ffmpeg", "nvenc_hq": True, "nvenc_fullres": True, "nvenc_qres": True, "nvenc_bref_middle": True
    })
    monkeypatch.setattr(video_service, "get_video_duration", lambda *a: 2700.0)
    monkeypatch.setattr(video_service, "get_video_resolution", lambda *a: (1920, 1080))
    monkeypatch.setattr(video_service, "get_audio_codec", lambda *a: "aac")
    monkeypatch.setattr(video_service.asyncio, "create_subprocess_exec", _fake_exec)

    ok = asyncio.run(video_service.burn_subtitles_to_video(str(in_vid), str(sub_srt), str(out_vid)))
    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    vf = cmd[cmd.index("-vf") + 1]
    assert ("ass=filename=" in vf or "subtitles=filename=" in vf)
    assert cmd[cmd.index("-preset") + 1] == "p5"
    assert cmd[cmd.index("-multipass") + 1] == "fullres"
    assert int(cmd[cmd.index("-b:v") + 1].rstrip("k")) >= 4500


def test_generate_multi_quality_variants_ram_uses_lanczos_p5_and_burn(tmp_path, monkeypatch):
    in_vid = tmp_path / "src_1080p.mp4"
    in_vid.write_bytes(b"\x00" * 8192)
    sub_srt = tmp_path / "sub.srt"
    sub_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nසිංහල උපසිරැසි\n", encoding="utf-8")
    out_dir = tmp_path / "variants"

    captured_cmds = []

    class _FakeProc:
        def __init__(self, out_files):
            self.returncode = 0
            self.stderr = self
            self._out_files = out_files
            self._done = False

        async def read(self, n=4096):
            if not self._done:
                self._done = True
                return b"frame=120 fps=300.0 time=00:00:05.00\n"
            return b""

        async def wait(self):
            for p in self._out_files:
                with open(p, "wb") as f:
                    f.write(b"\x00" * 4096)
            return 0

    async def _fake_exec(*cmd, **kwargs):
        cmd_l = list(cmd)
        captured_cmds.append(cmd_l)
        mp4s = [tok for tok in cmd_l if str(tok).endswith(".mp4") and tok != str(in_vid)]
        return _FakeProc(mp4s)

    monkeypatch.setattr(video_service, "get_ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(video_service, "detect_hw_encoder", lambda *a: "h264_nvenc")
    monkeypatch.setattr(video_service, "detect_cuda_pipeline_caps", lambda *a: {
        "ffmpeg_bin": "ffmpeg", "nvenc_hq": True, "nvenc_fullres": True, "nvenc_qres": True,
        "nvenc_bref_middle": True, "scale_cuda": False, "hwaccel_cuda": False
    })
    monkeypatch.setattr(video_service, "get_video_duration", lambda *a: 3600.0)
    monkeypatch.setattr(video_service, "get_video_resolution", lambda *a: (1920, 1080))
    monkeypatch.setattr(video_service, "get_audio_codec", lambda *a: "aac")
    monkeypatch.setattr(video_service.asyncio, "create_subprocess_exec", _fake_exec)

    res = asyncio.run(
        video_service.generate_multi_quality_variants_ram(
            str(in_vid), str(out_dir), slug="testfilm", sub_path=str(sub_srt), qualities=("1080p", "720p")
        )
    )
    assert "1080p" in res and "720p" in res
    assert len(captured_cmds) >= 1
    cmd = captured_cmds[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "flags=lanczos+accurate_rnd+full_chroma_int" in fc
    assert "fast_bilinear" not in fc
    assert ("ass=filename=" in fc or "subtitles=filename=" in fc)
    assert cmd[cmd.index("-preset") + 1] == "p5"
    assert cmd[cmd.index("-multipass") + 1] == "fullres"
