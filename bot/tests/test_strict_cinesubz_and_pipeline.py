import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from services import queue_service, leech_service


class TestStrictCineSubzAndPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_can_accept_next_capacity(self):
        """Test that can_accept_next returns True when < 2 items, False when >= 2."""
        q = queue_service.QueueService()
        self.assertTrue(q.can_accept_next())
        self.assertTrue(q.is_idle())

        # Fake queue items
        item1 = MagicMock()
        q._pending_items.append(item1)
        self.assertTrue(q.can_accept_next())
        self.assertFalse(q.is_idle())

        item2 = MagicMock()
        q._pending_items.append(item2)
        self.assertFalse(q.can_accept_next())

        # When an item finishes and is removed
        q._pending_items.remove(item1)
        self.assertTrue(q.can_accept_next())

    async def test_strict_cinesubz_skips_external_torrents(self):
        """Test that --strict-cinesubz skips immediately when no direct candidate is found."""
        mock_client = MagicMock()
        mock_status = AsyncMock()
        mock_status.edit_text = AsyncMock()

        with patch("services.leech_service.find_all_candidates", new_callable=AsyncMock) as mock_find:
            await leech_service.run_auto_leech(
                client=mock_client,
                status_msg=mock_status,
                user_id=12345,
                query_text="NonExistentMovie 2026 --strict-cinesubz",
                auto_publish=True,
            )
            # find_all_candidates should NEVER be called!
            mock_find.assert_not_called()
            # status_msg should show skip message
            mock_status.edit_text.assert_called()
            call_text = mock_status.edit_text.call_args[0][0]
            self.assertIn("CineSubz", call_text)
            self.assertIn("skip", call_text.lower())

    async def test_pipelined_locks_independent(self):
        """Test that _download_lock and _encode_upload_lock can be held independently by different tasks."""
        dl_lock = leech_service._get_download_lock()
        enc_lock = leech_service._get_encode_upload_lock()

        # Both locks start unlocked
        self.assertFalse(dl_lock.locked())
        self.assertFalse(enc_lock.locked())

        # Task 1 holds encode lock, Task 2 holds download lock simultaneously
        await enc_lock.acquire()
        self.assertTrue(enc_lock.locked())

        # dl_lock can still be acquired without blocking!
        await dl_lock.acquire()
        self.assertTrue(dl_lock.locked())

        dl_lock.release()
        enc_lock.release()
        self.assertFalse(dl_lock.locked())
        self.assertFalse(enc_lock.locked())
