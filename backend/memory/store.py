"""
SIA — Long-term memory (SQLite), user-visible and deletable.

Categories:
  preference  how the user likes things done ("prefers Brave for browsing")
  profile     approved personal details used for forms (name, email, phone, skills…)
  project     project context ("backend project lives in D:/code/api")
  workflow    saved procedures (procedural memory) — steps the user approved
  fact        anything else the user asked SIA to remember

Rules: secrets are refused; nothing is stored as fact unless the user stated it
(SmartMemory's automatic extraction is tagged source="auto" so it can be reviewed).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import or_, select

from backend.core.logger import get_logger
from backend.database.db import session_scope
from backend.database.models import MemoryItem

log = get_logger("memory_store")

CATEGORIES = ("preference", "profile", "project", "workflow", "fact")

_SECRET = re.compile(
    r"(password|passwd|pwd|otp|one.time|cvv|pin\b|api[_ -]?key|secret|token|bearer|private key)"
    r"|(\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b)|(gsk_|sk-|sk_|AIza)",
    re.IGNORECASE,
)


def looks_secret(key: str, value: str) -> bool:
    return bool(_SECRET.search(f"{key} {value}"))


def _row(m: MemoryItem) -> dict:
    return {"id": m.id, "category": m.category, "key": m.key, "value": m.value, "source": m.source,
            "updated_at": m.updated_at.isoformat() if m.updated_at else ""}


class MemoryStore:
    def upsert(self, category: str, key: str, value: str, source: str = "user") -> dict | None:
        category = category if category in CATEGORIES else "fact"
        key, value = key.strip()[:128], value.strip()
        if not key or not value or looks_secret(key, value):
            log.warning("Memory upsert refused for key=%r (empty or secret-like)", key)
            return None
        with session_scope() as s:
            m = s.scalar(select(MemoryItem).where(MemoryItem.category == category, MemoryItem.key == key))
            if m:
                m.value, m.source = value, source
            else:
                m = MemoryItem(category=category, key=key, value=value, source=source)
                s.add(m)
            s.flush()
            return _row(m)

    def search(self, query: str = "", category: str | None = None, limit: int = 50) -> list[dict]:
        with session_scope() as s:
            q = select(MemoryItem)
            if category:
                q = q.where(MemoryItem.category == category)
            if query.strip():
                like = f"%{query.strip()}%"
                q = q.where(or_(MemoryItem.key.ilike(like), MemoryItem.value.ilike(like)))
            q = q.order_by(MemoryItem.updated_at.desc()).limit(limit)
            return [_row(m) for m in s.scalars(q)]

    def delete(self, item_id: str) -> bool:
        with session_scope() as s:
            m = s.get(MemoryItem, item_id)
            if not m:
                return False
            s.delete(m)
            return True

    def clear(self, category: str | None = None) -> int:
        with session_scope() as s:
            q = select(MemoryItem)
            if category:
                q = q.where(MemoryItem.category == category)
            items = list(s.scalars(q))
            for m in items:
                s.delete(m)
            return len(items)

    def context_block(self, limit: int = 25) -> str:
        """Compact block of preferences + profile for agent prompts."""
        items = [i for c in ("preference", "profile", "project") for i in self.search(category=c, limit=limit)]
        if not items:
            return ""
        return "Known about the user (stated by them):\n" + "\n".join(
            f"- [{i['category']}] {i['key']}: {i['value']}" for i in items[:limit])

    def migrate_json_facts(self, path: Path) -> int:
        """One-time import of the old ved_facts.json (file kept as-is)."""
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            return 0
        n = 0
        for k, v in (data or {}).items():
            if self.upsert("fact", str(k), str(v), source="migrated"):
                n += 1
        return n


memory_store = MemoryStore()
