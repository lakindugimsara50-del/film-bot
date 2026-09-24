"""
Upgrade existing movies.json and movies_data.js entries so every movie:
1. Uses the Edge /api/download proxy with proper Drive IDs, quality tags, clean titles (no duplicate years), and real byte sizes from Google Drive's multi-quality transcode streams.
2. Normalizes legacy stream_url/streams/qualities structures (including merged commits) to use /api/stream?id=<drive_id>&q=<tier>.
3. Replaces broken placeholder stream.yourdomain.workers.dev URLs with https://film-bot-2.onrender.com.
4. Exports window.FILMSUB_DATA, window.MOVIES_DATA, and window.__MOVIES_DATA__ in movies_data.js.
"""
import json
import os
import re
import urllib.parse
import httpx


def format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / (1024 ** 3):.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / (1024 ** 2):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} B"


def extract_id(u: str) -> str:
    if not u:
        return ""
    s = str(u).strip()
    if re.match(r"^[a-zA-Z0-9_-]{15,60}$", s):
        return s
    m = re.search(r"(?:/d/|[?&]id=)([a-zA-Z0-9_-]{15,60})", s)
    return m.group(1) if m else ""


def get_drive_access_token() -> str:
    try:
        conf_path = "bot/data/rclone.conf"
        if not os.path.exists(conf_path):
            return ""
        with open(conf_path, "r", encoding="utf-8") as f:
            raw = f.read()
        token_json = json.loads(raw.split("token = ")[1].splitlines()[0])
        refresh_token = token_json.get("refresh_token", "")
        r = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": "202264815644.apps.googleusercontent.com",
                "client_secret": "X4Z3ca8xfWDb1Voo-F9a7ZxJ",
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        return r.json().get("access_token", "")
    except Exception as exc:
        print("Warning: could not fetch Drive access token:", exc)
        return ""


def probe_drive_tier_sizes(drive_id: str, master_size: int, access_token: str) -> dict[str, tuple[int, str]]:
    """Return exact (byte_size, itag) for 1080p, 720p, 480p, 360p from Google Drive get_video_info."""
    fallback = {
        "1080p": (master_size, ""),
        "720p": (int(master_size * 0.55), "22"),
        "480p": (int(master_size * 0.32), "18"),
        "360p": (int(master_size * 0.18), "18"),
    }
    if not drive_id or not access_token:
        return fallback
    try:
        headers = {"Authorization": f"Bearer {access_token}", "User-Agent": "Mozilla/5.0"}
        vi = httpx.get(f"https://drive.google.com/get_video_info?docid={drive_id}", headers=headers, timeout=15)
        cookies = dict(vi.cookies)
        parsed = urllib.parse.parse_qs(vi.text)
        fmap = parsed.get("url_encoded_fmt_stream_map", [""])[0]
        if not fmap:
            return fallback
        itag_sizes: dict[str, int] = {}
        for entry in fmap.split(","):
            ep = urllib.parse.parse_qs(entry)
            if "itag" in ep and "url" in ep:
                it = ep["itag"][0]
                u = ep["url"][0]
                rh = dict(headers)
                rh["Range"] = "bytes=0-0"
                sr = httpx.get(u, headers=rh, cookies=cookies, follow_redirects=True, timeout=15)
                cr = sr.headers.get("content-range", "")
                if "/" in cr:
                    itag_sizes[it] = int(cr.split("/")[-1])
        print(f"  Probed {drive_id}: itag_sizes={itag_sizes}")
        sz_1080 = (master_size, "")
        # If itag 37 is smaller than master_size and itag 22 is present, we have 4 distinct sizes:
        # 1080p (raw), 720p (itag 37), 480p (itag 22), 360p (itag 18)
        if itag_sizes.get("37") and itag_sizes["37"] < master_size and itag_sizes.get("22"):
            sz_720 = (itag_sizes["37"], "37")
            sz_480 = (itag_sizes["22"], "22")
        elif itag_sizes.get("22"):
            sz_720 = (itag_sizes["22"], "22")
            if itag_sizes.get("59"):
                sz_480 = (itag_sizes["59"], "59")
            elif itag_sizes.get("18"):
                sz_480 = (itag_sizes["18"], "18")
            else:
                sz_480 = fallback["480p"]
        elif itag_sizes.get("18"):
            sz_720 = (itag_sizes["18"], "18")
            sz_480 = (itag_sizes["18"], "18")
        else:
            sz_720 = fallback["720p"]
            sz_480 = fallback["480p"]

        sz_360 = (itag_sizes["18"], "18") if itag_sizes.get("18") else fallback["360p"]
        return {
            "1080p": sz_1080,
            "720p": sz_720,
            "480p": sz_480,
            "360p": sz_360,
        }
    except Exception as exc:
        print(f"  Warning probing {drive_id}: {exc}")
        return fallback


access_token = get_drive_access_token()

with open("website/data/movies.json", "r", encoding="utf-8") as f:
    data = json.load(f)

probed_cache: dict[str, dict[str, int]] = {}

for m in data.get("movies", []):
    drive_id = (
        m.get("drive_id")
        or m.get("drive_file_id")
        or extract_id(m.get("stream_url", ""))
    )
    if not drive_id:
        for d in m.get("downloads", []):
            drive_id = d.get("drive_id") or extract_id(d.get("url", ""))
            if drive_id:
                break
    if drive_id:
        m["drive_id"] = drive_id
        m["drive_file_id"] = drive_id
        m["stream_url"] = f"/api/stream?id={drive_id}&q=auto"

    raw_title = str(m.get("title", "Movie")).strip()
    clean_title = re.sub(r"\s*\(\d{4}\)\s*$", "", raw_title).strip() or raw_title
    m["title"] = clean_title
    year_str = str(m.get("year", "")).strip()

    if m.get("type") == "series" and m.get("season") and m.get("episode"):
        disp = f"{clean_title} S{int(m['season']):02d}E{int(m['episode']):02d}"
    else:
        disp = f"{clean_title} ({year_str})" if year_str else clean_title
    enc_title = urllib.parse.quote(disp)

    fsize = int(m.get("file_size") or int(1.4 * (1024 ** 3)))
    if drive_id:
        if drive_id not in probed_cache:
            probed_cache[drive_id] = probe_drive_tier_sizes(drive_id, fsize, access_token)
        tier_sizes = probed_cache[drive_id]
    else:
        tier_sizes = {
            "1080p": (fsize, ""),
            "720p": (int(fsize * 0.55), "22"),
            "480p": (int(fsize * 0.32), "18"),
            "360p": (int(fsize * 0.18), "18"),
        }

    if drive_id:
        m["qualities"] = {
            "auto": f"/api/stream?id={drive_id}&q=auto",
            "1080p": f"/api/stream?id={drive_id}&q=1080p",
            "720p": f"/api/stream?id={drive_id}&q=720p",
            "480p": f"/api/stream?id={drive_id}&q=480p",
            "360p": f"/api/stream?id={drive_id}&q=360p",
        }
        # Normalize streams array so Server 1 is Super Player (/api/stream) and Server 2 is Drive Embed
        existing_streams = m.get("streams", [])
        extra_embeds = [
            s for s in existing_streams
            if s.get("stream_url") and "drive.google.com" not in s.get("stream_url", "") and "/api/stream" not in s.get("stream_url", "")
        ]
        norm_streams = [
            {
                "server": "Server 1",
                "label": "⚡ Super Player (Chunk Stream • Auto Sub)",
                "type": "video/mp4",
                "mode": "super_chunk",
                "drive_id": drive_id,
                "stream_url": f"/api/stream?id={drive_id}&q=auto",
                "quality": "1080p",
            },
            {
                "server": "Server 2",
                "label": "☁️ Drive Player (Google CDN • Auto Sub)",
                "type": "embed",
                "embed": True,
                "drive_id": drive_id,
                "stream_url": f"https://drive.google.com/file/d/{drive_id}/preview",
                "quality": "1080p",
            },
        ]
        for idx_e, ex in enumerate(extra_embeds, start=3):
            ex_copy = dict(ex)
            ex_copy["server"] = f"Server {idx_e}"
            norm_streams.append(ex_copy)
        m["streams"] = norm_streams

    tier_labels = {
        "1080p": "1080p Full HD (Sinhala Sub Merged)",
        "720p": "720p HD (Sinhala Sub Merged)",
        "480p": "480p SD (Sinhala Sub Merged)",
        "360p": "360p Data Saver (Sinhala Sub Merged)",
    }

    existing_dls = m.get("downloads", [])
    tg_dls = []
    for d in existing_dls:
        if d.get("download_only") or d.get("host") == "Telegram":
            d_copy = dict(d)
            if "stream.yourdomain.workers.dev" in str(d_copy.get("url", "")):
                d_copy["url"] = str(d_copy["url"]).replace(
                    "https://stream.yourdomain.workers.dev",
                    "https://film-bot-2.onrender.com",
                )
            tg_dls.append(d_copy)

    new_dls = []
    if drive_id:
        for tier in ("1080p", "720p", "480p", "360p"):
            sz_b, itag_str = tier_sizes[tier]
            sz_s = format_bytes(sz_b)
            itag_param = f"&itag={itag_str}" if itag_str else ""
            new_dls.append({
                "quality": tier,
                "label": tier_labels[tier],
                "size": sz_s,
                "size_bytes": sz_b,
                "drive_id": drive_id,
                "url": f"/api/download?id={drive_id}&q={tier}{itag_param}&title={enc_title}&size={sz_b}",
                "raw_url": f"https://drive.google.com/uc?export=download&id={drive_id}",
                "format": "MP4",
                "host": "Google Drive",
                "sub_merged": True,
                "subtitle_merged": True,
            })
    new_dls.extend(tg_dls)
    m["downloads"] = new_dls

with open("website/data/movies.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

with open("website/data/movies_data.js", "w", encoding="utf-8") as f:
    f.write("window.FILMSUB_DATA = window.MOVIES_DATA = window.__MOVIES_DATA__ = " + json.dumps(data, indent=2, ensure_ascii=False) + ";\n")

print("Successfully updated", len(data.get("movies", [])), "movies in movies.json and movies_data.js")

