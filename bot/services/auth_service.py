"""
auth_service.py — Multi-user authorization management for Film Bot.

Allows the bot owner to grant upload/leech permissions to other team members
by user ID or Telegram @username.
Persists authorized users to bot/data/authorized_users.json.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Set

import config

log = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_AUTH_FILE = os.path.join(_DATA_DIR, "authorized_users.json")


class AuthService:
    def __init__(self):
        self._authorized_ids: Set[int] = set(config.ADMIN_IDS)
        self._authorized_usernames: Set[str] = set()
        self._metadata: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load authorized users from JSON file."""
        os.makedirs(_DATA_DIR, exist_ok=True)
        if not os.path.exists(_AUTH_FILE):
            self._save()
            return

        try:
            with open(_AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            for uid in data.get("user_ids", []):
                try:
                    self._authorized_ids.add(int(uid))
                except Exception:
                    pass

            for un in data.get("usernames", []):
                clean_un = un.lstrip("@").lower().strip()
                if clean_un:
                    self._authorized_usernames.add(clean_un)

            self._metadata = data.get("metadata", {})
            log.info(
                "[AuthService] Loaded %d IDs, %d usernames.",
                len(self._authorized_ids),
                len(self._authorized_usernames),
            )
        except Exception as exc:
            log.error("[AuthService] Failed to load %s: %s", _AUTH_FILE, exc)

    def _save(self) -> None:
        """Persist authorized users to JSON file."""
        os.makedirs(_DATA_DIR, exist_ok=True)
        try:
            payload = {
                "user_ids": sorted(list(self._authorized_ids)),
                "usernames": sorted(list(self._authorized_usernames)),
                "metadata": self._metadata,
            }
            with open(_AUTH_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as exc:
            log.error("[AuthService] Failed to save %s: %s", _AUTH_FILE, exc)

    def is_authorized(self, user_id: int, username: Optional[str] = None) -> bool:
        """Check if user is authorized to use bot upload features."""
        if user_id in config.ADMIN_IDS:
            return True
        if user_id in self._authorized_ids:
            return True
        if username:
            clean_un = username.lstrip("@").lower().strip()
            if clean_un in self._authorized_usernames:
                return True
        return False

    def is_owner(self, user_id: int) -> bool:
        """Primary admin / owner check."""
        return user_id in config.ADMIN_IDS

    def is_admin(self, user_id: int) -> bool:
        """Admin or authorized uploader check."""
        return self.is_owner(user_id) or (user_id in self._authorized_ids)

    def add_user(self, identifier: str, added_by: int = 0) -> str:
        """
        Authorize a user by user_id (e.g. 123456789) or username (e.g. @john).
        Returns human-readable result message.
        """
        identifier = identifier.strip()
        if not identifier:
            return "❌ කරුණාකර වලංගු User ID එකක් හෝ @username එකක් ලබාදෙන්න."

        if identifier.startswith("@") or not identifier.isdigit():
            clean_un = identifier.lstrip("@").lower().strip()
            if not clean_un:
                return "❌ වලංගු නොවන Username එකක්."
            self._authorized_usernames.add(clean_un)
            self._metadata[clean_un] = {
                "type": "username",
                "added_by": added_by,
                "added_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._save()
            log.info("[AuthService] Authorized @%s (by %s)", clean_un, added_by)
            return f"✅ <b>@{clean_un}</b> පරිශීලකයාට Bot වෙත චිත්‍රපට එක්කිරීමේ අවසරය (Uploader Access) ලබාදෙන ලදී!"
        else:
            uid = int(identifier)
            self._authorized_ids.add(uid)
            self._metadata[str(uid)] = {
                "type": "user_id",
                "added_by": added_by,
                "added_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._save()
            log.info("[AuthService] Authorized user_id %s (by %s)", uid, added_by)
            return f"✅ User ID: <code>{uid}</code> සඳහා Bot වෙත චිත්‍රපට එක්කිරීමේ අවසරය (Uploader Access) ලබාදෙන ලදී!"

    def remove_user(self, identifier: str) -> str:
        """Revoke authorization for a user."""
        identifier = identifier.strip()
        if identifier.startswith("@") or not identifier.isdigit():
            clean_un = identifier.lstrip("@").lower().strip()
            if clean_un in self._authorized_usernames:
                self._authorized_usernames.remove(clean_un)
                self._metadata.pop(clean_un, None)
                self._save()
                return f"🗑️ <b>@{clean_un}</b> ගේ අවසරය සාර්ථකව ඉවත් කරන ලදී."
            return f"❌ <b>@{clean_un}</b> අවසර ලත් ලැයිස්තුවේ හමු නොවීය."
        else:
            uid = int(identifier)
            if uid in config.ADMIN_IDS:
                return "⚠️ ප්‍රධාන Admin ගිණුම් ඉවත් කළ නොහැක."
            if uid in self._authorized_ids:
                self._authorized_ids.remove(uid)
                self._metadata.pop(str(uid), None)
                self._save()
                return f"🗑️ User ID: <code>{uid}</code> ගේ අවසරය සාර්ථකව ඉවත් කරන ලදී."
            return f"❌ User ID: <code>{uid}</code> අවසර ලත් ලැයිස්තුවේ හමු නොවීය."

    def list_authorized(self) -> List[dict]:
        """Return list of all authorized users."""
        results = []
        for aid in config.ADMIN_IDS:
            results.append({"id": aid, "type": "Owner / Primary Admin", "name": f"Admin ({aid})"})

        for uid in self._authorized_ids:
            if uid not in config.ADMIN_IDS:
                meta = self._metadata.get(str(uid), {})
                results.append({
                    "id": uid,
                    "type": "Authorized Uploader",
                    "added_at": meta.get("added_at", "N/A"),
                })

        for un in self._authorized_usernames:
            meta = self._metadata.get(un, {})
            results.append({
                "username": f"@{un}",
                "type": "Authorized Uploader",
                "added_at": meta.get("added_at", "N/A"),
            })

        return results


# Global singleton
auth_service = AuthService()
