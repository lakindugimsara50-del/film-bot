import asyncio
import os
import sys
import tempfile
import time
import unittest
import urllib.parse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "bot")))

import subprocess
import re
from bot.services.video_service import (
    detect_hw_encoder,
    generate_multi_quality_variants_ram,
    compress_smart_1080p,
    get_ffmpeg_binary,
)
from bot.services.cloud_drive.rclone_client import find_rclone_binary
import bot.services.downloader as downloader
import bot.services.leech_service as leech_service
import bot.config as config


def _probe_height(video_path: str) -> int:
    ffmpeg_bin = get_ffmpeg_binary()
    proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-i", video_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    m = re.search(r"Video:.*?,\s*(\d+)x(\d+)", proc.stderr)
    return int(m.group(2)) if m else 0


class TestPipelineUpgrade(unittest.TestCase):
    def test_config_flags_enabled(self):
        self.assertTrue(config.ENABLE_MULTI_QUALITY_RAM)
        self.assertTrue(config.ENABLE_TORRENT_RACING)

    def test_downloader_urllib_imported(self):
        self.assertTrue(hasattr(downloader, "urllib"))
        self.assertEqual(downloader.urllib.parse.unquote("Hello%20World.mp4"), "Hello World.mp4")

    def test_rclone_binary_cached_and_fast(self):
        t0 = time.perf_counter()
        bin1 = find_rclone_binary()
        t1 = time.perf_counter()
        bin2 = find_rclone_binary()
        t2 = time.perf_counter()
        self.assertEqual(bin1, bin2)
        # Cached lookup must be < 5ms
        self.assertLess(t2 - t1, 0.005)

    def test_single_pass_multi_quality_variants_and_fast_1080p(self):
        ffmpeg_bin = get_ffmpeg_binary()
        self.assertTrue(ffmpeg_bin, "FFmpeg binary must be available")

        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                # Generate a synthetic 1920x1080 (1080p) MP4 with stereo audio (2.5 seconds)
                src_1080p = os.path.join(tmpdir, "source_1080p.mp4")
                gen_proc = subprocess.run(
                    [
                        ffmpeg_bin,
                        "-y",
                        "-hide_banner",
                        "-f", "lavfi",
                        "-i", "testsrc=duration=2.5:size=1920x1080:rate=24",
                        "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=44100:duration=2.5",
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "20",
                        "-c:a", "aac",
                        "-ac", "2",
                        src_1080p,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(gen_proc.returncode, 0)
                self.assertEqual(_probe_height(src_1080p), 1080)

                # Create a test Sinhala SRT file
                srt_path = os.path.join(tmpdir, "sinhala.srt")
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write(
                        "1\n00:00:00,500 --> 00:00:02,200\nසිංහල උපසිරැසි පරීක්ෂාව (FilmSub)\n\n"
                    )

                # 1. Test fast-path 1080p remux (no video re-encode when <=1.95GB)
                out_1080 = os.path.join(tmpdir, "out_1080p.mp4")
                ok_1080 = await compress_smart_1080p(
                    src_1080p,
                    out_1080,
                    sub_path=srt_path,
                )
                self.assertTrue(ok_1080)
                self.assertTrue(os.path.exists(out_1080))
                self.assertEqual(_probe_height(out_1080), 1080)

                # 2. Test single-pass split=3 multi-quality generation (720p, 480p, 360p)
                variants = await generate_multi_quality_variants_ram(
                    input_path=src_1080p,
                    output_dir=tmpdir,
                    slug="test-movie-2026",
                    sub_path=srt_path,
                    qualities=("720p", "480p", "360p"),
                )
                self.assertIn("720p", variants)
                self.assertIn("480p", variants)
                self.assertIn("360p", variants)

                size_720 = os.path.getsize(variants["720p"])
                size_480 = os.path.getsize(variants["480p"])
                size_360 = os.path.getsize(variants["360p"])

                self.assertGreater(size_720, size_480)
                self.assertGreater(size_480, size_360)
                self.assertGreater(size_360, 1000)

                self.assertEqual(_probe_height(variants["720p"]), 720)
                self.assertEqual(_probe_height(variants["480p"]), 480)
                self.assertEqual(_probe_height(variants["360p"]), 360)

        asyncio.run(_run())

    def test_is_genuine_sinhala_subtitle(self):
        from bot.services.subtitle_service import is_genuine_sinhala_subtitle

        # Genuine Sinhala text with > 15 characters
        sinhala_text = "මෙම චිත්‍රපටය සිංහල උපසිරැසි සමඟ නරඹන්න සහ බාගත කරන්න."
        self.assertTrue(is_genuine_sinhala_subtitle(sinhala_text))

        # English-only subtitle
        english_text = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\nHello world, this is an English subtitle track.\n"
        self.assertFalse(is_genuine_sinhala_subtitle(english_text))

        # Empty or too short
        self.assertFalse(is_genuine_sinhala_subtitle(""))
        self.assertFalse(is_genuine_sinhala_subtitle(None))
        self.assertFalse(is_genuine_sinhala_subtitle("සිංහල"))

    def test_tmdb_query_normalization(self):
        from bot.services import tmdb_service

        async def _check_tmdb():
            meta = await tmdb_service.fetch_metadata("ice age 01")
            self.assertEqual(meta.get("title"), "Ice Age")
            self.assertEqual(meta.get("year"), 2002)
            self.assertTrue(meta.get("poster_url"))

        asyncio.run(_check_tmdb())

    def test_compress_smart_1080p_oversized_routing(self):
        from unittest.mock import patch, AsyncMock
        from bot.services.video_service import compress_smart_1080p, MAX_TELEGRAM_BOT_SIZE

        async def _check():
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(b"dummy_data")
                in_path = f.name
            out_path = in_path + ".out.mp4"

            try:
                # Mock size to simulate 2.3 GB KGF Chapter 2 file
                with patch("os.path.getsize", return_value=int(2.3 * 1024 * 1024 * 1024)), \
                     patch("bot.services.video_service.compress_video", AsyncMock(return_value=True)) as mock_comp:
                    res = await compress_smart_1080p(in_path, out_path)
                    self.assertTrue(res)
                    self.assertTrue(mock_comp.called)
                    self.assertEqual(mock_comp.call_args[1]["input_path"], in_path)
                    self.assertEqual(mock_comp.call_args[1]["target_size_bytes"], int(1.85 * 1024 * 1024 * 1024))
            finally:
                if os.path.exists(in_path):
                    os.remove(in_path)
                if os.path.exists(out_path):
                    os.remove(out_path)

        asyncio.run(_check())


if __name__ == "__main__":
    unittest.main()
