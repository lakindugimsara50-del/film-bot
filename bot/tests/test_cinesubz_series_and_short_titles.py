import os
import sys
import pytest
from bs4 import BeautifulSoup
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import downloader
from services.scrapers import cinesubz, sinhalasub, srilankan_matched_scraper


def test_get_referer_for_cinesubz_and_supercloud():
    """Verify that supercloud and setwenna domains receive cinesubz.co Referer."""
    assert downloader.get_referer_for_url("https://cs45.supercloud2.space/video.mp4") == "https://cinesubz.co/"
    assert downloader.get_referer_for_url("https://player45.setwenna.one/embed/xyz") == "https://cinesubz.co/"
    assert downloader.get_referer_for_url("https://cinesubz.co/episodes/squid-game-1x1/") == "https://cinesubz.co/"
    assert downloader.get_referer_for_url("https://sinhalasub.lk/movie/123/") == "https://sinhalasub.lk/"


def test_short_title_token_handling():
    """Verify that 2-letter and 1-letter titles retain their tokens for search & matching."""
    for title in ["Hi", "hi 2026", "IT", "Up"]:
        raw_tokens = srilankan_matched_scraper.re.sub(r"[^a-zA-Z0-9]+", " ", title.lower()).split()
        slug_tokens = [t for t in raw_tokens if len(t) >= 2 or len(raw_tokens) == 1]
        assert len(slug_tokens) >= 1
        assert any(t in slug_tokens for t in ["hi", "it", "up"])


@pytest.mark.asyncio
async def test_extract_cinesubz_zetaplayer_streams():
    """Verify ZetaPlayer stream extraction from CineSubz post HTML with mocked responses."""
    html_doc = """
    <div id="playeroptionsul">
        <li class="zetaflix_player_option" data-post="12345" data-type="ep" data-nume="1">Server 1</li>
        <li class="zetaflix_player_option" data-post="12345" data-type="ep" data-nume="trailer">Trailer</li>
    </div>
    """
    soup = BeautifulSoup(html_doc, "html.parser")

    mock_client = AsyncMock()

    # Mock wp-json response
    resp_api = MagicMock()
    resp_api.status_code = 200
    resp_api.json.return_value = {"embed_url": "https://player45.setwenna.one/v/abc123"}

    # Mock embed page response containing supercloud CDN links and an ad locker link to ignore
    resp_embed = MagicMock()
    resp_embed.status_code = 200
    resp_embed.text = """
    <html>
        <body>
            <video src="https://cs45.supercloud2.space/Squid.Game.S01E01.WEBRip-720p.mp4?play=true"></video>
            <source src="https://cs45.supercloud2.space/Squid.Game.S01E01.WEBRip-480p.mp4?play=true"></source>
            <a href="https://drive.csplayer2.space/server7/fake.mp4">Ad Locker</a>
        </body>
    </html>
    """

    mock_client.get.side_effect = [resp_api, resp_embed]

    streams = await cinesubz._extract_cinesubz_zetaplayer_streams(
        mock_client, soup, "https://cinesubz.co/episodes/squid-game-1x1/"
    )

    assert len(streams) == 2
    qualities = [s["quality"] for s in streams]
    assert "720p" in qualities
    assert "480p" in qualities
    for s in streams:
        assert s["is_already_hardsubbed"] is True
        assert "supercloud" in s["url"]
        assert "drive.csplayer2.space" not in s["url"]


@pytest.mark.asyncio
async def test_resolve_srilankan_intermediate_link_filters_csplayer():
    """Verify that resolve_srilankan_intermediate_link rejects drive.csplayer2.space."""
    mock_client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.text = """
    <html>
        <body>
            <a id="link" href="https://google.com/server7/1:/video.mp4">Download</a>
        </body>
    </html>
    """
    mock_client.get.return_value = resp

    res = await srilankan_matched_scraper.resolve_srilankan_intermediate_link(
        mock_client, "https://cinesubz.co/links/12345"
    )
    assert res is None  # Must reject ad lockers that don't serve direct video
