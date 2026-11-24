"""
Sia AI — Conversation Store
==============================
In-memory conversation history with automatic JSON persistence.

Each message is stored as:
  {"role": "user"|"assistant", "content": "...", "ts": 1234567890.123}

get_history() returns clean LLM-format dicts: [{"role": ..., "content": ...}]
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Literal

from backend.core.logger import get_logger

log = get_logger("memory")

Role = Literal["user", "assistant", "system"]


class ConversationStore:
    """
    Thread-safe conversation history store.
    Persists to JSON on every write so history survives restarts.
    """

    def __init__(self, history_path: str | None = None):
        from backend.core.config import settings
        self._path   = Path(history_path or settings.HISTORY_PATH)
        self._max    = settings.MAX_HISTORY
        self._lock   = threading.Lock()
        self._history: list[dict] = []
        self._load()

    # ── Write ──────────────────────────────────────────────────────────────

    def add(self, role: Role, content: str) -> None:
        """Append a message and persist."""
        if not content.strip():
            return

        with self._lock:
            self._history.append({
                "role":    role,
                "content": content.strip(),
                "ts":      time.time(),
            })
            # Trim: keep last MAX_HISTORY *pairs* (×2 messages)
            cap = self._max * 2
            if len(self._history) > cap:
                self._history = self._history[-cap:]

        self._save()

    def clear(self) -> None:
        """Wipe all history."""
        with self._lock:
            self._history.clear()
        self._save()
        log.info("Conversation history cleared")

    # ── Read ───────────────────────────────────────────────────────────────

    def get_history(self, limit: int | None = None) -> list[dict]:
        """
        Return messages in LLM-ready format: [{"role": ..., "content": ...}]
        Excludes the internal timestamp field.
        """
        with self._lock:
            msgs = self._history[-limit:] if limit else self._history[:]
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def recent(self, n: int = 10) -> list[dict]:
        """Return last n raw messages (including timestamps) for display."""
        with self._lock:
            return self._history[-n:][:]

    def count(self) -> int:
        with self._lock:
            return len(self._history)

    # ── Persistence ────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                snapshot = list(self._history)
            self._path.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("Could not persist history: %s", e)

    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._history = data
                    log.info("Loaded %d messages from history", len(self._history))
        except Exception as e:
            log.warning("Could not load history: %s — starting fresh", e)
            self._history = []


# ── Singleton ──────────────────────────────────────────────────────────────
conversation_store = ConversationStore()