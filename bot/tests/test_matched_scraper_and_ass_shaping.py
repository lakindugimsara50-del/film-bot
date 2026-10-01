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


def test_short_query_title_matching_hi_2026():
    """Verify word-boundary matching correctly matches short title 'hi' / 'hi 2026'
    and rejects false positive substrings like 'white', 'history', 'his', 'hitman'."""
    from services.scrapers.srilankan_matched_scraper import matches_title_and_year, score_candidate_post

    # 1. Exact / valid matches
    assert matches_title_and_year("hi", "Hi (2026) Sinhala Subtitles")
    assert matches_title_and_year("hi", "https://sinhalasub.lk/movies/hi-2026-sinhala-sub/")
    assert matches_title_and_year("hi", "Hi 2026", year=2026)
    assert matches_title_and_year("hi", "Watch Hi (2026) Full Movie")

    # 2. Rejection of false positive substrings
    assert not matches_title_and_year("hi", "White House Down (2013)")
    assert not matches_title_and_year("hi", "A History of Violence (2005)")
    assert not matches_title_and_year("hi", "His House (2020)")
    assert not matches_title_and_year("hi", "Hitman (2007)")
    assert not matches_title_and_year("hi", "The Shining (1980)")

    # 3. Year discrepancy rejection
    assert not matches_title_and_year("hi", "Hi (2012) Sinhala Subtitles", year=2026)

    # 4. Score candidate post
    high_score = score_candidate_post("hi", "https://sinhalasub.lk/movies/hi-2026/", "Hi (2026) Sinhala Sub", year=2026)
    assert high_score > 50

    wrong_score = score_candidate_post("hi", "https://sinhalasub.lk/movies/white-house-down/", "White House Down (2013)")
    assert wrong_score == 0


def test_portal_sub_classification_all_sri_lankan_portals():
    """Verify classification of pre-burned vs standalone subtitle Sri Lankan portals."""
    from services.scrapers.srilankan_matched_scraper import PRE_HARDSUBBED_PORTALS, PORTALS

    pre_hardsubbed = {"SinhalaSub", "CineSubz"}
    assert PRE_HARDSUBBED_PORTALS == pre_hardsubbed

    portal_names = {p["name"] for p in PORTALS}
    # All required portals must be present in PORTALS
    expected_portals = {
        "SinhalaSub", "CineSubz", "Baiscope", "BaiscopeDownloads",
        "PirateLK", "Cines", "Cineru", "Subz", "Zoom", "LKSubs"
    }
    for ep in expected_portals:
        assert ep in portal_names, f"Portal {ep} missing from PORTALS list"

    # Pre-hardsubbed check
    for p in PORTALS:
        name = p["name"]
        if name in ("SinhalaSub", "CineSubz"):
            assert name in PRE_HARDSUBBED_PORTALS
        else:
            assert name not in PRE_HARDSUBBED_PORTALS


def test_modular_scrapers_import_and_contracts():
    """Verify that individual Sri Lankan scraper modules exist and export async search."""
    import inspect
    from services.scrapers import sinhalasub, cinesubz, baiscope, piratelk

    for mod in (sinhalasub, cinesubz, baiscope, piratelk):
        assert hasattr(mod, "search")
        assert inspect.iscoroutinefunction(mod.search)


def test_find_candidates_alias_and_parse_query_hi_2026():
    """Verify parse_query extracts 'hi' and 2026, and _find_candidates alias is available."""
    from services import leech_service

    parsed = leech_service.parse_query("/boost hi 2026")
    assert parsed.title.lower() == "hi"
    assert parsed.year == 2026

    # Verify alias exists and is callable
    assert hasattr(leech_service, "_find_candidates")
    assert callable(leech_service._find_candidates)


def test_multi_word_and_short_token_matching():
    """Verify that multi-word titles require all non-year tokens and support short words."""
    from services.scrapers.srilankan_matched_scraper import matches_title_and_year

    # 1. Multi-word short token titles
    assert matches_title_and_year("ip man", "Ip Man (2008) Sinhala Subtitles", year=2008)
    assert matches_title_and_year("hi nanna", "Hi Nanna (2023) Sinhala Sub", year=2023)
    assert matches_title_and_year("dr who", "Dr Who Season 1", year=None)

    # 2. Rejection when one required token is missing
    assert not matches_title_and_year("deadpool wolverine", "Deadpool (2016)")
    assert not matches_title_and_year("hi nanna", "Hi (2026)")

    # 3. Year tolerance +-1 year for international release variance
    assert matches_title_and_year("hi", "Hi (2025)", year=2026)
    assert not matches_title_and_year("hi", "Hi (2020)", year=2026)


def test_burn_subtitles_to_video_contract():
    """Verify video_service exports burn_subtitles_to_video."""
    import inspect
    from services import video_service

    assert hasattr(video_service, "burn_subtitles_to_video")
    assert inspect.iscoroutinefunction(video_service.burn_subtitles_to_video)


@pytest.mark.asyncio
async def test_relative_url_and_zero_torrent_in_scraper():
    """Verify scraper resolves relative URLs and strictly excludes magnet/torrent links."""
    from services.scrapers import srilankan_matched_scraper
    import httpx

    class MockSearchResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

        def json(self):
            return []

    html_content = """
    <html><body>
        <div class="result-item">
            <a href="/movies/hi-2026-sinhala-subtitles/">Hi (2026) Sinhala Subtitles</a>
        </div>
    </body></html>
    """

    post_content = """
    <html><body>
        <h1>Hi (2026) Sinhala Subtitles</h1>
        <a href="magnet:?xt=urn:btih:1234567890abcdef">Torrent Magnet 1080p</a>
        <a href="https://cines.lk/download/hi.2026.torrent">Download Torrent</a>
        <a href="https://pixeldrain.com/u/test1234">PixelDrain 720p Direct</a>
        <a href="/download/sub.zip">උපසිරැසි බාගත කරන්න</a>
    </body></html>
    """

    class MockClient:
        async def get(self, url, headers=None, timeout=None):
            if "?s=" in url:
                return MockSearchResponse(html_content)
            elif "/movies/hi-2026" in url:
                return MockSearchResponse(post_content)
            elif "sub.zip" in url:
                return MockSearchResponse("fake-zip-data" * 20)
            return MockSearchResponse("", 404)

    mock_client = MockClient()
    portal = {"name": "Cines", "base": "https://cines.lk", "wp_api": False}

    candidates = await srilankan_matched_scraper._search_portal(
        client=mock_client,
        portal=portal,
        query="hi 2026",
        clean_title="hi",
        year=2026,
        season=None,
        episode=None,
        temp_dir="/tmp",
    )

    assert len(candidates) > 0
    for cand in candidates:
        u = cand["url"].lower()
        # Must not have any magnet or torrent
        assert not u.startswith("magnet:")
        assert ".torrent" not in u
        # Must have relative URL resolved
        assert cand["post_url"].startswith("https://cines.lk/movies/hi-2026")
        # Must be direct HTTP
        assert "pixeldrain.com" in u
        # Since Cines is a standalone sub portal, is_already_hardsubbed must be False
        assert cand["is_already_hardsubbed"] is False


def test_build_subtitles_burn_filter_escapes_windows_path_colons():
    """Verify that build_subtitles_burn_filter escapes Windows drive colons and backslashes."""
    filt = build_subtitles_burn_filter(r"C:\Users\lakin\temp\sub.ass")
    # Must use forward slashes and escaped colon
    assert r"C\:/" in filt or "C\\:/" in filt
    assert "\\" not in filt.replace(r"\:", "")
    assert filt.startswith("ass=filename=")


@pytest.mark.asyncio
async def test_burn_subtitles_to_video_real_execution():
    """Verify that burn_subtitles_to_video executes FFmpeg without filterchain syntax errors."""
    import subprocess
    from services.video_service import burn_subtitles_to_video, get_ffmpeg_binary

    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        pytest.skip("FFmpeg not installed in test environment")

    with tempfile.TemporaryDirectory() as tmpdir:
        in_vid = os.path.join(tmpdir, "in.mp4")
        sub_srt = os.path.join(tmpdir, "sub.srt")
        out_vid = os.path.join(tmpdir, "out.mp4")

        # Generate a small 1-second dummy video
        subprocess.run(
            [ffmpeg_bin, "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1", "-c:v", "libx264", in_vid],
            capture_output=True,
            check=True,
        )
        with open(sub_srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:01,000\nක්‍රියාදාම (Action)\n")

        ok = await burn_subtitles_to_video(in_vid, sub_srt, out_vid)
        assert ok is True
        assert os.path.exists(out_vid)
        assert os.path.getsize(out_vid) > 1024


def test_verify_text_with_header_noise():
    """Verify that combining URL/H1/title protects against noisy sidebars with conflicting years."""
    from services.scrapers.srilankan_matched_scraper import matches_title_and_year

    # Sidebar contains 2021, 2022, 2023, but movie post URL and H1 contain 'Hi (2026)'
    noisy_page = "Top Movies 2021 Best of 2022 Archives 2023 " + ("filler " * 100)
    post_url = "https://sinhalasub.lk/movies/hi-2026/"
    h1_text = "Hi (2026) Sinhala Subtitles"

    # Combined verify_text correctly recognizes 2026
    verify_text = f"{post_url} {h1_text} {noisy_page}"
    assert matches_title_and_year("hi", verify_text, year=2026)

