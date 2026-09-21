"""
tmdb_service.py — Fetch movie metadata from The Movie Database (TMDB).

Provides:
  fetch_metadata(query, year)  → rich dict with all fields needed by movies.json
  get_imdb_rating(imdb_id)     → IMDb rating string via TMDB's external IDs
"""

import logging
import httpx
from config import TMDB_API_KEY

log = logging.getLogger(__name__)

# ── TMDB endpoint constants ───────────────────────────────────────────────────
_BASE = "https://api.themoviedb.org/3"
_IMG_W500 = "https://image.tmdb.org/t/p/w500"
_IMG_ORIG = "https://image.tmdb.org/t/p/original"


# ─────────────────────────────────────────────────────────────────────────────
async def fetch_metadata(query: str, year: int = None) -> dict:
    """
    Search TMDB for *query* and return a dict containing all fields that
    movies.json expects.

    Args:
        query: Movie title (English or native language).
        year:  Release year (optional, improves accuracy).

    Returns:
        dict with keys: title, title_si, year, imdb_id, tmdb_id, poster_url,
        backdrop_url, genres, duration, description, cast, director, rating
    """
    if not TMDB_API_KEY:
        raise RuntimeError("TMDB_API_KEY is not configured.")

    params: dict = {
        "api_key": TMDB_API_KEY,
        "query": query,
        "language": "en-US",
        "include_adult": False,
    }
    if year:
        params["year"] = year

    async with httpx.AsyncClient(timeout=20) as client:
        # ── Step 1: Search for the movie ────────────────────────────────────
        resp = await client.get(f"{_BASE}/search/movie", params=params)
        resp.raise_for_status()
        results = resp.json().get("results", [])

        if not results:
            log.warning("TMDB: No results for '%s' (%s)", query, year)
            return _empty_metadata(query, year)

        movie = results[0]
        tmdb_id: int = movie["id"]
        log.info("TMDB: Found movie '%s' (tmdb_id=%s)", movie.get("title"), tmdb_id)

        # ── Step 2: Fetch full details (runtime, genres) ────────────────────
        detail_resp = await client.get(
            f"{_BASE}/movie/{tmdb_id}",
            params={"api_key": TMDB_API_KEY, "language": "en-US"},
        )
        detail_resp.raise_for_status()
        details = detail_resp.json()

        # ── Step 3: Fetch credits (cast + director) ─────────────────────────
        credits_resp = await client.get(
            f"{_BASE}/movie/{tmdb_id}/credits",
            params={"api_key": TMDB_API_KEY},
        )
        credits_resp.raise_for_status()
        credits = credits_resp.json()

        # ── Step 4: Fetch external IDs (imdb_id) ────────────────────────────
        ext_resp = await client.get(
            f"{_BASE}/movie/{tmdb_id}/external_ids",
            params={"api_key": TMDB_API_KEY},
        )
        ext_resp.raise_for_status()
        external = ext_resp.json()

    # ── Parse genres ─────────────────────────────────────────────────────────
    genres = [g["name"] for g in details.get("genres", [])]

    # ── Parse top-5 cast members ──────────────────────────────────────────────
    cast = [
        {"name": member["name"], "character": member.get("character", "")}
        for member in credits.get("cast", [])[:5]
    ]

    # ── Parse director(s) ─────────────────────────────────────────────────────
    director = ", ".join(
        c["name"]
        for c in credits.get("crew", [])
        if c.get("job") == "Director"
    ) or "Unknown"

    # ── Poster / backdrop URLs ────────────────────────────────────────────────
    poster_path = details.get("poster_path") or movie.get("poster_path")
    backdrop_path = details.get("backdrop_path") or movie.get("backdrop_path")
    poster_url = f"{_IMG_W500}{poster_path}" if poster_path else ""
    backdrop_url = f"{_IMG_ORIG}{backdrop_path}" if backdrop_path else ""

    # ── Release year ──────────────────────────────────────────────────────────
    release_date: str = details.get("release_date", "")
    release_year: int = int(release_date[:4]) if len(release_date) >= 4 else (year or 0)

    imdb_id: str = external.get("imdb_id", "")

    # ── Fetch IMDb rating ─────────────────────────────────────────────────────
    rating = await get_imdb_rating(imdb_id) if imdb_id else "N/A"

    return {
        "title": details.get("title", movie.get("title", query)),
        "title_si": "",  # Sinhala title — to be filled manually if desired
        "year": release_year,
        "imdb_id": imdb_id,
        "tmdb_id": str(tmdb_id),
        "poster_url": poster_url,
        "backdrop_url": backdrop_url,
        "genres": genres,
        "duration": details.get("runtime", 0),  # minutes
        "description": details.get("overview", ""),
        "cast": cast,
        "director": director,
        "rating": rating,
    }


# ─────────────────────────────────────────────────────────────────────────────
async def get_imdb_rating(imdb_id: str) -> str:
    """
    Fetch the IMDb vote average from TMDB's /find endpoint and return it as a
    string such as '8.1'.  Falls back to 'N/A' on any error.
    """
    if not imdb_id or not TMDB_API_KEY:
        return "N/A"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/find/{imdb_id}",
                params={
                    "api_key": TMDB_API_KEY,
                    "external_source": "imdb_id",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            movie_results = data.get("movie_results", [])
            if movie_results:
                vote_avg = movie_results[0].get("vote_average", 0)
                return str(round(float(vote_avg), 1))
    except Exception as exc:
        log.warning("Could not fetch IMDb rating for %s: %s", imdb_id, exc)

    return "N/A"


# ─────────────────────────────────────────────────────────────────────────────
def _empty_metadata(query: str, year: int = None) -> dict:
    """Return a blank metadata dict when TMDB returns no results."""
    return {
        "title": query,
        "title_si": "",
        "year": year or 0,
        "imdb_id": "",
        "tmdb_id": "",
        "poster_url": "",
        "backdrop_url": "",
        "genres": [],
        "duration": 0,
        "description": "",
        "cast": [],
        "director": "Unknown",
        "rating": "N/A",
    }


# ─────────────────────────────────────────────────────────────────────────────
async def fetch_by_imdb_id(imdb_id: str) -> dict:
    """
    Resolve an IMDb ID (e.g. 'tt1375666') to full TMDB metadata.

    Uses TMDB /find endpoint then delegates to fetch_metadata with tmdb_id.
    Returns empty metadata dict on failure.
    """
    if not imdb_id or not TMDB_API_KEY:
        return _empty_metadata(imdb_id)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/find/{imdb_id}",
                params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("movie_results", [])
            if not results:
                log.warning("TMDB: No movie results for IMDb ID: %s", imdb_id)
                return _empty_metadata(imdb_id)

            movie = results[0]
            tmdb_id = movie["id"]
            title = movie.get("title", "")
            year_str = movie.get("release_date", "")[:4]
            year = int(year_str) if year_str.isdigit() else None
            log.info("TMDB: Resolved IMDb %s -> tmdb_id=%s title=%r", imdb_id, tmdb_id, title)

        # Fetch full metadata using the resolved title + year
        meta = await fetch_metadata(title, year)
        # Ensure imdb_id is set even if TMDB doesn't surface it in search
        if not meta.get("imdb_id"):
            meta["imdb_id"] = imdb_id
        return meta

    except Exception as exc:
        log.warning("fetch_by_imdb_id failed for %s: %s", imdb_id, exc)
        return _empty_metadata(imdb_id)
