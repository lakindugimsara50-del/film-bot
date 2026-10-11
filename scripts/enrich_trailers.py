import json
import os
import time
import urllib.request
import urllib.parse

TMDB_API_KEY = "dcbe3262c901719ed8b0291e751bc69a"
MOVIES_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "website", "data", "movies.json")
MOVIES_JS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "website", "data", "movies_data.js")

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FilmBotTrailerEnricher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        pass
    return None

def extract_best_youtube_trailer(results):
    if not results or not isinstance(results, list):
        return None
    yt_vids = [v for v in results if v.get("site") == "YouTube" and v.get("key")]
    if not yt_vids:
        return None
    # 1. Official Trailer
    for v in yt_vids:
        if v.get("type") == "Trailer" and v.get("official") is True:
            return v.get("key")
    # 2. Any Trailer
    for v in yt_vids:
        if v.get("type") == "Trailer":
            return v.get("key")
    # 3. Teaser
    for v in yt_vids:
        if v.get("type") == "Teaser":
            return v.get("key")
    # 4. Any YouTube clip
    return yt_vids[0].get("key")

def get_trailer_for_movie(movie):
    tmdb_id = movie.get("tmdb_id")
    imdb_id = movie.get("imdb_id")
    is_series = movie.get("type") == "series" or bool(movie.get("season"))
    
    # 1. Resolve tmdb_id if missing but imdb_id exists
    if not tmdb_id and imdb_id:
        find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
        find_data = fetch_json(find_url)
        if find_data:
            m_res = find_data.get("movie_results", [])
            tv_res = find_data.get("tv_results", [])
            if is_series and tv_res:
                tmdb_id = str(tv_res[0].get("id"))
            elif m_res:
                tmdb_id = str(m_res[0].get("id"))
                is_series = False
            elif tv_res:
                tmdb_id = str(tv_res[0].get("id"))
                is_series = True
    
    if not tmdb_id:
        return None

    # Check endpoints (tv vs movie)
    primary_type = "tv" if is_series else "movie"
    fallback_type = "movie" if is_series else "tv"

    for media_type in (primary_type, fallback_type):
        # First with language=en-US
        url_en = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/videos?api_key={TMDB_API_KEY}&language=en-US"
        data = fetch_json(url_en)
        key = extract_best_youtube_trailer(data.get("results") if data else None)
        if key:
            return key
            
        # Fallback without language restriction
        url_all = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/videos?api_key={TMDB_API_KEY}"
        data = fetch_json(url_all)
        key = extract_best_youtube_trailer(data.get("results") if data else None)
        if key:
            return key

    return None

def main():
    with open(MOVIES_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    movies = catalog.get("movies", [])
    print(f"Total movies to inspect: {len(movies)}")
    
    updated_count = 0
    already_had = 0
    not_found = 0

    for idx, m in enumerate(movies):
        title = m.get("title", f"Movie #{idx}")
        current_id = m.get("trailer_id")
        if current_id:
            already_had += 1
            continue

        key = get_trailer_for_movie(m)
        if key:
            m["trailer_id"] = key
            m["trailer_url"] = f"https://www.youtube-nocookie.com/embed/{key}"
            updated_count += 1
            print(f"[{idx+1}/{len(movies)}] Added trailer for: {title} -> {key}")
        else:
            not_found += 1
            print(f"[{idx+1}/{len(movies)}] NO trailer found for: {title}")
        time.sleep(0.08) # Respect TMDB rate limit

    # Save to movies.json
    with open(MOVIES_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    print(f"Saved {MOVIES_JSON}")

    # Save to movies_data.js
    js_content = "window.FILMSUB_DATA = window.MOVIES_DATA = window.__MOVIES_DATA__ = " + json.dumps(catalog, ensure_ascii=False, indent=2) + ";\n"
    with open(MOVIES_JS, "w", encoding="utf-8") as f:
        f.write(js_content)
    print(f"Saved {MOVIES_JS}")

    print("\nSummary:")
    print(f"  Already had trailer: {already_had}")
    print(f"  Newly updated:       {updated_count}")
    print(f"  No trailer found:    {not_found}")
    print(f"  Total with trailer:  {already_had + updated_count} / {len(movies)}")

if __name__ == "__main__":
    main()
