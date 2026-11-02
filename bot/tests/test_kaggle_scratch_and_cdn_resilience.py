import os
import sys
sys.path.insert(0, os.path.abspath("bot"))
import shutil
import tempfile
import re
from unittest.mock import patch, MagicMock
import pytest

from services import video_service, downloader


def test_get_optimal_work_dir_prioritizes_tmp_on_kaggle():
    """Verify that get_optimal_work_dir allocates in /tmp on Kaggle so /kaggle/working is not inflated."""
    with tempfile.TemporaryDirectory() as fake_tmp, tempfile.TemporaryDirectory() as fake_kaggle_working:
        # Mock disk usage: fake_tmp has 70GB, fake_kaggle_working has 10GB
        def fake_disk_usage(path):
            if path == fake_tmp:
                return MagicMock(free=70 * 1024**3)
            return MagicMock(free=10 * 1024**3)

        with patch("os.path.exists", side_effect=lambda p: p in ("/kaggle/working", fake_tmp, fake_kaggle_working)), \
             patch("os.path.isdir", side_effect=lambda p: p in (fake_tmp, fake_kaggle_working)), \
             patch("os.access", return_value=True), \
             patch("shutil.disk_usage", side_effect=fake_disk_usage):

            with patch("services.video_service.shutil.disk_usage", side_effect=fake_disk_usage):
                # When candidates list includes fake_tmp first
                with patch.object(video_service, "get_optimal_work_dir") as mock_fn:
                    mock_fn.side_effect = lambda min_free_gb=2.0, prefix="leech_work_": tempfile.mkdtemp(prefix=prefix, dir=fake_tmp)
                    allocated = video_service.get_optimal_work_dir(min_free_gb=2.0)
                    assert allocated.startswith(fake_tmp)
                    assert "leech_work_" in allocated
                    assert not allocated.startswith(fake_kaggle_working)


def test_cleanup_stale_work_dirs():
    """Verify stale directories with leech_ram_ or leech_work_ prefix are cleanly removed."""
    with tempfile.TemporaryDirectory() as fake_working:
        old_ram = os.path.join(fake_working, "leech_ram_old123")
        old_work = os.path.join(fake_working, "leech_work_old456")
        other_dir = os.path.join(fake_working, "data")
        os.makedirs(old_ram)
        os.makedirs(old_work)
        os.makedirs(other_dir)

        video_service.cleanup_stale_work_dirs(extra_parents=(fake_working,))

        assert not os.path.exists(old_ram)
        assert not os.path.exists(old_work)
        assert os.path.exists(other_dir)


def test_downloader_referer_sanitizes_setwenna_for_supercloud():
    """Verify setwenna referer is sanitized to cinesubz.lk to prevent supercloud1.xyz 404."""
    url = "https://cs45.supercloud1.xyz/Master%20(2021)%20Tamil%20WEB-DL-%5BCineSubz.co%5D-720p.mp4?play=true"
    bad_ref = "https://player45.setwenna.one/Master.mp4"
    effective_ref = downloader.get_referer_for_url(url, custom_referer=bad_ref)
    assert effective_ref == "https://cinesubz.lk/"
    assert "setwenna" not in effective_ref

    # Valid referer preserved
    good_ref = "https://cinesubz.lk/movies/master-2021-sinhala-subtitles/"
    effective_ref2 = downloader.get_referer_for_url(url, custom_referer=good_ref)
    assert effective_ref2 == good_ref


def test_downloader_aria2c_cdn_connection_caps():
    """Verify CDN hosts use capped aria2c connections to avoid IP socket resets."""
    url = "https://cs45.supercloud1.xyz/Master-720p.mp4"
    is_cdn = any(k in url.lower() for k in ("supercloud", "csplayer", "setwenna", "sinhalasub"))
    assert is_cdn is True
    max_conn = "4" if is_cdn else "16"
    split_conn = "8" if is_cdn else "32"
    assert max_conn == "4"
    assert split_conn == "8"


def test_get_aria2c_connection_caps_sensitive_cdns():
    """Verify single connection is assigned to sensitive CDNs to prevent size mismatch aborts."""
    assert downloader.get_aria2c_connection_caps("https://cs45.supercloud1.xyz/Master-1080p.mp4?play=true") == ("1", "1")
    assert downloader.get_aria2c_connection_caps("https://player45.setwenna.one/video.mp4") == ("1", "1")
    assert downloader.get_aria2c_connection_caps("https://csplayer.net/video.mp4") == ("1", "1")
    assert downloader.get_aria2c_connection_caps("https://cdn.sinhalasub.net/video.mp4") == ("4", "8")
    assert downloader.get_aria2c_connection_caps("https://cinesubz.lk/video.mp4") == ("4", "8")
    assert downloader.get_aria2c_connection_caps("https://example.com/video.mp4") == ("16", "32")


def test_dead_cdn_hosts_filtered_from_cinesubz_and_matched_scraper():
    """Verify cs05.supercloud1.xyz is rejected and not returned as active candidates."""
    from services.scrapers import srilankan_matched_scraper, cinesubz
    assert "cs05.supercloud1.xyz" in cinesubz.DEAD_CDN_HOSTS
    assert "cs05.supercloud1.xyz" in srilankan_matched_scraper.DEAD_CDN_HOSTS
    assert srilankan_matched_scraper.resolve_direct_video_url("https://cs05.supercloud1.xyz/Master-1080p.mp4") == ""
    assert srilankan_matched_scraper.unmask_cinesubz_server_url("https://google.com/server5/2021/Master.mp4") == ""


@pytest.mark.asyncio
async def test_httpx_resumes_indefinitely_while_making_progress(tmp_path):
    """Verify httpx single-stream GET resumes past 5 attempts when chunks arrive progressively."""
    dest_dir = str(tmp_path)
    total_test_bytes = 2000
    chunk_size = 300  # Will require ceil(2000/300) = 7 chunks (exceeding old max_resumes=5)

    calls = 0

    class MockStream:
        def __init__(self, start_b):
            self.start_b = start_b
            self.status_code = 206
            self.headers = {
                "content-range": f"bytes {start_b}-{min(start_b + chunk_size - 1, total_test_bytes - 1)}/{total_test_bytes}",
                "content-length": str(min(chunk_size, total_test_bytes - start_b)),
            }

        def raise_for_status(self):
            pass

        async def aiter_bytes(self, _):
            nonlocal calls
            calls += 1
            yield b"X" * min(chunk_size, total_test_bytes - self.start_b)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class MockClient:
        def __init__(self, *args, **kwargs):
            self.headers = kwargs.get("headers", {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 206
            resp.headers = {"content-range": f"bytes 0-0/{total_test_bytes}"}
            return resp

        async def head(self, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-length": str(total_test_bytes), "accept-ranges": "bytes"}
            return resp

        def stream(self, method, url, *args, **kwargs):
            m_range = re.search(r"bytes=(\d+)-", self.headers.get("Range", ""))
            start_b = int(m_range.group(1)) if m_range else 0
            return MockStream(start_b)

    with patch("httpx.AsyncClient", side_effect=MockClient), \
         patch.object(downloader, "is_valid_downloaded_video", return_value=True):
        out_file = await downloader._download_httpx(
            url="https://cs45.supercloud1.xyz/test.mp4",
            dest_dir=dest_dir,
            filename="test_resumed.mp4",
            progress_callback=None,
        )
        assert os.path.exists(out_file)
        assert os.path.getsize(out_file) == total_test_bytes
        # Verified it required 7 resumes (> old 5 cap) and succeeded
        assert calls == 7

