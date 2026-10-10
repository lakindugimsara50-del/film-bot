import asyncio
import os
import sys
import pytest
from unittest.mock import patch, AsyncMock

# Ensure bot directory is on sys.path
sys.path.insert(0, os.path.abspath("bot"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import github_service


@pytest.mark.asyncio
async def test_search_and_delete_movie():
    sample_catalog = {
        "movies": [
            {
                "id": "ice-age-2002",
                "slug": "ice-age-2002",
                "title": "Ice Age",
                "year": 2002,
                "quality": "1080p",
            },
            {
                "id": "ice-age-the-meltdown-2006",
                "slug": "ice-age-the-meltdown-2006",
                "title": "Ice Age: The Meltdown",
                "year": 2006,
                "quality": "720p",
                "message_id": 1234,
            },
            {
                "id": "inception-2010",
                "slug": "inception-2010",
                "title": "Inception",
                "year": 2010,
                "quality": "1080p",
            },
        ],
        "last_updated": "2026-10-10T00:00:00Z"
    }

    # 1. Test search_movies
    with patch.object(github_service, "get_movies_json", AsyncMock(return_value=(sample_catalog.copy(), "sha123"))):
        results = await github_service.search_movies("ice age")
        assert len(results) == 2
        assert results[0]["title"] == "Ice Age"
        assert results[1]["title"] == "Ice Age: The Meltdown"

    # 2. Test delete_movie
    mock_commit = AsyncMock(return_value=True)
    with patch.object(github_service, "get_movies_json", AsyncMock(return_value=(dict(sample_catalog), "sha123"))), \
         patch.object(github_service, "_commit_movies_json", mock_commit):

        success, deleted = await github_service.delete_movie("ice-age-the-meltdown-2006")
        assert success is True
        assert deleted is not None
        assert deleted["slug"] == "ice-age-the-meltdown-2006"
        assert deleted["title"] == "Ice Age: The Meltdown"
        mock_commit.assert_called_once()

        # Check remaining in committed dict
        committed_dict = mock_commit.call_args[0][0]
        remaining_slugs = [m["slug"] for m in committed_dict["movies"]]
        assert "ice-age-the-meltdown-2006" not in remaining_slugs
        assert "ice-age-2002" in remaining_slugs
        assert "inception-2010" in remaining_slugs
