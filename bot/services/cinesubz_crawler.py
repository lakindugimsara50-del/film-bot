"""
cinesubz_crawler.py — Automated Reverse-Catalog Crawler & Harvester for CineSubz.

Crawls all movies and TV series from CineSubz from the OLDEST releases up to the NEWEST,
verifies each release against the local website catalog (website/data/movies.json),
skips any release that already exists on the site, and sequentially feeds missing releases
into the auto-leech queue strictly one-by-one so VPS/Colab RAM and storage never overflow.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse

from bs4 import BeautifulSoup
import httpx
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from services.catalog_audit_service import load_website_catalog, normalize_title
from services import queue_service

log = logging.getLogger(__name__)

CINESUBZ_BASE_URLS = [
    "https://cinesubz.lk",
    "https://cinesubz.co",
    "https://cinesubz.net",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
}

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cinesubz_crawler_state.json"
)
WEBSITE_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "website", "data", "cinesubz_crawler_state.json"
)
COLAB_GDRIVE_STATE_DIR = "/content/drive/MyDrive/film_bot"
COLAB_GDRIVE_STATE_FILE = os.path.join(COLAB_GDRIVE_STATE_DIR, "cinesubz_crawler_state.json")


def clean_cinesubz_title(raw_text: str) -> Dict[str, Any]:
    """
    Extract clean title, year, IMDb ID, and series indicators from CineSubz raw title text.
    Example:
      'Maremma (2026) Sinhala Subtitles |   ' -> title='Maremma', year=2026, is_series=False
      'Arcane (2021) S01 & S02 Complete' -> title='Arcane', year=2021, is_series=True
    """
    t = (raw_text or "").strip()

    # Extract year
    year: Optional[int] = None
    ym = re.search(r"\b(19\d\d|20\d\d)\b", t)
    if ym:
        year = int(ym.group(1))

    # Detect series
    is_series = bool(
        re.search(r"\b(?:s\d{1,2}|season\s*\d{1,2}|tv\s*series|complete\s*season|episodes?|ep\d+)\b", t, re.IGNORECASE)
    )

    # Clean title
    clean = t
    clean = re.sub(r"\[.*?\]", " ", clean)
    clean = re.sub(r"[\u0D80-\u0DFF]+", " ", clean)  # Strip Sinhala text
    clean = re.sub(
        r"\b(?:sinhala\s*subtitles?|sinhala\s*sub|subtitles?|subz|hardsub|web-?dl|bluray|hdrip|watch\s*online|complete|tv\s*series|with\s*sinhala)\b.*$",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    if year:
        clean = re.sub(rf"\b{year}\b", "", clean)
    clean = re.sub(r"\b(?:s\d{1,2}|season\s*\d{1,2}|ep\d{1,2}|episode\s*\d{1,2})\b.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[\(\)\-_–|:;]+", " ", clean).strip()
    clean = re.sub(r"\s+", " ", clean).strip()

    return {
        "raw_title": t,
        "clean_title": clean or t,
        "year": year,
        "is_series": is_series,
    }


async def get_cinesubz_max_pages(client: httpx.AsyncClient, section: str = "movies") -> int:
    """Detect total pages for a section (e.g. 'movies' or 'tvshows') on CineSubz with multi-retries and robust regex."""
    for base in CINESUBZ_BASE_URLS:
        for attempt in range(3):
            try:
                url = f"{base}/{section}/"
                resp = await client.get(url, headers=HEADERS, timeout=15.0)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    pag = soup.find(class_=re.compile(r"pagination|pages|page-nav", re.I))
                    if pag:
                        # 1. Look for 'Page 1 of 480'
                        for elem in pag.find_all(["span", "div"], class_=re.compile(r"total|pages|current", re.I)):
                            m = re.search(r"of\s+(\d+)", elem.get_text(), re.I)
                            if m:
                                return int(m.group(1))
                        # 2. Fallback: scan page links in pagination
                        page_nums = []
                        for a in pag.find_all("a", href=True):
                            pm = re.search(r"/page/(\d+)/?", a["href"])
                            if pm:
                                page_nums.append(int(pm.group(1)))
                        if page_nums:
                            return max(page_nums)

                    # 3. Scan whole document for pagination links
                    doc_page_nums = []
                    for a in soup.find_all("a", href=re.compile(rf"/{section}/page/(\d+)")):
                        pm = re.search(rf"/{section}/page/(\d+)", a["href"])
                        if pm:
                            doc_page_nums.append(int(pm.group(1)))
                    if doc_page_nums:
                        return max(doc_page_nums)
                    break
            except Exception as exc:
                log.debug("[CineSubzCrawler] Max page detection attempt %d for %s on %s: %s", attempt + 1, section, base, exc)
                await asyncio.sleep(1.0)

    # Robust default: CineSubz has 480+ movie pages and 45+ TV show pages
    return 480 if section == "movies" else 45


async def scrape_cinesubz_page(
    client: httpx.AsyncClient,
    page: int,
    section: str = "movies",
) -> List[Dict[str, Any]]:
    """Scrape all .display-item cards from a CineSubz page."""
    items: List[Dict[str, Any]] = []
    for base in CINESUBZ_BASE_URLS:
        try:
            page_url = f"{base}/{section}/page/{page}/" if page > 1 else f"{base}/{section}/"
            resp = await client.get(page_url, headers=HEADERS, timeout=12.0)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".display-item")
            for card in cards:
                a_link = card.find("a", href=True)
                if not a_link:
                    continue
                href = a_link["href"].strip()
                if f"/{section}/" not in href or "/page/" in href:
                    continue

                h3_elem = card.find("h3") or card.find("h2") or a_link
                raw_title = h3_elem.get_text(strip=True) if h3_elem else ""

                # Extract IMDb ID if present
                imdb_id = ""
                r1_elem = card.find(class_="rating1")
                if r1_elem:
                    im_m = re.search(r"tt\d+", r1_elem.get_text())
                    if im_m:
                        imdb_id = im_m.group(0)

                parsed = clean_cinesubz_title(raw_title)
                c_title = parsed["clean_title"]
                yr = parsed["year"]

                if not c_title or len(c_title) < 2:
                    continue

                items.append({
                    "url": href,
                    "raw_title": raw_title,
                    "clean_title": c_title,
                    "year": yr,
                    "imdb_id": imdb_id,
                    "is_series": bool(section == "tvshows" or parsed["is_series"]),
                    "page": page,
                    "section": section,
                })

            if items:
                break
        except Exception as p_err:
            log.debug("[CineSubzCrawler] Error scraping page %s of %s on %s: %s", page, section, base, p_err)

    return items


async def scrape_cinesubz_tvshow_episodes(
    client: httpx.AsyncClient,
    tvshow_url: str,
) -> List[Dict[str, Any]]:
    """Scrape and parse all episode links from a CineSubz TV show page."""
    episodes: List[Dict[str, Any]] = []
    seen_eps: Set[Tuple[int, int]] = set()

    try:
        resp = await client.get(tvshow_url, headers=HEADERS, timeout=12.0)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            h = a["href"].strip()
            if "/episodes/" not in h:
                continue

            # Extract S and E from URL
            # e.g. hawkeye-1x1/ -> (1, 1), hawkeye-s01-e02/ -> (1, 2), stranger-things-4x9/ -> (4, 9)
            s_num, e_num = None, None
            m1 = re.search(r"[-_/](\d{1,2})x0*(\d{1,2})(?:[-_/]|$)", h)
            if m1:
                s_num, e_num = int(m1.group(1)), int(m1.group(2))
            else:
                m2 = re.search(r"s0*(\d{1,2})[-_]?e0*(\d{1,2})", h, re.IGNORECASE)
                if m2:
                    s_num, e_num = int(m2.group(1)), int(m2.group(2))
                else:
                    m3 = re.search(r"episode[-_]0*(\d{1,2})", h, re.IGNORECASE)
                    if m3:
                        s_num, e_num = 1, int(m3.group(1))

            if s_num is not None and e_num is not None:
                ep_key = (s_num, e_num)
                if ep_key not in seen_eps:
                    seen_eps.add(ep_key)
                    episodes.append({
                        "url": h,
                        "season": s_num,
                        "episode": e_num,
                        "label": f"S{s_num:02d}E{e_num:02d}",
                    })

        # Sort episodes chronologically: S01E01, S01E02...
        episodes.sort(key=lambda x: (x["season"], x["episode"]))
    except Exception as exc:
        log.warning("[CineSubzCrawler] Failed to scrape episodes for %s: %s", tvshow_url, exc)

    return episodes


def is_item_in_catalog(
    item: Dict[str, Any],
    catalog: Dict[str, Any],
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> bool:
    """
    Check if a release is already published on our website catalog.
    Checks:
      1. Exact IMDb ID match (if present)
      2. Normalized Title + Year match
      3. TV Series (Title, Season, Episode) tuple match
      4. Slug partial match
    """
    imdb_id = (item.get("imdb_id") or "").lower().strip()
    if imdb_id and imdb_id in catalog.get("imdb_ids", set()) and (season is None and episode is None):
        return True

    title = item.get("clean_title", "")
    nt = normalize_title(title)
    if not nt:
        return False

    year = item.get("year")

    # Series Episode Check
    if season is not None and episode is not None:
        if (nt, season, episode) in catalog.get("series_episodes", set()):
            return True
        # Check slugs
        ep_part = f"s{season:02d}e{episode:02d}"
        t_slug = nt.replace(" ", "-")
        for sl in catalog.get("slugs", set()):
            if t_slug in sl and ep_part in sl:
                return True
        return False

    # Series Title Check
    if item.get("is_series"):
        if nt in catalog.get("series_titles", set()):
            return True
        if year and (nt, year) in catalog.get("title_years", set()):
            return True

    # Movie Check
    if year:
        if (nt, year) in catalog.get("title_years", set()):
            return True
    else:
        if nt in catalog.get("movie_titles", set()):
            return True

    # Slugs Check
    t_slug = nt.replace(" ", "-")
    for sl in catalog.get("slugs", set()):
        if year and sl == f"{t_slug}-{year}":
            return True
        elif not year and (sl == t_slug or sl.startswith(f"{t_slug}-")):
            return True

    return False


class CineSubzCrawler:
    """
    Autonomous crawler for CineSubz catalog.
    Iterates pages in reverse (oldest to newest), verifies against website catalog,
    and feeds missing titles strictly sequentially into the leech queue.
    """

    def __init__(self):
        self._is_running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._admin_chat_id: int = 0
        self._client: Optional[Client] = None
        self._status_msg: Optional[Message] = None

        self.mode: str = "movies"  # "movies", "tvshows", "all"
        self.current_movie_page: int = 0
        self.max_movie_pages: int = 0
        self.current_tv_page: int = 0
        self.max_tv_pages: int = 0
        self.total_scraped: int = 0
        self.total_skipped: int = 0
        self.total_queued: int = 0
        self.current_item_name: str = "None"
        self.status_detail: str = "Idle"
        self.started_at: float = 0.0

        self.load_state()

    def load_state(self) -> None:
        """Load persistent crawler state from disk, checking local, Google Drive, or website backup."""
        self.last_completed_item: str = "None"
        for p in (STATE_FILE, COLAB_GDRIVE_STATE_FILE, WEBSITE_STATE_FILE):
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.mode = data.get("mode", "all")
                    self.current_movie_page = data.get("current_movie_page", 0)
                    self.max_movie_pages = data.get("max_movie_pages", 0)
                    self.current_tv_page = data.get("current_tv_page", 0)
                    self.max_tv_pages = data.get("max_tv_pages", 0)
                    self.total_scraped = data.get("total_scraped", 0)
                    self.total_skipped = data.get("total_skipped", 0)
                    self.total_queued = data.get("total_queued", 0)
                    self.last_completed_item = data.get("last_completed_item", "None")
                    log.info("[CineSubzCrawler] Loaded state from %s: Movie page %d/%d, TV page %d/%d (Last: %s)",
                             p, self.current_movie_page, self.max_movie_pages, self.current_tv_page, self.max_tv_pages, self.last_completed_item)
                    return
                except Exception as e:
                    log.debug("[CineSubzCrawler] Could not load state from %s: %s", p, e)

    def save_state(self) -> None:
        """Save persistent crawler state to disk and all cloud backups."""
        data = {
            "mode": self.mode,
            "current_movie_page": self.current_movie_page,
            "max_movie_pages": self.max_movie_pages,
            "current_tv_page": self.current_tv_page,
            "max_tv_pages": self.max_tv_pages,
            "total_scraped": self.total_scraped,
            "total_skipped": self.total_skipped,
            "total_queued": self.total_queued,
            "last_completed_item": getattr(self, "last_completed_item", "None"),
            "updated_at": time.time(),
        }
        for p in (STATE_FILE, WEBSITE_STATE_FILE, COLAB_GDRIVE_STATE_FILE):
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass

    def is_running(self) -> bool:
        return self._is_running

    async def start(
        self,
        client: Client,
        admin_chat_id: int,
        mode: str = "movies",
        start_page: Optional[int] = None,
        status_msg: Optional[Message] = None,
    ) -> Tuple[bool, str]:
        """Start the background crawler loop."""
        if self._is_running:
            return False, "⚠️ Crawler එක දැනටමත් ක්‍රියාත්මක වෙමින් පවතී (Already Running)!"

        self._is_running = True
        self._client = client
        self._admin_chat_id = admin_chat_id
        self._status_msg = status_msg
        self.mode = mode.lower()
        self.started_at = time.time()
        self.status_detail = "Initializing..."

        if start_page is not None and start_page > 0:
            if self.mode == "tvshows":
                self.current_tv_page = start_page
            else:
                self.current_movie_page = start_page

        self._worker_task = asyncio.create_task(self._crawler_worker())
        log.info("[CineSubzCrawler] Started crawler in mode '%s'", self.mode)
        return True, f"🚀 <b>CineSubz Auto-Harvester ආරම්භ විය!</b>\n📊 Mode: <b>{self.mode.upper()}</b> (Oldest ➔ Newest)"

    def stop(self) -> bool:
        """Stop the crawler gracefully."""
        if not self._is_running:
            return False
        self._is_running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        self.status_detail = "Stopped by user"
        self.save_state()
        log.info("[CineSubzCrawler] Crawler stopped.")
        return True

    def get_status_text(self) -> str:
        """Format human-readable crawler status."""
        st_icon = "🟢 <b>ක්‍රියාත්මකයි (Running)</b>" if self._is_running else "🔴 <b>නැවතී ඇත (Stopped/Idle)</b>"
        page_info = ""
        if self.mode in ("movies", "all"):
            m_curr = self.current_movie_page or self.max_movie_pages
            page_info += f"• <b>චිත්‍රපට පිටුව:</b> Page {m_curr} / {self.max_movie_pages} (Oldest ➔ 1)\n"
        if self.mode in ("tvshows", "all"):
            tv_curr = self.current_tv_page or self.max_tv_pages
            page_info += f"• <b>TV Series පිටුව:</b> Page {tv_curr} / {self.max_tv_pages} (Oldest ➔ 1)\n"

        return (
            f"🕷 <b>CineSubz Auto-Harvester / Crawler</b>\n\n"
            f"⚡ <b>තත්ත්වය:</b> {st_icon}\n"
            f"📊 <b>ක්‍රමය (Mode):</b> {self.mode.upper()}\n"
            f"{page_info}"
            f"🎬 <b>වත්මන් ගොනුව:</b> {self.current_item_name}\n"
            f"📝 <b>විස්තරය:</b> <i>{self.status_detail}</i>\n\n"
            f"📌 <b>අවසන් වූ ගොනුව (Last Finished):</b> <code>{getattr(self, 'last_completed_item', 'None')}</code>\n\n"
            f"📈 <b>සංඛ්‍යාලේඛන (Stats):</b>\n"
            f"  • Scraped: <b>{self.total_scraped}</b>\n"
            f"  • Skipped (Already on Site): <b>{self.total_skipped}</b>\n"
            f"  • Enqueued for Leech: <b>{self.total_queued}</b>\n\n"
            f"💡 <i>නැවැත්වීමට: <code>/crawl stop</code> | නැවත පටන් ගැනීමට: <code>/crawl resume</code></i>"
        )

    async def _update_status_msg(self) -> None:
        """Edit status message if available."""
        if self._status_msg:
            try:
                await self._status_msg.edit_text(self.get_status_text(), parse_mode=ParseMode.HTML)
            except Exception:
                pass

    async def _crawler_worker(self) -> None:
        """Main worker loop iterating through CineSubz archive."""
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True, verify=False) as http_client:
            try:
                # 1. Detect max pages if not known
                if self.mode in ("movies", "all"):
                    if self.max_movie_pages <= 0:
                        self.status_detail = "Detecting movie pages..."
                        await self._update_status_msg()
                        self.max_movie_pages = await get_cinesubz_max_pages(http_client, "movies")
                    if self.current_movie_page <= 0:
                        self.current_movie_page = self.max_movie_pages

                if self.mode in ("tvshows", "all"):
                    if self.max_tv_pages <= 0:
                        self.status_detail = "Detecting TV show pages..."
                        await self._update_status_msg()
                        self.max_tv_pages = await get_cinesubz_max_pages(http_client, "tvshows")
                    if self.current_tv_page <= 0:
                        self.current_tv_page = self.max_tv_pages

                self.save_state()

                # 2. Crawl Movies (Oldest to Newest)
                if self.mode in ("movies", "all"):
                    log.info("[CineSubzCrawler] Starting movie crawl from page %d down to 1...", self.current_movie_page)
                    while self._is_running and self.current_movie_page >= 1:
                        p = self.current_movie_page
                        self.status_detail = f"Scraping movies page {p}/{self.max_movie_pages}..."
                        await self._update_status_msg()

                        items = await scrape_cinesubz_page(http_client, p, "movies")
                        # Process items on this page (oldest first within page is bottom-up, or in order)
                        for item in reversed(items):
                            if not self._is_running:
                                break

                            self.total_scraped += 1
                            item_label = f"{item['clean_title']} ({item['year']})" if item['year'] else item['clean_title']
                            self.current_item_name = item_label

                            # Check website catalog
                            fresh_catalog = load_website_catalog()
                            if is_item_in_catalog(item, fresh_catalog):
                                self.total_skipped += 1
                                log.info("[CineSubzCrawler] [SKIP - Already on site] %s", item_label)
                                self.status_detail = f"Skipping {item_label} (Already on site)"
                                await asyncio.sleep(0.5)
                                continue

                            # Item is MISSING from our site!
                            self.total_queued += 1
                            self.status_detail = f"Enqueuing {item_label} for Auto-Leech..."
                            await self._update_status_msg()

                            # Prepare query: direct URL + title + year with --auto for full auto publish
                            item_url = item.get("url") or ""
                            if item_url:
                                q_text = f"{item_url} {item['clean_title']} {item['year']} --auto" if item["year"] else f"{item_url} {item['clean_title']} --auto"
                            else:
                                q_text = f"{item['clean_title']} {item['year']} --auto" if item["year"] else f"{item['clean_title']} --auto"
                            log.info("[CineSubzCrawler] 🎯 Enqueuing missing movie: %s (Page %d)", q_text, p)

                            # Send dedicated leech notification in admin chat
                            leech_status_msg = None
                            if self._client and self._admin_chat_id:
                                try:
                                    leech_status_msg = await self._client.send_message(
                                        chat_id=self._admin_chat_id,
                                        text=f"🕷 <b>CineSubz Harvester [Page {p}/{self.max_movie_pages}]:</b>\n"
                                             f"🎬 <b>{item_label}</b> පෝලිමට එක්කරමින් පවතී...",
                                        parse_mode=ParseMode.HTML,
                                    )
                                except Exception as s_err:
                                    log.debug("[CineSubzCrawler] Status msg send note: %s", s_err)

                            # Add to queue
                            if self._client and leech_status_msg:
                                await queue_service.add_to_queue(
                                    client=self._client,
                                    status_msg=leech_status_msg,
                                    user_id=self._admin_chat_id,
                                    query_text=q_text,
                                    title_hint=item_label,
                                    auto_publish=True,
                                    imdb_id=item.get("imdb_id", ""),
                                )

                            # Wait until queue has completely finished processing this film before enqueuing next
                            # Ensures strictly one movie at a time (zero VPS memory or Seedr overflow)
                            log.info("[CineSubzCrawler] Waiting for '%s' to complete before next item...", item_label)
                            wait_ticks = 0
                            while self._is_running and not queue_service.is_idle():
                                self.status_detail = f"Processing '{item_label}' (Leech + Uploading)..."
                                wait_ticks += 1
                                if wait_ticks % 3 == 0:
                                    await self._update_status_msg()
                                await asyncio.sleep(5.0)

                            self.last_completed_item = item_label
                            self.save_state()
                            await self._update_status_msg()
                            await asyncio.sleep(2.0)

                        self.current_movie_page -= 1
                        self.save_state()
                        await asyncio.sleep(2.0)

                # 3. Crawl TV Series (Oldest to Newest)
                if self.mode in ("tvshows", "all") and self._is_running:
                    log.info("[CineSubzCrawler] Starting TV Series crawl from page %d down to 1...", self.current_tv_page)
                    while self._is_running and self.current_tv_page >= 1:
                        p = self.current_tv_page
                        self.status_detail = f"Scraping TV shows page {p}/{self.max_tv_pages}..."
                        await self._update_status_msg()

                        tv_shows = await scrape_cinesubz_page(http_client, p, "tvshows")
                        for show in reversed(tv_shows):
                            if not self._is_running:
                                break

                            show_title = show["clean_title"]
                            self.current_item_name = show_title
                            self.status_detail = f"Scraping episodes for {show_title}..."

                            # Fetch all episodes from the CineSubz TV show page
                            episodes = await scrape_cinesubz_tvshow_episodes(http_client, show["url"])
                            log.info("[CineSubzCrawler] TV Show '%s' has %d episode(s) on CineSubz", show_title, len(episodes))

                            for ep in episodes:
                                if not self._is_running:
                                    break

                                s_num, e_num = ep["season"], ep["episode"]
                                ep_tag = f"{show_title} {ep['label']}"
                                self.total_scraped += 1

                                fresh_catalog = load_website_catalog()
                                if is_item_in_catalog(show, fresh_catalog, season=s_num, episode=e_num):
                                    self.total_skipped += 1
                                    log.info("[CineSubzCrawler] [SKIP - Already on site] %s", ep_tag)
                                    continue

                                # Episode is MISSING from our site!
                                self.total_queued += 1
                                self.status_detail = f"Enqueuing {ep_tag}..."
                                await self._update_status_msg()

                                ep_url = ep.get("url") or ""
                                if ep_url:
                                    ep_query = f"{ep_url} {show_title} S{s_num:02d}E{e_num:02d} --auto"
                                else:
                                    ep_query = f"{show_title} S{s_num:02d}E{e_num:02d} --auto"
                                log.info("[CineSubzCrawler] 🎯 Enqueuing missing episode: %s", ep_query)

                                leech_status_msg = None
                                if self._client and self._admin_chat_id:
                                    try:
                                        leech_status_msg = await self._client.send_message(
                                            chat_id=self._admin_chat_id,
                                            text=f"🕷 <b>CineSubz Harvester [TV Series]:</b>\n"
                                                 f"📺 <b>{ep_tag}</b> පෝලිමට එක්කරමින් පවතී...",
                                            parse_mode=ParseMode.HTML,
                                        )
                                    except Exception:
                                        pass

                                if self._client and leech_status_msg:
                                    await queue_service.add_to_queue(
                                        client=self._client,
                                        status_msg=leech_status_msg,
                                        user_id=self._admin_chat_id,
                                        query_text=ep_query,
                                        title_hint=ep_tag,
                                        auto_publish=True,
                                    )

                                # Wait until episode completes before queuing the next episode
                                wait_ticks_tv = 0
                                while self._is_running and not queue_service.is_idle():
                                    self.status_detail = f"Processing '{ep_tag}' (Leech + Uploading)..."
                                    wait_ticks_tv += 1
                                    if wait_ticks_tv % 3 == 0:
                                        await self._update_status_msg()
                                    await asyncio.sleep(5.0)

                                self.last_completed_item = ep_tag
                                self.save_state()
                                await self._update_status_msg()
                                await asyncio.sleep(2.0)

                        self.current_tv_page -= 1
                        self.save_state()
                        await asyncio.sleep(2.0)

                self._is_running = False
                self.status_detail = "Completed all pages!"
                self.save_state()
                await self._update_status_msg()
                if self._client and self._admin_chat_id:
                    try:
                        await self._client.send_message(
                            chat_id=self._admin_chat_id,
                            text="🎉 <b>CineSubz Auto-Harvester සාර්ථකව අවසන් විය!</b>\nසියලුම පැරණි චිත්‍රපට සහ කතා මාලා පරික්ෂා කර වෙබ් අඩවියට එක් කරන ලදී.",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass

            except asyncio.CancelledError:
                log.info("[CineSubzCrawler] Worker cancelled.")
                self._is_running = False
                self.status_detail = "Cancelled"
                self.save_state()
            except Exception as w_err:
                log.exception("[CineSubzCrawler] Worker error: %s", w_err)
                self._is_running = False
                self.status_detail = f"Error: {w_err}"
                self.save_state()


async def resolve_series_episodes(
    series_name: str,
    year: Optional[int] = None,
    target_season: Optional[int] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Resolve all seasons and episodes for a TV series.
    Tries CineSubz TV show page directly for exact Sinhala-subbed episodes,
    and queries TMDb across all seasons to discover complete episode structure.
    """
    clean_series = re.sub(
        r"\b(?:s\d{1,2}|season\s*\d{1,2}|episodes?|ep\s*\d{1,2}|complete)\b.*$",
        "",
        series_name,
        flags=re.IGNORECASE,
    ).strip()
    if not clean_series:
        clean_series = series_name.strip()

    canonical_title = clean_series
    release_year = year
    imdb_id = ""
    tmdb_seasons: List[Dict[str, Any]] = []

    # 1. Fetch metadata from TMDb
    try:
        from services import tmdb_service
        meta = await tmdb_service.fetch_metadata(
            clean_series,
            year=year,
            is_series=True,
            season=target_season or 1,
            episode=1,
        )
        if meta and meta.get("type") == "series":
            canonical_title = meta.get("title") or clean_series
            release_year = meta.get("year") or year
            imdb_id = meta.get("imdb_id") or ""
            tmdb_seasons = meta.get("seasons", [])
    except Exception as exc:
        log.warning("[CineSubzCrawler] TMDb series lookup error for %s: %s", clean_series, exc)

    # 2. Try scraping CineSubz TV show page for direct episode list
    cinesubz_eps: List[Dict[str, Any]] = []
    should_close_client = False
    if client is None:
        client = httpx.AsyncClient(headers=HEADERS, timeout=12.0, follow_redirects=True, verify=False)
        should_close_client = True

    try:
        slug = re.sub(r"[^a-z0-9]+", "-", canonical_title.lower()).strip("-")
        test_slugs = [slug]
        if release_year:
            test_slugs.append(f"{slug}-{release_year}")
            test_slugs.append(f"{slug}-{release_year}-tv-series")
            test_slugs.append(f"{slug}-{release_year}-sinhala-sub")
        test_slugs.append(f"{slug}-tv-series")
        test_slugs.append(f"{slug}-sinhala-sub")
        test_slugs.append(f"{slug}-season-1")
        raw_slug = re.sub(r"[^a-z0-9]+", "-", clean_series.lower()).strip("-")
        if raw_slug not in test_slugs:
            test_slugs.append(raw_slug)
            if release_year:
                test_slugs.append(f"{raw_slug}-{release_year}")
                test_slugs.append(f"{raw_slug}-{release_year}-tv-series")
            test_slugs.append(f"{raw_slug}-tv-series")

        for base in CINESUBZ_BASE_URLS:
            for s_cand in test_slugs:
                cand_url = f"{base}/tvshows/{s_cand}/"
                found = await scrape_cinesubz_tvshow_episodes(client, cand_url)
                if found:
                    cinesubz_eps = found
                    log.info("[CineSubzCrawler] Found %d episode(s) on CineSubz at %s", len(found), cand_url)
                    break
            if cinesubz_eps:
                break

        # Fallback: Search CineSubz catalog directly if direct slug probe didn't resolve episodes
        if not cinesubz_eps:
            import urllib.parse
            for base in CINESUBZ_BASE_URLS:
                search_queries = [canonical_title]
                if release_year:
                    search_queries.insert(0, f"{canonical_title} {release_year}")
                for sq in search_queries:
                    try:
                        s_url = f"{base}/?s={urllib.parse.quote(sq)}"
                        resp = await client.get(s_url, timeout=12.0)
                        if resp.status_code == 200:
                            s_soup = BeautifulSoup(resp.text, "html.parser")
                            tv_links: List[str] = []
                            for a in s_soup.find_all("a", href=True):
                                href = a["href"].strip()
                                if "/tvshows/" in href and href not in tv_links:
                                    tv_links.append(href)
                            for tv_link in tv_links:
                                found = await scrape_cinesubz_tvshow_episodes(client, tv_link)
                                if found:
                                    cinesubz_eps = found
                                    log.info("[CineSubzCrawler] Found %d episode(s) on CineSubz via search at %s", len(found), tv_link)
                                    break
                            if cinesubz_eps:
                                break
                    except Exception as s_exc:
                        log.debug("[CineSubzCrawler] Search probe error for %s: %s", sq, s_exc)
                if cinesubz_eps:
                    break
    except Exception as cs_err:
        log.debug("[CineSubzCrawler] CineSubz direct series probe note: %s", cs_err)
    finally:
        if should_close_client:
            await client.aclose()

    # 3. Assemble complete episode list
    final_episodes: List[Dict[str, Any]] = []
    seen_ep_keys: Set[Tuple[int, int]] = set()

    # If CineSubz returned episodes
    for ep in cinesubz_eps:
        s_num = ep["season"]
        e_num = ep["episode"]
        if target_season is not None and s_num != target_season:
            continue
        key = (s_num, e_num)
        if key not in seen_ep_keys:
            seen_ep_keys.add(key)
            final_episodes.append({
                "season": s_num,
                "episode": e_num,
                "label": f"S{s_num:02d}E{e_num:02d}",
                "url": ep.get("url", ""),
            })

    # Supplement/Fallback using TMDb seasons
    if tmdb_seasons:
        for s_obj in tmdb_seasons:
            s_num = s_obj.get("season_number", 0)
            if s_num <= 0:
                continue
            if target_season is not None and s_num != target_season:
                continue
            count = s_obj.get("episode_count", 0)
            if count <= 0:
                count = 8
            count = min(count, 40)
            for ep_i in range(1, count + 1):
                key = (s_num, ep_i)
                if key not in seen_ep_keys:
                    seen_ep_keys.add(key)
                    final_episodes.append({
                        "season": s_num,
                        "episode": ep_i,
                        "label": f"S{s_num:02d}E{ep_i:02d}",
                        "url": "",
                    })

    # If neither returned episodes, default to requested season (or 1) with 8 episodes
    if not final_episodes:
        s_num = target_season or 1
        for ep_i in range(1, 9):
            final_episodes.append({
                "season": s_num,
                "episode": ep_i,
                "label": f"S{s_num:02d}E{ep_i:02d}",
                "url": "",
            })

    final_episodes.sort(key=lambda x: (x["season"], x["episode"]))
    seasons_present = sorted(list(set(x["season"] for x in final_episodes)))

    return {
        "title": canonical_title,
        "year": release_year,
        "imdb_id": imdb_id,
        "episodes": final_episodes,
        "seasons_list": seasons_present,
        "total_episodes": len(final_episodes),
    }


# Global singleton instance
cinesubz_crawler = CineSubzCrawler()
