import io
import os
import sys
import zipfile
import pytest

sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import subtitle_service
from services.scrapers import piratelk, baiscope
from services.scrapers.srilankan_matched_scraper import matches_title_and_year, PRE_HARDSUBBED_PORTALS


def test_extract_clean_show_name():
    """Verify robust show name cleaning from various raw, display, and query strings."""
    fn = subtitle_service.extract_clean_show_name
    assert fn("Game of Thrones S08E06 - The Iron Throne") == "Game of Thrones"
    assert fn("Game of Thrones (2011) - S08E06") == "Game of Thrones"
    assert fn("Game of Thrones (2011)") == "Game of Thrones"
    assert fn("Game of Thrones 2011") == "Game of Thrones"
    assert fn("Game of Thrones - 8x06 - The Iron Throne") == "Game of Thrones"
    assert fn("Game of Thrones Season 8 Episode 6") == "Game of Thrones"
    assert fn("Game of Thrones - Season 8") == "Game of Thrones"
    assert fn("Game of Thrones - ") == "Game of Thrones"
    assert fn("Breaking Bad S02E05") == "Breaking Bad"
    assert fn("Stranger Things (2016) - S04E01") == "Stranger Things"
    assert fn("") == ""


def test_select_episode_srt():
    """Verify episode SRT selection handles single-file fallbacks, standard and non-standard naming."""
    fn = subtitle_service._select_episode_srt

    # 1. Single file archive always returns the only file
    assert fn(["The Iron Throne.srt"], season=8, episode=6) == "The Iron Throne.srt"
    assert fn(["Sinhala.srt"], season=1, episode=1) == "Sinhala.srt"

    # 2. Multi-file archive with standard S08E06 naming
    files = [
        "Game.of.Thrones.S08E01.srt",
        "Game.of.Thrones.S08E02.srt",
        "Game.of.Thrones.S08E06.srt",
    ]
    assert fn(files, season=8, episode=6) == "Game.of.Thrones.S08E06.srt"
    assert fn(files, season=8, episode=1) == "Game.of.Thrones.S08E01.srt"

    # 3. Multi-file archive with 8x06 naming
    files_x = ["GOT_8x01.srt", "GOT_8x06.srt"]
    assert fn(files_x, season=8, episode=6) == "GOT_8x06.srt"

    # 4. Multi-file archive with Episode 06 / Ep 06
    files_ep = ["Episode 01.srt", "Episode 06.srt"]
    assert fn(files_ep, season=8, episode=6) == "Episode 06.srt"

    # 5. Multi-file archive with plain episode number
    files_num = ["01 - Winterfell.srt", "06 - The Iron Throne.srt"]
    assert fn(files_num, season=8, episode=6) == "06 - The Iron Throne.srt"

    # 6. Fallback when no pattern matches
    files_unknown = ["track_a.srt", "track_b.srt"]
    assert fn(files_unknown, season=8, episode=6) == "track_a.srt"


def test_matches_title_and_year_series_variations():
    """Verify that matches_title_and_year supports Episode 06, Ep 06, and Season 8 Episode 6."""
    # Episode tokens
    assert matches_title_and_year("Game of Thrones", "Game of Thrones S08E06", season=8, episode=6)
    assert matches_title_and_year("Game of Thrones", "Game of Thrones Season 8 Episode 6", season=8, episode=6)
    assert matches_title_and_year("Game of Thrones", "Game of Thrones Season 8 - Episode 06", season=8, episode=6)
    assert matches_title_and_year("Game of Thrones", "Game of Thrones 8x06", season=8, episode=6)
    assert matches_title_and_year("Game of Thrones", "Game of Thrones Season 8 Episode 06 Sinhala Sub", season=8, episode=6)

    # Season level matching (episode=None)
    assert matches_title_and_year("Game of Thrones", "Game of Thrones Season 08 with Sinhala Subtitles", season=8, episode=None)
    assert matches_title_and_year("Game of Thrones", "https://piratelk.com/game-of-thrones-season-08-with-sinhala-subtitles/", season=8, episode=None)


@pytest.mark.asyncio
async def test_piratelk_series_search_with_season_pack_and_episode_page(tmp_path):
    """
    Verify PirateLK scraper when:
    - Main Season 8 page has the complete season subtitle zip pack
    - Main Season 8 page has an episode link to Episode 06 page
    - Episode 06 page has the PixelDrain video link
    - Candidate is returned with sub_srt_path attached and is_already_hardsubbed=False.
    """
    # Create genuine Sinhala SRT in a zip (requires > 10 cues and > 10 Sinhala words to be genuine)
    cues = []
    for i in range(1, 15):
        cues.append(f"{i}\n00:0{i:02d}:01,000 --> 00:0{i:02d}:04,000\nමේ සිංහල උපසිරැසි පරිවර්තනය අංක {i} වේ. ස්තූතියි.\n")
    sinhala_srt_content = "\n".join(cues).encode("utf-8")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("Game.of.Thrones.S08E06.Sinhala.srt", sinhala_srt_content)
    zip_bytes = zip_buf.getvalue()

    season_page_html = """
    <html>
      <head><title>Game of Thrones Season 08 with Sinhala Subtitles | PirateLK</title></head>
      <body>
        <h1>Game of Thrones Season 08 with Sinhala Subtitles</h1>
        <div class="entry-content">
          <p>Game of Thrones Season 8 complete episodes with Sinhala subtitles.</p>
          <a href="https://piratelk.com/download-subtitles/?id=got8" class="btn">සිංහල උපසිරැසි බාගත කරන්න (Complete Season Subtitle Zip)</a>
          <div class="episodes-list">
            <a href="https://piratelk.com/game-of-thrones-season-8-episode-6-with-sinhala-subtitles/">Episode 06 - The Iron Throne</a>
          </div>
        </div>
      </body>
    </html>
    """

    episode_page_html = """
    <html>
      <head><title>Game of Thrones Season 8 Episode 6 with Sinhala Subtitles | PirateLK</title></head>
      <body>
        <h1>Game of Thrones Season 8 Episode 6 with Sinhala Subtitles</h1>
        <div class="download-links">
          <a href="https://pixeldrain.com/u/gotS08E06pd">Download 720p HD WebRip (PixelDrain)</a>
        </div>
      </body>
    </html>
    """

    class FakeResponse:
        def __init__(self, content: bytes, text: str, status_code: int = 200, headers: dict = None):
            self.content = content
            self.text = text
            self.status_code = status_code
            self.headers = headers or {"content-type": "text/html"}

    class FakeClient:
        async def get(self, url, headers=None, timeout=None):
            u = str(url)
            if "download-subtitles" in u:
                return FakeResponse(zip_bytes, "", headers={"content-type": "application/zip"})
            elif "episode-6" in u:
                return FakeResponse(episode_page_html.encode("utf-8"), episode_page_html)
            elif "season-08" in u or "season-8" in u or "?s=" in u:
                return FakeResponse(season_page_html.encode("utf-8"), season_page_html)
            return FakeResponse(b"", "", status_code=404)

    results = await piratelk.search(
        client=FakeClient(),
        clean_title="Game of Thrones S08E06 - The Iron Throne",
        year=2011,
        season=8,
        episode=6,
        temp_dir=str(tmp_path),
    )

    assert len(results) > 0
    res = results[0]
    assert res["portal"] == "PirateLK"
    assert res["is_already_hardsubbed"] is False  # Must NOT be hardsubbed (must be burned/muxed)
    assert res["quality"] == "720p"
    assert "pixeldrain.com/api/file/gotS08E06pd" in res["url"]
    assert res["sub_srt_path"] is not None
    assert os.path.exists(res["sub_srt_path"])
    with open(res["sub_srt_path"], "r", encoding="utf-8") as f:
        assert "සිංහල උපසිරැසි" in f.read()


@pytest.mark.asyncio
async def test_piratelk_intermediate_html_subtitle_download(tmp_path):
    """Verify that PirateLK handles intermediate HTML download pages before reaching the zip."""
    cues = []
    for i in range(1, 15):
        cues.append(f"{i}\n00:0{i:02d}:01,000 --> 00:0{i:02d}:04,000\nමේ ඉන්සෙප්ෂන් සිංහල උපසිරැසි පරිවර්තනය අංක {i} වේ. ස්තූතියි.\n")
    sinhala_srt = "\n".join(cues).encode("utf-8")
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("sub.srt", sinhala_srt)
    zip_bytes = zip_buf.getvalue()

    post_html = """
    <html>
      <head><title>Inception (2010) Sinhala Subtitles | PirateLK</title></head>
      <body>
        <h1>Inception (2010) Sinhala Subtitles</h1>
        <a href="https://piratelk.com/get-sub/123">Download Subtitle</a>
        <a href="https://pixeldrain.com/u/incep123">Download 1080p FHD</a>
      </body>
    </html>
    """

    intermediate_html = """
    <html>
      <body>
        <p>Click below to download your subtitle:</p>
        <a href="https://piratelk.com/files/sub123.zip">Click here to Download Subtitle (.zip)</a>
      </body>
    </html>
    """

    class FakeResponse:
        def __init__(self, content: bytes, text: str, headers: dict = None):
            self.content = content
            self.text = text
            self.status_code = 200
            self.headers = headers or {"content-type": "text/html"}

    class FakeClient:
        async def get(self, url, headers=None, timeout=None):
            u = str(url)
            if "sub123.zip" in u:
                return FakeResponse(zip_bytes, "", headers={"content-type": "application/zip"})
            elif "get-sub" in u:
                return FakeResponse(intermediate_html.encode("utf-8"), intermediate_html)
            elif "inception" in u or "?s=" in u:
                return FakeResponse(post_html.encode("utf-8"), post_html)
            return FakeResponse(b"", "", headers={"content-type": "text/html"})

    results = await piratelk.search(
        client=FakeClient(),
        clean_title="Inception",
        year=2010,
        temp_dir=str(tmp_path),
    )

    assert len(results) > 0
    assert results[0]["sub_srt_path"] is not None
    assert os.path.exists(results[0]["sub_srt_path"])
    assert results[0]["is_already_hardsubbed"] is False


def test_portal_hardsub_distinction():
    """Verify that only SinhalaSub and CineSubz are pre-hardsubbed, others require subtitle burning."""
    clean_video_portals = ["PirateLK", "Baiscope", "BaiscopeDownloads", "Subz", "Cines", "Cineru", "Zoom", "LKSubs"]
    for p in clean_video_portals:
        assert p not in PRE_HARDSUBBED_PORTALS, f"{p} should not be marked pre-hardsubbed"

    assert "SinhalaSub" in PRE_HARDSUBBED_PORTALS
    assert "CineSubz" in PRE_HARDSUBBED_PORTALS


def test_nested_season_pack_zip_and_utf16_extraction(tmp_path):
    """Verify that _extract_srt_from_bytes correctly selects episode from nested folders and decodes UTF-16."""
    cues = []
    for i in range(1, 15):
        cues.append(f"{i}\n00:0{i:02d}:01,000 --> 00:0{i:02d}:04,000\nමේ පරිච්ඡේදය අංක {i} සඳහා වූ සැබෑ සිංහල දෙබස් පෙළකි.\n")
    sinhala_text = "\n".join(cues)
    utf16_bytes = b"\xff\xfe" + sinhala_text.encode("utf-16le")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("Game of Thrones Season 08/Game of Thrones [S08 E01]/AMZN.WEB-DL.srt", b"dummy 1")
        zf.writestr("Game of Thrones Season 08\\Game of Thrones [S08 E06]\\AMZN.WEB-DL.srt", utf16_bytes)

    out_srt, member = subtitle_service._extract_srt_from_bytes(
        zip_buf.getvalue(),
        temp_dir=str(tmp_path),
        season=8,
        episode=6,
        prefix="test_nested",
    )
    assert out_srt is not None
    assert os.path.exists(out_srt)
    assert "S08 E06" in member
    with open(out_srt, "rb") as f:
        raw_b = f.read()
    # Output must be normalized UTF-8 without BOM
    assert not raw_b.startswith(b"\xff\xfe")
    assert not raw_b.startswith(b"\xef\xbb\xbf")
    assert "සිංහල දෙබස්" in raw_b.decode("utf-8")


@pytest.mark.asyncio
async def test_piratelk_season_hub_navigation_and_multi_episode_video_links(tmp_path):
    """
    Verify:
    1. Navigation from series hub (tv-series) -> Season 8 page
    2. Season page containing both S08E01 and S08E06 video links filters to S08E06
    3. Navigation does NOT fetch PixelDrain links into soup
    """
    cues = [f"{i}\n00:0{i:02d}:01,000 --> 00:0{i:02d}:04,000\nසැබෑ සිංහල දෙබස් {i}\n" for i in range(1, 15)]
    sinhala_srt = "\n".join(cues).encode("utf-8")
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("Game.of.Thrones.S08E06.srt", sinhala_srt)

    hub_html = """
    <html>
      <head><title>Game of Thrones TV Series with Sinhala Subtitles | PirateLK</title></head>
      <body>
        <div class="entry-content">
          <a href="https://piratelk.com/game-of-thrones-season-08-with-sinhala-subtitles/">Click Here to Season 08</a>
        </div>
      </body>
    </html>
    """

    season_html = """
    <html>
      <head><title>Game of Thrones Season 08 with Sinhala Subtitles | PirateLK</title></head>
      <body>
        <div class="entry-content">
          <a href="https://piratelk.com/download/got-s08-sub.zip">Download Complete Season 8 Subtitle (.zip)</a>
          <div class="links">
            <a href="https://pixeldrain.com/u/ep01vid">Download S08E01 720p HDTV</a>
            <a href="https://pixeldrain.com/u/ep06vid">Download S08E06 720p HDTV</a>
          </div>
        </div>
      </body>
    </html>
    """

    class FakeClient:
        async def get(self, url, headers=None, timeout=None):
            u = str(url)
            class Resp:
                def __init__(self, content, text, headers=None):
                    self.content = content
                    self.text = text
                    self.status_code = 200
                    self.headers = headers or {"content-type": "text/html"}
            if "got-s08-sub.zip" in u:
                return Resp(zbuf.getvalue(), "", headers={"content-type": "application/zip"})
            elif "season-08" in u:
                return Resp(season_html.encode("utf-8"), season_html)
            elif "tv-series" in u or "?s=" in u:
                return Resp(hub_html.encode("utf-8"), hub_html)
            return Resp(b"", "", status_code=404)

    results = await piratelk.search(
        client=FakeClient(),
        clean_title="Game of Thrones",
        year=2011,
        season=8,
        episode=6,
        temp_dir=str(tmp_path),
    )

    assert len(results) == 1
    res = results[0]
    assert res["portal"] == "PirateLK"
    assert "ep06vid" in res["url"]
    assert res["sub_srt_path"] is not None
    assert os.path.exists(res["sub_srt_path"])
    assert res["is_already_hardsubbed"] is False


@pytest.mark.asyncio
async def test_baiscope_series_search_with_episode_and_standalone_sub(tmp_path):
    """Verify Baiscope scraper handles series search with season pack and episode video links."""
    cues = [f"{i}\n00:0{i:02d}:01,000 --> 00:0{i:02d}:04,000\nබයිස්කෝප් සිංහල දෙබස් {i}\n" for i in range(1, 15)]
    sinhala_srt = "\n".join(cues).encode("utf-8")
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("S08E06.srt", sinhala_srt)

    baiscope_html = """
    <html>
      <head><title>Game of Thrones Season 8 with Sinhala Subtitles | Baiscope.lk</title></head>
      <body>
        <div class="entry-content">
          <a href="https://baiscope.lk/download/got-s8.zip">සිංහල උපසිරැසි මෙතැනින් බාගත කරගන්න (Subtitle Zip)</a>
          <a href="https://pixeldrain.com/u/bgotS08E06">Download S08E06 720p WebRip</a>
        </div>
      </body>
    </html>
    """

    class FakeClient:
        async def get(self, url, headers=None, timeout=None):
            u = str(url)
            class Resp:
                def __init__(self, content, text, headers=None):
                    self.content = content
                    self.text = text
                    self.status_code = 200
                    self.headers = headers or {"content-type": "text/html"}
            if "got-s8.zip" in u:
                return Resp(zbuf.getvalue(), "", headers={"content-type": "application/zip"})
            return Resp(baiscope_html.encode("utf-8"), baiscope_html)

    results = await baiscope.search(
        client=FakeClient(),
        clean_title="Game of Thrones",
        year=2011,
        season=8,
        episode=6,
        temp_dir=str(tmp_path),
    )

    assert len(results) > 0
    res = results[0]
    assert res["portal"] in ("Baiscope", "BaiscopeDownloads")
    assert "bgotS08E06" in res["url"]
    assert res["sub_srt_path"] is not None
    assert os.path.exists(res["sub_srt_path"])
    assert res["is_already_hardsubbed"] is False

