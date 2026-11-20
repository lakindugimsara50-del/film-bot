"""
Unit and Integration Tests for Method 3: 30-Sec Scene Teaser Feed System.
"""

import json
import pytest
from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
WEBSITE_DATA = ROOT_DIR / "website" / "data"
TEASERS_JSON = WEBSITE_DATA / "teasers.json"
TEASERS_JS = WEBSITE_DATA / "teasers_data.js"
MOVIES_JSON = WEBSITE_DATA / "movies.json"

import sys
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.generate_teasers import (
    calculate_peak_window,
    select_hook,
    estimate_movie_scene_seek,
    GENRE_HOOKS,
    DEFAULT_HOOK
)
from bot.services.teaser_service import TeaserService


class TestPeakWindowAlgorithm:
    def test_heatmap_detects_highest_intensity_window(self):
        # Create a synthetic heatmap with a spike around 80s - 110s
        heatmap = []
        for s in range(0, 160, 2):
            val = 0.1
            if 80 <= s <= 110:
                val = 0.95  # Climax / Most Replayed
            heatmap.append({"start_time": float(s), "end_time": float(s + 2), "value": val})

        start, end, score, method = calculate_peak_window(heatmap, duration=160.0, window_size=30.0)
        assert method == "youtube_heatmap"
        assert 75.0 <= start <= 85.0
        assert end == round(start + 30.0, 1)
        assert score > 0.8

    def test_fallback_climax_when_heatmap_empty(self):
        start, end, score, method = calculate_peak_window([], duration=120.0, window_size=30.0)
        assert method == "climax_algorithm"
        assert start == round(120.0 * 0.58, 1)
        assert end == start + 30.0
        assert score >= 0.7

    def test_short_video_boundary(self):
        # Video is only 25 seconds long
        start, end, score, method = calculate_peak_window([], duration=25.0, window_size=30.0)
        assert start < end
        assert end <= 25.0


class TestGenreHooks:
    def test_action_hook(self):
        hook = select_hook(["Action", "Adventure"])
        assert "Action" in hook["en"]
        assert "සටන්" in hook["si"]

    def test_horror_hook(self):
        hook = select_hook(["Horror", "Mystery"])
        assert "Horror" in hook["en"] or "Scare" in hook["en"]
        assert "භීෂණාත්මක" in hook["si"]

    def test_comedy_hook(self):
        hook = select_hook(["Comedy"])
        assert "Comedy" in hook["en"] or "Hilarious" in hook["en"]
        assert "හාස්‍යජනක" in hook["si"]

    def test_default_fallback_hook(self):
        hook = select_hook([])
        assert hook == DEFAULT_HOOK


class TestMovieSceneSeek:
    def test_scene_seek_in_range(self):
        movie = {"duration": "120 min"}
        seek = estimate_movie_scene_seek(movie, peak_start=90.0, trailer_duration=150.0)
        # 120 mins = 7200s. Seek should be inside 25% - 75% range
        assert 1800 <= seek <= 5500


class TestTeasersDataIntegrity:
    def test_teasers_json_exists_and_valid(self):
        assert TEASERS_JSON.exists(), "teasers.json must exist"
        with open(TEASERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "teasers" in data
        teasers = data["teasers"]
        assert len(teasers) > 0, "Teasers catalog must have items"

        for t in teasers:
            assert t["id"].startswith("teaser-")
            assert t["movie_id"]
            assert t["title"]
            assert t["clip_start"] >= 0
            assert t["clip_end"] > t["clip_start"]
            assert t["formatted_seek_time"]
            assert t["scene_hook"]
            assert t["scene_hook_si"]
            assert t["youtube_embed_url"].startswith("https://www.youtube-nocookie.com/embed/")

    def test_teasers_js_synced(self):
        assert TEASERS_JS.exists(), "teasers_data.js must exist"
        with open(TEASERS_JS, "r", encoding="utf-8") as f:
            content = f.read()
        assert "window.FILMSUB_TEASERS =" in content


class TestTeaserService:
    def test_get_teaser_for_movie(self):
        with open(TEASERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        first_movie_id = data["teasers"][0]["movie_id"]

        teaser = TeaserService.get_teaser_for_movie(first_movie_id)
        assert teaser is not None
        assert teaser["movie_id"] == first_movie_id
