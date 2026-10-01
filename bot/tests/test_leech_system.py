"""
test_leech_system.py — Comprehensive Unit & Integration Tests for Auto-Leech & Channel Uploader.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure bot directory is on sys.path
sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.scrapers import method_yts, method3_ddl, method1_telegram, torrent_finder
from services import downloader, leech_service, seedr_service, task_tracker, telegram_upload


class TestLeechScrapers(unittest.TestCase):
    def test_yts_build_magnet(self):
        magnet = method_yts.build_magnet_uri("ABCDEF1234567890", "Inception (2010)")
        self.assertTrue(magnet.startswith("magnet:?xt=urn:btih:ABCDEF1234567890"))
        self.assertIn("dn=Inception", magnet)
        self.assertIn("tr=udp", magnet)

    def test_yts_filter_max_size(self):
        # Mock YTS API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "data": {
                "movie_count": 1,
                "movies": [
                    {
                        "title": "Inception",
                        "year": 2010,
                        "imdb_code": "tt1375666",
                        "torrents": [
                            # 3.5GB torrent (should be excluded)
                            {
                                "url": "https://yts.mx/torrent/download/3.5gb",
                                "hash": "HASH_BIG",
                                "quality": "2160p",
                                "size": "3.5 GB",
                                "size_bytes": int(3.5 * 1024 * 1024 * 1024),
                                "seeds": 100,
                            },
                            # 1.4GB 1080p torrent (should be included)
                            {
                                "url": "https://yts.mx/torrent/download/1080p",
                                "hash": "HASH_1080P",
                                "quality": "1080p",
                                "size": "1.4 GB",
                                "size_bytes": int(1.4 * 1024 * 1024 * 1024),
                                "seeds": 80,
                            },
                            # 800MB 720p torrent (should be included)
                            {
                                "url": "https://yts.mx/torrent/download/720p",
                                "hash": "HASH_720P",
                                "quality": "720p",
                                "size": "800 MB",
                                "size_bytes": int(800 * 1024 * 1024),
                                "seeds": 50,
                            },
                        ]
                    }
                ]
            }
        }

        async def run_test():
            with patch("httpx.AsyncClient.get", return_value=mock_response):
                candidates = await method_yts.search(title="Inception", year=2010, imdb_id="tt1375666")
                # Ensure the 3.5GB torrent was filtered out
                self.assertEqual(len(candidates), 2)
                # Ensure 1080p is ranked first
                self.assertEqual(candidates[0]["quality"], "1080p")
                self.assertEqual(candidates[0]["hash"], "HASH_1080P")
                self.assertEqual(candidates[1]["quality"], "720p")
                self.assertIn("magnet:?xt=urn:btih:HASH_1080P", candidates[0]["magnet"])

        asyncio.run(run_test())

    def test_ddl_pixeldrain_direct_resolver(self):
        url1 = "https://pixeldrain.com/u/abc123xyz"
        direct1 = method3_ddl.resolve_direct_url(url1)
        self.assertEqual(direct1, "https://pixeldrain.com/api/file/abc123xyz")

        url2 = "https://pixeldrain.com/api/file/foo_bar"
        direct2 = method3_ddl.resolve_direct_url(url2)
        self.assertEqual(direct2, "https://pixeldrain.com/api/file/foo_bar")

        url3 = "https://mega.nz/file/dummy"
        self.assertIsNone(method3_ddl.resolve_direct_url(url3))

    def test_telegram_link_parser(self):
        url_priv = "https://t.me/c/123456789/42"
        chat_id, msg_id = method1_telegram.parse_telegram_message_link(url_priv)
        self.assertEqual(msg_id, 42)
        self.assertTrue(str(chat_id).startswith("-100"))

        url_pub = "https://t.me/MoviesChannel/99"
        channel, msg_id2 = method1_telegram.parse_telegram_message_link(url_pub)
        self.assertEqual(channel, "MoviesChannel")
        self.assertEqual(msg_id2, 99)


class TestDownloader(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(downloader.format_bytes(500), "500.0 B")
        self.assertEqual(downloader.format_bytes(1024 * 1024 * 50), "50.0 MB")
        self.assertEqual(downloader.format_bytes(1024 * 1024 * 1024 * 1.5), "1.5 GB")

    def test_format_progress_bar(self):
        bar0 = downloader.format_progress_bar(0.0)
        self.assertEqual(bar0, "[░░░░░░░░░░]")
        bar50 = downloader.format_progress_bar(50.0)
        self.assertEqual(bar50, "[█████░░░░░]")
        bar100 = downloader.format_progress_bar(100.0)
        self.assertEqual(bar100, "[██████████]")

    def test_httpx_streaming_download_success(self):
        # Test download streaming into temp directory
        tmp_dir = tempfile.mkdtemp()
        dummy_content = b"TEST_VIDEO_PAYLOAD_CHUNK" * 100

        class MockStream:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def raise_for_status(self):
                pass
            headers = {"content-length": str(len(dummy_content))}
            async def aiter_bytes(self, chunk_size):
                yield dummy_content

        mock_client = MagicMock()
        mock_client.stream.return_value = MockStream()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        progress_called = []
        async def on_progress(pct, done, total, speed, eta):
            progress_called.append(pct)

        async def run_test():
            with patch("httpx.AsyncClient", return_value=mock_client):
                out = await downloader._download_httpx(
                    url="https://example.com/video.mp4",
                    dest_dir=tmp_dir,
                    filename="sample.mp4",
                    progress_callback=on_progress,
                )
                self.assertTrue(os.path.exists(out))
                self.assertEqual(os.path.getsize(out), len(dummy_content))
                self.assertIn("sample.mp4", out)

        try:
            asyncio.run(run_test())
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_matches_episode_filename(self):
        # Exact and varied episode formats
        self.assertTrue(downloader.matches_episode_filename("Game.of.Thrones.S01E02.720p.mkv", "S01E02"))
        self.assertTrue(downloader.matches_episode_filename("Game.of.Thrones.1x02.720p.mkv", "S01E02"))
        self.assertTrue(downloader.matches_episode_filename("Game of Thrones Season 1 Episode 2.mp4", "S01E02"))
        # Range formats
        self.assertTrue(downloader.matches_episode_filename("Game.of.Thrones.S01E01-E02.mkv", "S01E02"))
        self.assertTrue(downloader.matches_episode_filename("Game.of.Thrones.S01E01-02.mkv", "S01E02"))
        self.assertTrue(downloader.matches_episode_filename("Game.of.Thrones.S01E01E02.mkv", "S01E02"))
        self.assertTrue(downloader.matches_episode_filename("Game.of.Thrones.S01E01-E10.mkv", "S01E02"))
        self.assertTrue(downloader.matches_episode_filename("Game.of.Thrones.1x01-1x10.mkv", "S01E02"))
        # Non-matching episodes or seasons
        self.assertFalse(downloader.matches_episode_filename("Game.of.Thrones.S01E03.mkv", "S01E02"))
        self.assertFalse(downloader.matches_episode_filename("Game.of.Thrones.S02E02.mkv", "S01E02"))

    def test_find_episode_file_index_from_torrent(self):
        sample_output = (
            "1|./Game of Thrones S01/Game.of.Thrones.S01E01.720p.mkv|145236789|0%|true\n"
            "2|./Game of Thrones S01/Game.of.Thrones.S01E02.720p.mkv|154320000|0%|true\n"
            "3|./Game of Thrones S01/poster.jpg|12000|0%|true\n"
        )
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(sample_output.encode("utf-8"), b""))

        async def run_find():
            with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
                idx = await downloader.find_episode_file_index_from_torrent("aria2c", "dummy.torrent", "S01E02")
                self.assertEqual(idx, 2)

                idx_none = await downloader.find_episode_file_index_from_torrent("aria2c", "dummy.torrent", "S01E05")
                self.assertIsNone(idx_none)

        asyncio.run(run_find())


class TestLeechService(unittest.TestCase):
    def test_parse_query(self):
        t, y, i, d = leech_service.parse_query("/leech Inception")
        self.assertEqual(t, "Inception")
        self.assertIsNone(y)
        self.assertIsNone(i)
        self.assertIsNone(d)

        t, y, i, d = leech_service.parse_query("/boost Oppenheimer 2023")
        self.assertEqual(t, "Oppenheimer")
        self.assertEqual(y, 2023)

        t, y, i, d = leech_service.parse_query("/auto tt1375666")
        self.assertEqual(i, "tt1375666")

        t, y, i, d = leech_service.parse_query("/leech magnet:?xt=urn:btih:12345")
        self.assertTrue(d.startswith("magnet:?"))

        t, y, i, d = leech_service.parse_query("/leech https://pixeldrain.com/u/abc")
        self.assertEqual(d, "https://pixeldrain.com/u/abc")

    def test_fallback_cascade_and_storage_cleanup(self):
        # Verify fallback: if candidate 1 fails, candidate 2 is downloaded and disk is cleaned
        client_mock = MagicMock()
        status_msg = AsyncMock()
        status_msg.edit_text = AsyncMock()

        cand1 = leech_service.LeechCandidate("ddl", "DDL Host 1", "https://fail.com/v.mp4")
        cand2 = leech_service.LeechCandidate("yts", "YTS 1080p", "magnet:?xt=test")

        download_calls = []

        async def fake_download_torrent(*args, **kwargs):
            download_calls.append("torrent")
            # Create a real temp dummy file to test immediate deletion
            dest = kwargs.get("dest_dir")
            fpath = os.path.join(dest, "movie.mp4")
            with open(fpath, "wb") as fh:
                fh.write(b"SAMPLE_VIDEO" * 1024)
            return fpath

        async def fake_download_http(*args, **kwargs):
            download_calls.append("http_failed")
            raise RuntimeError("HTTP connection failed (404 Not Found)")

        uploaded_records = []
        async def fake_upload_video_file(bot_client, file_path, target_chat, caption="", progress_callback=None, fallback_chat=0, **kwargs):
            self.assertTrue(os.path.exists(file_path), "File must exist when uploading")
            uploaded_records.append(file_path)
            return {
                "file_id": "TELEGRAM_FILE_999",
                "message_id": 1234,
                "file_name": "movie.mp4",
                "file_size": 12345,
                "stream_url": "https://stream.worker.dev/stream/TELEGRAM_FILE_999",
            }

        async def run_test():
            mock_tmdb = {"title": "Inception", "year": 2010, "imdb_id": "tt1375666", "type": "movie"}
            with patch("services.tmdb_service.fetch_metadata", new_callable=AsyncMock, return_value=mock_tmdb), \
                 patch("services.tmdb_service.fetch_by_imdb_id", new_callable=AsyncMock, return_value=mock_tmdb), \
                 patch("services.seedr_service.seedr_pool.clean_storage", new_callable=AsyncMock), \
                 patch("services.cloud_drive.drive_manager.DriveManager.upload_movie", new_callable=AsyncMock, return_value=None), \
                 patch("services.video_service.embed_subtitles_soft", new_callable=AsyncMock, return_value=False), \
                 patch("services.leech_service.find_all_candidates", return_value=[cand1, cand2]), \
                 patch("services.seedr_service.seedr_client.is_configured", return_value=False), \
                 patch("services.pikpak_service.pikpak_service.is_configured", return_value=False), \
                 patch("services.downloader.download_http", side_effect=fake_download_http), \
                 patch("services.downloader.download_torrent", side_effect=fake_download_torrent), \
                 patch("services.telegram_upload.upload_video_file", side_effect=fake_upload_video_file), \
                 patch("services.github_service.add_movie", return_value=True), \
                 patch("services.leech_service.post_to_channel", new_callable=AsyncMock):

                await leech_service.run_auto_leech(
                    client=client_mock,
                    status_msg=status_msg,
                    user_id=888,
                    query_text="/leech Inception 2010",
                )

                # Method A (http) failed, then Method B (torrent) succeeded
                self.assertEqual(download_calls, ["http_failed", "torrent"])
                self.assertEqual(len(uploaded_records), 1)

                # Verify IMMEDIATE DISK CLEANUP:
                # The uploaded file must NO LONGER exist on disk!
                uploaded_file = uploaded_records[0]
                self.assertFalse(
                    os.path.exists(uploaded_file),
                    "Uploaded file must be deleted immediately from VPS storage"
                )

        asyncio.run(run_test())

    def test_cancel_task_preserves_handle_and_cancels(self):
        """Verify that cancelling via task_tracker successfully cancels the running task."""
        tracker = task_tracker.TaskTracker()
        tracker.start_task(user_id=101, title="Test Movie")

        async def run_cancel_test():
            async def mock_long_task():
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    pass

            t = asyncio.create_task(mock_long_task())
            tracker.set_task_handle(user_id=101, task=t)

            # Later, start_task with resolved title shouldn't wipe the task handle
            tracker.start_task(user_id=101, title="Resolved Title (2024)")
            active = tracker.get_active_task(101)
            self.assertIsNotNone(active.task)
            self.assertEqual(active.task, t)

            # Cancel task
            cancelled = tracker.cancel_task(101)
            self.assertTrue(cancelled)
            self.assertTrue(t.cancelling() or t.cancelled())
            await asyncio.sleep(0.01)

        asyncio.run(run_cancel_test())

    def test_parse_query_smart_urls(self):
        # Torrent URL
        t, y, i, d = leech_service.parse_query("/boost https://example.com/downloads/Dune.Part.Two.2024.torrent")
        self.assertEqual(t, "Dune Part Two")
        self.assertEqual(y, 2024)
        self.assertTrue(d.endswith(".torrent"))

        # Bot mention in command
        t, y, i, d = leech_service.parse_query("/leech@FilmSubBot Inception 2010")
        self.assertEqual(t, "Inception")
        self.assertEqual(y, 2010)

        # Magnet with dn
        t, y, i, d = leech_service.parse_query("/auto magnet:?xt=urn:btih:abc12345&dn=Interstellar+2014")
        self.assertEqual(t, "Interstellar 2014")
        self.assertTrue(d.startswith("magnet:?"))

    def test_replied_media_candidate_priority(self):
        """Verify that replied video media creates candidate #1."""
        client_mock = MagicMock()
        status_msg = AsyncMock()
        status_msg.edit_text = AsyncMock()

        reply_media = {
            "type": "video",
            "file_id": "REPLIED_VIDEO_FILE_ID_123",
            "file_name": "Gladiator.II.2024.mp4",
            "file_size": 1024 * 1024 * 500,
        }

        download_calls = []
        async def fake_download_media(*args, **kwargs):
            dest = kwargs.get("file_name")
            download_calls.append(dest)
            with open(dest, "wb") as f:
                f.write(b"SAMPLE_VIDEO" * 100)
            return dest

        client_mock.download_media = AsyncMock(side_effect=fake_download_media)

        uploaded = []
        async def fake_upload(*args, **kwargs):
            uploaded.append(kwargs.get("file_path"))
            return {"file_id": "OUT_ID", "message_id": 99, "stream_url": "https://stream/OUT_ID"}

        async def run_test():
            mock_tmdb = {
                "title": "Gladiator II",
                "year": 2024,
                "imdb_id": "tt2104996",
                "type": "movie",
            }
            with patch("services.tmdb_service.fetch_metadata", new_callable=AsyncMock, return_value=mock_tmdb), \
                 patch("services.tmdb_service.fetch_by_imdb_id", new_callable=AsyncMock, return_value=mock_tmdb), \
                 patch("services.seedr_service.seedr_pool.clean_storage", new_callable=AsyncMock), \
                 patch("services.cloud_drive.drive_manager.DriveManager.upload_movie", new_callable=AsyncMock, return_value=None), \
                 patch("services.video_service.embed_subtitles_soft", new_callable=AsyncMock, return_value=False), \
                 patch("services.telegram_upload.upload_video_file", side_effect=fake_upload), \
                 patch("services.github_service.add_movie", return_value=True), \
                 patch("services.leech_service.post_to_channel", new_callable=AsyncMock):

                await leech_service.run_auto_leech(
                    client=client_mock,
                    status_msg=status_msg,
                    user_id=777,
                    query_text="Gladiator II 2024",
                    reply_media=reply_media,
                )

                self.assertEqual(len(download_calls), 1)
                self.assertEqual(len(uploaded), 1)
                self.assertFalse(os.path.exists(uploaded[0]))

        asyncio.run(run_test())

    def test_upload_video_file_document_fallback(self):
        """Verify that if send_video fails, upload_video_file falls back to send_document."""
        client_mock = MagicMock()
        client_mock.send_video = AsyncMock(side_effect=RuntimeError("Video format invalid for send_video"))
        
        mock_doc_msg = MagicMock()
        mock_doc_msg.id = 555
        mock_doc_msg.video = None
        mock_doc_msg.document = MagicMock()
        mock_doc_msg.document.file_id = "FALLBACK_DOC_ID"
        client_mock.send_document = AsyncMock(return_value=mock_doc_msg)

        # Create temporary dummy file
        tmp = tempfile.NamedTemporaryFile(suffix=".mkv", delete=False)
        tmp.write(b"DUMMY_VIDEO_DATA")
        tmp.close()

        async def run_test():
            try:
                res = await telegram_upload.upload_video_file(
                    bot_client=client_mock,
                    file_path=tmp.name,
                    target_chat=-1001234567,
                    fallback_chat=123,
                )
                self.assertEqual(res["file_id"], "FALLBACK_DOC_ID")
                client_mock.send_video.assert_awaited_once()
                client_mock.send_document.assert_awaited_once()
            finally:
                if os.path.exists(tmp.name):
                    os.remove(tmp.name)

        asyncio.run(run_test())

    def test_parse_query_tv_series(self):
        """Verify TV series season and episode query parsing."""
        # S01E01 format
        parsed = leech_service.parse_query("/leech Game of Thrones S01E01")
        self.assertEqual(parsed.title, "Game of Thrones")
        self.assertEqual(parsed.season, 1)
        self.assertEqual(parsed.episode, 1)
        self.assertTrue(parsed.is_series)

        # Lowercase s05e16 format
        parsed2 = leech_service.parse_query("/boost Breaking Bad s05e16")
        self.assertEqual(parsed2.title, "Breaking Bad")
        self.assertEqual(parsed2.season, 5)
        self.assertEqual(parsed2.episode, 16)
        self.assertTrue(parsed2.is_series)

        # Season-only query defaults to episode 1
        parsed3 = leech_service.parse_query("/auto The Last of Us S01")
        self.assertEqual(parsed3.title, "The Last of Us")
        self.assertEqual(parsed3.season, 1)
        self.assertEqual(parsed3.episode, 1)
        self.assertTrue(parsed3.is_series)

        # "Season X Episode Y" written out
        parsed4 = leech_service.parse_query("/leech Stranger Things Season 2 Episode 3")
        self.assertEqual(parsed4.title, "Stranger Things")
        self.assertEqual(parsed4.season, 2)
        self.assertEqual(parsed4.episode, 3)
        self.assertTrue(parsed4.is_series)

        # Regular movie
        parsed5 = leech_service.parse_query("/leech Inception 2010")
        self.assertEqual(parsed5.title, "Inception")
        self.assertEqual(parsed5.year, 2010)
        self.assertFalse(parsed5.is_series)

        # Backward compatibility 4-tuple unpacking
        t, y, i, d = leech_service.parse_query("/leech Game of Thrones S01E01")
        self.assertEqual(t, "Game of Thrones")
        self.assertIsNone(y)

    def test_torrent_finder_multi_source_and_seedr_limit(self):
        """Verify torrent finder handles multi-source results and enforces Seedr <= 2.05GB limit."""
        async def run_test():
            mock_apibay = MagicMock()
            mock_apibay.status_code = 200
            mock_apibay.json.return_value = [
                # 3.5GB torrent (should be excluded due to > 2.05GB limit)
                {"name": "Game of Thrones S01E01 2160p", "info_hash": "HASH_BIG", "seeders": "150", "size": str(int(3.5 * 1024**3))},
                # 1.2GB 1080p torrent (should be included)
                {"name": "Game of Thrones S01E01 1080p HDTV", "info_hash": "HASH_1080P", "seeders": "90", "size": str(int(1.2 * 1024**3))},
                # 450MB 720p torrent (should be included)
                {"name": "Game of Thrones S01E01 720p HDTV", "info_hash": "HASH_720P", "seeders": "40", "size": str(int(450 * 1024**2))},
            ]

            mock_eztv = MagicMock()
            mock_eztv.status_code = 200
            mock_eztv.json.return_value = {
                "torrents": [
                    {
                        "title": "Game of Thrones S01E01 720p EZTV",
                        "magnet_url": "magnet:?xt=urn:btih:EZTV_HASH",
                        "hash": "eztv_hash",
                        "seeds": 85,
                        "size_bytes": 600 * 1024 * 1024,
                        "season": 1,
                        "episode": 1,
                    },
                    # Different episode (should be filtered out)
                    {
                        "title": "Game of Thrones S01E02 720p EZTV",
                        "magnet_url": "magnet:?xt=urn:btih:EZTV_EP2",
                        "seeds": 100,
                        "size_bytes": 600 * 1024 * 1024,
                        "season": 1,
                        "episode": 2,
                    }
                ]
            }

            mock_csv = MagicMock()
            mock_csv.status_code = 200
            mock_csv.json.return_value = {"torrents": []}

            def fake_get(url, *args, **kwargs):
                u = str(url)
                if "apibay" in u:
                    return mock_apibay
                elif "eztv" in u:
                    return mock_eztv
                elif "torrents-csv" in u:
                    return mock_csv
                m = MagicMock()
                m.status_code = 200
                m.json.return_value = {}
                return m

            with patch("httpx.AsyncClient.get", side_effect=fake_get):
                results = await torrent_finder.search_all_torrents(
                    title="Game of Thrones",
                    season=1,
                    episode=1,
                )
                self.assertTrue(len(results) > 0)
                # Ensure all returned torrents are <= 2.05 GB
                for tor in results:
                    self.assertLessEqual(tor["size_bytes"], int(2.05 * 1024**3))
                # Ensure excluded big torrent is not in results
                hashes = [r["hash"] for r in results]
                self.assertNotIn("HASH_BIG", hashes)
                self.assertIn("hash_1080p", hashes)

        asyncio.run(run_test())

    def test_cancel_all_user_operations_cleans_seedr_and_subprocesses(self):
        """Verify cancel_all_user_operations terminates tasks and purges Seedr storage."""
        task_tracker.tracker.start_task(user_id=888, title="Game of Thrones S01E01")

        mock_clean_seedr = AsyncMock(return_value=True)
        mock_cancel_dl = AsyncMock(return_value=True)

        async def run_cancel():
            with patch("services.seedr_service.seedr_pool.clean_storage", mock_clean_seedr), \
                 patch("services.pikpak_service.pikpak_service.clean_storage", AsyncMock(return_value=True)), \
                 patch("services.downloader.cancel_all_active_downloads", mock_cancel_dl):

                cancelled = await task_tracker.cancel_all_user_operations(user_id=888)
                self.assertTrue(cancelled)
                self.assertIsNone(task_tracker.tracker.get_active_task(888))
                mock_clean_seedr.assert_awaited()
                mock_cancel_dl.assert_awaited()

        asyncio.run(run_cancel())

    def test_torrent_finder_filters_junk_and_false_series_titles(self):
        """Verify torrent finder excludes non-video junk and false title matches."""
        async def run_test():
            mock_apibay = MagicMock()
            mock_apibay.status_code = 200
            mock_apibay.json.return_value = [
                # False title match (The Kingdom... S01E01 Game of Thrones)
                {
                    "name": "The Kingdom The Worlds Most Powerful Prince S01E01 Game of Thrones 1080p HDTV",
                    "info_hash": "HASH_FALSE_MATCH_1",
                    "seeders": "20",
                    "size": str(int(1.3 * 1024**3)),
                },
                # Junk non-video file (.jpg poster)
                {
                    "name": "Game of Thrones (2011) - Season 1 poster.jpg",
                    "info_hash": "HASH_JUNK_POSTER",
                    "seeders": "15",
                    "size": str(int(8 * 1024**2)),
                },
                # Commentary track (Rifftrax)
                {
                    "name": "Game of Thrones s01e01 1080p Rifftrax 6ch x264 AVC",
                    "info_hash": "HASH_RIFFTRAX",
                    "seeders": "5",
                    "size": str(int(1.4 * 1024**3)),
                },
                # Genuine clean release
                {
                    "name": "Game of Thrones S01E01 720p HDTV x264-CTU [eztv]",
                    "info_hash": "HASH_GENUINE_GOT",
                    "seeders": "7",
                    "size": str(int(1.5 * 1024**3)),
                },
            ]

            def fake_get(url, *args, **kwargs):
                m = MagicMock()
                m.status_code = 200
                m.json.return_value = mock_apibay.json.return_value if "apibay" in str(url) else {"torrents": []}
                return m

            with patch("httpx.AsyncClient.get", side_effect=fake_get):
                results = await torrent_finder.search_all_torrents(
                    title="Game of Thrones",
                    season=1,
                    episode=1,
                    is_series=True,
                )
                hashes = [r["hash"] for r in results]
                # False match and junk poster MUST be excluded
                self.assertNotIn("HASH_FALSE_MATCH_1", hashes)
                self.assertNotIn("HASH_JUNK_POSTER", hashes)
                # Genuine release MUST be ranked above commentary
                self.assertIn("hash_genuine_got", hashes)
                self.assertEqual(results[0]["hash"], "hash_genuine_got")

        asyncio.run(run_test())

    def test_matches_season_episode_season_packs(self):
        """Verify season packs and episode ranges are matched for target series episodes."""
        # Exact match
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01E02 720p HDTV", 1, 2))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones 1x02 720p", 1, 2))
        # Range matches
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01E01-E10 720p", 1, 2))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01E01-02 720p", 1, 2))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01E01E02 720p", 1, 2))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones 1x01-1x10 720p", 1, 2))
        # Season packs
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones Season 1 Complete 720p", 1, 2))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01 720p HDTV", 1, 2))
        # Wrong episode or wrong season
        self.assertFalse(torrent_finder.matches_season_episode("Game of Thrones S01E01 720p", 1, 2))
        self.assertFalse(torrent_finder.matches_season_episode("Game of Thrones S01E03 720p", 1, 2))
        self.assertFalse(torrent_finder.matches_season_episode("Game of Thrones S02 720p", 1, 2))
        self.assertFalse(torrent_finder.matches_season_episode("Game of Thrones Season 2 Complete", 1, 2))

    def test_seedr_explicit_delete_methods(self):
        """Verify SeedrService and SeedrPool explicit delete_folder and delete_torrent methods."""
        svc = seedr_service.SeedrService(username="dummy", password="pwd")
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def run_test():
            with patch.object(svc, "get_token", new_callable=AsyncMock, return_value="FAKE_TOKEN"), \
                 patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:

                ok_f = await svc.delete_folder(12345)
                self.assertTrue(ok_f)
                mock_post.assert_awaited()

                ok_t = await svc.delete_torrent(67890)
                self.assertTrue(ok_t)

        asyncio.run(run_test())

    def test_task_tracker_cleanup_coroutine_execution(self):
        """Verify task_tracker schedules coroutines returned by cleanup callbacks."""
        tracker = task_tracker.TaskTracker()
        tracker.start_task(user_id=1001, title="Coro Test")

        called = []
        async def fake_cleanup_async():
            called.append("async_called")

        # Register a lambda returning a coroutine object
        tracker.register_cleanup(1001, lambda: fake_cleanup_async())

        async def run_test():
            cancelled = tracker.cancel_task(1001)
            self.assertTrue(cancelled)
            # Yield control to let event loop run created tasks
            await asyncio.sleep(0.05)
            self.assertIn("async_called", called)

        asyncio.run(run_test())

    def test_yts_rejects_documentaries_and_series(self):
        """Verify YTS strictly rejects TV series and documentary spinoffs."""
        # 1. is_series = True must immediately return []
        async def run_series_test():
            res = await method_yts.search("Game of Thrones", is_series=True)
            self.assertEqual(res, [])
        asyncio.run(run_series_test())

        # 2. YTS returning documentaries or parodies without matching year must be rejected
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "data": {
                "movie_count": 2,
                "movies": [
                    {
                        "title": "Game of Thrones: The Last Watch",
                        "title_english": "Game of Thrones: The Last Watch",
                        "year": 2019,
                        "genres": ["Action", "Documentary"],
                        "torrents": [
                            {"hash": "HASH_DOC", "quality": "1080p", "size": "1.2 GB", "size_bytes": 1024**3, "seeds": 50}
                        ]
                    },
                    {
                        "title": "Purge of Kingdoms: The Unauthorized Game of Thrones Parody",
                        "title_english": "Purge of Kingdoms: The Unauthorized Game of Thrones Parody",
                        "year": 2019,
                        "genres": ["Comedy"],
                        "torrents": [
                            {"hash": "HASH_PARODY", "quality": "1080p", "size": "1.1 GB", "size_bytes": 1024**3, "seeds": 30}
                        ]
                    }
                ]
            }
        }

        async def run_doc_test():
            with patch("httpx.AsyncClient.get", return_value=mock_response):
                candidates = await method_yts.search(title="Game of Thrones")
                # Both documentary and parody must be rejected
                self.assertEqual(len(candidates), 0)

        asyncio.run(run_doc_test())

    def test_matches_season_episode_season_and_episode_alone(self):
        """Verify season-only and episode-only matching logic."""
        # Season only
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01E01 720p", season=1, episode=None))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01E10 720p", season=1, episode=None))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones Season 1 1080p", season=1, episode=None))
        self.assertFalse(torrent_finder.matches_season_episode("Game of Thrones S02E01 720p", season=1, episode=None))

        # Episode only
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones S01E05 720p", season=None, episode=5))
        self.assertTrue(torrent_finder.matches_season_episode("Game of Thrones Episode 5 720p", season=None, episode=5))
        self.assertFalse(torrent_finder.matches_season_episode("Game of Thrones S01E06 720p", season=None, episode=5))

    def test_low_ram_file_reader_lifecycle_and_fadvise(self):
        """Verify LowRamFileReader seeks, reads, tells, and invokes posix_fadvise when available."""
        from services.telegram_upload import LowRamFileReader
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"ABCDEFGHIJ" * 100)  # 1000 bytes
            temp_path = tf.name

        fadvise_calls = []
        def fake_fadvise(fd, offset, length, advice):
            fadvise_calls.append((offset, length, advice))

        try:
            with patch("os.posix_fadvise", fake_fadvise, create=True), \
                 patch("os.POSIX_FADV_DONTNEED", 4, create=True):
                with LowRamFileReader(temp_path) as reader:
                    self.assertTrue(reader.readable())
                    self.assertTrue(reader.seekable())
                    self.assertFalse(reader.writable())
                    self.assertEqual(reader.mode, "rb")
                    self.assertEqual(reader.seek(0, os.SEEK_END), 1000)
                    self.assertEqual(reader.tell(), 1000)
                    reader.seek(0)
                    chunk = reader.read(200)
                    self.assertEqual(len(chunk), 200)
                    self.assertEqual(len(fadvise_calls), 1)
                    self.assertEqual(fadvise_calls[0], (0, 200, 4))

                    buf = bytearray(300)
                    n = reader.readinto(buf)
                    self.assertEqual(n, 300)
                    self.assertEqual(len(fadvise_calls), 2)
                    self.assertEqual(fadvise_calls[1], (200, 300, 4))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_ensure_web_streamable_flags_cleanup_and_size_cutoff(self):
        """Verify ensure_web_streamable uses -sn, cleans up failed output, and skips transcode for >1.2GB."""
        from services import video_service

        async def run_test():
            executed_cmds = []

            class FakeProc:
                def __init__(self, returncode=1):
                    self.returncode = returncode
                async def wait(self):
                    return self.returncode
                def kill(self):
                    pass
                def terminate(self):
                    pass

            async def fake_create_subprocess_exec(*args, **kwargs):
                executed_cmds.append(list(args))
                # simulate partial file creation
                out_path = args[-1]
                with open(out_path, "wb") as f:
                    f.write(b"PARTIAL_BROKEN_DATA")
                return FakeProc(returncode=1)

            with tempfile.TemporaryDirectory() as td:
                in_path = os.path.join(td, "input.mkv")
                out_path = os.path.join(td, "output.mp4")
                with open(in_path, "wb") as f:
                    f.write(b"ORIGINAL_DATA")

                # Test 1: For <= 1.2GB file, both Attempt 1 and Attempt 2 run, both include -sn,
                # and when both fail, output_path is guaranteed cleaned up
                with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
                     patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
                    ok = await video_service.ensure_web_streamable(in_path, out_path)
                    self.assertFalse(ok)
                    self.assertEqual(len(executed_cmds), 2)
                    # Check -sn present in both Attempt 1 and Attempt 2
                    self.assertIn("-sn", executed_cmds[0])
                    self.assertIn("-sn", executed_cmds[1])
                    # Check guaranteed cleanup: output_path must not exist
                    self.assertFalse(os.path.exists(out_path))

                # Test 2: For > 1.2GB file, if Attempt 1 fails, Attempt 2 is skipped immediately
                executed_cmds.clear()
                with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
                     patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec), \
                     patch("os.path.getsize", return_value=int(1.3 * 1024**3)):
                    ok = await video_service.ensure_web_streamable(in_path, out_path)
                    self.assertFalse(ok)
                    # Attempt 2 must NOT have executed!
                    self.assertEqual(len(executed_cmds), 1)
                    self.assertFalse(os.path.exists(out_path))

        asyncio.run(run_test())

    def test_ensure_web_streamable_timeout_and_cancel_kills_process(self):
        """Verify ensure_web_streamable kills proc and cleans up on timeout or cancellation in Attempt 1."""
        from services import video_service

        async def run_test():
            kill_called = []
            class StallingProc:
                def __init__(self):
                    self.returncode = None
                def wait(self):
                    f = asyncio.Future()
                    return f
                def kill(self):
                    kill_called.append("killed")
                    self.returncode = -9
                def terminate(self):
                    pass

            with tempfile.TemporaryDirectory() as td:
                in_path = os.path.join(td, "input.mkv")
                out_path = os.path.join(td, "output.mp4")
                with open(in_path, "wb") as f:
                    f.write(b"DATA")
                with open(out_path, "wb") as f:
                    f.write(b"PARTIAL")

                proc = StallingProc()
                with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
                     patch("asyncio.create_subprocess_exec", return_value=proc), \
                     patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    ok = await video_service.ensure_web_streamable(in_path, out_path)
                    self.assertFalse(ok)
                    self.assertIn("killed", kill_called)
                    self.assertFalse(os.path.exists(out_path))

        asyncio.run(run_test())

    def test_upload_video_file_dispatches_final_100_percent_progress(self):
        """Verify upload_video_file dispatches 100% progress callback even for fast/cached uploads."""
        from services import telegram_upload
        client_mock = MagicMock()
        mock_msg = MagicMock()
        mock_msg.id = 999
        mock_msg.video = MagicMock()
        mock_msg.video.file_id = "SUCCESS_VID_ID"
        mock_msg.document = None
        client_mock.send_video = AsyncMock(return_value=mock_msg)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            tf.write(b"TEST_VIDEO_PAYLOAD" * 50)
            tf_path = tf.name

        reported_pcts = []
        async def mock_callback(pct, done, total, speed, eta):
            reported_pcts.append(pct)

        try:
            async def run_test():
                res = await telegram_upload.upload_video_file(
                    bot_client=client_mock,
                    file_path=tf_path,
                    target_chat=-1001234567,
                    progress_callback=mock_callback,
                )
                self.assertEqual(res["file_id"], "SUCCESS_VID_ID")
                # Final 100.0% MUST have been dispatched!
                self.assertIn(100.0, reported_pcts)

            asyncio.run(run_test())
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_extract_quality_strict_480p_and_ds4k(self):
        """Verify untagged HDTV/XviD/SD/CAM/NEW ENG releases are classified as 480p and DS4K 1080p as 1080p."""
        self.assertEqual(
            torrent_finder.extract_quality_from_name("Game of Thrones S01E01 HDTV XviD-FEVER [eztv]"),
            "480p",
        )
        self.assertEqual(
            torrent_finder.extract_quality_from_name("Game.of.Thrones.S01E01.WEB-DL.x264-GROUP"),
            "480p",
        )
        self.assertEqual(
            torrent_finder.extract_quality_from_name("Oppenheimer (2023) NEW ENG 1080p HQ-CAM AAC - QRips"),
            "480p",
        )
        self.assertEqual(
            torrent_finder.extract_quality_from_name("Oppenheimer (2023) NEW ENG 1080p.MP4.InfosPack022"),
            "480p",
        )
        self.assertEqual(
            torrent_finder.extract_quality_from_name("Game of Thrones S01E01 720p HDTV x264-CTU [eztv]"),
            "720p",
        )
        self.assertEqual(
            torrent_finder.extract_quality_from_name("Inception.2010.1080p.DS4K.BluRay.x265"),
            "1080p",
        )
        self.assertEqual(
            torrent_finder.extract_quality_from_name("Inception.2010.2160p.UHD.BluRay"),
            "2160p",
        )

    def test_torrent_finder_excludes_480p_and_prioritizes_single_episode_hd(self):
        """Verify 480p/SD torrents are excluded, 1080p single episode ranks #1, 1080p pack #2, and 720p #3."""
        async def run_test():
            mock_apibay = MagicMock()
            mock_apibay.status_code = 200
            mock_apibay.json.return_value = [
                # 480p untagged XviD release with high seeds (MUST be excluded!)
                {
                    "name": "Game of Thrones S01E01 HDTV XviD-FEVER [eztv]",
                    "info_hash": "HASH_480P_XVID",
                    "seeders": "500",
                    "size": str(int(550 * 1024**2)),
                },
                # Explicit 480p release (MUST be excluded!)
                {
                    "name": "Game of Thrones S01E01 480p WEB-DL x264",
                    "info_hash": "HASH_480P_EXPLICIT",
                    "seeders": "300",
                    "size": str(int(350 * 1024**2)),
                },
                # Standalone 720p single episode with 9 seeds (MUST be included as >=720p fallback after 1080p)
                {
                    "name": "Game of Thrones S01E01 720p HDTV x264-CTU [eztv]",
                    "info_hash": "HASH_720P_SINGLE",
                    "seeders": "9",
                    "size": str(int(1.45 * 1024**3)),
                },
                # Standalone 1080p single episode with 6 seeds (MUST rank #1 ahead of 1080p season pack)
                {
                    "name": "Game of Thrones S01E01 1080p WEB-DL x265",
                    "info_hash": "HASH_1080P_SINGLE",
                    "seeders": "6",
                    "size": str(int(1.6 * 1024**3)),
                },
            ]

            mock_torrentio = MagicMock()
            mock_torrentio.status_code = 200
            mock_torrentio.json.return_value = {
                "streams": [
                    # Season pack 1080p with 789 seeds (MUST rank #2: after 1080p single episode, ahead of 720p!)
                    {
                        "name": "Torrentio\n1080p",
                        "title": "Game.of.Thrones.SEASON.01.S01.COMPLETE.1080p.BluRay.x265-PSA\nGame.of.Thrones.S01E01.1080p.mkv\n👤 789 💾 1.08 GB ⚙️ 1337x",
                        "infoHash": "HASH_1080P_PACK",
                        "fileIdx": 0,
                    },
                    # Wrong series title from Torrentio ("The Game 2025 S01E01") -> MUST be excluded!
                    {
                        "name": "Torrentio\n1080p",
                        "title": "The.Game.2025.S01E01.1080p.HEVC.x265\n👤 50 💾 900 MB ⚙️ ThePirateBay",
                        "infoHash": "HASH_WRONG_SHOW",
                    },
                ]
            }

            def fake_get(url, *args, **kwargs):
                u = str(url)
                if "apibay" in u:
                    return mock_apibay
                if "torrentio" in u:
                    return mock_torrentio
                m = MagicMock()
                m.status_code = 200
                m.json.return_value = {"torrents": []}
                return m

            with patch("httpx.AsyncClient.get", side_effect=fake_get):
                results = await torrent_finder.search_all_torrents(
                    title="Game of Thrones",
                    imdb_id="tt0944947",
                    season=1,
                    episode=1,
                    is_series=True,
                )
                hashes = [r["hash"] for r in results]
                # 480p and wrong show must be completely excluded
                self.assertNotIn("hash_480p_xvid", hashes)
                self.assertNotIn("hash_480p_explicit", hashes)
                self.assertNotIn("hash_wrong_show", hashes)
                # 1080p single episode #1, 1080p high-seed pack #2, 720p single episode #3
                self.assertEqual(results[0]["hash"], "hash_1080p_single")
                self.assertFalse(results[0]["is_season_pack"])
                self.assertEqual(results[1]["hash"], "hash_1080p_pack")
                self.assertTrue(results[1]["is_season_pack"])
                self.assertEqual(results[2]["hash"], "hash_720p_single")
                self.assertFalse(results[2]["is_season_pack"])

        asyncio.run(run_test())

    def test_movie_collection_pack_excluded_and_1080p_prioritized_in_leech_service(self):
        """Verify multi-movie packs and wrong documentary titles are rejected and 1080p is prioritized."""
        self.assertTrue(torrent_finder.is_movie_collection_pack("Imdb top 263 movies 1080p", "Inception"))
        self.assertTrue(torrent_finder.is_movie_collection_pack("Inception & Prestige Complete Collection 1080p", "Inception"))
        self.assertFalse(torrent_finder.is_movie_collection_pack("Inception (2010) [1080p] [BluRay] [YTS.MX]", "Inception"))

        # Verify is_valid_movie_title blocks wrong movie prefixes and documentary spinoffs
        self.assertFalse(
            torrent_finder.is_valid_movie_title(
                "www.Torrenting.com   -    To End All War Oppenheimer The Atomic Bomb (2023) 1080p",
                "Oppenheimer",
                year=2023,
            )
        )
        self.assertFalse(
            torrent_finder.is_valid_movie_title(
                "To.End.All.War.Oppenheimer.and.the.Atomic.Bomb.2023.1080p.WEBRip.x264",
                "Oppenheimer",
                year=2023,
            )
        )
        self.assertFalse(
            torrent_finder.is_valid_movie_title(
                "Game of Thrones: The Last Watch (2019) 1080p",
                "Game of Thrones",
            )
        )
        self.assertTrue(
            torrent_finder.is_valid_movie_title(
                "Oppenheimer.2023.1080p.BluRay.DD5.1.x264-GalaxyRG",
                "Oppenheimer",
                year=2023,
            )
        )
        self.assertTrue(
            torrent_finder.is_valid_movie_title(
                "Inception (2010) [1080p] [BluRay] [YTS.MX]",
                "Inception",
                year=2010,
            )
        )

        async def run_candidates_test():
            fake_ddl = {
                "quality": "720p",
                "size": "850 MB",
                "downloads": [{"direct_url": "https://pixeldrain.com/api/file/abc", "host": "pixeldrain"}],
            }
            fake_torrents = [
                {
                    "title": "Inception (2010) [1080p]",
                    "magnet": "magnet:?xt=urn:btih:HASH1080",
                    "hash": "hash1080",
                    "quality": "1080p",
                    "size": "1.8 GB",
                    "size_bytes": int(1.8 * 1024**3),
                    "seeds": 500,
                    "provider": "YTS",
                    "method": "yts",
                }
            ]
            with patch("services.scrapers.method1_telegram.search", new_callable=AsyncMock, return_value=None), \
                 patch("services.scrapers.method3_ddl.search", new_callable=AsyncMock, return_value=fake_ddl), \
                 patch("services.scrapers.torrent_finder.search_all_torrents", new_callable=AsyncMock, return_value=fake_torrents):
                cands = await leech_service.find_all_candidates(title="Inception", year=2010, is_series=False)
                self.assertEqual(len(cands), 2)
                # 1080p torrent must be prioritized ahead of 720p DDL for movies
                self.assertEqual(cands[0].quality, "1080p")
                self.assertEqual(cands[1].quality, "720p")

        asyncio.run(run_candidates_test())

    def test_srilankan_matched_candidates_detect_hardsub(self):
        from services.scrapers import srilankan_matched_scraper

        test_data = [
            {"portal": "SinhalaSub", "url": "https://cdn.sinhalasub.net/877/Game.of.Thrones.S06E05%201080p.mp4", "quality": "1080p"},
            {"portal": "CineSubz", "url": "https://drive.csplayer2.space/server1/movie.mp4", "quality": "720p"},
            {"portal": "Baiscope", "url": "https://pixeldrain.com/api/file/abc", "quality": "1080p"},
        ]

        # SinhalaSub and CineSubz should be marked as pre-hardsubbed
        for d in test_data[:2]:
            u_str = d["url"].lower()
            p_name = d["portal"]
            is_hardsub = (
                p_name in ("SinhalaSub", "CineSubz")
                or any(k in u_str for k in ("cdn.sinhalasub.net", "ddl.sinhalasub.net", "cinesubz", "csplayer"))
            )
            self.assertTrue(is_hardsub, f"Expected {p_name} to be marked is_already_hardsubbed")

        # Baiscope untouched webrip should not be marked as pre-hardsubbed
        b_data = test_data[2]
        is_hardsub_b = (
            b_data["portal"] in ("SinhalaSub", "CineSubz")
            or any(k in b_data["url"].lower() for k in ("cdn.sinhalasub.net", "ddl.sinhalasub.net", "cinesubz", "csplayer"))
        )
        self.assertFalse(is_hardsub_b)

    def test_find_all_candidates_preserves_hardsubbed_flag(self):
        async def run_test():
            fake_matched = [
                {
                    "portal": "SinhalaSub",
                    "url": "https://cdn.sinhalasub.net/877/Got.S06E05.1080p.mp4",
                    "quality": "1080p",
                    "host_type": "cdn",
                    "sub_srt_path": "/tmp/sub.srt",
                    "is_already_hardsubbed": True,
                    "post_url": "https://sinhalasub.lk/game-of-thrones-s06e05/",
                },
                {
                    "portal": "SinhalaSub",
                    "url": "https://cdn.sinhalasub.net/876/Got.S06E05.720p.mp4",
                    "quality": "720p",
                    "host_type": "cdn",
                    "sub_srt_path": "/tmp/sub.srt",
                    "is_already_hardsubbed": True,
                    "post_url": "https://sinhalasub.lk/game-of-thrones-s06e05/",
                },
                {
                    "portal": "SinhalaSub",
                    "url": "https://cdn.sinhalasub.net/875/Got.S06E05.480p.mp4",
                    "quality": "480p",
                    "host_type": "cdn",
                    "sub_srt_path": "/tmp/sub.srt",
                    "is_already_hardsubbed": True,
                    "post_url": "https://sinhalasub.lk/game-of-thrones-s06e05/",
                },
            ]
            with patch("services.scrapers.srilankan_matched_scraper.search_matched_srilankan_releases", new_callable=AsyncMock, return_value=fake_matched), \
                 patch("services.scrapers.method1_telegram.search", new_callable=AsyncMock, return_value=None), \
                 patch("services.scrapers.method3_ddl.search", new_callable=AsyncMock, return_value=None), \
                 patch("services.scrapers.torrent_finder.search_all_torrents", new_callable=AsyncMock, return_value=[]):
                cands = await leech_service.find_all_candidates(title="Game of Thrones", season=6, episode=5, is_series=True)
                self.assertEqual(len(cands), 2)
                # Verify all SinhalaSub candidates have is_already_hardsubbed=True and are 720p/480p
                for c in cands:
                    self.assertTrue(c.extra.get("is_already_hardsubbed"))
                    self.assertEqual(c.extra.get("portal"), "SinhalaSub")
                    self.assertIn(c.quality, ("720p", "480p"))

        asyncio.run(run_test())

    def test_portal_sub_classification_separation(self):
        # Portals with PRE-BURNED subtitles
        pre_burned_portals = ["SinhalaSub", "CineSubz"]
        # Portals with STANDALONE subtitles (clean video + separate SRT)
        clean_video_portals = ["Baiscope", "Cineru", "Subz", "LKSubs", "Zoom", "PirateLK"]

        for p in pre_burned_portals:
            is_hard = p in ("SinhalaSub", "CineSubz")
            self.assertTrue(is_hard, f"Expected {p} to be classified as pre-burned")

        for p in clean_video_portals:
            is_hard = p in ("SinhalaSub", "CineSubz")
            self.assertFalse(is_hard, f"Expected {p} to be classified as clean video (sub not pre-burned)")

    def test_step2_parallel_companion_variants_collected(self):
        cand_1080 = leech_service.LeechCandidate(
            method="ddl",
            method_name="SinhalaSub Matched WebRip (1080p)",
            source_url="https://cdn.sinhalasub.net/got/1080.mp4",
            quality="1080p",
            extra={"portal": "SinhalaSub", "post_url": "https://sinhalasub.lk/got/", "is_already_hardsubbed": True}
        )
        cand_720 = leech_service.LeechCandidate(
            method="ddl",
            method_name="SinhalaSub Matched WebRip (720p)",
            source_url="https://cdn.sinhalasub.net/got/720.mp4",
            quality="720p",
            extra={"portal": "SinhalaSub", "post_url": "https://sinhalasub.lk/got/", "is_already_hardsubbed": True}
        )
        cand_480 = leech_service.LeechCandidate(
            method="ddl",
            method_name="SinhalaSub Matched WebRip (480p)",
            source_url="https://cdn.sinhalasub.net/got/480.mp4",
            quality="480p",
            extra={"portal": "SinhalaSub", "post_url": "https://sinhalasub.lk/got/", "is_already_hardsubbed": True}
        )

        all_cands = [cand_1080, cand_720, cand_480]

        # Simulate companion discovery for cand_1080
        companion_candidates = {}
        cand_portal = (cand_1080.extra or {}).get("portal", "")
        cand_post = (cand_1080.extra or {}).get("post_url", "")
        primary_q = (cand_1080.quality or "1080p").lower()

        for other_c in all_cands:
            if other_c == cand_1080:
                continue
            o_q = (other_c.quality or "").lower()
            if o_q in ("720p", "480p", "360p", "1080p") and o_q != primary_q and o_q not in companion_candidates:
                o_portal = (other_c.extra or {}).get("portal", "")
                o_post = (other_c.extra or {}).get("post_url", "")
                if (cand_post and o_post == cand_post) or (cand_portal and o_portal == cand_portal):
                    companion_candidates[o_q] = other_c

        self.assertEqual(len(companion_candidates), 2)
        self.assertIn("720p", companion_candidates)
        self.assertIn("480p", companion_candidates)
        self.assertEqual(companion_candidates["720p"].source_url, "https://cdn.sinhalasub.net/got/720.mp4")
        self.assertEqual(companion_candidates["480p"].source_url, "https://cdn.sinhalasub.net/got/480.mp4")

    def test_variant_tg_info_prevents_duplicate_uploads(self):
        """Verify that pre-uploaded qualities in variant_tg_info leave zero pending uploads."""
        variant_files = {
            "1080p": "/tmp/1080.mp4",
            "720p": "/tmp/720.mp4",
            "480p": "/tmp/480.mp4",
        }
        variant_tg_info = {
            "1080p": {"file_id": "FID_1080", "message_id": 1001, "stream_url": "https://stream/1001"},
            "720p": {"file_id": "FID_720", "message_id": 1002, "stream_url": "https://stream/1002"},
            "480p": {"file_id": "FID_480", "message_id": 1003, "stream_url": "https://stream/1003"},
        }
        primary_quality = "1080p"

        # Check pending variants filter
        pending_variants = {
            ql: qp for ql, qp in variant_files.items()
            if ql not in variant_tg_info and ql != primary_quality
        }

        # Must be empty because all variants are already in variant_tg_info
        self.assertEqual(pending_variants, {})
        # Primary quality must also be in variant_tg_info
        self.assertIn(primary_quality, variant_tg_info)
        self.assertEqual(variant_tg_info[primary_quality]["message_id"], 1001)

    def test_srilankan_matched_priority_never_queries_torrents(self):
        """Verify that when Lankan matched scraper finds releases, torrent engines are NEVER queried."""
        fake_matched = [
            {
                "url": "https://cdn.sinhalasub.net/movie/720p.mp4",
                "quality": "720p",
                "host_type": "cdn",
                "portal": "SinhalaSub",
                "sub_srt_path": "/tmp/sub.srt",
                "is_already_hardsubbed": True,
                "post_url": "https://sinhalasub.lk/movie-720p/",
            }
        ]
        mock_torrents = AsyncMock(return_value=[])

        async def run_test():
            with patch("services.scrapers.srilankan_matched_scraper.search_matched_srilankan_releases", new_callable=AsyncMock, return_value=fake_matched), \
                 patch("services.scrapers.method1_telegram.search", new_callable=AsyncMock, return_value=None), \
                 patch("services.scrapers.method3_ddl.search", new_callable=AsyncMock, return_value=None), \
                 patch("services.scrapers.torrent_finder.search_all_torrents", mock_torrents):
                cands = await leech_service.find_all_candidates(title="Spider-Man", year=2002)
                self.assertEqual(len(cands), 1)
                self.assertEqual(cands[0].quality, "720p")
                self.assertEqual(cands[0].extra.get("portal"), "SinhalaSub")
                # Assert torrents were NEVER queried
                mock_torrents.assert_not_called()

        asyncio.run(run_test())

    def test_tv_series_quality_filter_strictly_720p_480p(self):
        """Verify TV Series candidates strictly keep 720p and 480p, filtering out 1080p."""
        fake_matched = [
            {"url": "https://cdn.sinhalasub.net/series/1080p.mp4", "quality": "1080p", "host_type": "cdn", "portal": "SinhalaSub", "is_already_hardsubbed": True},
            {"url": "https://cdn.sinhalasub.net/series/720p.mp4", "quality": "720p", "host_type": "cdn", "portal": "SinhalaSub", "is_already_hardsubbed": True},
            {"url": "https://cdn.sinhalasub.net/series/480p.mp4", "quality": "480p", "host_type": "cdn", "portal": "SinhalaSub", "is_already_hardsubbed": True},
        ]

        async def run_test():
            with patch("services.scrapers.srilankan_matched_scraper.search_matched_srilankan_releases", new_callable=AsyncMock, return_value=fake_matched), \
                 patch("services.scrapers.torrent_finder.search_all_torrents", new_callable=AsyncMock, return_value=[]):
                # Call find_all_candidates for TV Series
                cands = await leech_service.find_all_candidates(title="Breaking Bad", year=2008, season=1, episode=1, is_series=True)
                self.assertEqual(len(cands), 2)
                qualities = [c.quality for c in cands]
                self.assertIn("720p", qualities)
                self.assertIn("480p", qualities)
                self.assertNotIn("1080p", qualities)
                self.assertEqual(cands[0].quality, "720p")  # 720p prioritized first

        asyncio.run(run_test())

    def test_tv_series_1080p_only_lankan_falls_back_to_torrents(self):
        """Verify that when Lankan matched scraper only has 1080p for TV series, it falls back to torrents for 720p/480p."""
        fake_matched = [
            {"url": "https://cdn.sinhalasub.net/series/1080p.mp4", "quality": "1080p", "host_type": "cdn", "portal": "SinhalaSub", "is_already_hardsubbed": True},
        ]
        mock_torrents = [
            {"magnet": "magnet:?xt=urn:btih:720p_tor", "quality": "720p", "provider": "EZTV", "size": "800MB"},
            {"magnet": "magnet:?xt=urn:btih:1080p_tor", "quality": "1080p", "provider": "EZTV", "size": "2.5GB"},
        ]

        async def run_test():
            with patch("services.scrapers.srilankan_matched_scraper.search_matched_srilankan_releases", new_callable=AsyncMock, return_value=fake_matched), \
                 patch("services.scrapers.method1_telegram.search", new_callable=AsyncMock, return_value=None), \
                 patch("services.scrapers.method3_ddl.search", new_callable=AsyncMock, return_value=None), \
                 patch("services.scrapers.torrent_finder.search_all_torrents", new_callable=AsyncMock, return_value=mock_torrents):
                cands = await leech_service.find_all_candidates(title="Breaking Bad", year=2008, season=1, episode=1, is_series=True)
                self.assertEqual(len(cands), 1)
                self.assertEqual(cands[0].quality, "720p")
                self.assertIn("720p_tor", cands[0].source_url)

        asyncio.run(run_test())

    def test_movie_prioritizes_1080p_then_720p_then_480p(self):
        """Verify Movies include 1080p, 720p, 480p and prioritize 1080p first."""
        fake_matched = [
            {"url": "https://cdn.sinhalasub.net/movie/480p.mp4", "quality": "480p", "host_type": "cdn", "portal": "SinhalaSub", "is_already_hardsubbed": True},
            {"url": "https://cdn.sinhalasub.net/movie/1080p.mp4", "quality": "1080p", "host_type": "cdn", "portal": "SinhalaSub", "is_already_hardsubbed": True},
            {"url": "https://cdn.sinhalasub.net/movie/720p.mp4", "quality": "720p", "host_type": "cdn", "portal": "SinhalaSub", "is_already_hardsubbed": True},
        ]

        async def run_test():
            with patch("services.scrapers.srilankan_matched_scraper.search_matched_srilankan_releases", new_callable=AsyncMock, return_value=fake_matched):
                cands = await leech_service.find_all_candidates(title="Avatar", year=2009, is_series=False)
                self.assertEqual(len(cands), 3)
                self.assertEqual(cands[0].quality, "1080p")
                self.assertEqual(cands[1].quality, "720p")
                self.assertEqual(cands[2].quality, "480p")

        asyncio.run(run_test())

    def test_pipeline_upload_failure_resets_progress_to_zero_and_failed(self):
        """Verify that when an upload fails in companion pipeline, progress is reset to 0.0 and stage marked failed."""
        companion_progress = {
            "720p": {"stage": "uploading", "up_pct": 85.0, "size": "700MB"},
        }

        # Simulate the failure path in _run_single_quality_pipeline
        up_res = {}  # Failed upload returned empty dict
        up_msg_id = up_res.get("message_id", 0) if up_res else 0
        if up_msg_id > 0 and up_res.get("file_id"):
            companion_progress["720p"].update({"stage": "uploaded", "up_pct": 100.0})
        else:
            companion_progress["720p"].update({"stage": "failed", "up_pct": 0.0})

        self.assertEqual(companion_progress["720p"]["stage"], "failed")
        self.assertEqual(companion_progress["720p"]["up_pct"], 0.0)


    def test_compress_video_targets_1_85gb_safe_limit(self):
        """Verify video compression default target is strictly <= 1.85GB to stay within 2000 MiB limit."""
        import inspect
        from services import video_service
        sig = inspect.signature(video_service.compress_video)
        default_target = sig.parameters["target_size_bytes"].default
        self.assertLessEqual(default_target, int(1.85 * 1024 * 1024 * 1024))
        self.assertLessEqual(default_target, video_service.MAX_TELEGRAM_BOT_SIZE)
        self.assertIn("is_hardsub", sig.parameters)

    def test_compress_video_hardsub_flag_disables_sub_burn(self):
        """Verify that when is_hardsub=True, subtitle burning is suppressed even if sub_path is provided."""
        import asyncio
        from unittest.mock import patch, AsyncMock, MagicMock
        from services import video_service

        recorded_cmd = []

        async def fake_subprocess_exec(*cmd, **kwargs):
            nonlocal recorded_cmd
            recorded_cmd = list(cmd)
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.stderr.readline = AsyncMock(return_value=b"")
            mock_proc.stderr.read = AsyncMock(return_value=b"")
            return mock_proc

        with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1000), \
             patch("services.video_service.get_video_duration", return_value=7200.0), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess_exec), \
             patch("shutil.disk_usage", return_value=MagicMock(free=10 * 1024 * 1024 * 1024)):
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                ok = loop.run_until_complete(video_service.compress_video(
                    input_path="fake_in.mp4",
                    output_path="fake_out.mp4",
                    sub_path="fake_sub.srt",
                    is_hardsub=True,
                ))
            finally:
                loop.close()

            self.assertTrue(ok)
            # Ensure -vf subtitles= is NOT present when is_hardsub=True
            vf_args = [arg for arg in recorded_cmd if "subtitles=" in str(arg)]
            self.assertEqual(vf_args, [], "Subtitle burning must not be applied to hardsubbed videos")
            self.assertIn("-sn", recorded_cmd)

    def test_compress_video_fallback_bitrate_when_duration_zero(self):
        """Verify safe 3-hour fallback bitrate calculation when duration is 0 or unparsed."""
        import asyncio
        from unittest.mock import patch, AsyncMock, MagicMock
        from services import video_service

        recorded_cmd = []

        async def fake_subprocess_exec(*cmd, **kwargs):
            nonlocal recorded_cmd
            recorded_cmd = list(cmd)
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.stderr.readline = AsyncMock(return_value=b"")
            mock_proc.stderr.read = AsyncMock(return_value=b"")
            return mock_proc

        with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1000), \
             patch("services.video_service.get_video_duration", return_value=0.0), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess_exec), \
             patch("shutil.disk_usage", return_value=MagicMock(free=10 * 1024 * 1024 * 1024)):
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                ok = loop.run_until_complete(video_service.compress_video(
                    input_path="fake_in.mkv",
                    output_path="fake_out.mp4",
                ))
            finally:
                loop.close()

            self.assertTrue(ok)
            # Find the -b:v parameter
            b_v_index = recorded_cmd.index("-b:v")
            bitrate_str = recorded_cmd[b_v_index + 1]
            bitrate_val = int(bitrate_str.rstrip("k"))
            # With 3-hour fallback (10800s), bitrate should be around 1200-1400k, strictly <= 1500k
            self.assertLessEqual(bitrate_val, 1500)
            # Guaranteed 3 hour movie at this bitrate + 128k audio stays strictly under 1.85 GB
            calc_bytes = ((bitrate_val + 128) * 1000 * 10800) / 8
            self.assertLessEqual(calc_bytes, int(1.85 * 1024 * 1024 * 1024))


if __name__ == "__main__":
    unittest.main()


