"""
test_branding_service.py — Comprehensive tests for the FilmSub / FilmZilla branding system.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure bot modules can be imported cleanly
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOT_DIR = os.path.join(PROJECT_ROOT, "bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from services.branding_service import (
    clean_source_branding,
    format_branded_filename,
    get_branded_thumbnail,
    build_branded_caption,
    build_branded_inline_keyboard,
    BRANDED_THUMB_PATH,
)


class TestBrandingService(unittest.TestCase):
    def test_clean_source_branding(self):
        self.assertEqual(clean_source_branding("Aasha [CineSubz.co] 480p"), "Aasha 480p")
        self.assertEqual(clean_source_branding("Sugriva WEB-DL [www.cinesubz.net] 1080p"), "Sugriva WEB-DL 1080p")
        self.assertEqual(clean_source_branding("Movie [BaiscopeLK.com] 720p"), "Movie 720p")
        self.assertEqual(clean_source_branding("Film [piratelk.com] 1080p"), "Film 1080p")

    def test_format_branded_filename_with_foreign_brand(self):
        res = format_branded_filename(
            title="Aasha",
            year="2026",
            quality="480p",
            language="Malayalam",
            original_name="Aasha (2026) Malayalam WEB-[CineSubz.co]-480p.mp4",
        )
        self.assertEqual(res, "Aasha (2026) Malayalam WEB-[FilmSub.lk]-480p.mp4")

    def test_format_branded_filename_with_alternate_cinesubz(self):
        res = format_branded_filename(
            title="Sugriva",
            year="2026",
            quality="1080p",
            language="Telugu",
            original_name="Sugriva (2026) Telugu WEB-DL [CineSubz.net] 1080p.mkv",
        )
        self.assertEqual(res, "Sugriva (2026) Telugu WEB-DL [FilmSub.lk] 1080p.mkv")

    def test_format_branded_filename_from_temp_slug(self):
        res = format_branded_filename(
            title="Master",
            year="2021",
            quality="720p",
            language="Tamil",
            original_name="fs_master-2021_720p.mp4",
        )
        self.assertEqual(res, "Master (2021) Tamil [720p] [FilmSub.lk].mp4")

    def test_format_branded_filename_clean_fallback(self):
        res = format_branded_filename(
            title="Chaos Walking",
            year="2021",
            quality="1080p",
            original_name="",
        )
        self.assertEqual(res, "Chaos Walking (2021) [1080p] [FilmSub.lk].mp4")

    def test_get_branded_thumbnail(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            copied = get_branded_thumbnail(tmp_path)
            self.assertTrue(os.path.exists(copied))
            self.assertGreater(os.path.getsize(copied), 1000)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_build_branded_caption(self):
        caption = build_branded_caption(
            title="Aasha [CineSubz.co]",
            year="2026",
            quality="480p",
            file_size=635910000,
            slug="aasha-2026",
            site_base_url="https://filmsub.pages.dev",
        )
        self.assertIn("Aasha (2026) [480p]", caption)
        self.assertNotIn("CineSubz", caption)
        self.assertIn("Quality:</b> 480p", caption)
        self.assertIn("File Size:", caption)
        self.assertIn("සිංහල උපසිරැසි", caption)
        self.assertIn("https://filmsub.pages.dev/movie.html?id=aasha-2026", caption)
        self.assertLess(len(caption), 1024)

    def test_build_branded_inline_keyboard(self):
        kb = build_branded_inline_keyboard(
            slug="aasha-2026",
            site_base_url="https://filmsub.pages.dev",
            bot_username="Filmsinhala200Bot",
        )
        self.assertIsNotNone(kb)
        self.assertGreaterEqual(len(kb.inline_keyboard), 3)

        # Row 1: Watch Online
        r1 = kb.inline_keyboard[0]
        self.assertEqual(len(r1), 1)
        self.assertIn("movie.html?id=aasha-2026", r1[0].url)

        # Row 2: Channel & Bot
        r2 = kb.inline_keyboard[1]
        self.assertEqual(len(r2), 2)
        self.assertIn("Filmsinhala200Bot", r2[0].url)
        self.assertIn("Filmsinhala200Bot", r2[1].url)

    def test_no_duplicate_movie_html_in_caption_and_keyboard(self):
        # When site_base_url is already a full watch URL with /movie.html?id=...
        full_url = "https://filmsub.pages.dev/movie.html?id=aasha-2026"
        caption = build_branded_caption(
            title="Aasha",
            year="2026",
            quality="720p",
            slug="aasha-2026",
            site_base_url=full_url,
        )
        self.assertNotIn("movie.html?id=aasha-2026/movie.html", caption)
        self.assertIn(full_url, caption)

        kb = build_branded_inline_keyboard(
            slug="aasha-2026",
            site_base_url=full_url,
        )
        r1 = kb.inline_keyboard[0]
        self.assertEqual(r1[0].url, full_url)
        self.assertNotIn("movie.html?id=aasha-2026/movie.html", r1[0].url)


class TestTelegramUploadBrandingIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_upload_video_file_brands_filename_and_passes_reply_markup(self):
        from services import telegram_upload

        client_mock = MagicMock()
        mock_msg = MagicMock()
        mock_msg.video = MagicMock(file_id="vid_12345")
        mock_msg.document = None
        mock_msg.id = 999
        client_mock.send_video = AsyncMock(return_value=mock_msg)
        client_mock.get_chat = AsyncMock(return_value=MagicMock(title="Private Channel"))

        with tempfile.TemporaryDirectory() as td:
            video_file = os.path.join(td, "Aasha (2026) Malayalam WEB-[CineSubz.co]-480p.mp4")
            with open(video_file, "wb") as f:
                f.write(b"\x00" * 4096)

            dummy_kb = MagicMock()
            res = await telegram_upload.upload_video_file(
                bot_client=client_mock,
                file_path=video_file,
                target_chat=-1004325759505,
                caption="Test Caption",
                reply_markup=dummy_kb,
            )

            self.assertEqual(res.get("file_id"), "vid_12345")
            self.assertEqual(res.get("message_id"), 999)

            # Verify send_video arguments
            client_mock.send_video.assert_awaited_once()
            call_kwargs = client_mock.send_video.call_args.kwargs
            self.assertEqual(call_kwargs.get("reply_markup"), dummy_kb)
            self.assertEqual(call_kwargs.get("file_name"), "Aasha (2026) Malayalam WEB-[FilmSub.lk]-480p.mp4")
            self.assertIsNotNone(call_kwargs.get("thumb"))


if __name__ == "__main__":
    unittest.main()
