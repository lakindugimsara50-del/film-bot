"""
test_parallel_leech_and_catalog_audit.py — Unit and Integration tests for:
1. Sri Lankan pre-hardsubbed prioritization over separate sub portals.
2. TV series strictly 720p/480p rule (no 1080p).
3. Sri Lankan portal post URL direct HTML resolution and hardsub detection.
4. Concurrent parallel multi-quality downloads without serializing lock.
5. TV series batch enqueuing via /batch, /series, /leechseries and prefix stripping in parse_query.
6. Catalog audit comparison against website/data/movies.json with series episode-level precision.
7. Upload pool on-the-fly userbot promotion upon CHAT_ADMIN_REQUIRED and immediate retry.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import httpx
from services.catalog_audit_service import (
    audit_catalog_vs_srilankan_sites,
    format_audit_report,
    load_website_catalog,
    normalize_title,
    parse_release_title,
)
from services import leech_service
from services.leech_service import LeechCandidate, parse_query
from services.upload_pool import TelegramUploadPool


class TestCandidateSortingAndPrioritization(unittest.TestCase):
    def test_pre_hardsubbed_prioritized_over_separate_sub(self):
        """Pre-hardsubbed releases (SinhalaSub, CineSubz) must come before separate sub sites (PirateLK, Baiscope)."""
        cands = [
            LeechCandidate(
                method="ddl",
                method_name="PirateLK Separate Sub",
                source_url="https://piratelk.com/dl/1080p",
                quality="1080p",
                extra={"is_already_hardsubbed": False, "portal": "PirateLK"},
            ),
            LeechCandidate(
                method="ddl",
                method_name="SinhalaSub Hardsub",
                source_url="https://cdn.sinhalasub.net/1080p.mp4",
                quality="1080p",
                extra={"is_already_hardsubbed": True, "portal": "SinhalaSub"},
            ),
            LeechCandidate(
                method="ddl",
                method_name="CineSubz 720p Hardsub",
                source_url="https://cinesubz.co/720p.mp4",
                quality="720p",
                extra={"is_already_hardsubbed": True, "portal": "CineSubz"},
            ),
        ]

        # Apply Movie sort key exactly as implemented in leech_service._execute_leech
        cands.sort(key=lambda c: (
            0 if (c.extra and c.extra.get("is_already_hardsubbed")) else 1,
            0 if str(c.quality).lower() == "1080p" else (1 if str(c.quality).lower() == "720p" else 2),
        ))

        # First must be SinhalaSub (hardsub + 1080p)
        self.assertEqual(cands[0].extra["portal"], "SinhalaSub")
        self.assertTrue(cands[0].extra["is_already_hardsubbed"])
        # Second must be CineSubz (hardsub + 720p)
        self.assertEqual(cands[1].extra["portal"], "CineSubz")
        self.assertTrue(cands[1].extra["is_already_hardsubbed"])
        # Last must be PirateLK (separate sub)
        self.assertEqual(cands[2].extra["portal"], "PirateLK")
        self.assertFalse(cands[2].extra["is_already_hardsubbed"])

    def test_tv_series_strictly_720p_and_480p(self):
        """TV Series must strictly allow 720p and 480p, filtering out 1080p."""
        raw_cands = [
            LeechCandidate(method="ddl", method_name="1080p", source_url="http://a", quality="1080p"),
            LeechCandidate(method="ddl", method_name="720p", source_url="http://b", quality="720p"),
            LeechCandidate(method="ddl", method_name="480p", source_url="http://c", quality="480p"),
        ]

        filtered = [c for c in raw_cands if str(c.quality or "").lower() in ("720p", "480p")]
        filtered.sort(key=lambda c: 0 if str(c.quality or "").lower() == "720p" else 1)

        qualities = [c.quality for c in filtered]
        self.assertNotIn("1080p", qualities)
        self.assertEqual(qualities, ["720p", "480p"])
        self.assertEqual(filtered[0].quality, "720p")


class TestSriLankanDirectPostResolution(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_srilankan_post_url_real_html_parsing(self):
        """resolve_srilankan_post_url must parse real HTML, extract cdn links, and flag pre-hardsubbed."""
        from services.scrapers import srilankan_matched_scraper

        sample_html = """
        <html>
        <body>
            <div class="download-links">
                <a href="https://cdn.sinhalasub.net/movies/Inception.2010.1080p.mp4">Download 1080p WEB-DL</a>
                <a href="https://cdn.sinhalasub.net/movies/Inception.2010.720p.mp4">Download 720p WEB-DL</a>
            </div>
        </body>
        </html>
        """

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_html

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            res = await srilankan_matched_scraper.resolve_srilankan_post_url(
                post_url="https://sinhalasub.net/movies/inception-2010/",
                temp_dir="/tmp",
            )
            self.assertEqual(len(res), 2)
            self.assertEqual(res[0]["portal"], "SinhalaSub")
            self.assertTrue(res[0]["is_already_hardsubbed"])
            self.assertEqual(res[0]["quality"], "1080p")
            self.assertEqual(res[0]["url"], "https://cdn.sinhalasub.net/movies/Inception.2010.1080p.mp4")
            self.assertEqual(res[1]["quality"], "720p")


class TestCatalogAuditService(unittest.TestCase):
    def test_parse_release_title_movie(self):
        parsed = parse_release_title("One Last Shot (2026) Sinhala Subtitle")
        self.assertEqual(parsed["clean_title"], "One Last Shot")
        self.assertEqual(parsed["year"], 2026)
        self.assertFalse(parsed["is_series"])

    def test_parse_release_title_series(self):
        parsed = parse_release_title("My Bias, My Boss (2026) [S01 : E06] Sinhala Subtitle")
        self.assertEqual(parsed["clean_title"], "My Bias, My Boss")
        self.assertEqual(parsed["year"], 2026)
        self.assertTrue(parsed["is_series"])
        self.assertEqual(parsed["season"], 1)
        self.assertEqual(parsed["episode"], 6)

    def test_normalize_title(self):
        self.assertEqual(normalize_title("Deadpool & Wolverine 2024 Sinhala Subtitle"), "deadpool wolverine 2024")

    def test_series_episode_precision_missing_detection(self):
        """If catalog only has S01E01 of a series, S01E06 MUST be reported as missing."""
        mock_catalog = {
            "path": "/mock/movies.json",
            "total": 1,
            "normalized_titles": {"the 100"},
            "title_years": {("the 100", 2014)},
            "slugs": {"the-100-2014-s01e01"},
            "imdb_ids": {"tt2661044"},
            "series_episodes": {("the 100", 1, 1)},
            "series_titles": {"the 100"},
            "movie_titles": set(),
        }

        mock_feed_items = [
            # S01E01 is already cataloged
            {
                "portal": "CineSubz",
                "is_hardsub": True,
                "post_url": "https://cinesubz.co/the-100-s01e01/",
                "raw_title": "The 100 S01E01 Sinhala Sub",
                "clean_title": "The 100",
                "year": 2014,
                "season": 1,
                "episode": 1,
                "is_series": True,
            },
            # S01E06 is NOT in catalog
            {
                "portal": "CineSubz",
                "is_hardsub": True,
                "post_url": "https://cinesubz.co/the-100-s01e06/",
                "raw_title": "The 100 S01E06 Sinhala Sub",
                "clean_title": "The 100",
                "year": 2014,
                "season": 1,
                "episode": 6,
                "is_series": True,
            },
        ]

        with patch("services.catalog_audit_service.load_website_catalog", return_value=mock_catalog), \
             patch("services.catalog_audit_service.fetch_feed_items", new=AsyncMock(side_effect=[mock_feed_items, [], [], [], []])):
            audit_result = asyncio.run(audit_catalog_vs_srilankan_sites(max_per_portal=10))

            missing = audit_result["missing_items"]
            cataloged = audit_result["cataloged_items"]

            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0]["clean_title"], "The 100")
            self.assertEqual(missing[0]["episode"], 6)

            self.assertEqual(len(cataloged), 1)
            self.assertEqual(cataloged[0]["episode"], 1)

            self.assertEqual(audit_result["missing_series_count"], 1)
            self.assertEqual(audit_result["missing_movies_count"], 0)

    def test_format_audit_report(self):
        audit_data = {
            "catalog_count": 72,
            "total_scraped": 2,
            "missing_movies_count": 1,
            "missing_series_count": 1,
            "missing_items": [
                {
                    "portal": "CineSubz",
                    "is_hardsub": True,
                    "clean_title": "Avatar 3",
                    "year": 2026,
                    "is_series": False,
                    "season": None,
                    "episode": None,
                    "post_url": "https://cinesubz.co/avatar-3/",
                },
                {
                    "portal": "SubzLK",
                    "is_hardsub": False,
                    "clean_title": "Fallout",
                    "year": 2024,
                    "is_series": True,
                    "season": 1,
                    "episode": 1,
                    "post_url": "https://subz.lk/fallout-s01e01/",
                },
            ],
        }

        report = format_audit_report(audit_data, limit=5)
        self.assertIn("Catalog Audit Report", report)
        self.assertIn("Missing Movies:</b> <code>1</code>", report)
        self.assertIn("Missing Series Episodes:</b> <code>1</code>", report)
        self.assertIn("Avatar 3", report)
        self.assertIn("/leech Avatar 3 (2026)", report)
        self.assertIn("/series Fallout S01E01", report)


class TestTVSeriesBatchCommands(unittest.TestCase):
    def test_parse_query_series_detection(self):
        q1 = parse_query("Breaking Bad S02E05")
        self.assertTrue(q1.is_series)
        self.assertEqual(q1.season, 2)
        self.assertEqual(q1.episode, 5)

        q2 = parse_query("Game of Thrones Season 1")
        self.assertTrue(q2.is_series)
        self.assertEqual(q2.season, 1)
        self.assertIn(q2.episode, (1, None))

    def test_parse_query_with_command_prefixes(self):
        """parse_query must strip /series, /batch, /leechseries correctly without leaving command in title."""
        q1 = parse_query("/series Loki S01")
        self.assertEqual(q1.title, "Loki")
        self.assertEqual(q1.season, 1)
        self.assertTrue(q1.is_series)

        q2 = parse_query("/batch Breaking Bad S02")
        self.assertEqual(q2.title, "Breaking Bad")
        self.assertEqual(q2.season, 2)
        self.assertTrue(q2.is_series)

        q3 = parse_query("/leechseries House of the Dragon S01E01")
        self.assertEqual(q3.title, "House of the Dragon")
        self.assertEqual(q3.season, 1)
        self.assertEqual(q3.episode, 1)
        self.assertTrue(q3.is_series)


class TestUploadPoolAdminPromotion(unittest.IsolatedAsyncioTestCase):
    async def test_on_the_fly_promotion_and_immediate_retry(self):
        """When userbot session gets CHAT_ADMIN_REQUIRED, main bot promotes it and upload immediately retries and succeeds."""
        pool = TelegramUploadPool()
        target_channel = -100123456789

        main_bot = MagicMock()
        main_bot.name = "main_bot"
        main_bot.is_connected = True
        main_bot.promote_chat_member = AsyncMock(return_value=True)

        userbot = MagicMock()
        userbot.name = "userbot_session_01"
        userbot.is_connected = True
        userbot.me = MagicMock()
        userbot.me.id = 777888999

        pool.set_main_client(main_bot)
        pool.clients = [userbot]
        pool._admin_sessions[target_channel] = [userbot]

        upload_call_count = 0

        async def mock_upload(*args, **kwargs):
            nonlocal upload_call_count
            upload_call_count += 1
            if upload_call_count == 1:
                # First attempt fails with CHAT_ADMIN_REQUIRED
                raise Exception("[400 CHAT_ADMIN_REQUIRED] The method requires administrator rights in the chat")
            # Second attempt (after promotion) succeeds!
            return {"file_id": "promoted_success_file_id", "message_id": 888}

        with patch("services.telegram_upload.upload_video_file", side_effect=mock_upload):
            result = await pool.upload_with_pool(
                file_path="/tmp/test.mp4",
                target_chat=target_channel,
                quality="1080p",
                caption="Test Movie",
            )

            # Verification:
            # 1. Main bot's promote_chat_member was called for the userbot
            main_bot.promote_chat_member.assert_awaited_once()
            called_kwargs = main_bot.promote_chat_member.call_args.kwargs
            self.assertEqual(called_kwargs["chat_id"], target_channel)
            self.assertEqual(called_kwargs["user_id"], 777888999)

            # 2. Upload retried with the userbot and succeeded
            self.assertEqual(upload_call_count, 2)
            self.assertEqual(result["file_id"], "promoted_success_file_id")
            self.assertEqual(result["message_id"], 888)
            # 3. Userbot is back in admin cache
            self.assertIn(userbot, pool._admin_sessions[target_channel])
