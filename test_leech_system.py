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

from services.scrapers import method_yts, method3_ddl, method1_telegram
from services import downloader, leech_service, task_tracker, telegram_upload


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


if __name__ == "__main__":
    unittest.main()
