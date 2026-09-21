"""
subtitle_service.py — Download, convert, and publish subtitle files.

Provides:
  download_subtitle(url, save_path)                → local .srt path
  srt_to_vtt(srt_path)                             → local .vtt path
  upload_subtitle_to_github(vtt_path, filename, …) → public raw GitHub URL
"""

import logging
import re
import os
import base64

import httpx

log = logging.getLogger(__name__)


_DEFAULT_SRT = """1
00:00:01,000 --> 00:00:06,000
FilmSub.lk වෙතින් සිංහල උපසිරැසි සමඟ

2
00:00:07,000 --> 00:00:12,000
නැරඹීමට සහ බාගත කිරීමට ස්තූතියි!
"""

# ─────────────────────────────────────────────────────────────────────────────
async def download_subtitle(url: str, save_path: str = "/tmp/sub.srt") -> str:
    """
    Download a subtitle file from *url* and save it to *save_path*.
    If url is 'default', 'auto', 'none', or if download fails, falls back to
    generating a default Sinhala subtitle file.
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

    if not url or url.lower() in ("default", "none", "auto", "sinhala", "sample"):
        log.info("Generating default Sinhala subtitle for: %s", save_path)
        with open(save_path, "w", encoding="utf-8") as fh:
            fh.write(_DEFAULT_SRT)
        return save_path

    log.info("Downloading subtitle from: %s", url)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        with open(save_path, "wb") as fh:
            fh.write(resp.content)

        log.info("Subtitle saved to: %s (%d bytes)", save_path, len(resp.content))
        return save_path

    except Exception as exc:
        log.warning("Subtitle download from '%s' failed (%s). Generating default Sinhala subtitle fallback.", url, exc)
        with open(save_path, "w", encoding="utf-8") as fh:
            fh.write(_DEFAULT_SRT)
        return save_path


# ─────────────────────────────────────────────────────────────────────────────
def srt_to_vtt(srt_path: str) -> str:
    """
    Convert an SRT subtitle file to WebVTT format.

    Key differences handled:
    - SRT uses commas for milliseconds: 00:00:01,000
    - VTT uses dots for milliseconds:   00:00:01.000
    - VTT requires 'WEBVTT' as the first line
    - Numeric-only sequence lines are removed (VTT doesn't use them)

    Returns the path to the newly created .vtt file.
    """
    if not os.path.isfile(srt_path):
        raise FileNotFoundError(f"SRT file not found: {srt_path}")

    vtt_path = srt_path.replace(".srt", ".vtt")

    with open(srt_path, "r", encoding="utf-8", errors="replace") as fh:
        srt_content = fh.read()

    # ── 1. Replace SRT timecode comma separators with dots ────────────────
    #    Pattern: HH:MM:SS,mmm --> HH:MM:SS,mmm  (arrow with optional spaces)
    vtt_content = re.sub(
        r"(\d{2}:\d{2}:\d{2}),(\d{3})",
        r"\1.\2",
        srt_content,
    )

    # ── 2. Strip lone digit lines (SRT sequence numbers) ─────────────────
    vtt_content = re.sub(r"^\d+\s*$", "", vtt_content, flags=re.MULTILINE)

    # ── 3. Collapse excess blank lines ────────────────────────────────────
    vtt_content = re.sub(r"\n{3,}", "\n\n", vtt_content.strip())

    # ── 4. Prepend WEBVTT header ─────────────────────────────────────────
    vtt_content = "WEBVTT\n\n" + vtt_content + "\n"

    with open(vtt_path, "w", encoding="utf-8") as fh:
        fh.write(vtt_content)

    log.info("Converted SRT → VTT: %s", vtt_path)
    return vtt_path


# ─────────────────────────────────────────────────────────────────────────────
async def upload_subtitle_to_github(
    vtt_path: str,
    filename: str,
    github_token: str,
    repo: str,
) -> str:
    """
    Upload a .vtt subtitle file to the GitHub repo under /subs/{filename}.

    Uses the GitHub Contents API (PUT).  If the file already exists the
    existing SHA is fetched first so the file is updated rather than
    duplicated.

    Args:
        vtt_path:     Local path to the .vtt file.
        filename:     Desired filename in the repo (e.g. 'avatar-3-si.vtt').
        github_token: Personal access token with repo scope.
        repo:         'owner/repo' string.

    Returns:
        The raw.githubusercontent.com public URL for the uploaded file.
    """
    if not os.path.isfile(vtt_path):
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    if not github_token or repo == "username/repo":
        import shutil
        local_subs_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "website", "subs")
        )
        os.makedirs(local_subs_dir, exist_ok=True)
        dest_path = os.path.join(local_subs_dir, filename)
        shutil.copyfile(vtt_path, dest_path)
        log.info("Subtitle saved LOCALLY: %s", dest_path)
        return f"subs/{filename}"

    api_path = f"subs/{filename}"
    api_url = f"https://api.github.com/repos/{repo}/contents/{api_path}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    with open(vtt_path, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode()

    async with httpx.AsyncClient(timeout=30) as client:
        # ── Check if file already exists (need SHA for update) ────────────
        sha: str = ""
        existing = await client.get(api_url, headers=headers)
        if existing.status_code == 200:
            sha = existing.json().get("sha", "")
            log.info("Subtitle '%s' already exists in repo, will update.", api_path)

        payload: dict = {
            "message": f"Add subtitle: {filename}",
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha  # Required when updating an existing file

        resp = await client.put(api_url, headers=headers, json=payload)
        resp.raise_for_status()

    # Build the raw URL (works without authentication)
    raw_url = f"https://raw.githubusercontent.com/{repo}/main/{api_path}"
    log.info("Subtitle uploaded: %s", raw_url)
    return raw_url


# ─────────────────────────────────────────────────────────────────────────────
def generate_placeholder_vtt(title: str, year: int = None) -> str:
    """
    Generate an inline data-URI VTT subtitle as a placeholder for bot-found movies.

    Returns a data:text/vtt URL that Video.js can load directly without a server.
    Replace with a real subtitle URL when available.
    """
    import urllib.parse
    year_str = f" ({year})" if year else ""
    vtt_content = (
        "WEBVTT\n\n"
        "1\n"
        "00:00:01.000 --> 00:00:06.000\n"
        f"FilmSub.lk - {title}{year_str}\n\n"
        "2\n"
        "00:00:07.000 --> 00:00:15.000\n"
        "Sinhala subtitles coming soon. Visit FilmSub.lk\n"
    )
    encoded = urllib.parse.quote(vtt_content, safe="")
    return f"data:text/vtt;charset=utf-8,{encoded}"
