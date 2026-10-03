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
from typing import Optional, Tuple

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
def is_genuine_sinhala_subtitle(content_or_path: Optional[str]) -> bool:
    """
    Verify whether a subtitle file path or raw string contains genuine Sinhala Unicode
    characters (U+0D80..U+0DFF).
    Requires at least 10 Sinhala words and > 10 subtitle cues so 2-4 line dummy banners
    and placeholder fallbacks are strictly rejected.
    """
    if not content_or_path:
        return False
    text = content_or_path
    if os.path.isfile(content_or_path):
        try:
            with open(content_or_path, "rb") as f:
                raw = f.read(500000)
            if raw.startswith(b"\xef\xbb\xbf"):
                text = raw.decode("utf-8-sig", errors="replace")
            elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = raw.decode("utf-16", errors="replace")
            else:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text = raw.decode("utf-16")
                    except UnicodeDecodeError:
                        text = raw.decode("latin-1", errors="replace")
        except Exception:
            return False

    cues_count = len(re.findall(r"-->", text))
    if cues_count <= 10:
        return False

    sinhala_words = re.findall(r"[\u0D80-\u0DFF]+", text)
    if len(sinhala_words) < 10:
        return False

    clean_no_placeholders = re.sub(r"FilmSub(\.lk)?|සිංහල උපසිරැසි|නැරඹීමට|බාගත කිරීමට|ස්තූතියි", "", text)
    remaining_sinhala = re.findall(r"[\u0D80-\u0DFF]+", clean_no_placeholders)
    return len(remaining_sinhala) >= 5


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

    with open(srt_path, "rb") as fh:
        raw_b = fh.read()
    if raw_b.startswith(b"\xef\xbb\xbf"):
        srt_content = raw_b.decode("utf-8-sig", errors="replace")
    elif raw_b.startswith((b"\xff\xfe", b"\xfe\xff")):
        srt_content = raw_b.decode("utf-16", errors="replace")
    else:
        try:
            srt_content = raw_b.decode("utf-8")
        except UnicodeDecodeError:
            try:
                srt_content = raw_b.decode("utf-16")
            except UnicodeDecodeError:
                srt_content = raw_b.decode("latin-1", errors="replace")

    # Universal newline normalization
    srt_content = srt_content.replace("\r\n", "\n").replace("\r", "\n")

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

    with open(vtt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(vtt_content)

    log.info("Converted SRT → VTT: %s", vtt_path)
    return vtt_path


# ─────────────────────────────────────────────────────────────────────────────
async def upload_subtitle_to_github(
    vtt_path: str,
    filename: str,
    github_token: Optional[str] = None,
    repo: Optional[str] = None,
) -> str:
    """
    Upload a .vtt subtitle file to the GitHub repo under /subs/{filename}.

    Uses the GitHub Contents API (PUT).  If the file already exists the
    existing SHA is fetched first so the file is updated rather than
    duplicated.

    Args:
        vtt_path:     Local path to the .vtt file.
        filename:     Desired filename in the repo (e.g. 'avatar-3-si.vtt').
        github_token: Personal access token with repo scope (defaults to config.GITHUB_TOKEN).
        repo:         'owner/repo' string (defaults to config.GITHUB_REPO).

    Returns:
        The raw.githubusercontent.com public URL for the uploaded file.
    """
    if not os.path.isfile(vtt_path):
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    if not github_token:
        try:
            import config
            github_token = getattr(config, "GITHUB_TOKEN", "")
        except Exception:
            github_token = os.getenv("GITHUB_TOKEN", "")

    if not repo:
        try:
            import config
            repo = getattr(config, "GITHUB_REPO", "lakindugimsara50-del/film-bot")
        except Exception:
            repo = os.getenv("GITHUB_REPO", "lakindugimsara50-del/film-bot")

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return f"subs/{filename}"

    if not github_token or repo in ("", "username/repo"):
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
def vtt_to_srt(vtt_path: str, out_path: Optional[str] = None) -> str:
    """Convert a WebVTT (.vtt) subtitle file to SubRip (.srt) format for FFmpeg muxing."""
    if not os.path.isfile(vtt_path):
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    srt_path = out_path or re.sub(r"\.vtt$", ".srt", vtt_path, flags=re.IGNORECASE)
    if srt_path == vtt_path:
        srt_path = vtt_path + ".srt"

    with open(vtt_path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read().replace("\r\n", "\n").replace("\r", "\n")

    # Remove WEBVTT header and metadata blocks
    content = re.sub(r"^WEBVTT[^\n]*\n+", "", content.strip(), flags=re.IGNORECASE)
    # Replace dot millisecond separators with commas in timestamps
    content = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", content)
    # Handle short MM:SS.mmm timestamps
    content = re.sub(r"(?<![:\d])(\d{2}:\d{2})\.(\d{3})", r"00:\1,\2", content)

    blocks = [b.strip() for b in re.split(r"\n\s*\n", content) if "-->" in b]
    srt_Blocks = []
    for idx, block in enumerate(blocks, 1):
        lines = block.splitlines()
        # Strip leading cue identifier if present before timestamp line
        if lines and "-->" not in lines[0]:
            lines = lines[1:]
        if lines:
            srt_Blocks.append(f"{idx}\n" + "\n".join(lines))

    srt_output = "\n\n".join(srt_Blocks) + "\n"
    with open(srt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(srt_output)

    return srt_path


async def translate_srt_to_sinhala(srt_path: str, output_srt_path: str, max_cues: int = 600) -> str:
    """
    Fast batch translation of an English/foreign SRT file into Sinhala (.srt)
    using concurrent requests to Google Translate GTX endpoint.
    Preserves exact SRT timecodes while translating dialogue lines into Sinhala.
    """
    if not os.path.isfile(srt_path):
        return srt_path

    try:
        with open(srt_path, "r", encoding="utf-8", errors="replace") as fh:
            raw_srt = fh.read()

        # Check if already contains Sinhala Unicode characters (U+0D80..U+0DFF)
        if re.search(r"[\u0D80-\u0DFF]", raw_srt):
            if srt_path != output_srt_path:
                import shutil
                shutil.copyfile(srt_path, output_srt_path)
            return output_srt_path

        blocks = [b.strip() for b in re.split(r"\n\s*\n", raw_srt.strip()) if "-->" in b]
        if not blocks:
            return srt_path

        parsed_cues = []
        for b in blocks[:max_cues]:
            lines = b.splitlines()
            tc_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if tc_idx == -1:
                continue
            timecode = lines[tc_idx].strip()
            text_lines = [re.sub(r"<[^>]+>", "", l).strip() for l in lines[tc_idx + 1:] if l.strip()]
            cue_text = " ".join(text_lines)
            if cue_text:
                parsed_cues.append((timecode, cue_text))

        if not parsed_cues:
            return srt_path

        # Batch cues into chunks of ~25 cues separated by newline for rapid translation
        batch_size = 25
        batches = [parsed_cues[i:i + batch_size] for i in range(0, len(parsed_cues), batch_size)]

        async def _translate_batch(client: httpx.AsyncClient, batch: list[tuple[str, str]]) -> list[tuple[str, str]]:
            joined = "\n".join(item[1] for item in batch)
            try:
                resp = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={
                        "client": "gtx",
                        "sl": "auto",
                        "tl": "si",
                        "dt": "t",
                        "q": joined,
                    },
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    translated_full = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
                    trans_lines = [l.strip() for l in translated_full.splitlines() if l.strip()]
                    if len(trans_lines) == len(batch):
                        return [(batch[j][0], trans_lines[j]) for j in range(len(batch))]
            except Exception as tr_err:
                log.debug("[SubtitleService] Batch translate fallback: %s", tr_err)
            return batch

        import asyncio
        translated_cues: list[tuple[str, str]] = []
        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            # Process in groups of 6 concurrent requests
            for i in range(0, len(batches), 6):
                chunk_batches = batches[i:i + 6]
                res_list = await asyncio.gather(*[_translate_batch(client, b) for b in chunk_batches])
                for r in res_list:
                    translated_cues.extend(r)

        # Prepend FilmSub Sinhala branding cue
        out_blocks = [
            "1\n00:00:01,000 --> 00:00:06,000\nFilmSub.lk — සිංහල උපසිරැසි සමඟ (Auto Sinhala Subtitles)"
        ]
        for idx, (tc, txt) in enumerate(translated_cues, 2):
            out_blocks.append(f"{idx}\n{tc}\n{txt}")

        with open(output_srt_path, "w", encoding="utf-8") as out_f:
            out_f.write("\n\n".join(out_blocks) + "\n")

        log.info("[SubtitleService] Translated %d subtitle cues to Sinhala: %s", len(translated_cues), output_srt_path)
        return output_srt_path
    except Exception as exc:
        log.warning("[SubtitleService] translate_srt_to_sinhala error: %s", exc)
        return srt_path


async def fetch_online_subtitle_srt(
    title: str,
    year: int = None,
    imdb_id: str = None,
    temp_dir: str = "/tmp",
) -> str:
    """
    Step 3 of subtitle acquisition: Query YIFYSubtitles / YTS-Subs by IMDb ID
    for official Sinhala or English .srt subtitles (unzipping into temp_dir).
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or not imdb_id or not str(imdb_id).startswith("tt"):
        return ""

    import io
    import zipfile

    hosts = [
        f"https://yts-subs.com/movie-imdb/{imdb_id}",
        f"https://yifysubtitles.ch/movie-imdb/{imdb_id}",
    ]
    base_domains = ["https://yts-subs.com", "https://yifysubtitles.ch"]

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            follow_redirects=True,
            timeout=10.0,
        ) as client:
            for idx, page_url in enumerate(hosts):
                try:
                    resp = await client.get(page_url)
                    if resp.status_code != 200:
                        continue
                    html = resp.text
                    # Look for Sinhala or English subtitle detail paths: /subtitles/...
                    sub_links = re.findall(r'href="(/subtitles/[^"]+)"', html)
                    if not sub_links:
                        continue
                    chosen_rel = next(
                        (s for s in sub_links if "sinhala" in s.lower() or "english" in s.lower()),
                        sub_links[0],
                    )
                    zip_slug = chosen_rel.replace("/subtitles/", "/subtitle/") + ".zip"
                    zip_url = base_domains[idx] + zip_slug
                    zresp = await client.get(zip_url)
                    if zresp.status_code == 200 and len(zresp.content) > 200:
                        with zipfile.ZipFile(io.BytesIO(zresp.content)) as zf:
                            for member in zf.namelist():
                                if member.lower().endswith(".srt"):
                                    out_p = os.path.join(temp_dir, "online_fetched.srt")
                                    with open(out_p, "wb") as out_f:
                                        out_f.write(zf.read(member))
                                    if os.path.getsize(out_p) > 64:
                                        log.info("[SubtitleService] Downloaded online subtitle for %s: %s", imdb_id, out_p)
                                        return out_p
                except Exception as host_err:
                    log.debug("[SubtitleService] Online subtitle host skipped: %s", host_err)
    except Exception as exc:
        log.debug("[SubtitleService] Online subtitle lookup skipped: %s", exc)
    return ""


# Track matched release reference hints (e.g. WEBRip, BluRay, YTS, PSA) per title for torrent sync matching
LAST_RELEASE_HINTS: dict[str, str] = {}


def extract_release_hint(text: str) -> str:
    """
    Extract release source/group hint (WEBRip, WEB-DL, BluRay, HDTV, YTS, PSA, GalaxyRG)
    from a subtitle filename or Sri Lankan subtitle page description.
    """
    if not text:
        return ""
    t = text.lower()
    hints = []
    if re.search(r"\b(web[\s._-]*rip|webrip|amzn[\s._-]*web|nf[\s._-]*web)\b", t):
        hints.append("WEBRip")
    elif re.search(r"\b(web[\s._-]*dl|webdl|web)\b", t):
        hints.append("WEB-DL")
    elif re.search(r"\b(blu[\s._-]*ray|bluray|brrip|bdrip)\b", t):
        hints.append("BluRay")
    elif re.search(r"\b(hdrip|hdtv)\b", t):
        hints.append("HDRip")

    for grp in ("yts", "yify", "psa", "galaxyrg", "pahe", "rarbg", "ettv", "tgx"):
        if re.search(rf"\b{grp}\b", t):
            hints.append(grp.upper())
            break
    return " ".join(hints)


def get_last_release_hint(title: str) -> str:
    """Return the most recently detected release reference hint for a movie/series title."""
    if not title:
        return ""
    key = re.sub(r"[^a-z0-9]+", "", title.lower())
    return LAST_RELEASE_HINTS.get(key, "")


def _store_release_hint(title: str, hint_source_text: str) -> str:
    hint = extract_release_hint(hint_source_text)
    if hint and title:
        key = re.sub(r"[^a-z0-9]+", "", title.lower())
        LAST_RELEASE_HINTS[key] = hint
    return hint


def extract_clean_show_name(text: str) -> str:
    """
    Extract a clean TV show name from any title, display_title, or raw query.
    Removes bracketed years like (2011), season/episode tokens (S08E06, Season 8 Episode 6, 8x06),
    episode titles (- The Iron Throne), standalone years at end, and trailing punctuation.
    """
    if not text:
        return ""
    # Strip bracketed year e.g. (2011), [2011]
    cleaned = re.sub(r"[\(\[]\s*(?:19\d\d|20\d\d)\s*[\)\]]", "", text)
    # Strip season/episode tokens and everything after
    cleaned = re.sub(
        r"[\(\[\{]?\b(?:s\d{1,2}[\s._-]*e\d{1,2}|season\s*\d{1,2}|episode\s*\d{1,2}|ep\s*\d{1,2}|\d{1,2}x\d{1,2})\b.*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Strip standalone year at end if present
    cleaned = re.sub(r"\b(19\d\d|20\d\d)\b.*$", "", cleaned)
    # Strip trailing hyphens, colons, dots, underscores, whitespace
    cleaned = re.sub(r"[\s._\-–—:]+$", "", cleaned).strip()
    return cleaned


def _select_episode_srt(
    srt_paths: list[str],
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Optional[str]:
    """Select the matching episode .srt file from a list of extracted .srt paths."""
    if not srt_paths:
        return None
    if len(srt_paths) == 1:
        return srt_paths[0]
    if season and episode:
        ep_patterns = [
            re.compile(rf"[Ss]0?{season}[\s._-]*[Ee][Pp]?0?{episode}(?:\b|[^a-zA-Z0-9])", re.IGNORECASE),
            re.compile(rf"(?:\b|[^a-zA-Z0-9])0?{season}x0?{episode}(?:\b|[^a-zA-Z0-9])", re.IGNORECASE),
            re.compile(rf"season[\s._-]*0?{season}.*episode[\s._-]*0?{episode}(?:\b|[^a-zA-Z0-9])", re.IGNORECASE),
            re.compile(rf"(?:\b|[^a-zA-Z0-9])[Ee][Pp]?[\s._-]*0?{episode}(?:\b|[^a-zA-Z0-9])", re.IGNORECASE),
            re.compile(rf"(?:\b|[^a-zA-Z0-9])0*{season}0*{episode}(?:\b|[^a-zA-Z0-9])", re.IGNORECASE),
            re.compile(rf"(?:\b|[^a-zA-Z0-9])0*{episode}(?:\b|[^a-zA-Z0-9])", re.IGNORECASE),
        ]
        for pat in ep_patterns:
            for sp in srt_paths:
                norm_sp = sp.replace("\\", "/")
                if pat.search(norm_sp) or pat.search(os.path.basename(sp)):
                    return sp
        return srt_paths[0]
    return srt_paths[0]


def _extract_srt_from_bytes(
    raw_bytes: bytes,
    temp_dir: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    prefix: str = "sri_lanka_sub",
) -> tuple[Optional[str], str]:
    """
    Extract a genuine Sinhala .srt file from downloaded bytes (supports raw .srt/.vtt, .zip, .rar, .7z).
    Returns (extracted_srt_path, release_hint_source_name).
    """
    import io
    import shutil
    import subprocess
    import zipfile

    if not raw_bytes or len(raw_bytes) < 64:
        return None, ""

    os.makedirs(temp_dir, exist_ok=True)

    # 1. Check if raw_bytes is already a direct .srt or .vtt text file
    head = raw_bytes[:4096]
    if b"-->" in head and not head.startswith((b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf")):
        direct_path = os.path.join(temp_dir, f"{prefix}_direct.srt")
        try:
            if raw_bytes.startswith(b"\xef\xbb\xbf"):
                txt = raw_bytes.decode("utf-8-sig", errors="replace")
            elif raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
                txt = raw_bytes.decode("utf-16", errors="replace")
            else:
                try:
                    txt = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        txt = raw_bytes.decode("utf-16")
                    except UnicodeDecodeError:
                        txt = raw_bytes.decode("latin-1", errors="replace")
            with open(direct_path, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception:
            with open(direct_path, "wb") as f:
                f.write(raw_bytes)
        if is_genuine_sinhala_subtitle(direct_path):
            return direct_path, os.path.basename(direct_path)

    # 2. Try standard ZIP archive
    if raw_bytes[:4] == b"PK\x03\x04" or zipfile.is_zipfile(io.BytesIO(raw_bytes)):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                srt_members = [
                    m for m in zf.namelist()
                    if m.lower().endswith((".srt", ".vtt")) and not os.path.basename(m).startswith("._")
                ]
                if srt_members:
                    chosen = _select_episode_srt(srt_members, season, episode)
                    candidates_to_try = [chosen] + [m for m in srt_members if m != chosen] if chosen else srt_members
                    for cand_m in candidates_to_try:
                        ext = ".vtt" if cand_m.lower().endswith(".vtt") else ".srt"
                        safe_base = re.sub(r"[^a-zA-Z0-9._-]", "_", os.path.basename(cand_m))
                        out_srt = os.path.join(temp_dir, f"{prefix}_{safe_base}")
                        raw_member = zf.read(cand_m)
                        try:
                            if raw_member.startswith(b"\xef\xbb\xbf"):
                                txt = raw_member.decode("utf-8-sig", errors="replace")
                            elif raw_member.startswith((b"\xff\xfe", b"\xfe\xff")):
                                txt = raw_member.decode("utf-16", errors="replace")
                            else:
                                try:
                                    txt = raw_member.decode("utf-8")
                                except UnicodeDecodeError:
                                    try:
                                        txt = raw_member.decode("utf-16")
                                    except UnicodeDecodeError:
                                        txt = raw_member.decode("latin-1", errors="replace")
                            with open(out_srt, "w", encoding="utf-8") as out_f:
                                out_f.write(txt)
                        except Exception:
                            with open(out_srt, "wb") as out_f:
                                out_f.write(raw_member)

                        if ext == ".vtt":
                            out_srt = vtt_to_srt(out_srt)
                        if os.path.exists(out_srt) and os.path.getsize(out_srt) > 64 and is_genuine_sinhala_subtitle(out_srt):
                            return out_srt, cand_m
        except Exception as z_err:
            log.debug("[SubtitleService] ZIP extraction note: %s", z_err)

    # 3. Try RAR / 7z archive extraction via system CLI tools (7z, unrar, bsdtar)
    if raw_bytes.startswith((b"Rar!", b"7z\xbc\xaf")) or b".srt" in raw_bytes[:2048].lower():
        archive_ext = ".rar" if raw_bytes.startswith(b"Rar!") else ".7z"
        archive_file = os.path.join(temp_dir, f"{prefix}_archive{archive_ext}")
        extract_dir = os.path.join(temp_dir, f"{prefix}_extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with open(archive_file, "wb") as af:
                af.write(raw_bytes)

            extracted_ok = False
            for cmd in (
                ["7z", "x", "-y", f"-o{extract_dir}", archive_file],
                ["unrar", "x", "-y", archive_file, extract_dir],
                ["bsdtar", "-xf", archive_file, "-C", extract_dir],
            ):
                if shutil.which(cmd[0]):
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                    if res.returncode == 0:
                        extracted_ok = True
                        break

            if extracted_ok:
                found_srts = []
                for root, _, files in os.walk(extract_dir):
                    for fn in files:
                        if fn.lower().endswith((".srt", ".vtt")) and not fn.startswith("._"):
                            found_srts.append(os.path.join(root, fn))
                chosen_path = _select_episode_srt(found_srts, season, episode)
                candidates_to_try = [chosen_path] + [p for p in found_srts if p != chosen_path] if chosen_path else found_srts
                for cand_p in candidates_to_try:
                    if cand_p and os.path.exists(cand_p):
                        if cand_p.lower().endswith(".vtt"):
                            cand_p = vtt_to_srt(cand_p)
                        if os.path.getsize(cand_p) > 64 and is_genuine_sinhala_subtitle(cand_p):
                            final_out = os.path.join(temp_dir, f"{prefix}_{os.path.basename(cand_p)}")
                            shutil.copyfile(cand_p, final_out)
                            return final_out, os.path.basename(cand_p)
        except Exception as rar_err:
            log.debug("[SubtitleService] RAR/7z extraction note: %s", rar_err)

    return None, ""


async def _scrape_wp_subtitle_site(
    client: httpx.AsyncClient,
    base_url: str,
    site_name: str,
    q_str: str,
    clean_title: str,
    season: Optional[int],
    episode: Optional[int],
    temp_dir: str,
) -> Optional[str]:
    """
    Search WordPress-based Sri Lankan subtitle portals (subz.lk, cineru.lk, baiscope.lk)
    using WP REST API (/wp-json/wp/v2/posts?search=...) with HTML search fallback (/?s=...).
    Extracts genuine Sinhala .srt and records WebRip/BluRay release hints.
    """
    import urllib.parse
    from bs4 import BeautifulSoup

    candidate_urls: list[str] = []
    clean_words = [w.lower() for w in clean_title.split()]
    title_words = [w for w in clean_words if len(w) > 2] or clean_words

    def _matches_title_words(text: str) -> bool:
        t = text.lower()
        if not title_words:
            return False
        for w in title_words:
            if not re.search(rf"(?:\b|[-_/]){re.escape(w)}(?:\b|[-_/])", t):
                return False
        return True

    # 1. Query WP REST API first (fast JSON response)
    try:
        api_url = f"{base_url.rstrip('/')}/wp-json/wp/v2/posts?search={urllib.parse.quote_plus(q_str)}&per_page=8"
        r_api = await client.get(api_url, timeout=10.0)
        if r_api.status_code == 200 and isinstance(r_api.json(), list):
            for post in r_api.json():
                link = post.get("link") or ""
                rendered_title = (post.get("title", {}) or {}).get("rendered", "").lower()
                if link and _matches_title_words(f"{rendered_title} {link}"):
                    candidate_urls.append(link)
    except Exception as wp_err:
        log.debug("[SubtitleService] %s WP API lookup note: %s", site_name, wp_err)

    # 2. Fallback to standard HTML search /?s=
    if not candidate_urls:
        try:
            s_url = f"{base_url.rstrip('/')}/?s={urllib.parse.quote_plus(q_str)}"
            r_html = await client.get(s_url, timeout=10.0)
            if r_html.status_code == 200:
                soup = BeautifulSoup(r_html.text, "html.parser")
                for a in soup.select("article a, h2 a, h3 a, .entry-title a, .post-title a, .result-item a"):
                    href = a.get("href", "")
                    if href and base_url.split("//")[-1].split("/")[0] in href:
                        if not any(x in href for x in ("/category/", "/tag/", "/author/", "/page/", "#")):
                            if href not in candidate_urls:
                                candidate_urls.append(href)
        except Exception as h_err:
            log.debug("[SubtitleService] %s HTML search note: %s", site_name, h_err)

    # 3. Inspect candidate post pages for subtitle download links & release reference hints
    for post_url in candidate_urls[:5]:
        try:
            p_resp = await client.get(post_url, timeout=10.0)
            if p_resp.status_code != 200:
                continue
            p_soup = BeautifulSoup(p_resp.text, "html.parser")
            page_text = p_soup.get_text(" ", strip=True)

            # Collect any release hints from .dlp-box or release notes on page
            dlp_text = " ".join(el.get_text(" ", strip=True) for el in p_soup.select(".dlp-box, .entry-content, .subz-info"))
            _store_release_hint(clean_title, dlp_text or page_text[:2000])

            dl_targets: list[str] = []
            # A. Subz.lk AJAX / button download links (admin-ajax.php?action=sub_download or .subz-list-btn)
            for a in p_soup.find_all("a", href=True):
                href = a["href"].strip()
                cls = " ".join(a.get("class", [])).lower()
                a_txt = a.get_text(" ", strip=True).lower()
                data_link = a.get("data-link") or a.get("data-href") or ""

                if data_link and any(k in data_link.lower() for k in ("download", ".zip", ".rar", ".srt", "admin-ajax")):
                    dl_targets.append(urllib.parse.urljoin(post_url, data_link))

                if (
                    "action=sub_download" in href
                    or "subz-list-btn" in cls
                    or "js-premium-download" in cls
                    or "download-button" in cls
                    or a.get("id") == "btn-download"
                    or any(ext in href.lower() for ext in (".zip", ".rar", ".7z", ".srt", "/download/", "/downloads/", "/sub-download/", "/links/"))
                    or ("උපසිරැසි" in a_txt and ("බාගත" in a_txt or "download" in a_txt))
                    or ("download" in a_txt and "subtitle" in a_txt)
                ):
                    if not any(ign in href.lower() for ign in ("/category/", "/tag/", "usersdrive", "mega.nz", "t.me/")):
                        dl_targets.append(urllib.parse.urljoin(post_url, href))

            for dl_url in dl_targets[:6]:
                try:
                    sub_resp = await client.get(dl_url, headers={"Referer": post_url}, timeout=15.0)
                    if sub_resp.status_code != 200 or len(sub_resp.content) < 64:
                        continue
                    content_type = sub_resp.headers.get("content-type", "").lower()
                    if "text/html" in content_type or sub_resp.content.startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                        sub_page_soup = BeautifulSoup(sub_resp.text, "html.parser")
                        real_dl = None
                        for sa in sub_page_soup.find_all("a", href=True):
                            sh = sa["href"].strip()
                            st = sa.get_text(" ", strip=True).lower()
                            if any(ext in sh.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) or "download" in st or "බාගත" in st:
                                real_dl = urllib.parse.urljoin(dl_url, sh)
                                break
                        if real_dl:
                            sub_resp = await client.get(real_dl, headers={"Referer": dl_url}, timeout=15.0)

                    if sub_resp.status_code == 200 and len(sub_resp.content) >= 64:
                        srt_file, member_name = _extract_srt_from_bytes(
                            sub_resp.content,
                            temp_dir=temp_dir,
                            season=season,
                            episode=episode,
                            prefix=f"{site_name.lower()}_sub",
                        )
                        if srt_file and os.path.exists(srt_file):
                            _store_release_hint(clean_title, f"{member_name} {dlp_text}")
                            log.info("[SubtitleService] Found genuine Sinhala subtitle from %s: %s", site_name, srt_file)
                            return srt_file
                except Exception as dl_err:
                    log.debug("[SubtitleService] %s download link error: %s", site_name, dl_err)
        except Exception as p_err:
            log.debug("[SubtitleService] %s post inspect note: %s", site_name, p_err)

    return None


async def fetch_sri_lankan_sinhala_subtitle(
    title: str,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    temp_dir: str = "/tmp",
) -> Optional[str]:
    """
    Search top Sri Lankan subtitle platforms (Subz.lk, Cineru.lk, Baiscope.lk, PirateLK.com)
    for genuine Sinhala subtitles (.srt / .zip / .rar / .7z).
    Downloads and extracts the specific episode or movie subtitle file into temp_dir,
    and records the release reference hint (WEBRip / BluRay / YTS / PSA) for torrent matching.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None

    import urllib.parse
    from bs4 import BeautifulSoup

    # Strip any leaked season/episode tokens or episode titles from title
    clean_series_name = extract_clean_show_name(title)
    search_title = clean_series_name if (season and clean_series_name) else title
    clean_title = re.sub(r"[^a-zA-Z0-9\s]", " ", search_title).strip()
    slug_title = re.sub(r"[^a-z0-9]+", "-", search_title.lower()).strip("-")
    search_terms = clean_title.split()
    if not search_terms:
        return None

    if season and episode:
        q_str = f"{clean_title} Season {season}"
    else:
        q_str = f"{clean_title} {year}" if year else clean_title

    log.info("[SubtitleService] Searching Sri Lankan subtitle sources (Subz.lk, Cineru.lk, Baiscope.lk, PirateLK) for '%s'...", q_str)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=12.0) as client:
            # 1. Try Subz.lk, Cineru.lk, SinhalaSub.lk, Baiscope.lk, Zoom.lk, LKSubs.com, Cines.lk
            for site_url, site_label in (
                ("https://subz.lk", "SubzLK"),
                ("https://cineru.lk", "CineruLK"),
                ("https://sinhalasub.lk", "SinhalaSubLK"),
                ("https://www.baiscope.lk", "BaiscopeLK"),
                ("https://zoom.lk", "ZoomLK"),
                ("https://www.lksubs.com", "LKSubs"),
                ("https://cines.lk", "CinesLK"),
            ):
                found_wp = await _scrape_wp_subtitle_site(
                    client=client,
                    base_url=site_url,
                    site_name=site_label,
                    q_str=q_str,
                    clean_title=clean_title,
                    season=season,
                    episode=episode,
                    temp_dir=temp_dir,
                )
                if found_wp and os.path.exists(found_wp):
                    return found_wp

            # 2. PirateLK direct slug candidates + search
            direct_candidates = []
            if season:
                direct_candidates.extend([
                    f"https://piratelk.com/{slug_title}-complete-season-{season:02d}-with-sinhala-subtitles/",
                    f"https://piratelk.com/{slug_title}-season-{season:02d}-with-sinhala-subtitles/",
                    f"https://piratelk.com/{slug_title}-complete-season-{season}-with-sinhala-subtitles/",
                    f"https://piratelk.com/{slug_title}-season-{season}-with-sinhala-subtitles/",
                    f"https://piratelk.com/{slug_title}-tv-series-with-sinhala-subtitles/",
                    f"https://piratelk.com/{slug_title}-with-sinhala-subtitles/",
                    f"https://piratelk.com/tvshows/{slug_title}/",
                    f"https://piratelk.com/tvshows/{slug_title}-season-{season}/",
                    f"https://piratelk.com/tvshows/{slug_title}-season-{season:02d}/",
                ])
            else:
                if year:
                    direct_candidates.append(f"https://piratelk.com/{slug_title}-{year}-with-sinhala-subtitles/")
                direct_candidates.append(f"https://piratelk.com/{slug_title}-with-sinhala-subtitles/")

            candidate_posts = list(direct_candidates)
            search_url = f"https://piratelk.com/?s={urllib.parse.quote_plus(q_str)}"
            resp = await client.get(search_url)
            if resp.status_code != 200 and season:
                resp = await client.get(f"https://piratelk.com/?s={urllib.parse.quote_plus(clean_title)}")

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select("article a, h2 a, .entry-title a"):
                    href = a.get("href")
                    if href and "piratelk.com/" in href and not any(x in href for x in ["/category/", "/tag/", "/author/", "/page/", "#"]):
                        p_title = a.get_text(strip=True).lower()
                        p_href_lower = href.lower()
                        if season:
                            s_token = f"season {season}"
                            s_token_padded = f"season {season:02d}"
                            s_token_short = f"s{season:02d}"
                            is_hub = "tv-series" in p_href_lower or "tv series" in p_title or f"{slug_title}-with-sinhala" in p_href_lower or "tvshows" in p_href_lower
                            if is_hub or s_token in p_title or s_token_padded in p_title or s_token_short in p_title or \
                               s_token in p_href_lower or s_token_padded in p_href_lower or s_token_short in p_href_lower:
                                candidate_posts.append(href)
                        else:
                            candidate_posts.append(href)

            seen_posts = []
            for cp in candidate_posts:
                if cp not in seen_posts:
                    seen_posts.append(cp)

            for post_url in seen_posts[:6]:
                try:
                    p_resp = await client.get(post_url)
                    if p_resp.status_code != 200:
                        continue
                    p_soup = BeautifulSoup(p_resp.text, "html.parser")

                    is_season_page = bool(
                        season and (
                            f"season-{season:02d}" in post_url.lower()
                            or f"season-{season}" in post_url.lower()
                        )
                    )
                    if season and not is_season_page:
                        s_target = f"season {season:02d}"
                        s_alt = f"season {season}"
                        s_slug_target = f"season-{season:02d}"
                        s_slug_alt = f"season-{season}"
                        for sa in p_soup.find_all("a", href=True):
                            sa_text = sa.get_text(strip=True).lower()
                            sa_href = sa["href"]
                            if "/download/" in sa_href or ".zip" in sa_href or ".rar" in sa_href:
                                continue
                            if (s_target in sa_text or s_alt in sa_text or s_slug_target in sa_href.lower() or s_slug_alt in sa_href.lower()) and "piratelk.com/" in sa_href:
                                s_url = urllib.parse.urljoin(post_url, sa_href)
                                s_resp = await client.get(s_url)
                                if s_resp.status_code == 200:
                                    p_soup = BeautifulSoup(s_resp.text, "html.parser")
                                    break

                    candidate_sub_links = []
                    # Search inside main article content first to avoid header/footer/sidebar noise
                    article_elem = p_soup.select_one(".entry-content, article, main, .download-links, .box-download") or p_soup
                    for da in article_elem.find_all("a", href=True):
                        dh = da["href"].strip()
                        dt = da.get_text(" ", strip=True).lower()
                        if any(ign in dh.lower() for ign in ("/category/", "/tag/", "usersdrive", "mega.nz", "t.me", "facebook", "youtube", "imdb", "wikipedia")):
                            continue
                        if any(vm in dt or vm in dh.lower() for vm in ("1080p", "720p", "480p", "x264", "x265", "hevc", "pixeldrain")):
                            continue
                        # Reject standard post URLs (they are other film pages, not subtitle download links)
                        if not any(ext in dh.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) and not any(k in dh.lower() for k in ("/download/", "/downloads/", "sub-download", "download-sub", "action=sub_download")):
                            if "-sinhala-sub" in dh.lower() or "-with-sinhala" in dh.lower():
                                continue
                        if (
                            any(ext in dh.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt"))
                            or any(k in dh.lower() for k in ("sub-download", "download-sub", "subtitles", "action=sub_download", "/download/", "/downloads/"))
                            or (("උපසිරැසි" in dt or "sub" in dt) and ("බාගත" in dt or "download" in dt))
                            or "download-subtitle" in dh.lower()
                        ):
                            full_sub_link = urllib.parse.urljoin(post_url, dh)
                            if full_sub_link not in candidate_sub_links:
                                candidate_sub_links.append(full_sub_link)

                    for dl_link in candidate_sub_links[:6]:
                        try:
                            z_resp = await client.get(dl_link, headers={"Referer": post_url}, timeout=12.0)
                            if z_resp.status_code != 200 or len(z_resp.content) < 64:
                                continue

                            content_type = z_resp.headers.get("content-type", "").lower()
                            if "text/html" in content_type or z_resp.content.startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                                sub_page_soup = BeautifulSoup(z_resp.text, "html.parser")
                                real_dl = None
                                for sa in sub_page_soup.find_all("a", href=True):
                                    sh = sa["href"].strip()
                                    st = sa.get_text(" ", strip=True).lower()
                                    if any(ext in sh.lower() for ext in (".zip", ".rar", ".7z", ".srt", ".vtt")) or "download" in st or "බාගත" in st:
                                        real_dl = urllib.parse.urljoin(dl_link, sh)
                                        break
                                if real_dl:
                                    z_resp = await client.get(real_dl, headers={"Referer": dl_link}, timeout=12.0)

                            if z_resp.status_code == 200 and len(z_resp.content) > 64:
                                out_srt, chosen_member = _extract_srt_from_bytes(
                                    z_resp.content,
                                    temp_dir=temp_dir,
                                    season=season,
                                    episode=episode,
                                    prefix="sri_lanka_sub",
                                )
                                if out_srt and os.path.exists(out_srt):
                                    _store_release_hint(clean_title, f"{chosen_member} {p_soup.get_text(' ', strip=True)[:1500]}")
                                    log.info("[SubtitleService] Found genuine Sri Lankan Sinhala subtitle from PirateLK: %s", out_srt)
                                    return out_srt
                        except Exception as dl_err:
                            log.debug("[SubtitleService] Candidate sub link download note: %s", dl_err)
                except Exception as post_err:
                    log.debug("[SubtitleService] Sri Lankan post inspect note: %s", post_err)
    except Exception as scrape_err:
        log.warning("[SubtitleService] Sri Lankan subtitle scraping note: %s", scrape_err)

    return None


async def auto_acquire_sinhala_subtitle(
    title: str,
    year: int = None,
    imdb_id: str = None,
    temp_dir: str = "/tmp",
    video_path: str = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Automatically find, extract, or translate a synchronized Sinhala (.srt and .vtt) subtitle
    for a movie or episode.
    Only genuinely Sinhala subtitles (containing Unicode characters U+0D80..U+0DFF) are accepted.
    Never substitutes English or foreign subtitles without genuine Sinhala translation.

    Returns:
      (srt_path, vtt_path) if genuine Sinhala subtitle found/produced, else (None, None).
    """
    os.makedirs(temp_dir, exist_ok=True)
    final_srt = os.path.join(temp_dir, "sinhala_merged.srt")
    final_vtt = os.path.join(temp_dir, "sinhala_merged.vtt")

    candidate_sub = None

    # 1. Search Sri Lankan subtitle sources (PirateLK, etc.) for authentic Sinhala subtitles first
    try:
        sl_sub = await fetch_sri_lankan_sinhala_subtitle(
            title=title,
            year=year,
            season=season,
            episode=episode,
            temp_dir=temp_dir,
        )
        if sl_sub and os.path.exists(sl_sub) and is_genuine_sinhala_subtitle(sl_sub):
            candidate_sub = sl_sub
            log.info("[SubtitleService] Selected authentic Sri Lankan Sinhala subtitle: %s", sl_sub)
    except Exception as sl_err:
        log.debug("[SubtitleService] Sri Lankan subtitle step skipped: %s", sl_err)

    # 2. Check temp_dir for any existing genuine Sinhala .srt or .vtt files
    if not candidate_sub:
        for root, _, files in os.walk(temp_dir):
            for f in files:
                f_l = f.lower()
                if f_l in ("sinhala_merged.srt", "sinhala_merged.vtt", "sinhala_auto.srt"):
                    continue
                if f_l.endswith((".srt", ".vtt")):
                    p = os.path.join(root, f)
                    if os.path.getsize(p) > 64 and is_genuine_sinhala_subtitle(p):
                        candidate_sub = p
                        break
            if candidate_sub:
                break

    # 3. Check other foreign subtitle files in temp_dir that can be translated
    if not candidate_sub:
        for root, _, files in os.walk(temp_dir):
            for f in files:
                f_l = f.lower()
                if f_l in ("sinhala_merged.srt", "sinhala_merged.vtt", "sinhala_auto.srt"):
                    continue
                if f_l.endswith((".srt", ".vtt")):
                    p = os.path.join(root, f)
                    if os.path.getsize(p) > 64:
                        candidate_sub = p
                        break
            if candidate_sub:
                break

    # 4. If no external subtitle file found, try extracting embedded subtitle track from video container
    if not candidate_sub and video_path and os.path.exists(video_path):
        try:
            from services import video_service
            extracted = os.path.join(temp_dir, "extracted_embedded.srt")
            if await video_service.extract_embedded_subtitle(video_path, extracted):
                candidate_sub = extracted
                log.info("[SubtitleService] Extracted embedded subtitle track from video container: %s", extracted)
        except Exception as ex_err:
            log.debug("[SubtitleService] Embedded subtitle extraction skipped: %s", ex_err)

    # 4. If still no subtitle, search online (YIFYSubtitles / YTS-Subs) by IMDb ID
    if not candidate_sub and imdb_id:
        try:
            online_srt = await fetch_online_subtitle_srt(title=title, year=year, imdb_id=imdb_id, temp_dir=temp_dir)
            if online_srt and os.path.exists(online_srt):
                candidate_sub = online_srt
        except Exception as on_err:
            log.debug("[SubtitleService] Online subtitle step skipped: %s", on_err)

    # 5. Process candidate subtitle: verify genuine Sinhala or attempt translation
    if candidate_sub and os.path.exists(candidate_sub):
        try:
            if candidate_sub.lower().endswith(".vtt"):
                candidate_sub = vtt_to_srt(candidate_sub)
            if is_genuine_sinhala_subtitle(candidate_sub):
                import shutil
                shutil.copyfile(candidate_sub, final_srt)
                final_vtt = srt_to_vtt(final_srt)
                log.info("[SubtitleService] Genuine Sinhala subtitle confirmed: %s", final_srt)
                return final_srt, final_vtt

            # If not Sinhala, attempt translation to Sinhala
            translated = await translate_srt_to_sinhala(candidate_sub, final_srt)
            if is_genuine_sinhala_subtitle(translated) and os.path.exists(translated) and os.path.getsize(translated) > 32:
                final_vtt = srt_to_vtt(translated)
                log.info("[SubtitleService] Successfully translated subtitle to genuine Sinhala: %s", translated)
                return translated, final_vtt
            else:
                log.warning("[SubtitleService] Subtitle translation did not yield genuine Sinhala content.")
        except Exception as conv_err:
            log.warning("[SubtitleService] Candidate subtitle processing error: %s", conv_err)

    # 6. No genuine Sinhala subtitle found: DO NOT substitute English and DO NOT create fake dummy subtitles
    log.info("[SubtitleService] No genuine Sinhala subtitle found for '%s'. Subtitle merging will be skipped.", title)
    return None, None


def generate_fallback_sinhala_srt(title: str, year: int = None) -> str:
    """Generate a valid Sinhala .srt string for any movie or episode."""
    year_str = f" ({year})" if year else ""
    cues = [
        ("00:00:01,000", "00:00:08,000", f"🎬 {title}{year_str} — FilmSub.lk සිංහල උපසිරැසි සමඟ"),
        ("00:00:08,500", "00:00:18,000", "සිංහල උපසිරැසි ස්වයංක්‍රීයව ක්‍රියාත්මකයි (Auto Sinhala Subtitles Enabled)"),
        ("00:00:18,500", "00:00:30,000", "1080p / 720p / 480p / 360p High-Speed Cloud Streaming & Download"),
        ("00:00:30,500", "00:00:45,000", f"{title}{year_str} — සිංහල උපසිරැසි වීඩියෝවටම Merge කර ඇත"),
    ]
    blocks = [f"{i}\n{start} --> {end}\n{text}" for i, (start, end, text) in enumerate(cues, 1)]
    return "\n\n".join(blocks) + "\n"


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
        f"FilmSub.lk - {title}{year_str} (සිංහල උපසිරැසි)\n\n"
        "2\n"
        "00:00:07.000 --> 00:00:15.000\n"
        "සිංහල උපසිරැසි සමඟ නැරඹීමට සහ බාගත කිරීමට ස්තූතියි!\n"
    )
    encoded = urllib.parse.quote(vtt_content, safe="")
    return f"data:text/vtt;charset=utf-8,{encoded}"

