"""
method3_ddl.py - Method 3: DDL Site Scraper (Pahe / PSArips / PixelDrain).

Searches DDL aggregator sites for lightweight x265 movie releases.
Parses HTML to extract direct download links (PixelDrain, Mega, GDrive).

All log strings are in English to avoid Windows charmap errors.
"""

import logging
import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# User-Agent that mimics a real browser
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Supported download hosts and their direct-link patterns
DDL_HOST_PATTERNS = {
    "pixeldrain": r"https://pixeldrain\.com/[^\s\"'<>]+",
    "mega": r"https://mega\.nz/[^\s\"'<>]+",
    "gdrive": r"https://drive\.google\.com/[^\s\"'<>]+",
    "gofile": r"https://gofile\.io/[^\s\"'<>]+",
    "mediafire": r"https://www\.mediafire\.com/[^\s\"'<>]+",
}

PAHE_MIRRORS = ["https://pahe.ink/", "https://pahe.li/", "https://pahe.plus/"]
PSARIPS_MIRRORS = ["https://psarips.net/", "https://psa.wf/"]


def resolve_direct_url(url: str) -> Optional[str]:
    """
    Resolve third-party file host URLs to direct, raw downloadable HTTP endpoints.
    Specifically converts PixelDrain view URLs to high-speed direct API download streams.
    """
    pd_match = re.search(r"pixeldrain\.com/(?:u|api/file)/([a-zA-Z0-9_-]+)", url)
    if pd_match:
        file_id = pd_match.group(1)
        return f"https://pixeldrain.com/api/file/{file_id}"
    return None


async def _search_pahe(title: str, year: Optional[int], client: httpx.AsyncClient) -> Optional[dict]:
    """Search Pahe mirrors for an x265 release and extract download links."""
    query = f"{title} {year}" if year else title
    for base_url in PAHE_MIRRORS:
        try:
            resp = await client.get(
                base_url,
                params={"s": query},
                headers=HEADERS,
                timeout=2.5,
            )
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("article", limit=5) or soup.find_all("div", class_=re.compile(r"post|result|item"), limit=5)
            if not articles:
                continue

            for article in articles:
                link_tag = article.find("a", href=True)
                if not link_tag:
                    continue
                href = link_tag["href"]
                if not href.startswith("http"):
                    href = base_url.rstrip("/") + "/" + href.lstrip("/")

                page_resp = await client.get(href, headers=HEADERS, timeout=2.5)
                if page_resp.status_code != 200:
                    continue

                page_text = page_resp.text
                downloads = []
                for host_name, pattern in DDL_HOST_PATTERNS.items():
                    matches = re.findall(pattern, page_text, re.IGNORECASE)
                    for m in matches:
                        direct = resolve_direct_url(m)
                        downloads.append({
                            "host": host_name,
                            "url": m,
                            "direct_url": direct or m,
                        })

                if not downloads:
                    continue

                title_text = article.get_text()
                quality = "1080p"
                if "720p" in title_text:
                    quality = "720p"
                elif "480p" in title_text:
                    quality = "480p"

                size_match = re.search(r"(\d+\.?\d*\s?(?:GB|MB))", title_text, re.IGNORECASE)
                size = size_match.group(1) if size_match else "Unknown"

                log.info("[M3-DDL] Pahe (%s) found %d download links for: %s", base_url, len(downloads), query)
                return {
                    "method": "ddl_pahe",
                    "quality": quality,
                    "size": size,
                    "server_label": "DDL (Pahe)",
                    "downloads": downloads,
                    "stream_url": None,
                }
        except Exception as exc:
            log.warning("[M3-DDL] Pahe mirror error on %s: %s", base_url, exc)
            continue
    return None


async def _search_psarips(title: str, year: Optional[int], client: httpx.AsyncClient) -> Optional[dict]:
    """Search PSArips mirrors for a release."""
    query = f"{title} {year}" if year else title
    for base_url in PSARIPS_MIRRORS:
        try:
            resp = await client.get(
                base_url,
                params={"s": query},
                headers=HEADERS,
                timeout=2.5,
            )
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("article", limit=3)
            if not articles:
                continue

            for article in articles:
                link_tag = article.find("a", href=True)
                if not link_tag:
                    continue
                href = link_tag["href"]

                page_resp = await client.get(href, headers=HEADERS, timeout=2.5)
                if page_resp.status_code != 200:
                    continue

                page_text = page_resp.text
                downloads = []
                for host_name, pattern in DDL_HOST_PATTERNS.items():
                    matches = re.findall(pattern, page_text, re.IGNORECASE)
                    for m in matches:
                        direct = resolve_direct_url(m)
                        downloads.append({
                            "host": host_name,
                            "url": m,
                            "direct_url": direct or m,
                        })

                if not downloads:
                    continue

                title_text = article.get_text()
                quality = "1080p"
                if "720p" in title_text:
                    quality = "720p"
                elif "480p" in title_text:
                    quality = "480p"

                size_match = re.search(r"(\d+\.?\d*\s?(?:GB|MB))", title_text, re.IGNORECASE)
                size = size_match.group(1) if size_match else "Unknown"

                log.info("[M3-DDL] PSArips (%s) found %d links for: %s", base_url, len(downloads), query)
                return {
                    "method": "ddl_psarips",
                    "quality": quality,
                    "size": size,
                    "server_label": "DDL (PSArips)",
                    "downloads": downloads,
                    "stream_url": None,
                }
        except Exception as exc:
            log.warning("[M3-DDL] PSArips mirror error on %s: %s", base_url, exc)
            continue
    return None


async def search(title: str, year: Optional[int] = None, imdb_id: Optional[str] = None, **_) -> Optional[dict]:
    """
    Method 3: DDL scraper (Pahe -> PSArips cascade).

    Returns result dict with downloads list, or None if not found.
    """
    log.info("[M3-DDL] Searching DDL sites for: %s (%s)", title, year or "?")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await _search_pahe(title, year, client)
        if result:
            return result
        result = await _search_psarips(title, year, client)
        if result:
            return result
    log.info("[M3-DDL] No DDL results found for: %s", title)
    return None
