import asyncio
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import video_service


class TestMultiGPUPool(unittest.IsolatedAsyncioTestCase):
    async def test_multi_gpu_pool_concurrency(self):
        """Test that GPUDevicePool dynamically allocates multiple GPUs without blocking."""
        pool = video_service.GPUDevicePool()

        # Mock discovery of 2 GPUs (like Kaggle T4 x2: [0, 1])
        with patch.object(pool, "_discover_devices", return_value=[0, 1]):
            # Acquire first GPU slot
            async with pool.acquire() as gpu_0:
                self.assertEqual(gpu_0, 0)
                # Second slot should be immediately available (not busy)
                self.assertFalse(pool.is_busy())

                # Acquire second GPU slot concurrently
                async with pool.acquire() as gpu_1:
                    self.assertEqual(gpu_1, 1)
                    # Now all slots are taken
                    self.assertTrue(pool.is_busy())

                # Once gpu_1 is released, pool is no longer busy
                self.assertFalse(pool.is_busy())

    async def test_gpu_semaphore_proxy_backwards_compatible(self):
        """Test that _GPU_ENCODE_SEMAPHORE proxy works seamlessly with async with and locked()."""
        sem = video_service._GPU_ENCODE_SEMAPHORE
        self.assertFalse(sem.locked())

        async with sem:
            pass
