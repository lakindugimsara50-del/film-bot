"""
test_pikpak_service.py — Unit and integration tests for PikPak cloud torrent service.
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

from services.pikpak_service import PikPakService


class TestPikPakService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.creds_file = os.path.join(self.test_dir, "pikpak_creds.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_credentials_save_and_load(self):
        with patch("services.pikpak_service.CREDS_FILE", self.creds_file):
            service = PikPakService()
            self.assertFalse(service.is_configured())

            service.save_credentials("test@example.com", "mypassword123")
            self.assertTrue(service.is_configured())

            # New instance should read saved credentials
            service2 = PikPakService()
            self.assertTrue(service2.is_configured())
            self.assertEqual(service2._username, "test@example.com")
            self.assertEqual(service2._password, "mypassword123")

    async def test_get_quota_summary(self):
        with patch("services.pikpak_service.CREDS_FILE", self.creds_file):
            service = PikPakService(username="user@test.com", password="pwd")
            mock_client = AsyncMock()
            mock_client.get_quota_info.return_value = {
                "quota": {
                    "limit": str(10 * 1024 * 1024 * 1024),  # 10 GB
                    "usage": str(2 * 1024 * 1024 * 1024),   # 2 GB
                }
            }
            service._client = mock_client

            quota = await service.get_quota_summary()
            self.assertEqual(quota["limit_gb"], 10.0)
            self.assertEqual(quota["usage_gb"], 2.0)
            self.assertEqual(quota["free_gb"], 8.0)
            self.assertEqual(quota["username"], "user@test.com")

    async def test_convert_magnet_to_direct_url_success(self):
        with patch("services.pikpak_service.CREDS_FILE", self.creds_file):
            service = PikPakService(username="user@test.com", password="pwd")
            mock_client = AsyncMock()

            # Mock offline_download
            mock_client.offline_download.return_value = {
                "task": {
                    "id": "task_123",
                    "file_id": "folder_456",
                    "phase": "PHASE_TYPE_COMPLETE",
                    "progress": 100,
                }
            }

            # Mock file_list returning files in folder
            mock_client.file_list.return_value = {
                "files": [
                    {"id": "file_sample", "name": "sample.mp4", "size": str(50 * 1024 * 1024)},
                    {"id": "file_movie", "name": "Avatar.2009.1080p.mp4", "size": str(2500 * 1024 * 1024)},
                    {"id": "file_nfo", "name": "info.txt", "size": "1024"},
                ]
            }

            # Mock get_download_url
            mock_client.get_download_url.return_value = {
                "web_content_link": "https://download.mypikpak.com/file_movie.mp4",
                "name": "Avatar.2009.1080p.mp4",
                "size": str(2500 * 1024 * 1024),
            }

            service._client = mock_client

            res = await service.convert_magnet_to_direct_url(
                magnet_link="magnet:?xt=urn:btih:avatar",
                timeout=10,
            )

            self.assertIsNotNone(res)
            self.assertEqual(res["direct_url"], "https://download.mypikpak.com/file_movie.mp4")
            self.assertEqual(res["file_name"], "Avatar.2009.1080p.mp4")
            self.assertEqual(res["file_size"], 2500 * 1024 * 1024)
            self.assertEqual(res["file_id"], "file_movie")
            self.assertEqual(res["service"], "pikpak")

    async def test_convert_magnet_cancellation_cleans_cloud(self):
        with patch("services.pikpak_service.CREDS_FILE", self.creds_file):
            service = PikPakService(username="user@test.com", password="pwd")
            mock_client = AsyncMock()

            mock_client.offline_download.return_value = {
                "task": {
                    "id": "task_cancel_1",
                    "file_id": "file_cancel_1",
                    "phase": "PHASE_TYPE_RUNNING",
                    "progress": 20,
                }
            }

            # Offline list raises CancelledError to simulate user /cancel during polling
            mock_client.offline_list.side_effect = asyncio.CancelledError()

            service._client = mock_client

            with self.assertRaises(asyncio.CancelledError):
                await service.convert_magnet_to_direct_url(
                    magnet_link="magnet:?xt=urn:btih:test_cancel",
                    timeout=10,
                )

            # Check that delete_tasks and delete_forever were invoked
            mock_client.delete_tasks.assert_awaited()
            mock_client.delete_forever.assert_awaited()

    async def test_clean_storage_purges_files_and_tasks(self):
        with patch("services.pikpak_service.CREDS_FILE", self.creds_file):
            service = PikPakService(username="user@test.com", password="pwd")
            mock_client = AsyncMock()

            mock_client.offline_list.return_value = {
                "tasks": [{"id": "t1"}, {"id": "t2"}]
            }
            mock_client.file_list.return_value = {
                "files": [{"id": "f1"}, {"id": "f2"}]
            }

            service._client = mock_client
            ok = await service.clean_storage()
            self.assertTrue(ok)

            mock_client.delete_tasks.assert_awaited_with(task_ids=["t1", "t2"], delete_files=True)
            mock_client.delete_forever.assert_awaited_with(ids=["f1", "f2"])


if __name__ == "__main__":
    unittest.main()
