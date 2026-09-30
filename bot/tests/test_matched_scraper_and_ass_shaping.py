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


@pytest.mark.asyncio
async def test_resolve_srilankan_intermediate_link():
    from services.scrapers.srilankan_matched_scraper import resolve_srilankan_intermediate_link
    import httpx

    # 1. Direct CDN link should resolve immediately without network request
    direct = await resolve_srilankan_intermediate_link(None, "https://cdn.sinhalasub.net/877/video.mp4")
    assert direct == "https://cdn.sinhalasub.net/877/video.mp4"

    # 2. Mock intermediate link unlocker (ZetaFlix zluFinalLink)
    class MockResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    class MockClient:
        async def get(self, url, headers=None, timeout=None):
            if "ircs3et5oo" in url:
                return MockResponse(
                    "<html><body><script>var zluFinalLink = 'https://cdn.sinhalasub.net/877/Game.of.Thrones.S06E05%201080p.mp4';</script></body></html>"
                )
            elif "pixeldrain_page" in url:
                return MockResponse(
                    "<html><body><a href='https://pixeldrain.com/u/ABMCjPTq'>Download</a></body></html>"
                )
            return MockResponse("", 404)

    mock_client = MockClient()
    resolved_zlu = await resolve_srilankan_intermediate_link(
        mock_client,
        "https://sinhalasub.lk/links/ircs3et5oo/",
        referer_url="https://sinhalasub.lk/episodes/game-of-thrones-s06e05/",
    )
    assert resolved_zlu == "https://cdn.sinhalasub.net/877/Game.of.Thrones.S06E05%201080p.mp4"

    resolved_pd = await resolve_srilankan_intermediate_link(
        mock_client,
        "https://sinhalasub.lk/links/pixeldrain_page/",
    )
    assert resolved_pd == "https://pixeldrain.com/api/file/ABMCjPTq"


def test_is_valid_downloaded_video():
    from services.downloader import is_valid_downloaded_video

    # 1. Non-existent file
    assert not is_valid_downloaded_video("/non/existent/path.mp4")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 2. Corrupt 2KB HTML ad page
        fake_2kb = os.path.join(tmpdir, "corrupt_ad.mp4")
        with open(fake_2kb, "wb") as f:
            f.write(b"<!DOCTYPE html><html><head><title>Ad Unlocker</title></head><body>Ad text</body></html>")
        assert not is_valid_downloaded_video(fake_2kb)

        # 3. 43KB HTML page
        fake_43kb = os.path.join(tmpdir, "ad_page.mp4")
        with open(fake_43kb, "wb") as f:
            f.write(b"<!doctype html>" + b"x" * 43000)
        assert not is_valid_downloaded_video(fake_43kb)

        # 4. JSON error file
        json_err = os.path.join(tmpdir, "error.mp4")
        with open(json_err, "wb") as f:
            f.write(b'{"error": "Rate limit exceeded"}')
        assert not is_valid_downloaded_video(json_err)

        # 5. Genuine video file simulation (>= 15MB, binary header)
        good_video = os.path.join(tmpdir, "valid_video.mp4")
        with open(good_video, "wb") as f:
            # Write 16MB of non-HTML binary data (simulating ftyp / moov atoms)
            f.write(b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2mp41")
            f.seek(16 * 1024 * 1024)
            f.write(b"\x00")
        assert is_valid_downloaded_video(good_video)
