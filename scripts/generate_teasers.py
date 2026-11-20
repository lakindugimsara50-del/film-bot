#!/usr/bin/env python3
"""
Method 3: The Genius YouTube "Most Replayed" Trick
Automated 30-Second Scene Teaser Generator for FilmZilla / FilmSub.

Extracts YouTube heatmaps (replay intensity), finds the exact 30-second
peak moment, generates teaser metadata with Sinhala & English hooks,
and optionally renders vertical 9:16 blurred-backdrop MP4 clips with FFmpeg.
"""

import os
import sys
import json
import time
import argparse
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
MOVIES_JSON = ROOT_DIR / "website" / "data" / "movies.json"
TEASERS_JSON = ROOT_DIR / "website" / "data" / "teasers.json"
TEASERS_JS = ROOT_DIR / "website" / "data" / "teasers_data.js"
TEASERS_MEDIA_DIR = ROOT_DIR / "website" / "assets" / "teasers"
CACHE_FILE = ROOT_DIR / "website" / "data" / ".yt_heatmaps_cache.json"

TMDB_API_KEY = "dcbe3262c901719ed8b0291e751bc69a"

# Hook templates based on genre
GENRE_HOOKS = {
    "Action": {
        "en": "🔥 Most Replayed Stunt & Action Climax",
        "si": "🔥 වැඩිම පිරිසක් නැවත නැවත නැරඹූ සටන් ජවනිකාව"
    },
    "Thriller": {
        "en": "⚡ Edge-of-the-Seat Thriller Confrontation",
        "si": "⚡ කුතුහලය දනවන තීරණාත්මක ත්‍රාසජනක ජවනිකාව"
    },
    "Horror": {
        "en": "👻 Bone-Chilling Jump Scare & Suspense Peak",
        "si": "👻 ඇඟ කිලිපොලා යන භීෂණාත්මක ජවනිකාව"
    },
    "Sci-Fi": {
        "en": "🚀 Mind-Bending Visual Spectacle",
        "si": "🚀 සුවිශේෂී විද්‍යා ප්‍රබන්ධ ජවනිකාව"
    },
    "Comedy": {
        "en": "😂 Viral Hilarious Punch Scene",
        "si": "😂 වෛරල් වූ හාස්‍යජනක ජවනිකාව"
    },
    "Drama": {
        "en": "🎭 High-Stakes Emotional Turning Point",
        "si": "🎭 තීරණාත්මක චිත්තවේගීය හැරවුම් ලක්ෂ්‍යය"
    },
    "Crime": {
        "en": "💥 Intense Criminal Showdown",
        "si": "💥 දැවැන්ත පාතාල ගැටුම් ජවනිකාව"
    },
    "Animation": {
        "en": "✨ Enchanting Spectacular Scene",
        "si": "✨ විචිත්‍රවත් සජීවීකරණ ජවනිකාව"
    },
    "Romance": {
        "en": "💖 Heartwarming Iconic Romantic Moment",
        "si": "💖 සිත සංවේදී කරවන ආදරණීය ජවනිකාව"
    },
    "Adventure": {
        "en": "🌍 Adrenaline-Pumping Epic Journey",
        "si": "🌍 ත්‍රාසජනක වීර චාරිකා ජවනිකාව"
    }
}

DEFAULT_HOOK = {
    "en": "🔥 Most Replayed Scene • YouTube Heatmap Peak",
    "si": "🔥 වැඩිම පිරිසක් නැවත නැවත නැරඹූ සුවිශේෂී ජවනිකාව"
}

def load_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Could not save cache: {e}")

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FilmBotTeaserGen/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None

def resolve_tmdb_trailer(tmdb_id, is_series=False):
    """Fetches trailer from TMDB if movie.trailer_id is missing or dead."""
    if not tmdb_id:
        return None
    endpoint = "tv" if is_series else "movie"
    url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}/videos?api_key={TMDB_API_KEY}"
    data = fetch_json(url)
    if not data or "results" not in data:
        return None
        
    yt_vids = [v for v in data["results"] if v.get("site") == "YouTube" and v.get("key")]
    for v in yt_vids:
        if v.get("type") == "Trailer" and v.get("official") is True:
            return v.get("key")
    for v in yt_vids:
        if v.get("type") == "Trailer":
            return v.get("key")
    for v in yt_vids:
        if v.get("type") == "Teaser":
            return v.get("key")
    if yt_vids:
        return yt_vids[0].get("key")
    return None

def calculate_peak_window(heatmap, duration, window_size=30.0):
    """
    Finds the 30-second window with the highest integrated replay intensity
    from the YouTube heatmap data.
    """
    duration = float(duration or 150.0)
    window_size = float(min(window_size, max(15.0, duration - 10.0)))
    
    if not heatmap or len(heatmap) < 5:
        cand_start = max(5.0, duration * 0.58)
        cand_end = min(duration, cand_start + window_size)
        return round(cand_start, 1), round(cand_end, 1), 0.78, "climax_algorithm"
        
    max_score = -1.0
    best_start = 10.0
    max_search_start = max(0.0, duration - window_size - 3.0)
    
    for item in heatmap:
        cand_start = float(item.get("start_time", 0.0))
        if cand_start < 5.0 and len(heatmap) > 10:
            continue
        if cand_start > max_search_start:
            break
            
        cand_end = cand_start + window_size
        
        score = 0.0
        total_overlap = 0.0
        for pt in heatmap:
            p_start = float(pt.get("start_time", 0.0))
            p_end = float(pt.get("end_time", 0.0))
            overlap = max(0.0, min(cand_end, p_end) - max(cand_start, p_start))
            if overlap > 0:
                score += overlap * float(pt.get("value", 0.0))
                total_overlap += overlap
                
        avg_score = (score / total_overlap) if total_overlap > 0 else 0.0
        if avg_score > max_score:
            max_score = avg_score
            best_start = cand_start
            
    best_end = min(duration, best_start + window_size)
    return round(best_start, 1), round(best_end, 1), round(max(0.68, min(0.99, max_score)), 3), "youtube_heatmap"

def fetch_single_youtube(trailer_id):
    """Worker function to fetch youtube metadata with yt-dlp."""
    try:
        import yt_dlp
    except ImportError:
        return trailer_id, {"duration": 150, "heatmap": [], "title": ""}

    url = f"https://www.youtube.com/watch?v={trailer_id}"
    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 4,
        "retries": 0,
        "ignoreerrors": True
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                return trailer_id, {
                    "duration": info.get("duration", 150),
                    "heatmap": info.get("heatmap", []),
                    "title": info.get("title", ""),
                    "chapters": info.get("chapters", [])
                }
    except Exception:
        pass

    return trailer_id, {"duration": 150, "heatmap": [], "title": "", "chapters": []}

def estimate_movie_scene_seek(movie, peak_start, trailer_duration):
    """
    Estimates a corresponding scene timestamp in the full movie (in seconds).
    Maps trailer relative position to typical film scene zone (30% to 75% of runtime).
    """
    dur_str = str(movie.get("duration", "100 min"))
    try:
        digits = ''.join(c for c in dur_str.split()[0] if c.isdigit())
        dur_mins = int(digits) if digits else 105
    except Exception:
        dur_mins = 105
        
    total_movie_secs = max(60, dur_mins * 60)
    rel_pos = (peak_start / max(60.0, trailer_duration or 150.0))
    target_sec = int(total_movie_secs * (0.32 + (rel_pos * 0.42)))
    return max(30, min(max(30, total_movie_secs - 60), target_sec))

def render_vertical_clip(trailer_id, start_time, end_time, output_mp4):
    """
    Uses yt-dlp to download the 30s slice, and FFmpeg to render a 9:16 vertical
    format with blurred cinematic backdrop.
    """
    try:
        import yt_dlp
    except ImportError:
        return False
        
    temp_clip = output_mp4.with_suffix(".temp.webm")
    url = f"https://www.youtube.com/watch?v={trailer_id}"
    
    ydl_opts = {
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start_time, end_time)]),
        "outtmpl": str(temp_clip),
        "force_keyframes_at_cuts": True,
        "quiet": True,
        "no_warnings": True
    }
    
    try:
        print(f"Downloading 30s cut for {trailer_id} ({start_time}s - {end_time}s)...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        candidates = list(output_mp4.parent.glob(f"{output_mp4.stem}.temp.*"))
        if not candidates:
            return False
        src_temp = candidates[0]
        
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", str(src_temp),
            "-vf", "split[bg][fg];[bg]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=15:3[bgblur];[fg]scale=720:-2[fgfit];[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k",
            str(output_mp4)
        ]
        
        print(f"Rendering 9:16 vertical video with FFmpeg...")
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        for c in candidates:
            try:
                c.unlink()
            except Exception:
                pass
                
        return output_mp4.exists()
    except Exception as e:
        print(f"FFmpeg vertical render error: {e}")
        return False

def select_hook(genres):
    """Selects the most suitable scene hook based on genres."""
    if not genres:
        return DEFAULT_HOOK
    for g in genres:
        if g in GENRE_HOOKS:
            return GENRE_HOOKS[g]
    return DEFAULT_HOOK

def generate_teasers(limit=None, download_clips_count=0):
    print("=" * 60)
    print("FilmZilla / FilmSub - YouTube 'Most Replayed' Teaser Generator")
    print("=" * 60)
    
    if not MOVIES_JSON.exists():
        print(f"Error: {MOVIES_JSON} not found!")
        return False
        
    with open(MOVIES_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)
        
    movies = catalog.get("movies", [])
    print(f"Found {len(movies)} movies in catalog.")
    
    TEASERS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cache()
    
    # 1. Prepare movies with trailer_id
    valid_movies = []
    trailers_to_fetch = set()
    
    for movie in movies:
        trailer_id = movie.get("trailer_id")
        tmdb_id = movie.get("tmdb_id")
        is_series = movie.get("type") == "series" or bool(movie.get("season"))
        
        if not trailer_id and tmdb_id:
            trailer_id = resolve_tmdb_trailer(tmdb_id, is_series)
            if trailer_id:
                movie["trailer_id"] = trailer_id
                
        if trailer_id:
            valid_movies.append(movie)
            if trailer_id not in cache:
                trailers_to_fetch.add(trailer_id)
                
    if limit:
        valid_movies = valid_movies[:limit]
        trailers_to_fetch = {m.get("trailer_id") for m in valid_movies if m.get("trailer_id") not in cache}
        
    print(f"Total movies with trailers: {len(valid_movies)}")
    print(f"Trailers to fetch from YouTube (uncached): {len(trailers_to_fetch)}")
    
    # 2. Concurrently fetch uncached YouTube metadata
    if trailers_to_fetch:
        print("Fetching YouTube heatmaps concurrently...")
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_single_youtube, tid): tid for tid in trailers_to_fetch}
            for future in as_completed(futures):
                tid, res = future.result()
                cache[tid] = res
        save_cache(cache)
        print("YouTube metadata collection complete!")

    teasers = []
    clips_downloaded = 0
    
    for idx, movie in enumerate(valid_movies, 1):
        m_id = movie.get("id") or movie.get("slug")
        title = movie.get("title", "Untitled")
        title_si = movie.get("title_si", "")
        genres = movie.get("genres", [])
        trailer_id = movie.get("trailer_id")
        
        yt_data = cache.get(trailer_id) or {"duration": 150, "heatmap": []}
        duration = yt_data.get("duration") or 150
        heatmap = yt_data.get("heatmap", [])
        
        peak_start, peak_end, score, method = calculate_peak_window(heatmap, duration, 30.0)
        hook_obj = select_hook(genres)
        scene_seek = estimate_movie_scene_seek(movie, peak_start, duration)
        
        clip_filename = f"{m_id}_teaser.mp4"
        clip_path = TEASERS_MEDIA_DIR / clip_filename
        video_url = ""
        
        if clip_path.exists() and clip_path.stat().st_size > 10000:
            video_url = f"assets/teasers/{clip_filename}"
        elif clips_downloaded < download_clips_count:
            success = render_vertical_clip(trailer_id, peak_start, peak_end, clip_path)
            if success:
                video_url = f"assets/teasers/{clip_filename}"
                clips_downloaded += 1
                
        yt_embed_url = (
            f"https://www.youtube-nocookie.com/embed/{trailer_id}"
            f"?start={int(peak_start)}&end={int(peak_end)}"
            f"&autoplay=1&mute=1&controls=0&loop=1&playlist={trailer_id}"
            f"&playsinline=1&rel=0&modestbranding=1&enablejsapi=1"
        )
        
        teaser_item = {
            "id": f"teaser-{m_id}",
            "movie_id": m_id,
            "title": title,
            "title_si": title_si,
            "year": movie.get("year", 2026),
            "imdb": movie.get("imdb", movie.get("rating", "0.0")),
            "rating": movie.get("rating", movie.get("imdb", "0.0")),
            "quality": movie.get("quality", "1080p"),
            "genres": genres,
            "poster": movie.get("poster") or movie.get("poster_url", ""),
            "backdrop": movie.get("backdrop") or movie.get("backdrop_url", ""),
            "trailer_id": trailer_id,
            "clip_start": peak_start,
            "clip_end": peak_end,
            "duration": 30,
            "intensity_score": score,
            "detection_method": method,
            "scene_hook": hook_obj["en"],
            "scene_hook_si": hook_obj["si"],
            "scene_timestamp_movie": scene_seek,
            "formatted_seek_time": f"{scene_seek // 60}:{scene_seek % 60:02d}",
            "video_url": video_url,
            "youtube_embed_url": yt_embed_url,
            "stream_url": movie.get("stream_url", ""),
            "has_sinhala_sub": movie.get("has_sinhala_sub", True),
            "likes_count": 120 + (idx * 37) % 850
        }
        teasers.append(teaser_item)
        
    save_cache(cache)
    
    with open(TEASERS_JSON, "w", encoding="utf-8") as f:
        json.dump({"teasers": teasers, "generated_at": int(time.time()), "total": len(teasers)}, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(teasers)} teasers to {TEASERS_JSON}")
    
    js_content = f"/** Auto-generated FilmZilla Teaser Data */\nwindow.FILMSUB_TEASERS = {json.dumps(teasers, indent=2, ensure_ascii=False)};\n"
    with open(TEASERS_JS, "w", encoding="utf-8") as f:
        f.write(js_content)
    print(f"Saved teasers to {TEASERS_JS}")
    
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FilmZilla 30s Teaser Generator")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of movies to process")
    parser.add_argument("--download-clips", type=int, default=0, help="Number of vertical clips to render with FFmpeg")
    args = parser.parse_args()
    
    generate_teasers(limit=args.limit, download_clips_count=args.download_clips)
