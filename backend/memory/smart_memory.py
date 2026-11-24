"""
Sia AI — Smart Memory vNext
============================
Replaces the fire-and-forget save_memory() in agent.py with an intelligent
filter that only stores what matters: facts about Ved and important conversations.

Architecture:
  Every turn → LLM memory filter (async, non-blocking)
    ↓ should_store=True?
  ChromaDB vector store (existing rag_service.save_memory)
  + facts.json flat file for fast retrieval of Ved's preferences

Usage in agent.py:
  Replace:
    self._save_memory_async(user_msg, assistant_msg)
  With:
    smart_memory.save_async(user_msg, assistant_msg)
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Optional

from backend.core.logger import get_logger

log = get_logger("smart_memory")

_ROOT        = Path(__file__).resolve().parents[2]   # fixed: was parents[3] (outside project)
_FACTS_PATH  = _ROOT / "data" / "ved_facts.json"


# ── Facts store (flat JSON for fast access) ───────────────────────────────────

class VedFactsStore:
    """
    Lightweight key-value store for facts about Ved.
    Examples:
      "preferred_language": "Hindi in the evenings"
      "occupation": "software developer"
      "project_current": "building Sia AI"
    """

    def __init__(self):
        self._path  = _FACTS_PATH
        self._facts: dict = {}
        self._lock  = threading.Lock()
        self._load()

    def _load(self):
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text("utf-8"))
                if isinstance(data, dict):
                    self._facts = data
                    log.info("Loaded %d facts about Ved", len(self._facts))
        except Exception as e:
            log.warning("Could not load facts: %s", e)
            self._facts = {}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                snap = dict(self._facts)
            self._path.write_text(
                json.dumps(snap, indent=2, ensure_ascii=False), "utf-8"
            )
        except Exception as e:
            log.warning("Could not save facts: %s", e)

    def set(self, key: str, value: str):
        with self._lock:
            self._facts[key] = value
        self._save()
        # Mirror into the SQLite memory table (visible + deletable in the UI)
        try:
            from backend.memory.store import memory_store
            memory_store.upsert("fact", key, str(value), source="auto")
        except Exception:
            pass

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._facts.get(key)

    def all(self) -> dict:
        with self._lock:
            return dict(self._facts)

    def as_context_block(self) -> str:
        """Format facts as a context block for injection into LLM prompts."""
        with self._lock:
            facts = dict(self._facts)
        if not facts:
            return ""
        lines = ["KNOWN FACTS ABOUT VED:"]
        for k, v in facts.items():
            lines.append(f"  — {k.replace('_', ' ').title()}: {v}")
        return "\n".join(lines)


ved_facts = VedFactsStore()


# ── Memory filter ─────────────────────────────────────────────────────────────

async def _should_store(user_msg: str, assistant_msg: str) -> tuple[bool, str]:
    """
    Ask the LLM to decide if this exchange is worth storing.
    Returns (should_store: bool, summary: str)
    """
    from backend.llm.llm_service import llm_service
    from backend.llm.prompts import MEMORY_FILTER_PROMPT

    prompt = f"""User said: "{user_msg}"
Sia replied: "{assistant_msg}"

Should this be stored in long-term memory?"""

    try:
        # Use a lightweight direct call — bypass history
        raw = await llm_service._complete([
            {"role": "system", "content": MEMORY_FILTER_PROMPT},
            {"role": "user",   "content": prompt},
        ])

        # Clean and parse
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(
                l for l in cleaned.splitlines()
                if not l.strip().startswith("```")
            ).strip()

        result = json.loads(cleaned)
        should = bool(result.get("should_store", False))
        summary = result.get("summary", "")
        reason  = result.get("reason",  "")
        log.debug("Memory filter: should_store=%s reason=%r", should, reason)
        return should, summary

    except Exception as e:
        log.debug("Memory filter LLM call failed (%s) — skipping storage", e)
        return False, ""


def _extract_facts(summary: str) -> dict:
    """
    Very simple fact extractor from a memory summary string.
    Looks for patterns like "Ved prefers X", "Ved works as Y", etc.
    Returns a dict of {key: value} pairs to store in ved_facts.
    """
    import re
    facts = {}
    patterns = [
        (r"[Vv]ed (?:is|works as|works in|studies)\s+(.+)", "occupation"),
        (r"[Vv]ed (?:likes|prefers|loves|enjoys)\s+(.+)", "preference"),
        (r"[Vv]ed (?:lives in|is from|is in)\s+(.+)", "location"),
        (r"[Vv]ed'?s?\s+(?:project|building|working on)\s+(?:is\s+)?(.+)", "current_project"),
        (r"[Vv]ed (?:speaks|uses)\s+(.+)", "language_habit"),
        (r"[Vv]ed (?:wants|needs|is planning to)\s+(.+)", "goal"),
    ]
    for pattern, key in patterns:
        m = re.search(pattern, summary)
        if m:
            facts[key] = m.group(1).strip().rstrip(".")
    return facts


# ── Main smart memory class ───────────────────────────────────────────────────

class SmartMemory:
    """
    Drop-in replacement for the fire-and-forget save in agent.py.
    Uses async LLM filter → only stores meaningful exchanges.
    """

    def save_async(self, user_msg: str, assistant_msg: str) -> None:
        """Non-blocking — fires the filter + save in background thread."""
        t = threading.Thread(
            target=self._run_in_new_loop,
            args=(user_msg, assistant_msg),
            daemon=True,
        )
        t.start()

    def _run_in_new_loop(self, user_msg: str, assistant_msg: str):
        """Run async save in a fresh event loop (background thread)."""
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._save(user_msg, assistant_msg))
        except Exception as e:
            log.debug("SmartMemory background error: %s", e)
        finally:
            loop.close()

    async def _save(self, user_msg: str, assistant_msg: str):
        should, summary = await _should_store(user_msg, assistant_msg)

        if not should:
            log.debug("SmartMemory: skipping (not memorable)")
            return

        log.info("SmartMemory: storing — %r", summary[:80])

        # 1. Store in ChromaDB vector memory (semantic search)
        try:
            from backend.rag.rag_service import rag_service
            rag_service.save_memory(user_msg, assistant_msg)
        except Exception as e:
            log.warning("SmartMemory: ChromaDB save failed: %s", e)

        # 2. Extract and store discrete facts about Ved
        if summary:
            facts = _extract_facts(summary)
            for key, val in facts.items():
                ved_facts.set(key, val)
                log.info("SmartMemory: fact stored — %s = %r", key, val)

    def get_facts_context(self) -> str:
        """Return facts block for injection into LLM prompts."""
        return ved_facts.as_context_block()


smart_memory = SmartMemory()
