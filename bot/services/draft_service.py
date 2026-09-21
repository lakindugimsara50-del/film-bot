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
    draft_id = f"draft_{len(drafts) + 1}_{uuid.uuid4().hex[:4]}"
    entry = {
        "id": draft_id,
        "title_hint": data.get("title_hint") or data.get("file_name") or "Untitled Movie",
        "file_id": data.get("file_id", ""),
        "file_name": data.get("file_name", ""),
        "file_size": data.get("file_size", 0),
        "film_url": data.get("film_url", ""),
        "quality": data.get("quality", "1080p"),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    drafts.append(entry)
    _save(drafts)
    return draft_id

def list_drafts() -> list[dict]:
    return _load()

def get_draft(draft_id: str) -> dict | None:
    for d in _load():
        if d.get("id") == draft_id:
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
