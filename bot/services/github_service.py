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
import os
from datetime import datetime, timezone

import httpx

import config

log = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_MOVIES_PATH = "website/data/movies.json"  # Path inside the repo
_MOVIES_DATA_JS_PATH = "website/data/movies_data.js"


GITHUB_TOKEN = getattr(config, "GITHUB_TOKEN", "")
GITHUB_REPO = getattr(config, "GITHUB_REPO", "username/repo")


def _get_active_token() -> str:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    return os.environ.get("GITHUB_TOKEN") or getattr(config, "GITHUB_TOKEN", "") or GITHUB_TOKEN or ""


def _get_active_repo() -> str:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    return os.environ.get("GITHUB_REPO") or getattr(config, "GITHUB_REPO", "") or GITHUB_REPO or ""


def _get_headers() -> dict:
    token = _get_active_token()
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ─────────────────────────────────────────────────────────────────────────────
_LOCAL_MOVIES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "website", "data", "movies.json")
)

async def get_movies_json() -> tuple[dict, str]:
    """
    Fetch the current movies.json from GitHub (or local file fallback).
    Supports files > 1MB where GitHub Contents API omits inline base64 content
    and supplies download_url / Git Data Blobs.
    """
    token = _get_active_token()
    repo = _get_active_repo()
    if not token or repo in ("", "username/repo"):
        if os.path.exists(_LOCAL_MOVIES_PATH):
            with open(_LOCAL_MOVIES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data, "local"
        return {"site": {}, "movies": []}, "local"

    url = f"{_API_BASE}/repos/{repo}/contents/{_MOVIES_PATH}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_get_headers())
        resp.raise_for_status()
        data = resp.json()

    sha: str = data.get("sha", "")
    raw_json = ""

    # Case 1: GitHub Contents API returned inline base64 content (files <= 1MB)
    if data.get("content"):
        try:
            raw_json = base64.b64decode(data["content"]).decode("utf-8")
        except Exception:
            raw_json = ""

    # Case 2: File > 1MB, GitHub Contents API returned download_url
    if not raw_json and data.get("download_url"):
        try:
            async with httpx.AsyncClient(timeout=30) as dl_client:
                dl_resp = await dl_client.get(data["download_url"], headers=_get_headers())
                if dl_resp.status_code == 200:
                    raw_json = dl_resp.text
        except Exception as dl_err:
            log.warning("[GitHubService] Failed to fetch movies.json via download_url: %s", dl_err)

    # Case 3: Fallback to Git Blobs API (supports up to 100MB)
    if not raw_json and sha:
        try:
            blob_url = f"{_API_BASE}/repos/{repo}/git/blobs/{sha}"
            async with httpx.AsyncClient(timeout=30) as blob_client:
                blob_resp = await blob_client.get(blob_url, headers=_get_headers())
                if blob_resp.status_code == 200:
                    raw_json = base64.b64decode(blob_resp.json().get("content", "")).decode("utf-8")
        except Exception as b_err:
            log.warning("[GitHubService] Failed to fetch movies.json via Git Blobs API: %s", b_err)

    # Case 4: Fallback to local movies.json file if network/API parsing had issues
    if not raw_json and os.path.exists(_LOCAL_MOVIES_PATH):
        try:
            with open(_LOCAL_MOVIES_PATH, "r", encoding="utf-8") as f:
                content_dict = json.load(f)
            log.info("Fetched movies.json from LOCAL fallback (%d movies)", len(content_dict.get("movies", [])))
            return content_dict, sha or "local"
        except Exception as lf_err:
            log.warning("[GitHubService] Local fallback read failed: %s", lf_err)

    if not raw_json:
        raise RuntimeError("movies.json content is empty from GitHub and local fallback")

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
    Retries up to 3 times on SHA conflict (HTTP 409) with exponential backoff.
    Updates both website/data/movies.json and website/data/movies_data.js.
    """
    target_id = movie.get("id") or movie.get("slug")
    token = _get_active_token()
    repo = _get_active_repo()
    updated_json = ""

    for attempt in range(1, 4):
        try:
            content_dict, sha = await get_movies_json()
        except Exception as exc:
            log.error("Could not fetch movies.json before adding movie: %s", exc)
            return False

        # ── Ensure added_at and added_date are populated for website homepage sorting ─
        if not movie.get("added_at"):
            movie["added_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not movie.get("added_date"):
            movie["added_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # ── Append or update in place and update the timestamp ───────────────────
        movies: list = content_dict.setdefault("movies", [])
        existing_idx = None
        if target_id:
            for idx, m in enumerate(movies):
                if m.get("id") == target_id or m.get("slug") == target_id:
                    existing_idx = idx
                    break
        if existing_idx is not None:
            movies[existing_idx] = movie
            log.info("Updated existing movie '%s' (index %d) in movies.json", target_id, existing_idx)
        else:
            movies.append(movie)
            log.info("Appended new movie '%s' to movies.json", target_id)
        content_dict["last_updated"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        msg = f"Add movie: {movie.get('title', 'Unknown')} ({movie.get('year', '')})"
        ok = await _commit_movies_json(content_dict, sha, msg)
        if ok:
            return True
        elif attempt < 3:
            import asyncio
            await asyncio.sleep(1.0 * attempt)
        else:
            return False
    return True


async def _commit_movies_json(content_dict: dict, sha: str, commit_message: str) -> bool:
    """Internal: commit movies.json and movies_data.js to GitHub."""
    token = _get_active_token()
    repo = _get_active_repo()
    updated_json = json.dumps(content_dict, ensure_ascii=False, indent=2)

    # 1. Always write locally so local files are up-to-date
    try:
        os.makedirs(os.path.dirname(_LOCAL_MOVIES_PATH), exist_ok=True)
        with open(_LOCAL_MOVIES_PATH, "w", encoding="utf-8") as f:
            f.write(updated_json)
        js_path = os.path.join(os.path.dirname(_LOCAL_MOVIES_PATH), "movies_data.js")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write("window.FILMSUB_DATA = window.MOVIES_DATA = window.__MOVIES_DATA__ = " + updated_json + ";\n")
        log.info("movies.json & movies_data.js updated LOCALLY.")
    except Exception as exc:
        log.warning("Could not write movies locally: %s", exc)

    if not token or repo in ("", "username/repo"):
        return True

    # If sha is local, fetch current remote sha
    if sha == "local":
        try:
            url_meta = f"{_API_BASE}/repos/{repo}/contents/{_MOVIES_PATH}"
            async with httpx.AsyncClient(timeout=20) as client:
                r_meta = await client.get(url_meta, headers=_get_headers())
                if r_meta.status_code == 200:
                    sha = r_meta.json().get("sha", "")
        except Exception:
            pass

    encoded = base64.b64encode(updated_json.encode("utf-8")).decode("ascii")

    for attempt in range(1, 4):
        payload = {
            "message": commit_message,
            "content": encoded,
            "branch": "main",
        }
        if sha and sha != "local":
            payload["sha"] = sha

        url = f"{_API_BASE}/repos/{repo}/contents/{_MOVIES_PATH}"
        headers = _get_headers()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.put(url, headers=headers, json=payload)
                resp.raise_for_status()
            log.info("movies.json updated on GitHub repo %s.", repo)
            break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409 and attempt < 3:
                log.warning("[GitHubService] SHA conflict (HTTP 409) updating movies.json (attempt %d/3). Retrying...", attempt)
                import asyncio
                await asyncio.sleep(1.0 * attempt)
                try:
                    url_meta = f"{_API_BASE}/repos/{repo}/contents/{_MOVIES_PATH}"
                    async with httpx.AsyncClient(timeout=20) as client2:
                        r_meta = await client2.get(url_meta, headers=_get_headers())
                        if r_meta.status_code == 200:
                            sha = r_meta.json().get("sha", "")
                except Exception:
                    pass
                continue
            log.error("GitHub PUT movies.json failed (HTTP %s): %s", exc.response.status_code, exc.response.text)
            return False
        except Exception as exc:
            log.error("Unexpected error updating movies.json: %s", exc)
            return False

    # Also sync movies_data.js
    try:
        js_content = f"window.FILMSUB_DATA = window.MOVIES_DATA = window.__MOVIES_DATA__ = {updated_json};\n"
        await upload_file(
            content=js_content.encode("utf-8"),
            path=_MOVIES_DATA_JS_PATH,
            message=commit_message,
        )
    except Exception as js_err:
        log.warning("Could not sync movies_data.js to GitHub: %s", js_err)

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
    Retries up to 3 times on SHA conflict (HTTP 409).

    Args:
        content: Raw bytes to upload.
        path:    Repo-relative path, e.g. 'assets/posters/avatar3.jpg'.
        message: Git commit message.

    Returns:
        The public raw.githubusercontent.com URL to the file.
    """
    token = _get_active_token()
    repo = _get_active_repo()
    if not token or repo in ("", "username/repo"):
        return ""
    headers = _get_headers()
    api_url = f"{_API_BASE}/repos/{repo}/contents/{path}"
    encoded = base64.b64encode(content).decode("ascii")

    for attempt in range(1, 4):
        sha: str = ""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                existing = await client.get(api_url, headers=headers)
                if existing.status_code == 200:
                    sha = existing.json().get("sha", "")

                payload: dict = {"message": message, "content": encoded, "branch": "main"}
                if sha:
                    payload["sha"] = sha

                resp = await client.put(api_url, headers=headers, json=payload)
                resp.raise_for_status()

            raw_url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
            log.info("Uploaded file to GitHub: %s", raw_url)
            return raw_url
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409 and attempt < 3:
                log.warning("[GitHubService] SHA conflict (HTTP 409) uploading %s (attempt %d/3). Retrying...", path, attempt)
                import asyncio
                await asyncio.sleep(1.0 * attempt)
                continue
            raise
    return ""


_STREAM_ENDPOINT_PATH = "website/data/stream_endpoint.json"
_STREAM_ENDPOINT_JS_PATH = "website/data/stream_endpoint.js"
_LOCAL_STREAM_ENDPOINT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "website", "data", "stream_endpoint.json")
)
_LOCAL_STREAM_ENDPOINT_JS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "website", "data", "stream_endpoint.js")
)


async def publish_live_stream_endpoint(stream_base_url: str) -> bool:
    """
    Publish the active streaming server base URL (e.g. Cloudflare tunnel or Render URL)
    to website/data/stream_endpoint.json and stream_endpoint.js on GitHub and locally.
    Enables zero-configuration live Telegram video streaming on the website.
    """
    clean_url = stream_base_url.strip().rstrip("/")
    if not clean_url or "localhost" in clean_url or "127.0.0.1" in clean_url:
        return False

    payload = {
        "stream_base_url": clean_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "online",
    }
    json_bytes = json.dumps(payload, indent=2).encode("utf-8")
    js_bytes = f"window.FILMSUB_STREAM_CONFIG = {json.dumps(payload, indent=2)};\n".encode("utf-8")

    # Always write locally first
    try:
        os.makedirs(os.path.dirname(_LOCAL_STREAM_ENDPOINT_PATH), exist_ok=True)
        with open(_LOCAL_STREAM_ENDPOINT_PATH, "wb") as f:
            f.write(json_bytes)
        with open(_LOCAL_STREAM_ENDPOINT_JS_PATH, "wb") as f:
            f.write(js_bytes)
    except Exception as loc_err:
        log.debug("[GitHubService] Local stream_endpoint write note: %s", loc_err)

    token = _get_active_token()
    repo = _get_active_repo()
    if not token or repo in ("", "username/repo"):
        return True

    try:
        await upload_file(
            content=json_bytes,
            path=_STREAM_ENDPOINT_PATH,
            message=f"feat(stream): update live stream endpoint to {clean_url}",
        )
        await upload_file(
            content=js_bytes,
            path=_STREAM_ENDPOINT_JS_PATH,
            message=f"feat(stream): update live stream js config to {clean_url}",
        )
        log.info("[GitHubService] Successfully published live stream endpoint to GitHub: %s", clean_url)
        return True
    except Exception as exc:
        log.warning("[GitHubService] Failed to publish stream endpoint to GitHub: %s", exc)
        return False

