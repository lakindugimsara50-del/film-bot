"""
catalog_audit_service.py — Missing Catalog Comparison & Live Sri Lankan Portal Auditor.

Compares website catalog (website/data/movies.json) against live releases from Sri Lankan
subtitle portals (SinhalaSub, CineSubz, PirateLK, Baiscope, Subz.lk) to find new movies
and series not yet uploaded or published to the website.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse

from bs4 import BeautifulSoup
import httpx

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

AUDIT_PORTALS = [
    {
        "name": "CineSubz",
        "feed_url": "https://cinesubz.co/feed/",
        "is_hardsub": True,
    },
    {
        "name": "SinhalaSub",
        "feed_url": "https://sinhalasub.net/feed/",
        "is_hardsub": True,
    },
    {
        "name": "PirateLK",
        "feed_url": "https://piratelk.com/feed/",
        "is_hardsub": False,
    },
    {
        "name": "Baiscope",
        "feed_url": "https://www.baiscope.lk/feed/",
        "is_hardsub": False,
    },
    {
        "name": "SubzLK",
        "feed_url": "https://subz.lk/feed/",
        "is_hardsub": False,
    },
]


def get_movies_json_path() -> str:
    """Resolve absolute path to website/data/movies.json."""
    candidates = [
        "/content/film_web_site/website/data/movies.json",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "website", "data", "movies.json"),
        os.path.join(os.getcwd(), "website", "data", "movies.json"),
        os.path.join(os.getcwd(), "data", "movies.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[1]


def normalize_title(title: str) -> str:
    """Normalize a title string for fuzzy comparison."""
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"\b(sinhala|subtitles?|subtitle|sinhala sub|sinhala subz|subz|hardsub|webrip|hdrip|bluray|x264|1080p|720p|480p)\b", "", t)
    # Remove Sinhala unicode characters
    t = re.sub(r"[\u0D80-\u0DFF]+", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def parse_release_title(raw_title: str) -> Dict[str, Any]:
    """
    Extract clean title, year, season, episode, and type from portal release title.
    Examples:
      'One Last Shot (2026) Sinhala Subtitle' -> title='One Last Shot', year=2026, is_series=False
      'My Bias, My Boss (2026) [S01 : E06] Sinhala Subtitle' -> title='My Bias, My Boss', year=2026, s=1, e=6, is_series=True
    """
    t = raw_title.strip()

    # Detect season and episode
    is_series = False
    season: Optional[int] = None
    episode: Optional[int] = None

    se_match = re.search(r"(?:\[|\(|\b)(?:s|season\s*)0*(\d{1,2})\s*[:\-_xe/]\s*(?:e|ep|episode\s*)0*(\d{1,2})(?:\]|\)|\b)", t, re.IGNORECASE)
    x_match = re.search(r"\b(\d{1,2})x0*(\d{1,2})\b", t, re.IGNORECASE)
    s_only = re.search(r"\b(?:s|season\s*)0*(\d{1,2})\b", t, re.IGNORECASE)
    e_only = re.search(r"\b(?:e|ep|episode\s*)0*(\d{1,2})\b", t, re.IGNORECASE)

    if se_match:
        season = int(se_match.group(1))
        episode = int(se_match.group(2))
        is_series = True
    elif x_match:
        season = int(x_match.group(1))
        episode = int(x_match.group(2))
        is_series = True
    elif s_only:
        season = int(s_only.group(1))
        is_series = True
        if e_only:
            episode = int(e_only.group(1))

    # Detect year
    year: Optional[int] = None
    ym = re.search(r"\b(19\d\d|20\d\d)\b", t)
    if ym:
        year = int(ym.group(1))

    # Strip junk tags from title
    clean = t
    # Remove bracketed/parenthesized tags
    clean = re.sub(r"\[.*?\]", " ", clean)
    # Remove Sinhala unicode characters
    clean = re.sub(r"[\u0D80-\u0DFF]+", " ", clean)
    # Remove keywords
    clean = re.sub(r"\b(sinhala\s*subtitles?|sinhala\s*sub|subtitles?|subz|hardsub|web-?dl|bluray|x264|1080p|720p|480p)\b.*$", "", clean, flags=re.IGNORECASE)
    # Remove year from title if present
    if year:
        clean = re.sub(rf"\b{year}\b", "", clean)
    # Remove series markers
    clean = re.sub(r"\b(?:s\d{1,2}|season\s*\d{1,2}|e\d{1,2}|episode\s*\d{1,2}|\d{1,2}x\d{1,2})\b.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[\(\)\-_–|:;]+", " ", clean).strip()
    clean = re.sub(r"\s+", " ", clean).strip()

    clean_lower = clean.lower()
    if (
        clean.isdigit()
        or len(clean) < 3
        or bool(re.match(r"^(.)\1+$", clean))
        or not re.search(r"[aeiouy0-9]", clean_lower)
        or any(jk in clean_lower for jk in ("protected", "test", "demo", "sample", "kasun", "admin", "asdf", "asdd", "qwerty"))
    ):
        clean = ""

    return {
        "raw_title": raw_title,
        "clean_title": clean,
        "year": year,
        "season": season,
        "episode": episode,
        "is_series": is_series,
    }


def load_website_catalog() -> Dict[str, Any]:
    """Load existing catalog metadata from movies.json."""
    path = get_movies_json_path()
    if not os.path.exists(path):
        log.warning("[CatalogAudit] movies.json not found at %s", path)
        return {
            "path": path,
            "total": 0,
            "normalized_titles": set(),
            "title_years": set(),
            "slugs": set(),
            "imdb_ids": set(),
        }

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        movies = data.get("movies", [])
        norm_titles: Set[str] = set()
        title_years: Set[Tuple[str, Optional[int]]] = set()
        slugs: Set[str] = set()
        imdb_ids: Set[str] = set()
        series_episodes: Set[Tuple[str, int, int]] = set()
        series_titles: Set[str] = set()
        movie_titles: Set[str] = set()

        for m in movies:
            t = m.get("title", "")
            nt = normalize_title(t)
            if nt:
                norm_titles.add(nt)
            y = m.get("year")
            if nt and y:
                title_years.add((nt, int(y)))
            s = m.get("slug") or m.get("id")
            if s:
                slugs.add(str(s).lower().strip())
            imdb = m.get("imdb_id")
            if imdb:
                imdb_ids.add(str(imdb).lower().strip())

            is_ser = (m.get("type") == "series") or (m.get("season") is not None)
            s_num = m.get("season")
            e_num = m.get("episode")
            if is_ser:
                if nt:
                    series_titles.add(nt)
                if nt and s_num is not None and e_num is not None:
                    try:
                        series_episodes.add((nt, int(s_num), int(e_num)))
                    except (ValueError, TypeError):
                        pass
            else:
                if nt:
                    movie_titles.add(nt)

        return {
            "path": path,
            "total": len(movies),
            "normalized_titles": norm_titles,
            "title_years": title_years,
            "slugs": slugs,
            "imdb_ids": imdb_ids,
            "series_episodes": series_episodes,
            "series_titles": series_titles,
            "movie_titles": movie_titles,
        }
    except Exception as exc:
        log.error("[CatalogAudit] Failed loading movies.json: %s", exc)
        return {
            "path": path,
            "total": 0,
            "normalized_titles": set(),
            "title_years": set(),
            "slugs": set(),
            "imdb_ids": set(),
            "series_episodes": set(),
            "series_titles": set(),
            "movie_titles": set(),
        }


async def fetch_feed_items(portal: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetch and parse RSS feed items for a Sri Lankan portal."""
    name = portal["name"]
    url = portal["feed_url"]
    is_hardsub = portal.get("is_hardsub", False)
    items: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=12.0, follow_redirects=True, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.debug("[CatalogAudit] Portal %s feed returned HTTP %s", name, resp.status_code)
                return []

            soup = BeautifulSoup(resp.text, "xml")
            feed_items = soup.find_all("item")
            for it in feed_items:
                t_elem = it.find("title")
                l_elem = it.find("link")
                if not t_elem or not l_elem:
                    continue
                raw_t = t_elem.get_text(strip=True)
                link = l_elem.get_text(strip=True)
                if not raw_t or not link:
                    continue

                parsed = parse_release_title(raw_t)
                if not parsed["clean_title"]:
                    continue

                items.append({
                    "portal": name,
                    "is_hardsub": is_hardsub,
                    "post_url": link,
                    "raw_title": raw_t,
                    "clean_title": parsed["clean_title"],
                    "year": parsed["year"],
                    "season": parsed["season"],
                    "episode": parsed["episode"],
                    "is_series": parsed["is_series"],
                })
    except Exception as exc:
        log.debug("[CatalogAudit] Exception fetching feed for %s: %s", name, exc)

    return items


async def audit_catalog_vs_srilankan_sites(max_per_portal: int = 15) -> Dict[str, Any]:
    """
    Perform a complete catalog audit comparing website/data/movies.json
    against live Sri Lankan portal RSS feeds.
    Returns categorized missing and cataloged items.
    """
    catalog = load_website_catalog()
    norm_titles = catalog["normalized_titles"]
    title_years = catalog["title_years"]
    slugs = catalog["slugs"]

    # Concurrently fetch RSS feeds from portals
    tasks = [fetch_feed_items(p) for p in AUDIT_PORTALS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_scraped: List[Dict[str, Any]] = []
    seen_posts: Set[str] = set()

    for res in results:
        if isinstance(res, list):
            for it in res[:max_per_portal]:
                p_url = it["post_url"]
                if p_url not in seen_posts:
                    seen_posts.add(p_url)
                    all_scraped.append(it)

    norm_titles = catalog["normalized_titles"]
    title_years = catalog["title_years"]
    slugs = catalog["slugs"]
    series_episodes = catalog.get("series_episodes", set())
    series_titles = catalog.get("series_titles", set())
    movie_titles = catalog.get("movie_titles", set())

    missing_items: List[Dict[str, Any]] = []
    cataloged_items: List[Dict[str, Any]] = []

    for it in all_scraped:
        c_title = it["clean_title"]
        nt = normalize_title(c_title)
        yr = it["year"]
        is_ser = it.get("is_series", False)
        s = it.get("season")
        e = it.get("episode")

        is_present = False
        if is_ser:
            if s is not None and e is not None:
                if (nt, s, e) in series_episodes:
                    is_present = True
                else:
                    ep_slug_part = f"s{s:02d}e{e:02d}"
                    if any(nt.replace(" ", "-") in slug and ep_slug_part in slug for slug in slugs):
                        is_present = True
            else:
                if yr and (nt, yr) in title_years:
                    is_present = True
                elif nt in series_titles:
                    is_present = True
        else:
            if yr and (nt, yr) in title_years:
                is_present = True
            elif nt in movie_titles or nt in norm_titles:
                is_present = True
            else:
                # Check slug pattern
                base_slug = re.sub(r"[^a-z0-9]+", "-", f"{c_title} {yr or ''}".lower()).strip("-")
                if base_slug in slugs:
                    is_present = True

        if is_present:
            cataloged_items.append(it)
        else:
            missing_items.append(it)

    # Sort missing items:
    # 1. Pre-hardsubbed portals first (CineSubz, SinhalaSub)
    # 2. Year descending
    missing_items.sort(
        key=lambda x: (
            0 if x.get("is_hardsub") else 1,
            -(x.get("year") or 0),
            x.get("clean_title", ""),
        )
    )

    missing_movies = [m for m in missing_items if not m.get("is_series")]
    missing_series = [m for m in missing_items if m.get("is_series")]

    return {
        "catalog_path": catalog["path"],
        "catalog_count": catalog["total"],
        "total_scraped": len(all_scraped),
        "total_missing": len(missing_items),
        "missing_movies_count": len(missing_movies),
        "missing_series_count": len(missing_series),
        "total_cataloged": len(cataloged_items),
        "missing_items": missing_items,
        "cataloged_items": cataloged_items,
    }


def format_audit_report(audit_data: Dict[str, Any], limit: int = 10) -> str:
    """
    Format a rich Telegram HTML report with 1-click leech commands.
    """
    cat_total = audit_data.get("catalog_count", 0)
    scraped_total = audit_data.get("total_scraped", 0)
    missing = audit_data.get("missing_items", [])
    missing_total = len(missing)
    missing_movies_cnt = audit_data.get("missing_movies_count", len([m for m in missing if not m.get("is_series")]))
    missing_series_cnt = audit_data.get("missing_series_count", len([m for m in missing if m.get("is_series")]))

    lines = [
        "📊 <b>Sri Lankan Sites ➔ Catalog Audit Report</b>\n",
        f"🎬 <b>Website Catalog:</b> <code>{cat_total}</code> Movies/Series",
        f"🌐 <b>Live Releases Inspected:</b> <code>{scraped_total}</code>",
        f"⚡ <b>Missing Total:</b> <b>{missing_total}</b> new releases",
        f"   ▫️ 🎬 <b>Missing Movies:</b> <code>{missing_movies_cnt}</code>",
        f"   ▫️ 📺 <b>Missing Series Episodes:</b> <code>{missing_series_cnt}</code>\n",
    ]

    if not missing:
        lines.append("🎉 <b>All live releases are already up to date in the catalog!</b>")
        return "\n".join(lines)

    lines.append("📋 <b>Missing Releases (Click command to Leech):</b>\n")

    for i, it in enumerate(missing[:limit], 1):
        p_name = it["portal"]
        is_hard = it["is_hardsub"]
        c_title = it["clean_title"]
        yr = it["year"]
        is_series = it["is_series"]
        s = it["season"]
        e = it["episode"]
        post_url = it["post_url"]

        tag = "⚡ <b>Hardsub</b>" if is_hard else "📝 <b>Burn Sub</b>"
        yr_str = f" ({yr})" if yr else ""

        if is_series:
            ep_str = f" S{s:02d}E{e:02d}" if (s and e) else (f" S{s:02d}" if s else "")
            header = f"{i}. 📺 <b>{c_title}{yr_str}{ep_str}</b> [{p_name} • {tag}]"
            leech_cmd = f"/series {c_title}{ep_str}"
        else:
            header = f"{i}. 🎬 <b>{c_title}{yr_str}</b> [{p_name} • {tag}]"
            leech_cmd = f"/leech {c_title}{yr_str}".strip()

        lines.append(f"{header}")
        lines.append(f"   👉 <code>{leech_cmd}</code>")
        lines.append(f"   🔗 <a href='{post_url}'>View Source Post</a>\n")

    if missing_total > limit:
        lines.append(f"<i>...and {missing_total - limit} more missing releases.</i>\n")

    lines.append("💡 <i>Tip: Tap any command above to auto-download and upload to Telegram & Website!</i>")
    return "\n".join(lines)
