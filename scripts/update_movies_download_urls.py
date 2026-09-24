"""
Upgrade existing movies.json and movies_data.js entries so every download
uses the Edge /api/download proxy with proper Drive IDs, quality tags, and titles.
"""
import json
import re
import urllib.parse

with open("website/data/movies.json", "r", encoding="utf-8") as f:
    data = json.load(f)


def extract_id(u: str) -> str:
    if not u:
        return ""
    m = re.search(r"(?:/d/|id=)([a-zA-Z0-9_-]{15,})", str(u))
    return m.group(1) if m else ""


for m in data.get("movies", []):
    drive_id = m.get("drive_file_id") or extract_id(m.get("stream_url", ""))
    if not drive_id:
        for d in m.get("downloads", []):
            drive_id = d.get("drive_id") or extract_id(d.get("url", ""))
            if drive_id:
                break
    if drive_id:
        m["drive_file_id"] = drive_id

    title = m.get("title", "Movie")
    if m.get("type") == "series" and m.get("season") and m.get("episode"):
        disp = f"{title} S{int(m['season']):02d}E{int(m['episode']):02d}"
    else:
        disp = f"{title} ({m.get('year', '')})".replace(" ()", "")
    enc_title = urllib.parse.quote(disp)

    fsize = m.get("file_size") or int(1.4 * (1024 ** 3))
    q_map = m.get("qualities") or {}

    new_dls = []
    seen_q = set()
    for d in m.get("downloads", []):
        q_raw = str(d.get("quality", "")).strip()
        if "Cloud High-Speed" in q_raw:
            continue
        if d.get("download_only") or d.get("host") == "Telegram":
            new_dls.append(d)
            continue
        q_clean = "1080p"
        for tier in ("1080p", "720p", "480p", "360p"):
            if tier in q_raw:
                q_clean = tier
                break
        if q_clean in seen_q:
            continue
        seen_q.add(q_clean)
        q_drive_id = (
            d.get("drive_id")
            or extract_id(q_map.get(q_clean, ""))
            or extract_id(d.get("url", ""))
            or drive_id
        )
        ratios = {"1080p": 1.0, "720p": 0.55, "480p": 0.32, "360p": 0.18}
        sz_bytes = d.get("size_bytes") or int(fsize * ratios.get(q_clean, 1.0))
        if q_drive_id:
            d["drive_id"] = q_drive_id
            d["raw_url"] = f"https://drive.google.com/uc?export=download&id={q_drive_id}"
            d["url"] = f"/api/download?id={q_drive_id}&q={q_clean}&title={enc_title}&size={sz_bytes}"
            d["host"] = "Google Drive"
        d["quality"] = q_clean
        d["size_bytes"] = sz_bytes
        d["sub_merged"] = True
        d["subtitle_merged"] = True
        new_dls.append(d)
    m["downloads"] = new_dls

with open("website/data/movies.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

with open("website/data/movies_data.js", "w", encoding="utf-8") as f:
    f.write("window.MOVIES_DATA = " + json.dumps(data, indent=2, ensure_ascii=False) + ";\n")

print("Successfully updated", len(data.get("movies", [])), "movies in movies.json and movies_data.js")
