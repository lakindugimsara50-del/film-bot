"""
test_cinesubz_crawler_and_full_series.py

Unit and integration tests for:
1. CineSubz reverse archive crawler (CineSubzCrawler)
2. TV Series episode discovery & multi-season expansion (resolve_series_episodes)
3. Website catalog deduplication against movies.json (is_item_in_catalog)
4. Persistent crawler state tracking and status reporting
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Ensure bot directory is in sys.path
BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from services.cinesubz_crawler import (
    clean_cinesubz_title,
    get_cinesubz_max_pages,
    scrape_cinesubz_page,
    scrape_cinesubz_tvshow_episodes,
    is_item_in_catalog,
    resolve_series_episodes,
    CineSubzCrawler,
)


def test_clean_cinesubz_title_movie():
    """Test title cleaning for movie entries."""
    raw = "Maremma (2026) Sinhala Subtitles | සින්හල උපසිරැසි සමඟ"
    parsed = clean_cinesubz_title(raw)
    assert parsed["clean_title"] == "Maremma"
    assert parsed["year"] == 2026
    assert parsed["is_series"] is False

    raw2 = "Godzilla vs. Kong (2021) Sinhala Sub"
    parsed2 = clean_cinesubz_title(raw2)
    assert "Godzilla vs Kong" in parsed2["clean_title"] or "Godzilla" in parsed2["clean_title"]
    assert parsed2["year"] == 2021
    assert parsed2["is_series"] is False


def test_clean_cinesubz_title_series():
    """Test title cleaning and series detection."""
    raw = "Arcane (2021) S01 & S02 Complete Sinhala Subtitles"
    parsed = clean_cinesubz_title(raw)
    assert "Arcane" in parsed["clean_title"]
    assert parsed["year"] == 2021
    assert parsed["is_series"] is True

    raw2 = "Loki Season 2 Sinhala Subtitles"
    parsed2 = clean_cinesubz_title(raw2)
    assert "Loki" in parsed2["clean_title"]
    assert parsed2["is_series"] is True


@pytest.mark.asyncio
async def test_scrape_cinesubz_page_parsing():
    """Test parsing HTML cards from CineSubz."""
    mock_html = """
    <html>
      <body>
        <div class="module-wrapper">
          <div class="display-item">
            <a href="https://cinesubz.lk/movies/maremma-2026-sinhala-subtitles/">
              <span class="rating1">IMDb 6.8 tt28736184</span>
              <h3>Maremma (2026) Sinhala Subtitles</h3>
            </a>
          </div>
          <div class="display-item">
            <a href="https://cinesubz.lk/movies/interstellar-2014-sinhala-subtitles/">
              <span class="rating1">IMDb 8.7 tt0816692</span>
              <h3>Interstellar (2014) Sinhala Subtitles</h3>
            </a>
          </div>
        </div>
      </body>
    </html>
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_html
    mock_client.get = AsyncMock(return_value=mock_resp)

    items = await scrape_cinesubz_page(mock_client, page=226, section="movies")
    assert len(items) == 2
    assert items[0]["clean_title"] == "Maremma"
    assert items[0]["year"] == 2026
    assert items[0]["imdb_id"] == "tt28736184"
    assert items[1]["clean_title"] == "Interstellar"
    assert items[1]["year"] == 2014
    assert items[1]["imdb_id"] == "tt0816692"


@pytest.mark.asyncio
async def test_scrape_cinesubz_tvshow_episodes():
    """Test extracting all episode links from TV show page."""
    mock_html = """
    <html>
      <body>
        <div class="episodios">
          <a href="https://cinesubz.lk/episodes/hawkeye-1x1/">Episode 1</a>
          <a href="https://cinesubz.lk/episodes/hawkeye-1x2/">Episode 2</a>
          <a href="https://cinesubz.lk/episodes/hawkeye-1x3/">Episode 3</a>
          <a href="https://cinesubz.lk/episodes/hawkeye-s01-e04/">Episode 4</a>
          <a href="https://cinesubz.lk/episodes/hawkeye-1x5/">Episode 5</a>
          <a href="https://cinesubz.lk/episodes/hawkeye-1x6-end/">Episode 6 End</a>
        </div>
      </body>
    </html>
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_html
    mock_client.get = AsyncMock(return_value=mock_resp)

    episodes = await scrape_cinesubz_tvshow_episodes(mock_client, "https://cinesubz.lk/tvshows/hawkeye/")
    assert len(episodes) == 6
    assert episodes[0]["season"] == 1 and episodes[0]["episode"] == 1
    assert episodes[1]["season"] == 1 and episodes[1]["episode"] == 2
    assert episodes[3]["season"] == 1 and episodes[3]["episode"] == 4
    assert episodes[5]["season"] == 1 and episodes[5]["episode"] == 6
    assert episodes[5]["label"] == "S01E06"


def test_is_item_in_catalog_deduplication():
    """Test that items already on site are skipped and missing items are detected."""
    mock_catalog = {
        "imdb_ids": {"tt1375666", "tt0816692"},
        "title_years": {("inception", 2010), ("interstellar", 2014)},
        "movie_titles": {"inception", "interstellar"},
        "series_titles": {"arcane"},
        "series_episodes": {
            ("arcane", 1, 1),
            ("arcane", 1, 2),
            ("arcane", 1, 3),
        },
        "slugs": {"inception-2010", "interstellar-2014", "arcane-s01e01", "arcane-s01e02", "arcane-s01e03"},
    }

    # 1. Movie already on site by IMDb ID
    item_existing_imdb = {"clean_title": "Different Title", "year": 2010, "imdb_id": "tt1375666", "is_series": False}
    assert is_item_in_catalog(item_existing_imdb, mock_catalog) is True

    # 2. Movie already on site by Title + Year
    item_existing_title = {"clean_title": "Interstellar", "year": 2014, "imdb_id": "", "is_series": False}
    assert is_item_in_catalog(item_existing_title, mock_catalog) is True

    # 3. New movie NOT on site
    item_new_movie = {"clean_title": "Deadpool and Wolverine", "year": 2024, "imdb_id": "tt6263850", "is_series": False}
    assert is_item_in_catalog(item_new_movie, mock_catalog) is False

    # 4. Series episode already on site (Arcane S01E01)
    show_item = {"clean_title": "Arcane", "year": 2021, "imdb_id": "tt11126994", "is_series": True}
    assert is_item_in_catalog(show_item, mock_catalog, season=1, episode=1) is True

    # 5. Series episode NOT on site (Arcane S01E04)
    assert is_item_in_catalog(show_item, mock_catalog, season=1, episode=4) is False

    # 6. Series episode in Season 2 NOT on site (Arcane S02E01)
    assert is_item_in_catalog(show_item, mock_catalog, season=2, episode=1) is False


def test_crawler_state_and_status():
    """Test crawler state loading, saving, and status formatting."""
    crawler = CineSubzCrawler()
    crawler.mode = "movies"
    crawler.current_movie_page = 226
    crawler.max_movie_pages = 226
    crawler.total_scraped = 45
    crawler.total_skipped = 30
    crawler.total_queued = 15
    crawler.current_item_name = "Interstellar (2014)"
    crawler.status_detail = "Testing status"

    status_text = crawler.get_status_text()
    assert "CineSubz Auto-Harvester" in status_text
    assert "Page 226 / 226" in status_text
    assert "Interstellar (2014)" in status_text
    assert "Scraped: <b>45</b>" in status_text
    assert "Skipped (Already on Site): <b>30</b>" in status_text
    assert "Enqueued for Leech: <b>15</b>" in status_text


@pytest.mark.asyncio
async def test_resolve_series_episodes_multi_season():
    """Test full multi-season series resolution."""
    mock_tmdb_meta = {
        "type": "series",
        "title": "Arcane",
        "year": 2021,
        "imdb_id": "tt11126994",
        "seasons": [
            {"season_number": 1, "episode_count": 9, "name": "Season 1"},
            {"season_number": 2, "episode_count": 9, "name": "Season 2"},
        ],
    }

    with patch("services.tmdb_service.fetch_metadata", AsyncMock(return_value=mock_tmdb_meta)):
        resolved = await resolve_series_episodes("Arcane")
        assert resolved["title"] == "Arcane"
        assert resolved["year"] == 2021
        assert resolved["imdb_id"] == "tt11126994"
        assert len(resolved["episodes"]) == 18
        assert resolved["seasons_list"] == [1, 2]
        assert resolved["episodes"][0]["label"] == "S01E01"
        assert resolved["episodes"][8]["label"] == "S01E09"
        assert resolved["episodes"][9]["label"] == "S02E01"
        assert resolved["episodes"][17]["label"] == "S02E09"


@pytest.mark.asyncio
async def test_resolve_series_episodes_single_target_season():
    """Test resolving a single target season when requested."""
    mock_tmdb_meta = {
        "type": "series",
        "title": "Hawkeye",
        "year": 2021,
        "imdb_id": "tt10160804",
        "seasons": [
            {"season_number": 1, "episode_count": 6, "name": "Season 1"},
        ],
    }

    with patch("services.tmdb_service.fetch_metadata", AsyncMock(return_value=mock_tmdb_meta)):
        resolved = await resolve_series_episodes("Hawkeye", target_season=1)
        assert resolved["title"] == "Hawkeye"
        assert len(resolved["episodes"]) == 6
        assert resolved["seasons_list"] == [1]
        assert resolved["episodes"][5]["label"] == "S01E06"


@pytest.mark.asyncio
async def test_crawler_start_modes():
    """Test /crawl start vs /crawl resume vs /crawl <number>."""
    crawler = CineSubzCrawler()
    mock_client = MagicMock()

    with patch.object(crawler, "_crawler_worker", AsyncMock()):
        # 1. Fresh start: resets page to 0 (to detect max pages and start fresh)
        crawler.current_movie_page = 150
        crawler.total_scraped = 50
        ok, msg = await crawler.start(mock_client, 12345, mode="movies", resume=False)
        assert ok is True
        assert crawler.current_movie_page == 0
        assert crawler.total_scraped == 0
        assert "මුල සිටම ආරම්භ විය" in msg
        crawler.stop()

        # 2. Resume: preserves saved page
        crawler.current_movie_page = 150
        crawler.total_scraped = 50
        ok2, msg2 = await crawler.start(mock_client, 12345, mode="movies", resume=True)
        assert ok2 is True
        assert crawler.current_movie_page == 150
        assert "නැවතුණු තැනින් (Page 150)" in msg2
        crawler.stop()

        # 3. Direct page jump (/crawl 1 or /crawl 50)
        ok3, msg3 = await crawler.start(mock_client, 12345, mode="movies", start_page=1, single_page_only=True)
        assert ok3 is True
        assert crawler.current_movie_page == 1
        assert crawler.single_page_only is True
        assert "Page 1" in msg3
        crawler.stop()


@pytest.mark.asyncio
async def test_crawl_command_handler_dispatch():
    """Test Telegram /crawl start, /crawl resume, and /crawl 1 dispatches."""
    from handlers.leech_handler import register
    from pyrogram import Client

    mock_app = MagicMock(spec=Client)
    handlers_dict = {}

    def mock_on_message(filter_obj):
        def decorator(func):
            handlers_dict["crawl"] = func
            return func
        return decorator

    mock_app.on_message = mock_on_message
    mock_app.on_callback_query = MagicMock(return_value=lambda f: f)

    with patch("handlers.leech_handler._is_admin", return_value=True):
        register(mock_app)
        crawl_handler = handlers_dict.get("crawl")
        assert crawl_handler is not None

        mock_crawler = MagicMock()
        mock_crawler.start = AsyncMock(return_value=(True, "Started"))
        mock_crawler.stop = MagicMock(return_value=True)
        mock_crawler.is_running = MagicMock(return_value=False)
        mock_crawler.get_status_text = MagicMock(return_value="Status OK")
        mock_crawler.mode = "movies"
        mock_crawler.current_movie_page = 220
        mock_crawler.max_movie_pages = 226

        # Test /crawl start -> calls start(resume=False)
        with patch("services.cinesubz_crawler.cinesubz_crawler", mock_crawler):
            msg_start = MagicMock()
            msg_start.from_user.id = 999
            msg_start.text = "/crawl start"
            msg_start.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))

            await crawl_handler(mock_app, msg_start)
            mock_crawler.start.assert_called_with(
                client=mock_app,
                admin_chat_id=999,
                mode="movies",
                start_page=None,
                status_msg=msg_start.reply_text.return_value,
                resume=False,
            )

            # Test /crawl resume -> calls start(resume=True)
            msg_resume = MagicMock()
            msg_resume.from_user.id = 999
            msg_resume.text = "/crawl resume"
            msg_resume.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))

            await crawl_handler(mock_app, msg_resume)
            mock_crawler.start.assert_called_with(
                client=mock_app,
                admin_chat_id=999,
                mode="movies",
                start_page=220,
                status_msg=msg_resume.reply_text.return_value,
                resume=True,
            )

            # Test /crawl 1 -> calls start(start_page=1, single_page_only=True)
            msg_1 = MagicMock()
            msg_1.from_user.id = 999
            msg_1.text = "/crawl 1"
            msg_1.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))

            await crawl_handler(mock_app, msg_1)
            mock_crawler.start.assert_called_with(
                client=mock_app,
                admin_chat_id=999,
                mode="movies",
                start_page=1,
                status_msg=msg_1.reply_text.return_value,
                single_page_only=True,
            )

            # Test /crawl 50 -> calls start(start_page=50)
            msg_50 = MagicMock()
            msg_50.from_user.id = 999
            msg_50.text = "/crawl 50"
            msg_50.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))

            await crawl_handler(mock_app, msg_50)
            mock_crawler.start.assert_called_with(
                client=mock_app,
                admin_chat_id=999,
                mode="movies",
                start_page=50,
                status_msg=msg_50.reply_text.return_value,
                single_page_only=False,
            )
