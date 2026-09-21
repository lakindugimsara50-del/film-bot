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
            log.info("TMDB: No movie results for '%s', searching TV series...", query)
            tv_resp = await client.get(
                f"{_BASE}/search/tv",
                params={"api_key": TMDB_API_KEY, "query": query, "language": "en-US"},
            )
            if tv_resp.status_code == 200:
                tv_results = tv_resp.json().get("results", [])
                if tv_results:
                    return await _fetch_tv_metadata(client, tv_results[0])

            log.warning("TMDB: No movie or TV results for '%s' (%s)", query, year)
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
        "type": "movie",
    }


# ─────────────────────────────────────────────────────────────────────────────
async def _fetch_tv_metadata(client: httpx.AsyncClient, tv_item: dict) -> dict:
    """Fetch full TV series metadata including seasons and episodes from TMDB."""
    tmdb_id = tv_item["id"]
    name = tv_item.get("name", "Unknown Series")
    log.info("TMDB: Fetching TV details for '%s' (tmdb_id=%s)", name, tmdb_id)

    # 1. TV Details
    detail_resp = await client.get(
        f"{_BASE}/tv/{tmdb_id}",
        params={"api_key": TMDB_API_KEY, "language": "en-US"},
    )
    detail_resp.raise_for_status()
    details = detail_resp.json()

    # 2. Credits
    credits = {}
    try:
        cred_resp = await client.get(
            f"{_BASE}/tv/{tmdb_id}/credits",
            params={"api_key": TMDB_API_KEY},
        )
        if cred_resp.status_code == 200:
            credits = cred_resp.json()
    except Exception as e:
        log.warning("Could not fetch credits for TV %s: %s", tmdb_id, e)

    # 3. External IDs
    imdb_id = ""
    try:
        ext_resp = await client.get(
            f"{_BASE}/tv/{tmdb_id}/external_ids",
            params={"api_key": TMDB_API_KEY},
        )
        if ext_resp.status_code == 200:
            imdb_id = ext_resp.json().get("imdb_id", "")
    except Exception as e:
        log.warning("Could not fetch external_ids for TV %s: %s", tmdb_id, e)

    genres = [g["name"] for g in details.get("genres", [])]
    cast = [
        {"name": m["name"], "character": m.get("character", "")}
        for m in credits.get("cast", [])[:5]
    ]
    created_by = ", ".join(c["name"] for c in details.get("created_by", [])) or "Unknown"

    poster_path = details.get("poster_path") or tv_item.get("poster_path")
    backdrop_path = details.get("backdrop_path") or tv_item.get("backdrop_path")
    poster_url = f"{_IMG_W500}{poster_path}" if poster_path else ""
    backdrop_url = f"{_IMG_ORIG}{backdrop_path}" if backdrop_path else ""

    first_air_date: str = details.get("first_air_date", "")
    release_year: int = int(first_air_date[:4]) if len(first_air_date) >= 4 else 0

    rating = await get_imdb_rating(imdb_id) if imdb_id else str(round(float(details.get("vote_average", 0)), 1)) or "N/A"

    # Seasons list
    raw_seasons = details.get("seasons", [])
    seasons = []
    for s in raw_seasons:
        if s.get("season_number", 0) > 0:
            seasons.append({
                "season_number": s.get("season_number"),
                "name": s.get("name", f"Season {s.get('season_number')}"),
                "episode_count": s.get("episode_count", 0),
                "poster_url": f"{_IMG_W500}{s.get('poster_path')}" if s.get("poster_path") else "",
                "air_date": s.get("air_date", ""),
            })

    ep_dur = (details.get("episode_run_time") or [45])[0] if details.get("episode_run_time") else 45

    return {
        "title": details.get("name", name),
        "title_si": "",
        "year": release_year,
        "imdb_id": imdb_id,
        "tmdb_id": str(tmdb_id),
        "poster_url": poster_url,
        "backdrop_url": backdrop_url,
        "genres": genres,
        "duration": ep_dur,
        "description": details.get("overview", ""),
        "cast": cast,
        "director": created_by,
        "rating": rating,
        "type": "series",
        "number_of_seasons": details.get("number_of_seasons", len(seasons)),
        "number_of_episodes": details.get("number_of_episodes", 0),
        "seasons": seasons,
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
