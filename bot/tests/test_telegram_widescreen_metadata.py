"""
test_telegram_widescreen_metadata.py — Tests for 16:9 widescreen metadata and thumbnail generation.

Verifies:
1. get_video_metadata extracts video dimensions or falls back to standard 16:9 widescreen.
2. generate_video_thumbnail produces valid 640x360 16:9 JPEG thumbnails.
3. upload_video_file passes duration, width, height, thumb, and supports_streaming=True to send_video.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure bot directory is on sys.path
sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import telegram_upload
from services.video_service import (
    get_video_duration,
    get_video_metadata,
    get_video_resolution,
    generate_video_thumbnail,
)


class TestTelegramWidescreenMetadata(unittest.TestCase):
    def test_get_video_metadata_filename_fallbacks(self):
        """Verify fallback to standard 16:9 widescreen when probe fails."""
        meta_1080 = get_video_metadata("non_existent_movie_1080p.mp4")
        self.assertEqual(meta_1080["width"], 1920)
        self.assertEqual(meta_1080["height"], 1080)
        self.assertEqual(meta_1080["duration"], 0)

        meta_720 = get_video_metadata("series_s01e01_720p.mkv")
        self.assertEqual(meta_720["width"], 1280)
        self.assertEqual(meta_720["height"], 720)

        meta_480 = get_video_metadata("sample_480p.mp4")
        self.assertEqual(meta_480["width"], 854)
        self.assertEqual(meta_480["height"], 480)

        meta_default = get_video_metadata("unknown_video.mp4")
        self.assertEqual(meta_default["width"], 1280)
        self.assertEqual(meta_default["height"], 720)

    def test_get_video_metadata_probed_values(self):
        """Verify get_video_metadata uses real probed values when resolution is found."""
        with patch("services.video_service.get_video_resolution", return_value=(1920, 800)), \
             patch("services.video_service.get_video_duration", return_value=5400.6):
            meta = get_video_metadata("dummy_cinematic.mp4")
            self.assertEqual(meta["width"], 1920)
            self.assertEqual(meta["height"], 800)
            self.assertEqual(meta["duration"], 5401)

    def test_upload_video_file_passes_widescreen_attributes(self):
        """Verify upload_video_file passes explicit width, height, duration, and supports_streaming=True to send_video."""
        client_mock = MagicMock()
        mock_msg = MagicMock()
        mock_msg.id = 101
        mock_msg.video = MagicMock()
        mock_msg.video.file_id = "VID_WIDESCREEN_ID"
        mock_msg.document = None
        client_mock.send_video = AsyncMock(return_value=mock_msg)
        client_mock.is_connected = True

        with tempfile.NamedTemporaryFile(suffix="_1080p.mp4", delete=False) as tf, \
             tempfile.NamedTemporaryFile(suffix="_thumb.jpg", delete=False) as thf:
            tf.write(b"DUMMY_1080P_PAYLOAD" * 20)
            thf.write(b"DUMMY_THUMB_PAYLOAD" * 100)
            tf_path = tf.name
            th_path = thf.name

        try:
            with patch("services.video_service.get_video_metadata", return_value={"width": 1920, "height": 1080, "duration": 3600}), \
                 patch("services.video_service.generate_video_thumbnail", return_value=th_path):

                async def run_test():
                    res = await telegram_upload.upload_video_file(
                        bot_client=client_mock,
                        file_path=tf_path,
                        target_chat=-100987654321,
                    )
                    self.assertEqual(res["file_id"], "VID_WIDESCREEN_ID")
                    client_mock.send_video.assert_awaited_once()
                    call_kwargs = client_mock.send_video.call_args.kwargs
                    self.assertEqual(call_kwargs.get("width"), 1920)
                    self.assertEqual(call_kwargs.get("height"), 1080)
                    self.assertEqual(call_kwargs.get("duration"), 3600)
                    self.assertEqual(call_kwargs.get("thumb"), th_path)
                    self.assertEqual(call_kwargs.get("supports_streaming"), True)

                asyncio.run(run_test())
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)
            if os.path.exists(th_path):
                os.remove(th_path)

    def test_generate_video_thumbnail_real_ffmpeg(self):
        """Verify real FFmpeg produces a valid 640x360 16:9 JPEG thumbnail."""
        from services.video_service import get_ffmpeg_binary
        exe = get_ffmpeg_binary()
        if not exe:
            self.skipTest("FFmpeg binary not available")

        import subprocess
        with tempfile.TemporaryDirectory() as td:
            vid = os.path.join(td, "test_1080p.mp4")
            cmd = [exe, "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=1920x1080:rate=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", vid]
            proc = subprocess.run(cmd, capture_output=True)
            if proc.returncode != 0:
                self.skipTest("Failed to generate dummy video with FFmpeg")

            th_out = os.path.join(td, "out_thumb.jpg")
            res_thumb = generate_video_thumbnail(vid, thumb_path=th_out, duration=2.0)
            self.assertIsNotNone(res_thumb)
            self.assertTrue(os.path.exists(th_out))
            self.assertGreater(os.path.getsize(th_out), 500)

            # Probe generated thumbnail to ensure exact 640x360 16:9 dimensions
            w, h = get_video_resolution(th_out)
            self.assertEqual(w, 640)
            self.assertEqual(h, 360)


if __name__ == "__main__":
    unittest.main()
