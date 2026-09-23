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
import re

# ─────────────────────────────────────────────────────────────────────────────
async def fetch_metadata(
    query: str,
    year: int = None,
    is_series: bool = False,
    season: int = None,
    episode: int = None,
) -> dict:
    """
    Search TMDB for *query* and return a dict containing all fields that
    movies.json expects. Intelligently detects whether the query is a TV series
    or a movie.
    """
    if not TMDB_API_KEY:
        raise RuntimeError("TMDB_API_KEY is not configured.")

    # 1. Regex analysis for Season and Episode tokens in query string
    s_match = re.search(r"\b(?:s|season\s*)(\d{1,2})\b", query, re.IGNORECASE)
    e_match = re.search(r"\b(?:e|episode\s*|ep\s*)(\d{1,2})\b", query, re.IGNORECASE)
    x_match = re.search(r"\b(\d{1,2})x(\d{1,2})\b", query, re.IGNORECASE)

    req_season = season
    req_episode = episode

    if x_match:
        req_season = req_season or int(x_match.group(1))
        req_episode = req_episode or int(x_match.group(2))
        is_series = True
    else:
        if s_match:
            req_season = req_season or int(s_match.group(1))
            is_series = True
        if e_match:
            req_episode = req_episode or int(e_match.group(1))
            is_series = True

    clean_query = re.sub(
        r"\b(s\d{1,2}\s*e\d{1,2}|season\s*\d+|s\d{1,2}|episode\s*\d+|ep\s*\d+|\d{1,2}x\d{1,2})\b.*$",
        "",
        query,
        flags=re.IGNORECASE,
    ).strip() or query

    async with httpx.AsyncClient(timeout=20) as client:
        selected_media_type = "tv" if is_series else None
        selected_item = None

        if is_series:
            # Explicit TV series query
            tv_resp = await client.get(
                f"{_BASE}/search/tv",
                params={"api_key": TMDB_API_KEY, "query": clean_query, "language": "en-US"},
            )
            if tv_resp.status_code == 200:
                tv_results = tv_resp.json().get("results", [])
                if tv_results:
                    selected_item = tv_results[0]
                    selected_media_type = "tv"
        else:
            # Disambiguate using /search/multi (or movie vs tv popularity check)
            multi_resp = await client.get(
                f"{_BASE}/search/multi",
                params={"api_key": TMDB_API_KEY, "query": clean_query, "language": "en-US"},
            )
            if multi_resp.status_code == 200:
                multi_results = multi_resp.json().get("results", [])
                tv_candidates = [it for it in multi_results if it.get("media_type") == "tv"]
                movie_candidates = [it for it in multi_results if it.get("media_type") == "movie"]

                top_tv = tv_candidates[0] if tv_candidates else None
                top_movie = movie_candidates[0] if movie_candidates else None

                if top_tv and top_movie:
                    tv_name = (top_tv.get("name") or "").strip().lower()
                    movie_title = (top_movie.get("title") or "").strip().lower()
                    q_lower = clean_query.lower()

                    # Exact title check
                    tv_exact = (tv_name == q_lower)
                    movie_exact = (movie_title == q_lower)

                    tv_pop = float(top_tv.get("popularity", 0))
                    movie_pop = float(top_movie.get("popularity", 0))

                    if tv_exact and not movie_exact:
                        selected_item = top_tv
                        selected_media_type = "tv"
                    elif movie_exact and not tv_exact:
                        selected_item = top_movie
                        selected_media_type = "movie"
                    elif tv_pop > movie_pop * 1.5:
                        selected_item = top_tv
                        selected_media_type = "tv"
                    else:
                        selected_item = top_movie
                        selected_media_type = "movie"
                elif top_tv:
                    selected_item = top_tv
                    selected_media_type = "tv"
                elif top_movie:
                    selected_item = top_movie
                    selected_media_type = "movie"

        if not selected_item:
            # Fallback: try search/movie directly
            params = {"api_key": TMDB_API_KEY, "query": clean_query, "language": "en-US", "include_adult": False}
            if year:
                params["year"] = year
            resp = await client.get(f"{_BASE}/search/movie", params=params)
            if resp.status_code == 200 and resp.json().get("results"):
                selected_item = resp.json()["results"][0]
                selected_media_type = "movie"

        if not selected_item:
            log.warning("TMDB: No movie or TV results for '%s' (%s)", clean_query, year)
            return _empty_metadata(query, year)

        # Process metadata based on selected media type
        if selected_media_type == "tv":
            meta = await _fetch_tv_metadata(client, selected_item)
            # Default to season 1 episode 1 if none specified
            meta["current_season"] = req_season or 1
            meta["current_episode"] = req_episode or 1

            # Fetch specific episode details if season and episode known
            if meta.get("tmdb_id") and meta.get("current_season") and meta.get("current_episode"):
                try:
                    ep_resp = await client.get(
                        f"{_BASE}/tv/{meta['tmdb_id']}/season/{meta['current_season']}/episode/{meta['current_episode']}",
                        params={"api_key": TMDB_API_KEY, "language": "en-US"},
                    )
                    if ep_resp.status_code == 200:
                        ep_data = ep_resp.json()
                        meta["episode_title"] = ep_data.get("name", "")
                        if ep_data.get("overview"):
                            meta["episode_overview"] = ep_data.get("overview")
                        if ep_data.get("runtime"):
                            meta["duration"] = ep_data.get("runtime")
                        log.info("TMDB: Found episode details S%02dE%02d: %s", meta["current_season"], meta["current_episode"], ep_data.get("name"))
                except Exception as ep_err:
                    log.debug("Could not fetch episode details: %s", ep_err)

            return meta

        movie = selected_item
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
        "original_title": details.get("original_title", movie.get("original_title", "")),
        "original_language": details.get("original_language", movie.get("original_language", "en")),
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
        "original_title": details.get("original_name", tv_item.get("original_name", "")),
        "original_language": details.get("original_language", tv_item.get("original_language", "en")),
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
    Resolve an IMDb ID (e.g. 'tt1375666' or 'tt0944947') to full TMDB metadata.
    Supports both movies and TV shows/episodes.
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

            movie_results = data.get("movie_results", [])
            tv_results = data.get("tv_results", [])
            tv_ep_results = data.get("tv_episode_results", [])

            if tv_ep_results:
                ep = tv_ep_results[0]
                show_id = ep.get("show_id")
                req_season = ep.get("season_number", 1)
                req_episode = ep.get("episode_number", 1)
                show_resp = await client.get(f"{_BASE}/tv/{show_id}", params={"api_key": TMDB_API_KEY, "language": "en-US"})
                if show_resp.status_code == 200:
                    meta = await _fetch_tv_metadata(client, show_resp.json())
                    meta["current_season"] = req_season
                    meta["current_episode"] = req_episode
                    meta["episode_title"] = ep.get("name", "")
                    meta["imdb_id"] = imdb_id
                    return meta

            if tv_results:
                top_tv = tv_results[0]
                tv_pop = float(top_tv.get("popularity", 0))
                movie_pop = float(movie_results[0].get("popularity", 0)) if movie_results else 0
                if tv_pop >= movie_pop or not movie_results:
                    meta = await _fetch_tv_metadata(client, top_tv)
                    meta["current_season"] = 1
                    meta["current_episode"] = 1
                    meta["imdb_id"] = imdb_id
                    return meta

            if movie_results:
                movie = movie_results[0]
                title = movie.get("title", "")
                year_str = movie.get("release_date", "")[:4]
                year = int(year_str) if year_str.isdigit() else None
                log.info("TMDB: Resolved IMDb %s -> tmdb_id=%s title=%r", imdb_id, movie.get("id"), title)
                meta = await fetch_metadata(title, year)
                if not meta.get("imdb_id"):
                    meta["imdb_id"] = imdb_id
                return meta

            log.warning("TMDB: No movie or TV results for IMDb ID: %s", imdb_id)
            return _empty_metadata(imdb_id)

    except Exception as exc:
        log.warning("fetch_by_imdb_id failed for %s: %s", imdb_id, exc)
        return _empty_metadata(imdb_id)
