"""
github_service.py — Read and write movies.json (and arbitrary files) on GitHub.

All site content lives in a GitHub repo; pushing updates there triggers the
GitHub Actions CI/CD pipeline which rebuilds and redeploys the static site.

Provides:
  get_movies_json()      → (content_dict, sha)
  add_movie(movie_dict)  → bool
  upload_file(…)         → raw URL string
"""

import base64
import json
import logging
from datetime import datetime, timezone

import httpx

from config import GITHUB_TOKEN, GITHUB_REPO

log = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_MOVIES_PATH = "website/data/movies.json"  # Path inside the repo
_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ─────────────────────────────────────────────────────────────────────────────
import os

_LOCAL_MOVIES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "website", "data", "movies.json")
)

async def get_movies_json() -> tuple[dict, str]:
    """
    Fetch the current movies.json from GitHub (or local file fallback).
    """
    if not GITHUB_TOKEN or GITHUB_REPO == "username/repo":
        if os.path.exists(_LOCAL_MOVIES_PATH):
            with open(_LOCAL_MOVIES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data, "local"
        return {"site": {}, "movies": []}, "local"

    url = f"{_API_BASE}/repos/{GITHUB_REPO}/contents/{_MOVIES_PATH}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()

    sha: str = data["sha"]
    raw_json = base64.b64decode(data["content"]).decode("utf-8")

    try:
        content_dict = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"movies.json is not valid JSON: {exc}") from exc

    log.info("Fetched movies.json (sha=%s, %d movies)", sha, len(content_dict.get("movies", [])))
    return content_dict, sha


# ─────────────────────────────────────────────────────────────────────────────
async def add_movie(movie: dict) -> bool:
    """
    Append *movie* to the movies list in movies.json and push to GitHub
    (or save locally if GITHUB_TOKEN is not configured).
    """
    try:
        content_dict, sha = await get_movies_json()
    except Exception as exc:
        log.error("Could not fetch movies.json before adding movie: %s", exc)
        return False

    # ── Append and update the timestamp ──────────────────────────────────────
    movies: list = content_dict.setdefault("movies", [])
    movies.append(movie)
    content_dict["last_updated"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    updated_json = json.dumps(content_dict, ensure_ascii=False, indent=2)

    if sha == "local" or not GITHUB_TOKEN:
        try:
            os.makedirs(os.path.dirname(_LOCAL_MOVIES_PATH), exist_ok=True)
            with open(_LOCAL_MOVIES_PATH, "w", encoding="utf-8") as f:
                f.write(updated_json)
            js_path = os.path.join(os.path.dirname(_LOCAL_MOVIES_PATH), "movies_data.js")
            with open(js_path, "w", encoding="utf-8") as f:
                f.write("window.FILMSUB_DATA = " + updated_json + ";\n")
            log.info("movies.json & movies_data.js updated LOCALLY. Movie '%s' added.", movie.get("title", "?"))
            
            # Automatically push update to Cloudflare Pages
            try:
                import subprocess
                website_dir = os.path.dirname(os.path.dirname(_LOCAL_MOVIES_PATH))
                subprocess.Popen(
                    ["npx", "wrangler", "pages", "deploy", website_dir, "--project-name", "filmsub", "--commit-dirty=true"],
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                log.info("Cloudflare Pages auto-deploy triggered for filmsub")
            except Exception as cf_err:
                log.warning("Cloudflare Pages auto-deploy skipped: %s", cf_err)

            return True
        except Exception as exc:
            log.error("Failed to write movies.json locally: %s", exc)
            return False

    encoded = base64.b64encode(updated_json.encode("utf-8")).decode("ascii")

    payload = {
        "message": f"Add movie: {movie.get('title', 'Unknown')} ({movie.get('year', '')})",
        "content": encoded,
        "sha": sha,  # Required — identifies the blob being replaced
    }

    url = f"{_API_BASE}/repos/{GITHUB_REPO}/contents/{_MOVIES_PATH}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(url, headers=_HEADERS, json=payload)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.error(
            "GitHub PUT movies.json failed (HTTP %s): %s",
            exc.response.status_code,
            exc.response.text,
        )
        return False
    except Exception as exc:
        log.error("Unexpected error updating movies.json: %s", exc)
        return False

    log.info(
        "movies.json updated on GitHub. Movie '%s' added.", movie.get("title", "?")
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
async def upload_file(
    content: bytes,
    path: str,
    message: str = "Bot upload",
) -> str:
    """
    Upload arbitrary binary *content* to *path* inside the GitHub repo.

    If the file already exists it will be updated; otherwise it is created.

    Args:
        content: Raw bytes to upload.
        path:    Repo-relative path, e.g. 'assets/posters/avatar3.jpg'.
        message: Git commit message.

    Returns:
        The public raw.githubusercontent.com URL to the file.
    """
    api_url = f"{_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    encoded = base64.b64encode(content).decode("ascii")

    # ── Check for existing file (need SHA to update) ─────────────────────────
    sha: str = ""
    async with httpx.AsyncClient(timeout=20) as client:
        existing = await client.get(api_url, headers=_HEADERS)
        if existing.status_code == 200:
            sha = existing.json().get("sha", "")

        payload: dict = {"message": message, "content": encoded}
        if sha:
            payload["sha"] = sha

        resp = await client.put(api_url, headers=_HEADERS, json=payload)
        resp.raise_for_status()

    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{path}"
    log.info("Uploaded file to GitHub: %s", raw_url)
    return raw_url
