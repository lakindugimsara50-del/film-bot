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
            with patch("services.leech_service.find_all_candidates", return_value=[cand1, cand2]), \
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
            with patch("services.telegram_upload.upload_video_file", side_effect=fake_upload), \
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
                self.assertIn("HASH_1080P", hashes)

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
                self.assertIn("HASH_GENUINE_GOT", hashes)
                self.assertEqual(results[0]["hash"], "HASH_GENUINE_GOT")

        asyncio.run(run_test())

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


if __name__ == "__main__":
    unittest.main()

