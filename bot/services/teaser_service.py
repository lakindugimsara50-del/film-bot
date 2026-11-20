"""
Teaser Service for FilmZilla Bot & Web Portal.
Implements Method 3: The Genius YouTube "Most Replayed" Trick.
Generates 30-second scene teaser metadata and vertical clips.
"""

import os
import sys
import json
import logging
from pathlib import Path

# Add project root to sys.path
BOT_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BOT_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from generate_teasers import (
        calculate_peak_window,
        fetch_single_youtube,
        resolve_tmdb_trailer,
        select_hook,
        estimate_movie_scene_seek,
        render_vertical_clip,
        generate_teasers,
        MOVIES_JSON,
        TEASERS_JSON,
        TEASERS_JS,
        TEASERS_MEDIA_DIR
    )
except ImportError:
    pass

logger = logging.getLogger(__name__)

class TeaserService:
    @staticmethod
    def generate_all_teasers(limit=None, download_clips_count=0):
        """Generates teasers for the catalog."""
        try:
            return generate_teasers(limit=limit, download_clips_count=download_clips_count)
        except Exception as e:
            logger.error(f"Error generating teasers: {e}")
            return False

    @staticmethod
    def get_teaser_for_movie(movie_id):
        """Fetches teaser metadata for a single movie."""
        if not TEASERS_JSON.exists():
            return None
        try:
            with open(TEASERS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            teasers = data.get("teasers", [])
            for t in teasers:
                if t.get("movie_id") == movie_id or t.get("id") == f"teaser-{movie_id}":
                    return t
        except Exception as e:
            logger.error(f"Error reading teasers: {e}")
        return None

    @staticmethod
    def add_or_update_movie_teaser(movie, download_clip=False):
        """Computes and updates teaser entry for a newly added movie."""
        try:
            trailer_id = movie.get("trailer_id")
            tmdb_id = movie.get("tmdb_id")
            if not trailer_id and tmdb_id:
                trailer_id = resolve_tmdb_trailer(tmdb_id, movie.get("type") == "series")
                if trailer_id:
                    movie["trailer_id"] = trailer_id
                    
            if not trailer_id:
                return None
                
            _, yt_data = fetch_single_youtube(trailer_id)
            duration = yt_data.get("duration") or 150
            heatmap = yt_data.get("heatmap", [])
            
            peak_start, peak_end, score, method = calculate_peak_window(heatmap, duration, 30.0)
            hook_obj = select_hook(movie.get("genres", []))
            scene_seek = estimate_movie_scene_seek(movie, peak_start, duration)
            m_id = movie.get("id") or movie.get("slug")
            
            clip_filename = f"{m_id}_teaser.mp4"
            clip_path = TEASERS_MEDIA_DIR / clip_filename
            video_url = ""
            
            if download_clip:
                success = render_vertical_clip(trailer_id, peak_start, peak_end, clip_path)
                if success:
                    video_url = f"assets/teasers/{clip_filename}"
            elif clip_path.exists():
                video_url = f"assets/teasers/{clip_filename}"
                
            teaser_item = {
                "id": f"teaser-{m_id}",
                "movie_id": m_id,
                "title": movie.get("title", ""),
                "title_si": movie.get("title_si", ""),
                "year": movie.get("year", 2026),
                "imdb": movie.get("imdb", movie.get("rating", "0.0")),
                "rating": movie.get("rating", movie.get("imdb", "0.0")),
                "quality": movie.get("quality", "1080p"),
                "genres": movie.get("genres", []),
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
                "youtube_embed_url": f"https://www.youtube-nocookie.com/embed/{trailer_id}?start={int(peak_start)}&end={int(peak_end)}&autoplay=1&mute=1&controls=0&loop=1&playlist={trailer_id}&playsinline=1&rel=0&modestbranding=1&enablejsapi=1",
                "stream_url": movie.get("stream_url", ""),
                "has_sinhala_sub": movie.get("has_sinhala_sub", True),
                "likes_count": 180
            }
            
            # Read current, prepend or update, and save
            teasers = []
            if TEASERS_JSON.exists():
                with open(TEASERS_JSON, "r", encoding="utf-8") as f:
                    teasers = json.load(f).get("teasers", [])
                    
            teasers = [t for t in teasers if t.get("movie_id") != m_id]
            teasers.insert(0, teaser_item)
            
            with open(TEASERS_JSON, "w", encoding="utf-8") as f:
                json.dump({"teasers": teasers, "total": len(teasers)}, f, indent=2, ensure_ascii=False)
                
            js_content = f"/** Auto-generated FilmZilla Teaser Data */\nwindow.FILMSUB_TEASERS = {json.dumps(teasers, indent=2, ensure_ascii=False)};\n"
            with open(TEASERS_JS, "w", encoding="utf-8") as f:
                f.write(js_content)
                
            return teaser_item
        except Exception as e:
            logger.error(f"Error in add_or_update_movie_teaser: {e}")
            return None
