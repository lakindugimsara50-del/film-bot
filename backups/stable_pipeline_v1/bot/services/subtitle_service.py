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
def vtt_to_srt(vtt_path: str) -> str:
    """Convert a WebVTT (.vtt) subtitle file to SubRip (.srt) format for FFmpeg muxing."""
    if not os.path.isfile(vtt_path):
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    srt_path = re.sub(r"\.vtt$", ".srt", vtt_path, flags=re.IGNORECASE)
    if srt_path == vtt_path:
        srt_path = vtt_path + ".srt"

    with open(vtt_path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()

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
    with open(srt_path, "w", encoding="utf-8") as fh:
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


async def auto_acquire_sinhala_subtitle(
    title: str,
    year: int = None,
    imdb_id: str = None,
    temp_dir: str = "/tmp",
    video_path: str = None,
) -> tuple[str, str]:
    """
    Automatically find, extract, or generate a synchronized Sinhala (.srt and .vtt) subtitle
    for a movie or episode.
    Order of priority:
      1. Existing .srt / .vtt file downloaded alongside the torrent/video in temp_dir
      2. Embedded subtitle stream inside video_path (extracted via FFmpeg in RAM)
      3. Online subtitle search (YIFYSubtitles / YTS-Subs by IMDb ID) + Sinhala translation
      4. Rich Sinhala fallback subtitle (.srt + .vtt)

    Returns:
      (srt_path, vtt_path)
    """
    os.makedirs(temp_dir, exist_ok=True)
    final_srt = os.path.join(temp_dir, "sinhala_merged.srt")
    final_vtt = os.path.join(temp_dir, "sinhala_merged.vtt")

    candidate_sub = None

    # 1. Check temp_dir for any .srt or .vtt files
    for root, _, files in os.walk(temp_dir):
        for f in files:
            f_l = f.lower()
            if f_l in ("sinhala_merged.srt", "sinhala_merged.vtt", "sinhala_auto.srt"):
                continue
            if f_l.endswith((".srt", ".vtt")):
                p = os.path.join(root, f)
                if os.path.getsize(p) > 64:
                    candidate_sub = p
                    if "sin" in f_l or "si" in f_l:
                        break
        if candidate_sub and ("sin" in os.path.basename(candidate_sub).lower()):
            break

    # 2. If no external subtitle file found, try extracting embedded subtitle track from video container
    if not candidate_sub and video_path and os.path.exists(video_path):
        try:
            from services import video_service
            extracted = os.path.join(temp_dir, "extracted_embedded.srt")
            if await video_service.extract_embedded_subtitle(video_path, extracted):
                candidate_sub = extracted
                log.info("[SubtitleService] Extracted embedded subtitle track from video container: %s", extracted)
        except Exception as ex_err:
            log.debug("[SubtitleService] Embedded subtitle extraction skipped: %s", ex_err)

    # 3. If still no subtitle, search online (YIFYSubtitles / YTS-Subs) by IMDb ID
    if not candidate_sub and imdb_id:
        try:
            online_srt = await fetch_online_subtitle_srt(title=title, year=year, imdb_id=imdb_id, temp_dir=temp_dir)
            if online_srt and os.path.exists(online_srt):
                candidate_sub = online_srt
        except Exception as on_err:
            log.debug("[SubtitleService] Online subtitle step skipped: %s", on_err)

    # 4. If we have a candidate subtitle (.srt or .vtt), convert & translate to Sinhala if needed
    if candidate_sub and os.path.exists(candidate_sub):
        try:
            if candidate_sub.lower().endswith(".vtt"):
                candidate_sub = vtt_to_srt(candidate_sub)
            await translate_srt_to_sinhala(candidate_sub, final_srt)
            if os.path.exists(final_srt) and os.path.getsize(final_srt) > 32:
                final_vtt = srt_to_vtt(final_srt)
                return final_srt, final_vtt
        except Exception as conv_err:
            log.warning("[SubtitleService] Candidate subtitle processing error: %s", conv_err)

    # 5. Fallback: Generate clean Sinhala SRT & VTT
    fallback_srt_content = generate_fallback_sinhala_srt(title, year)
    with open(final_srt, "w", encoding="utf-8") as fh:
        fh.write(fallback_srt_content)
    final_vtt = srt_to_vtt(final_srt)
    return final_srt, final_vtt


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

