"""
draft_service.py — Manage pending movie uploads / drafts.
Allows saving video files or download links to draft storage,
and publishing them to the website later on demand.
"""

import json
import os
import uuid
from datetime import datetime, timezone

DRAFTS_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "drafts.json")
)

def _load() -> list[dict]:
    if not os.path.exists(DRAFTS_FILE):
        return []
    try:
        with open(DRAFTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save(drafts: list[dict]) -> None:
    os.makedirs(os.path.dirname(DRAFTS_FILE), exist_ok=True)
    with open(DRAFTS_FILE, "w", encoding="utf-8") as f:
        json.dump(drafts, f, ensure_ascii=False, indent=2)

def save_draft(data: dict) -> str:
    drafts = _load()
    draft_id = data.get("id") or data.get("draft_id") or f"draft_{len(drafts) + 1}_{uuid.uuid4().hex[:4]}"
    
    # Check if updating existing draft
    existing_idx = None
    for idx, d in enumerate(drafts):
        if d.get("id") == draft_id:
            existing_idx = idx
            break

    entry = {
        "id": draft_id,
        "title_hint": data.get("title_hint") or data.get("movie_name") or data.get("file_name") or "Untitled Movie",
        "movie_name": data.get("movie_name", ""),
        "year": data.get("year"),
        "file_id": data.get("file_id", ""),
        "message_id": data.get("message_id", 0),
        "file_name": data.get("file_name", ""),
        "file_size": data.get("file_size", 0),
        "film_url": data.get("film_url", ""),
        "stream_url": data.get("stream_url", ""),
        "quality": data.get("quality", "1080p"),
        "subtitles": data.get("subtitles", []),
        "subtitle_url": data.get("subtitle_url", ""),
        "meta": data.get("meta", {}),
        "movie_entry": data.get("movie_entry", {}),
        "created_at": data.get("created_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "channel_id": data.get("channel_id", 0),
    }

    if existing_idx is not None:
        drafts[existing_idx] = entry
    else:
        drafts.append(entry)

    _save(drafts)
    return draft_id

def update_draft(draft_id: str, updates: dict) -> bool:
    drafts = _load()
    for d in drafts:
        if d.get("id") == draft_id:
            d.update(updates)
            _save(drafts)
            return True
    return False

def list_drafts() -> list[dict]:
    return _load()

def get_draft(draft_id: str) -> dict | None:
    for d in _load():
        if d.get("id") == draft_id:
            return d
    return None

def find_draft_by_media(file_id: str = "", message_id: int = 0) -> dict | None:
    for d in reversed(_load()):
        if file_id and d.get("file_id") == file_id:
            return d
        if message_id and d.get("message_id") == message_id:
            return d
    return None

def delete_draft(draft_id: str) -> bool:
    drafts = _load()
    initial_len = len(drafts)
    drafts = [d for d in drafts if d.get("id") != draft_id]
    if len(drafts) != initial_len:
        _save(drafts)
        return True
    return False

