import asyncio
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import video_service
from services import queue_service


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

    def test_discover_devices_dual_gpu(self):
        """Dual T4 (e.g. Kaggle) should allocate 4 slots: [0, 1, 0, 1] for 1080p, 720p, 480p concurrency."""
        pool = video_service.GPUDevicePool()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.device_count.return_value = 2

        with patch.dict("sys.modules", {"torch": mock_torch}):
            slots = pool._discover_devices()
            self.assertEqual(slots, [0, 1, 0, 1])
            self.assertEqual(len(slots), 4)

    def test_discover_devices_single_gpu(self):
        """Single GPU (Colab / Kaggle 1x) should allocate 3 slots: [0, 0, 0]."""
        pool = video_service.GPUDevicePool()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.device_count.return_value = 1

        with patch.dict("sys.modules", {"torch": mock_torch}):
            slots = pool._discover_devices()
            self.assertEqual(slots, [0, 0, 0])
            self.assertEqual(len(slots), 3)

    def test_discover_devices_fallback_zero_gpu(self):
        """Fallback when no GPU is detected should default to 3 virtual slots: [0, 0, 0]."""
        pool = video_service.GPUDevicePool()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": mock_torch}), \
             patch("subprocess.check_output", side_effect=Exception("No nvidia-smi")):
            slots = pool._discover_devices()
            self.assertEqual(slots, [0, 0, 0])

    async def test_acquire_with_timeout_success(self):
        """Acquiring a slot within timeout should succeed."""
        pool = video_service.GPUDevicePool()
        with patch.object(pool, "_discover_devices", return_value=[0]):
            async with pool.acquire(timeout=1.0) as dev:
                self.assertEqual(dev, 0)

    async def test_acquire_with_timeout_exhausted(self):
        """Acquiring when all slots are busy should raise TimeoutError if timeout expires."""
        pool = video_service.GPUDevicePool()
        with patch.object(pool, "_discover_devices", return_value=[0]):
            async with pool.acquire(timeout=1.0):
                # Now pool is empty (1 slot total)
                with self.assertRaises((asyncio.TimeoutError, TimeoutError)):
                    async with pool.acquire(timeout=0.05):
                        pass

    async def test_queue_service_is_user_busy(self):
        """Test queue_service.is_user_busy correctly reflects user queue and active tasks."""
        qs = queue_service.QueueService()
        user_id = 99999

        # Initially idle
        self.assertFalse(qs.is_user_busy(user_id))

        # Add mock pending item
        mock_item = MagicMock()
        mock_item.user_id = user_id
        mock_item.is_cancelled = False
        qs._pending_items.append(mock_item)

        # Now busy
        self.assertTrue(qs.is_user_busy(user_id))
        self.assertFalse(qs.is_user_busy(11111))  # Different user is not busy

        # Remove from pending
        qs._pending_items.remove(mock_item)
        self.assertFalse(qs.is_user_busy(user_id))

        # Add to active tasks
        mock_task = MagicMock()
        mock_task.done.return_value = False
        qs._active_tasks[mock_task] = mock_item
        self.assertTrue(qs.is_user_busy(user_id))

        # When task is done
        mock_task.done.return_value = True
        self.assertFalse(qs.is_user_busy(user_id))

        # Add to _active_item
        qs._active_tasks.clear()
        qs._active_item = mock_item
        self.assertTrue(qs.is_user_busy(user_id))
        qs._active_item = None
        self.assertFalse(qs.is_user_busy(user_id))

    async def test_compress_video_prepend_intro_single_pass_command(self):
        """Verify compress_video constructs single-pass concat filter and calls ffmpeg once."""
        pool = video_service._GPU_POOL
        dummy_input = "dummy_input.mp4"
        dummy_output = "dummy_output.mp4"
        dummy_intro = "dummy_intro.mp4"

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1024 * 1024 * 100), \
             patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
             patch("services.video_service.get_video_resolution", return_value=(1280, 720)), \
             patch("services.video_service.get_video_duration", return_value=3600.0), \
             patch("services.video_service.get_or_create_filmzilla_intro", return_value=dummy_intro), \
             patch("services.video_service.detect_hw_encoder", return_value=None), \
             patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("shutil.disk_usage") as mock_disk:

            mock_disk.return_value.free = 10 * 1024 * 1024 * 1024
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait = MagicMock(return_value=asyncio.sleep(0.01))
            mock_proc.stderr.read = MagicMock(return_value=asyncio.sleep(0.01, result=b""))
            mock_exec.return_value = mock_proc

            res = await video_service.compress_video(
                input_path=dummy_input,
                output_path=dummy_output,
                prepend_intro=True,
                trim_start=10.0,
            )

            self.assertTrue(mock_exec.called)
            call_args = list(mock_exec.call_args[0])
            # Check ffmpeg command arguments
            self.assertIn("-filter_complex", call_args)
            fc_idx = call_args.index("-filter_complex")
            filter_str = call_args[fc_idx + 1]
            self.assertIn("concat=n=2:v=1:a=0", filter_str)
            self.assertIn("-ss", call_args)
            ss_idx = call_args.index("-ss")
            self.assertEqual(call_args[ss_idx + 1], "10.0")


if __name__ == "__main__":
    unittest.main()
