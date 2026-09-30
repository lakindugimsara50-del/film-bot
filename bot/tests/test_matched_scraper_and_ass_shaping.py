import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.scrapers.srilankan_matched_scraper import (
    resolve_direct_video_url,
    detect_quality_from_context,
    VIDEO_HOST_PATTERNS,
    PORTALS,
)
from services.video_service import (
    srt_to_ass_sinhala_shaped,
    build_subtitles_burn_filter,
)


def test_resolve_direct_video_url():
    assert resolve_direct_video_url("https://pixeldrain.com/u/AbCd1234") == "https://pixeldrain.com/api/file/AbCd1234"
    assert resolve_direct_video_url("https://pixeldrain.com/api/file/xyz98765") == "https://pixeldrain.com/api/file/xyz98765"
    assert resolve_direct_video_url("https://other.com/file/1234.mp4") == "https://other.com/file/1234.mp4"


def test_detect_quality_from_context():
    assert detect_quality_from_context("Download 1080p FHD WebRip", "https://site.com/d/1") == "1080p"
    assert detect_quality_from_context("720p HD Ready", "https://site.com/d/2") == "720p"
    assert detect_quality_from_context("480p SD Mobile", "https://site.com/d/3") == "480p"
    assert detect_quality_from_context("Untagged Release", "https://site.com/d/4") == "720p"


def test_video_host_patterns():
    assert VIDEO_HOST_PATTERNS["pixeldrain"].search("https://pixeldrain.com/u/abc12345")
    assert VIDEO_HOST_PATTERNS["gdrive"].search("https://drive.google.com/file/d/12345abcdef/view")
    assert VIDEO_HOST_PATTERNS["magnet"].search("magnet:?xt=urn:btih:abcdef1234567890&dn=Test")
    assert VIDEO_HOST_PATTERNS["direct_mp4"].search("https://cdn.example.com/movies/film.mp4")


def test_srt_to_ass_sinhala_shaped_preserves_zwj_and_header():
    sample_srt = """1
00:00:01,000 --> 00:00:04,500
ක්‍රියාදාම සහ විද්‍යා ප්‍රබන්ධ (Sci-Fi)

2
00:00:05,000 --> 00:00:08,200
ස්තූතියි! <b>රමණීය</b> සිනමා අත්දැකීමක්!
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        srt_file = os.path.join(tmpdir, "test_si.srt")
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write(sample_srt)

        ass_file = srt_to_ass_sinhala_shaped(srt_file)
        assert os.path.exists(ass_file)
        assert ass_file.endswith(".ass")

        with open(ass_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Check ASS headers
        assert "[Script Info]" in content
        assert "ScriptType: v4.00+" in content
        assert "PlayResX: 1920" in content
        assert "PlayResY: 1080" in content
        assert "Noto Sans Sinhala" in content

        # Check dialogue format and Sinhala ligatures (ZWJ preservation)
        assert "Dialogue: 0,0:00:01.00,0:00:04.50" in content
        assert "ක්‍රියාදාම" in content  # contains ZWJ rakaransaya
        assert "ප්‍රබන්ධ" in content
        assert "Dialogue: 0,0:00:05.00,0:00:08.20" in content
        # Check HTML tag conversion (<b> to {\b1})
        assert "{\\b1}රමණීය{\\b0}" in content


def test_build_subtitles_burn_filter_with_ass():
    with tempfile.TemporaryDirectory() as tmpdir:
        ass_path = os.path.join(tmpdir, "test.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("[Script Info]\nTitle: Test\n")

        filt = build_subtitles_burn_filter(ass_path)
        assert filt.startswith("ass=filename=")
        assert "test.ass" in filt
