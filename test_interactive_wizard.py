import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure bot directory is in sys.path
sys.path.insert(0, os.path.abspath("bot"))

import config
from handlers import wizard, sub_handler
from services import draft_service

class TestInteractiveWizard(unittest.TestCase):
    def setUp(self):
        wizard.USER_SESSIONS.clear()

    def test_extract_title_hint(self):
        self.assertEqual(wizard._extract_title_hint("Cocaine.Bear.2023.1080p.WEBRip.x264.mp4"), "Cocaine Bear 2023")
        self.assertEqual(wizard._extract_title_hint("Avatar.Fire.and.Ash.2025.720p.mkv"), "Avatar Fire and Ash 2025")

    def test_text_handler_reply_to_video_initiates_search(self):
        """When a user replies to a video message with a movie title, wizard should detect video and query TMDB."""
        app_mock = MagicMock()
        registered_handlers = []
        app_mock.on_message = lambda *args, **kwargs: (lambda fn: registered_handlers.append(fn) or fn)
        app_mock.on_callback_query = lambda *args, **kwargs: (lambda fn: registered_handlers.append(fn) or fn)

        wizard.register(app_mock)
        text_handler_fn = next(fn for fn in registered_handlers if fn.__name__ == "text_handler")

        # Mock reply message with video
        mock_reply = MagicMock()
        mock_reply.id = 999
        mock_reply.chat.id = -1004325759505
        mock_reply.video = MagicMock(file_id="VID_ABC_123", file_name="movie.mp4", file_size=1024*1024*500)
        mock_reply.document = None

        mock_msg = MagicMock()
        mock_msg.from_user.id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 12345
        mock_msg.text = "Cocaine Bear 2023"
        mock_msg.reply_to_message = mock_reply

        with patch("handlers.wizard._is_admin", return_value=True), \
             patch("handlers.wizard._handle_movie_name_search", new_callable=AsyncMock) as mock_search:
            
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(text_handler_fn(app_mock, mock_msg))
                self.assertTrue(mock_search.called)
                args = mock_search.call_args[0]
                session_arg = args[2]
                self.assertEqual(session_arg["file_id"], "VID_ABC_123")
                self.assertEqual(session_arg["reply_message_id"], 999)
                self.assertEqual(session_arg["title_hint"], "Cocaine Bear 2023")
            finally:
                loop.close()

    def test_standalone_subtitle_upload_detected(self):
        """When user uploads an .srt or .vtt file without being in wizard, it should present draft/movie choices."""
        app_mock = MagicMock()
        registered_handlers = []
        app_mock.on_message = lambda *args, **kwargs: (lambda fn: registered_handlers.append(fn) or fn)
        app_mock.on_callback_query = lambda *args, **kwargs: (lambda fn: registered_handlers.append(fn) or fn)

        wizard.register(app_mock)
        media_handler_fn = next(fn for fn in registered_handlers if fn.__name__ == "media_upload_handler")

        mock_doc = MagicMock(file_name="subtitles.srt", file_id="SUB_FILE_ID")
        mock_msg = MagicMock()
        mock_msg.from_user.id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 12345
        mock_msg.document = mock_doc
        mock_msg.video = None

        with patch("handlers.wizard._is_admin", return_value=True), \
             patch("handlers.wizard._handle_standalone_subtitle_upload", new_callable=AsyncMock) as mock_standalone:
            
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(media_handler_fn(app_mock, mock_msg))
                self.assertTrue(mock_standalone.called)
                self.assertEqual(mock_standalone.call_args[0][2], mock_doc)
            finally:
                loop.close()

    def test_wizard_pub_direct_callback(self):
        """Clicking wiz:pub_direct should publish immediately without manual sub prompt."""
        app_mock = MagicMock()
        registered_cbs = []
        app_mock.on_callback_query = lambda *args, **kwargs: (lambda fn: registered_cbs.append(fn) or fn)
        app_mock.on_message = lambda *args, **kwargs: (lambda fn: fn)

        wizard.register(app_mock)
        cb_fn = next(fn for fn in registered_cbs if fn.__name__ == "wizard_callbacks")

        user_id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 12345
        wizard.USER_SESSIONS[user_id] = {
            "session_id": "sess_1",
            "step": "CHOICE",
            "file_id": "VID_1",
            "file_name": "test.mp4",
            "file_size": 1000,
            "title_hint": "Test Movie",
            "meta": {"title": "Test Movie", "year": 2024}
        }

        mock_query = MagicMock()
        mock_query.data = "wiz:pub_direct"
        mock_query.from_user.id = user_id
        mock_query.answer = AsyncMock()
        mock_query.message.edit_text = AsyncMock()

        with patch("handlers.wizard._is_admin", return_value=True), \
             patch("handlers.wizard._finalize_and_publish", new_callable=AsyncMock) as mock_finalize:
            
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(cb_fn(app_mock, mock_query))
                self.assertTrue(mock_query.answer.called)
                self.assertNotIn(user_id, wizard.USER_SESSIONS)
            finally:
                loop.close()

    def test_update_published_movie_sub(self):
        """When user clicks Add Sub on published movie and uploads .vtt, it updates movies.json."""
        user_id = 12345
        slug = "cocaine-bear-2023"
        wizard.USER_SESSIONS[user_id] = {"step": "WAITING_SUB", "slug": slug}

        existing_movies = [
            {"id": "cocaine-bear-2023", "slug": "cocaine-bear-2023", "title": "Cocaine Bear", "year": 2023, "subtitles": []}
        ]

        app_mock = MagicMock()
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()

        with patch("services.github_service.get_movies_json", AsyncMock(return_value=({"movies": existing_movies}, "sha123"))), \
             patch("services.github_service.add_movie", AsyncMock(return_value=True)) as mock_add_movie:

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    wizard._update_published_movie_sub(
                        client=app_mock,
                        status_msg=status_msg,
                        slug=slug,
                        sub_url="data:text/vtt;charset=utf-8,WEBVTT...",
                        user_id=user_id,
                    )
                )
                self.assertTrue(mock_add_movie.called)
                updated = mock_add_movie.call_args[0][0]
                self.assertEqual(updated["slug"], slug)
                self.assertTrue(updated["has_sinhala_sub"])
                self.assertEqual(len(updated["subtitles"]), 1)
                self.assertNotIn(user_id, wizard.USER_SESSIONS)
            finally:
                loop.close()

    def test_add_command_reply_to_video_routes_to_search(self):
        """Replying to video with /add Cocaine Bear should initiate search and not crash legacy one-liner."""
        app_mock = MagicMock()
        registered_handlers = []
        app_mock.on_message = lambda *args, **kwargs: (lambda fn: registered_handlers.append(fn) or fn)
        app_mock.on_callback_query = lambda *args, **kwargs: (lambda fn: registered_handlers.append(fn) or fn)

        wizard.register(app_mock)
        add_fn = next(fn for fn in registered_handlers if fn.__name__ == "add_command_router")

        mock_reply = MagicMock()
        mock_reply.id = 888
        mock_reply.chat.id = -1004325759505
        mock_reply.video = MagicMock(file_id="VID_888", file_name="movie.mp4", file_size=50000)
        mock_reply.document = None

        mock_msg = MagicMock()
        mock_msg.from_user.id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 12345
        mock_msg.text = "/add Cocaine Bear"
        mock_msg.reply_to_message = mock_reply

        with patch("handlers.wizard._is_admin", return_value=True), \
             patch("handlers.wizard._handle_movie_name_search", new_callable=AsyncMock) as mock_search:

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(add_fn(app_mock, mock_msg))
                self.assertTrue(mock_search.called)
                args = mock_search.call_args[0]
                self.assertEqual(args[3], "Cocaine Bear")
            finally:
                loop.close()

    def test_drafts_menu_includes_sub_buttons(self):
        """_show_drafts_menu should include sub button for each draft."""
        drafts = [
            {"id": "draft_1_test", "title_hint": "Draft Movie 1", "file_size": 1000, "created_at": "2026-09-22"}
        ]
        msg = MagicMock()
        msg.reply_text = AsyncMock()

        with patch("services.draft_service.list_drafts", return_value=drafts):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(wizard._show_drafts_menu(msg, edit=False))
                self.assertTrue(msg.reply_text.called)
                kb = msg.reply_text.call_args.kwargs["reply_markup"]
                row = kb.inline_keyboard[0]
                callbacks = [btn.callback_data for btn in row]
                self.assertIn("draft:pub:draft_1_test", callbacks)
                self.assertIn("leech_act:sub:draft_1_test", callbacks)
                self.assertIn("draft:del:draft_1_test", callbacks)
            finally:
                loop.close()

    def test_sub_action_callback_long_slug(self):
        """sub_action_callback should match movies even when slug is long and sliced."""
        user_id = 999
        sub_handler.PENDING_SUB_DOCS[user_id] = "FILE_ID_SUB"

        existing_movies = [
            {
                "id": "deadpool-and-wolverine-2024-sinhala-sub",
                "slug": "deadpool-and-wolverine-2024-sinhala-sub",
                "title": "Deadpool & Wolverine",
                "subtitles": []
            }
        ]

        app_mock = MagicMock()
        app_mock.download_media = AsyncMock(return_value="dummy.vtt")

        mock_query = MagicMock()
        mock_query.from_user.id = user_id
        mock_query.from_user.username = "admin"
        # 45-character sliced slug
        mock_query.data = "sub_act:movie:deadpool-and-wolverine-2024-sinhala-s"
        mock_query.answer = AsyncMock()
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        mock_query.message.reply_text = AsyncMock(return_value=status_msg)

        registered_cbs = []
        app_mock.on_callback_query = lambda *args, **kwargs: (lambda fn: registered_cbs.append(fn) or fn)
        app_mock.on_message = lambda *args, **kwargs: (lambda fn: fn)

        sub_handler.register(app_mock)
        cb_fn = next(fn for fn in registered_cbs if fn.__name__ == "sub_action_callback")

        with patch("services.auth_service.auth_service.is_authorized", return_value=True), \
             patch("services.github_service.get_movies_json", AsyncMock(return_value=({"movies": existing_movies}, "sha"))), \
             patch("services.github_service.add_movie", AsyncMock(return_value=True)) as mock_add, \
             patch("builtins.open", unittest.mock.mock_open(read_data="WEBVTT\n1\n00:00:01 --> 00:00:05\nTest")):

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(cb_fn(app_mock, mock_query))
                self.assertTrue(mock_add.called)
                target_saved = mock_add.call_args[0][0]
                self.assertEqual(target_saved["slug"], "deadpool-and-wolverine-2024-sinhala-sub")
                self.assertTrue(target_saved["has_sinhala_sub"])
            finally:
                loop.close()

    def test_save_draft_with_meta_builds_movie_entry(self):
        """Clicking wiz:save_draft when session has TMDB meta should build full movie_entry."""
        app_mock = MagicMock()
        registered_cbs = []
        app_mock.on_callback_query = lambda *args, **kwargs: (lambda fn: registered_cbs.append(fn) or fn)
        app_mock.on_message = lambda *args, **kwargs: (lambda fn: fn)

        wizard.register(app_mock)
        cb_fn = next(fn for fn in registered_cbs if fn.__name__ == "wizard_callbacks")

        user_id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 12345
        wizard.USER_SESSIONS[user_id] = {
            "session_id": "sess_meta_1",
            "step": "CHOICE",
            "file_id": "VID_DRAFT_1",
            "file_name": "cocaine_bear.mp4",
            "file_size": 1024 * 1024 * 500,
            "title_hint": "Cocaine Bear 2023",
            "meta": {"title": "Cocaine Bear", "year": 2023, "imdb": "6.0", "genres": ["Comedy", "Thriller"]},
        }

        mock_query = MagicMock()
        mock_query.data = "wiz:save_draft"
        mock_query.from_user.id = user_id
        mock_query.answer = AsyncMock()
        mock_query.message.edit_text = AsyncMock()

        with patch("handlers.wizard._is_admin", return_value=True), \
             patch("services.draft_service.save_draft") as mock_save:
            mock_save.return_value = "draft_saved_123"

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(cb_fn(app_mock, mock_query))
                self.assertTrue(mock_save.called)
                saved_session = mock_save.call_args[0][0]
                self.assertIn("movie_entry", saved_session)
                self.assertEqual(saved_session["movie_entry"]["title"], "Cocaine Bear")
                self.assertNotIn("yoursite.lk", saved_session["movie_entry"]["site_url"])
                self.assertTrue(saved_session["movie_entry"]["site_url"].startswith("https://filmsub.pages.dev"))
            finally:
                loop.close()

if __name__ == '__main__':
    unittest.main()
