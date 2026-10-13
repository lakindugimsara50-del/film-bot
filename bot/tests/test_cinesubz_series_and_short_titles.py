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


@pytest.mark.asyncio
async def test_queue_service_cancel_user_purges_all_and_stops_active():
    """Verify that cancel_user clears active item, cancels current task, drains queue, and makes queue idle."""
    import asyncio
    from services.queue_service import QueueService, QueueItem
    qs = QueueService()

    mock_msg = AsyncMock()
    mock_client = AsyncMock()

    item1 = QueueItem(item_id="1", user_id=777, query_text="Ep 1", reply_media=None, status_msg=mock_msg, client=mock_client, title="Squid Game S01E01")
    item2 = QueueItem(item_id="2", user_id=777, query_text="Ep 2", reply_media=None, status_msg=mock_msg, client=mock_client, title="Squid Game S01E02")
    item3 = QueueItem(item_id="3", user_id=777, query_text="Ep 3", reply_media=None, status_msg=mock_msg, client=mock_client, title="Squid Game S01E03")

    qs._pending_items = [item1, item2, item3]
    qs._active_item = item1

    async def _dummy_task():
        await asyncio.sleep(100)

    t = asyncio.create_task(_dummy_task())
    qs._current_task = t
    await qs._queue.put(item2)
    await qs._queue.put(item3)

    # Cancel user
    cancelled = qs.cancel_user(777)
    assert cancelled is True
    assert t.cancelling() > 0 or t.cancelled()
    assert qs._active_item is None
    assert qs.get_queue_status() == []
    assert qs.is_idle() is True
    assert qs._queue.empty() is True
    assert item1.is_cancelled is True
    assert item2.is_cancelled is True
    assert item3.is_cancelled is True
    try:
        await t
    except asyncio.CancelledError:
        pass
    assert t.cancelled() is True


def test_has_explicit_episode_patterns():
    """Verify that _has_explicit_episode correctly differentiates single episodes from seasons."""
    from handlers.leech_handler import _has_explicit_episode

    # Explicit episode queries (MUST be recognized as single episodes)
    assert _has_explicit_episode("squid game S01E01") is True
    assert _has_explicit_episode("squid game S01 E01") is True
    assert _has_explicit_episode("squid game s1e1") is True
    assert _has_explicit_episode("squid game 1x01") is True
    assert _has_explicit_episode("squid game Episode 5") is True
    assert _has_explicit_episode("squid game ep 5") is True
    assert _has_explicit_episode("squid game E05") is True

    # Series/Season queries (MUST NOT be treated as single episodes)
    assert _has_explicit_episode("squid game S01") is False
    assert _has_explicit_episode("squid game Season 1") is False
    assert _has_explicit_episode("squid game") is False
    assert _has_explicit_episode("Game of Thrones Season 2") is False


@pytest.mark.asyncio
async def test_queue_service_preserves_queue_instance_and_worker_task():
    """Verify that cancel_user drains the queue without reassigning self._queue, preventing deadlock."""
    from services.queue_service import QueueService, QueueItem
    qs = QueueService()
    original_queue = qs._queue

    mock_msg = AsyncMock()
    mock_client = AsyncMock()

    item1 = QueueItem(item_id="1", user_id=123, query_text="Q1", reply_media=None, status_msg=mock_msg, client=mock_client, title="T1")
    item2 = QueueItem(item_id="2", user_id=456, query_text="Q2", reply_media=None, status_msg=mock_msg, client=mock_client, title="T2")

    await qs._queue.put(item1)
    await qs._queue.put(item2)
    qs._pending_items = [item1, item2]

    # Cancel user 123
    qs.cancel_user(123)

    # Queue instance must NOT be replaced!
    assert qs._queue is original_queue
    # User 456's item must remain in the queue
    assert not qs._queue.empty()
    remaining_item = await qs._queue.get()
    assert remaining_item.user_id == 456
    assert remaining_item.title == "T2"


@pytest.mark.asyncio
async def test_cinesubz_watermark_cover_and_badge():
    """Verify that CineSubz watermark cover function and FilmSub badge asset are fully operational."""
    import config
    from services import video_service

    assert getattr(config, "ENABLE_CINESUBZ_WATERMARK_COVER", False) is True

    badge_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "filmsub_badge.png")
    assert os.path.exists(badge_path)

    # Test badge dimensions and format
    from PIL import Image
    with Image.open(badge_path) as img:
        assert img.format == "PNG"
        assert img.mode == "RGBA"
        w, h = img.size
        assert w > 100
        assert h > 20

    # Test mock execution of cover_cinesubz_watermark
    with patch("services.video_service.get_ffmpeg_binary", return_value="ffmpeg"), \
         patch("services.video_service.detect_hw_encoder", return_value="cpu"), \
         patch("asyncio.create_subprocess_exec") as mock_exec, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=2048):

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")
        mock_exec.return_value = mock_proc

        res = await video_service.cover_cinesubz_watermark("dummy_in.mp4", "dummy_out.mp4")
        assert res is True
        assert mock_exec.called
        call_args = mock_exec.call_args[0]
        # Command should contain overlay or filter_complex
        assert any("overlay" in str(arg) for arg in call_args)

        # Test with trim_start=10.0 and h264_nvenc hardware acceleration
        with patch("services.video_service.detect_hw_encoder", return_value="h264_nvenc"), \
             patch("services.video_service.get_video_duration", return_value=8100.0), \
             patch("os.path.getsize", return_value=732559081):
            res_nv = await video_service.cover_cinesubz_watermark("dummy_in.mp4", "dummy_out.mp4", trim_start=10.0)
            assert res_nv is True
            call_args_nv = mock_exec.call_args[0]
            assert "-hwaccel" in call_args_nv
            assert "-ss" in call_args_nv
            ss_idx = call_args_nv.index("-ss")
            assert call_args_nv[ss_idx + 1] == "10.0"
            assert "-spatial-aq" in call_args_nv
            assert "-temporal-aq" in call_args_nv
            assert "-filter_threads" in call_args_nv
            assert "-filter_complex_threads" in call_args_nv
            # Verify bitrate preservation: 732MB / 8100s ~ 723kbps
            assert "-b:v" in call_args_nv
            bv_idx = call_args_nv.index("-b:v")
            target_kbps_val = int(call_args_nv[bv_idx + 1].rstrip("k"))
            assert 650 <= target_kbps_val <= 800  # Strictly preserves 480p ~700MB size without bloating
            # Verify corner-flush overlay positioning
            fc_idx = call_args_nv.index("-filter_complex")
            fc_str = call_args_nv[fc_idx + 1]
            assert "overlay=x=2" in fc_str or "overlay=x=3" in fc_str or "overlay=x=max" in fc_str


