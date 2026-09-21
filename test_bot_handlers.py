import asyncio
import unittest
from unittest.mock import patch, MagicMock
import os
import sys

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

if __name__ == '__main__':
    unittest.main()
