import os
import sys
import pytest

sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.cloud_drive.drive_manager import DriveManager
from services.cloud_drive.onedrive_client import OneDriveClient
from services.cloud_drive.gdrive_client import GoogleDriveClient


class DummyOneDrive(OneDriveClient):
    """Mock OneDrive for testing quota and upload without network."""

    def __init__(self, drive_id: str, remaining_gb: float, is_active: bool = True):
        super().__init__(drive_id=drive_id, name=f"Test_{drive_id}", refresh_token="dummy_token")
        self._rem_bytes = int(remaining_gb * (1024**3))
        self.is_active = is_active

    async def get_quota(self):
        if not self.is_active:
            raise RuntimeError("Drive offline")
        return {
            "total_bytes": 100 * 1024**3,
            "used_bytes": (100 * 1024**3) - self._rem_bytes,
            "remaining_bytes": self._rem_bytes,
            "state": "normal",
        }

    async def upload_file(self, local_path, filename=None, progress_callback=None):
        return {
            "file_id": f"item_{self.drive_id}",
            "filename": filename or "test.mp4",
            "size": 1000,
            "web_url": f"https://onedrive.mock/{self.drive_id}",
            "stream_url": f"https://onedrive.mock/{self.drive_id}?download=1",
            "download_url": f"https://onedrive.mock/{self.drive_id}?download=1",
            "drive_id": self.drive_id,
            "provider": "onedrive",
        }


@pytest.mark.asyncio
async def test_drive_manager_selection_picks_drive_with_most_space(tmp_path):
    dm = DriveManager()
    dm.drives = {
        "drive_1": DummyOneDrive("drive_1", remaining_gb=10.0),
        "drive_2": DummyOneDrive("drive_2", remaining_gb=85.0),  # Most space
        "drive_3": DummyOneDrive("drive_3", remaining_gb=40.0),
    }

    best = await dm.pick_best_drive(required_bytes=1024 * 1024 * 500)
    assert best is not None
    assert best.drive_id == "drive_2"


@pytest.mark.asyncio
async def test_drive_manager_skips_inactive_drives(tmp_path):
    dm = DriveManager()
    dm.drives = {
        "drive_active": DummyOneDrive("drive_active", remaining_gb=20.0, is_active=True),
        "drive_broken": DummyOneDrive("drive_broken", remaining_gb=90.0, is_active=False),
    }

    best = await dm.pick_best_drive(required_bytes=1000)
    assert best is not None
    assert best.drive_id == "drive_active"


@pytest.mark.asyncio
async def test_drive_manager_offline_movies_detection(tmp_path):
    dm = DriveManager()
    dm.drives = {
        "drive_1": DummyOneDrive("drive_1", remaining_gb=50.0, is_active=True),
        "drive_2": DummyOneDrive("drive_2", remaining_gb=50.0, is_active=False),  # Offline
    }

    dm.movie_index = {
        "movie-1": {"title": "Batman", "drive_id": "drive_1", "movie_slug": "movie-1"},
        "movie-2": {"title": "Inception", "drive_id": "drive_2", "movie_slug": "movie-2"},
        "movie-3": {"title": "Interstellar", "drive_id": "drive_2", "movie_slug": "movie-3"},
    }

    offline_report = await dm.get_offline_movies()
    assert offline_report["total_offline_drives"] == 1
    assert offline_report["total_affected_movies"] == 2
    slugs = [m["movie_slug"] for m in offline_report["affected_movies"]]
    assert "movie-2" in slugs
    assert "movie-3" in slugs
    assert "movie-1" not in slugs


@pytest.mark.asyncio
async def test_drive_manager_upload_and_index(tmp_path):
    # Create a dummy local video file
    dummy_file = tmp_path / "sample.mp4"
    dummy_file.write_bytes(b"dummy video data")

    dm = DriveManager()
    dm.drives = {
        "drive_test": DummyOneDrive("drive_test", remaining_gb=50.0, is_active=True),
    }

    res = await dm.upload_movie(
        local_path=str(dummy_file),
        movie_slug="test-movie-2024",
        movie_title="Test Movie (2024)",
    )

    assert res is not None
    assert res["stream_url"] == "https://onedrive.mock/drive_test?download=1"
    assert "test-movie-2024" in dm.movie_index
    assert dm.movie_index["test-movie-2024"]["drive_id"] == "drive_test"
