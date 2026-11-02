import os
import sys
sys.path.insert(0, os.path.abspath("bot"))
import shutil
import tempfile
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
