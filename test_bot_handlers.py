import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure bot directory is in sys.path
sys.path.insert(0, os.path.abspath("bot"))

import config
from handlers import add_movie, announce
from services import telegram_upload

class TestBotHandlers(unittest.TestCase):
    def test_build_movie_dict_complete_schema(self):
        meta = {
            "title": "Interstellar",
            "title_si": "ඉන්ටර්ස්ටෙලර්",
            "year": 2014,
            "rating": "8.5",
            "poster_url": "https://image.tmdb.org/t/p/w500/poster.jpg",
            "backdrop_url": "https://image.tmdb.org/t/p/original/backdrop.jpg",
            "genres": ["Adventure", "Drama", "Sci-Fi"],
            "duration": 169,
            "description": "Space exploration",
            "director": "Christopher Nolan",
            "cast": [{"name": "Matthew McConaughey", "character": "Cooper"}]
        }
        movie = add_movie._build_movie_dict(
            meta=meta,
            slug="interstellar",
            quality="1080p",
            lang="Sinhala",
            file_id="FILE123",
            stream_url="https://api.telegram.org/file/botTOKEN/videos/file_0",
            message_id=42,
            file_name="movie.mp4",
            file_size=2168431,
            subtitle_url="subs/interstellar-2014-sinhala.vtt",
            site_url="http://localhost:8000/movie.html?id=interstellar"
        )
        
        # Verify schema
        self.assertEqual(movie["id"], "interstellar-2014")
        self.assertEqual(movie["slug"], "interstellar")
        self.assertEqual(movie["poster"], "https://image.tmdb.org/t/p/w500/poster.jpg")
        self.assertEqual(movie["poster_url"], "https://image.tmdb.org/t/p/w500/poster.jpg")
        self.assertEqual(movie["backdrop"], "https://image.tmdb.org/t/p/original/backdrop.jpg")
        self.assertEqual(movie["backdrop_url"], "https://image.tmdb.org/t/p/original/backdrop.jpg")
        self.assertEqual(movie["imdb"], "8.5")
        self.assertEqual(movie["rating"], "8.5")
        self.assertTrue(movie["trending"])
        self.assertEqual(len(movie["streams"]), 1)
        self.assertEqual(movie["streams"][0]["stream_url"], "https://api.telegram.org/file/botTOKEN/videos/file_0")
        self.assertEqual(len(movie["downloads"]), 1)
        self.assertEqual(movie["downloads"][0]["url"], "https://api.telegram.org/file/botTOKEN/videos/file_0")
        self.assertEqual(len(movie["subtitles"]), 1)
        self.assertEqual(movie["subtitles"][0]["url"], "subs/interstellar-2014-sinhala.vtt")

    def test_announce_build_message(self):
        movie = {
            "title": "Interstellar",
            "year": 2014,
            "rating": "8.5",
            "genres": ["Sci-Fi", "Adventure"],
            "duration": 169,
            "description": "Space movie",
            "slug": "interstellar",
            "quality": "1080p",
            "lang": "Sinhala",
            "site_url": "http://localhost:8000/movie.html?id=interstellar",
            "downloads": [{"quality": "1080p", "url": "https://download.url"}],
            "files": []
        }
        msg = announce._build_message(movie)
        self.assertIn("Interstellar", msg)
        self.assertIn("http://localhost:8000/movie.html?id=interstellar", msg)
        self.assertIn("https://download.url", msg)

    def test_get_file_stream_url_strip_slashes(self):
        url = telegram_upload.get_file_stream_url("test_id")
        self.assertNotIn("dev//stream", url)
        self.assertTrue(url.endswith("/stream/test_id"))

    def test_task_tracker_lifecycle(self):
        from services.task_tracker import TaskTracker, TaskStatus
        tracker = TaskTracker()

        # 1. Initially idle
        self.assertIn("කිසිදු වැඩක් නැත", tracker.get_status_summary())
        self.assertIsNone(tracker.get_any_active_task())

        # 2. Start task
        info = tracker.start_task(user_id=123, title="Inception")
        self.assertEqual(info.status, TaskStatus.PROCESSING)
        self.assertEqual(tracker.get_any_active_task().title, "Inception")
        self.assertIn("දැනට ක්‍රියාත්මකයි", tracker.get_status_summary())
        self.assertIn("Inception", tracker.get_status_summary())

        # 3. Step updates
        tracker.set_step(123, "2/4 - සිංහල උපසිරැසි")
        self.assertIn("2/4", tracker.get_status_summary())

        # 4. Cancellation
        cancelled = tracker.cancel_task(123)
        self.assertTrue(cancelled)
        self.assertIsNone(tracker.get_any_active_task())
        self.assertIn("අවලංගු විය", tracker.get_status_summary())

        # 5. Failure
        tracker.start_task(user_id=456, title="Oppenheimer")
        tracker.fail_task(456, "Connection timeout")
        self.assertIn("අසාර්ථක විය", tracker.get_status_summary())
        self.assertIn("Oppenheimer", tracker.get_status_summary())
        self.assertIn("Connection timeout", tracker.get_status_summary())

        # 6. Success
        tracker.start_task(user_id=789, title="Dunkirk")
        tracker.complete_task(789)
        self.assertIn("අවසන් වූ කාර්යය", tracker.get_status_summary())
        self.assertIn("Dunkirk", tracker.get_status_summary())
        self.assertIn("කිසිදු වැඩක් නැත", tracker.get_status_summary())

        # 7. Cancel any active task without specific user_id
        tracker.start_task(user_id=999, title="Tenet")
        self.assertIsNotNone(tracker.get_any_active_task())
        cancelled_any = tracker.cancel_task(None)
        self.assertTrue(cancelled_any)
        self.assertIsNone(tracker.get_any_active_task())
        self.assertIn("අවලංගු විය", tracker.get_status_summary())
        self.assertIn("Tenet", tracker.get_status_summary())

    def test_task_tracker_per_user_isolation(self):
        from services.task_tracker import TaskTracker
        tracker = TaskTracker()

        # User 1 starts task A
        tracker.start_task(user_id=1, title="Movie One")
        # User 2 starts task B
        tracker.start_task(user_id=2, title="Movie Two")

        # User 1 checks status -> should see Movie One
        summary_u1 = tracker.get_status_summary(user_id=1)
        self.assertIn("Movie One", summary_u1)
        self.assertNotIn("Movie Two", summary_u1)

        # User 2 checks status -> should see Movie Two
        summary_u2 = tracker.get_status_summary(user_id=2)
        self.assertIn("Movie Two", summary_u2)
        self.assertNotIn("Movie One", summary_u2)

        # User 1 cancels Movie One
        tracker.cancel_task(user_id=1)
        # User 1 should see cancelled
        self.assertIn("අවලංගු විය", tracker.get_status_summary(user_id=1))
        self.assertIn("Movie One", tracker.get_status_summary(user_id=1))

        # User 2's task should still be processing!
        self.assertIn("දැනට ක්‍රියාත්මකයි", tracker.get_status_summary(user_id=2))
        self.assertIn("Movie Two", tracker.get_status_summary(user_id=2))

        # User 2 completes
        tracker.complete_task(user_id=2)
        self.assertIn("අවසන් වූ කාර්යය", tracker.get_status_summary(user_id=2))

        # User 1 still sees their cancellation preserved
        self.assertIn("අවලංගු විය", tracker.get_status_summary(user_id=1))
        self.assertIn("Movie One", tracker.get_status_summary(user_id=1))

    def test_site_url_sanitization_and_never_yoursite_lk(self):
        from handlers.sub_handler import _clean_site_url
        clean_url = _clean_site_url("avatar-3")
        self.assertNotIn("yoursite.lk", clean_url)
        self.assertTrue(clean_url.startswith("https://filmsub.pages.dev"))

        # Test announce._build_message sanitization
        movie = {
            "title": "Cocaine Bear",
            "year": 2023,
            "rating": "6.0",
            "genres": ["Comedy", "Thriller"],
            "duration": 95,
            "description": "Wild bear story",
            "slug": "cocaine-bear-2023",
            "quality": "1080p",
            "lang": "Sinhala",
            "site_url": "https://yoursite.lk/movie.html?id=cocaine-bear-2023",
            "downloads": [],
            "files": []
        }
        msg = announce._build_message(movie)
        self.assertNotIn("yoursite.lk", msg)
        self.assertIn("https://filmsub.pages.dev/movie.html?id=cocaine-bear-2023", msg)

    def test_pyrogram_64bit_channel_support(self):
        import pyrogram.utils
        filmhost_channel_id = -1004325759505
        # Must not raise ValueError: Peer id invalid
        peer_type = pyrogram.utils.get_peer_type(filmhost_channel_id)
        self.assertEqual(peer_type, "channel")

    def test_draft_service_full_metadata_lifecycle(self):
        from services import draft_service
        draft_payload = {
            "title_hint": "Deadpool and Wolverine",
            "movie_name": "Deadpool & Wolverine",
            "year": 2024,
            "file_id": "BAACAgIAAxkBAAI_...",
            "message_id": 12345,
            "stream_url": "https://stream.worker.dev/stream/12345",
            "quality": "1080p",
            "channel_id": -1004325759505,
            "movie_entry": {
                "id": "deadpool-and-wolverine-2024",
                "slug": "deadpool-and-wolverine-2024",
                "title": "Deadpool & Wolverine",
                "subtitles": []
            }
        }
        draft_id = draft_service.save_draft(draft_payload)
        self.assertTrue(draft_id)

        retrieved = draft_service.get_draft(draft_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["channel_id"], -1004325759505)
        self.assertEqual(retrieved["stream_url"], "https://stream.worker.dev/stream/12345")
        self.assertEqual(retrieved["movie_entry"]["title"], "Deadpool & Wolverine")

        # Test update
        updated = draft_service.update_draft(draft_id, {"subtitle_url": "data:text/vtt;..."})
        self.assertTrue(updated)
        retrieved2 = draft_service.get_draft(draft_id)
        self.assertEqual(retrieved2["subtitle_url"], "data:text/vtt;...")

        # Test delete
        deleted = draft_service.delete_draft(draft_id)
        self.assertTrue(deleted)
        self.assertIsNone(draft_service.get_draft(draft_id))

    def test_telegram_upload_never_dumps_to_dm_when_channel_configured(self):
        from services import telegram_upload
        with patch.object(telegram_upload, "PRIVATE_CHANNEL_ID", -1004325759505):
            mock_client = MagicMock()
            mock_client.is_connected = True
            # Simulate failure when sending to channel
            mock_client.send_video.side_effect = Exception("FloodWait")
            mock_client.send_document.side_effect = Exception("FloodWait")

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_vid:
                tmp_vid.write(b"dummy video data")
                tmp_vid_path = tmp_vid.name

            try:
                loop = asyncio.new_event_loop()
                with self.assertRaises(RuntimeError) as ctx:
                    loop.run_until_complete(
                        telegram_upload.upload_video_file(
                            bot_client=mock_client,
                            file_path=tmp_vid_path,
                            target_chat=-1004325759505,
                            fallback_chat=12345678,  # User DM ID
                        )
                    )
                self.assertIn("failed", str(ctx.exception).lower())
                # Verify send_video was NEVER attempted to fallback_chat
                for call_args in mock_client.send_video.call_args_list:
                    self.assertNotEqual(call_args.kwargs.get("chat_id"), 12345678)
            finally:
                if os.path.exists(tmp_vid_path):
                    os.remove(tmp_vid_path)
                loop.close()

    def test_github_service_add_movie_in_place_update(self):
        from services import github_service
        existing_movies = [
            {"id": "avatar-3", "slug": "avatar-3", "title": "Avatar 3", "subtitles": []},
            {"id": "oppenheimer", "slug": "oppenheimer", "title": "Oppenheimer", "subtitles": []}
        ]

        updated_movie = {
            "id": "avatar-3",
            "slug": "avatar-3",
            "title": "Avatar 3",
            "subtitles": [{"language": "Sinhala", "label": "Sinhala Sub", "url": "data:text/vtt;..."}],
            "has_sinhala_sub": True
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_movies_path = os.path.join(tmpdir, "movies.json")
            with patch.object(github_service, "_LOCAL_MOVIES_PATH", tmp_movies_path), \
                 patch.object(github_service, "get_movies_json", return_value=({"movies": existing_movies}, "local")), \
                 patch.object(github_service, "GITHUB_TOKEN", ""):
                
                loop = asyncio.new_event_loop()
                try:
                    res = loop.run_until_complete(github_service.add_movie(updated_movie))
                    self.assertTrue(res)
                    with open(tmp_movies_path, "r", encoding="utf-8") as f:
                        saved_data = json.load(f)
                    committed_movies = saved_data["movies"]
                    # Must update in-place without adding duplicate!
                    self.assertEqual(len(committed_movies), 2)
                    avatar = next(m for m in committed_movies if m["slug"] == "avatar-3")
                    self.assertTrue(avatar["has_sinhala_sub"])
                    self.assertEqual(len(avatar["subtitles"]), 1)
                finally:
                    loop.close()

    def test_auth_service_is_admin_check(self):
        from services.auth_service import AuthService
        service = AuthService()
        with patch("config.ADMIN_IDS", [111, 222]):
            service._authorized_ids = {333}
            self.assertTrue(service.is_admin(111))
            self.assertTrue(service.is_admin(222))
            self.assertTrue(service.is_admin(333))
            self.assertFalse(service.is_admin(444))

if __name__ == '__main__':
    unittest.main()


