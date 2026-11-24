"""
Sia AI — Agent Core vNext
===========================
Changes from v1:
  ✓ Language detection on every user input
  ✓ Detected language passed to LLM (multilingual replies)
  ✓ SmartMemory replaces fire-and-forget (facts about Ved only)
  ✓ Local router spoken responses now language-aware
  ✓ Everything else (3-tier routing, RAG, action execution) unchanged
"""
from __future__ import annotations
import asyncio
import re
import time
from typing import AsyncGenerator

from backend.agent.intent_parser import intent_parser, ParsedIntent, ActionStep
from backend.llm.llm_service import llm_service
from backend.memory.conversation_store import conversation_store
from backend.rag.rag_service import rag_service
from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("agent")


from backend.agent.router import (  # re-exported for backward compatibility
    _detect_language, _LOCAL_SPOKEN, _localised_spoken, _make_intent, _local_route,
)


# ── RAG query detection ────────────────────────────────────────────────────────

_RAG_KEYWORDS = [
    "summarize", "summarise", "what's in", "what is in", "find in my",
    "search my", "my resume", "my document", "my notes", "my pdf",
    "my files", "from my files", "in my documents", "what did i",
    "do you remember", "last time", "yesterday you", "what have we",
    "recall", "remember when", "you mentioned", "we discussed",
    "in my downloads", "in my desktop",
]

def _is_rag_query(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _RAG_KEYWORDS)


# Cache ChromaDB status — checked once per process, not per turn
_RAG_DOCS_AVAILABLE: bool | None = None

def _rag_has_docs() -> bool:
    """Returns True only if ChromaDB documents collection is non-empty. Cached."""
    global _RAG_DOCS_AVAILABLE
    if _RAG_DOCS_AVAILABLE is None:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=settings.CHROMA_DOCS_PATH)
            col    = client.get_or_create_collection("documents")
            _RAG_DOCS_AVAILABLE = col.count() > 0
            log.info("RAG docs available: %s (count=%d)", _RAG_DOCS_AVAILABLE, col.count())
        except Exception as e:
            log.warning("ChromaDB check failed: %s", e)
            _RAG_DOCS_AVAILABLE = False
    return _RAG_DOCS_AVAILABLE


def _build_rag_context(user_input: str) -> str:
    try:
        doc_context = rag_service.search_documents(user_input, n=5)
        mem_context = rag_service.recall_memory(user_input, n=3)
        parts = []
        if doc_context: parts.append(f"RELEVANT DOCUMENTS:\n{doc_context}")
        if mem_context: parts.append(f"RELEVANT PAST CONVERSATIONS:\n{mem_context}")
        if parts:
            return "\n\n" + "\n\n".join(parts)
    except Exception as e:
        log.warning("RAG context retrieval failed: %s", e)
    return ""


# ── Agent ─────────────────────────────────────────────────────────────────────

class AgentResponse:
    def __init__(self, text: str, spoken: str = "", intent: ParsedIntent | None = None,
                 action_results: list[str] = None, elapsed_ms: int = 0,
                 lang: str = 'en-IN'):
        self.text           = text
        self.spoken         = spoken or text
        self.intent         = intent
        self.action_results = action_results or []
        self.elapsed_ms     = elapsed_ms
        self.lang           = lang   # NEW: for TTS voice selection

    def to_dict(self) -> dict:
        return {
            "text":           self.text,
            "spoken":         self.spoken,
            "lang":           self.lang,
            "type":           self.intent.type if self.intent else "chat",
            "intent":         self.intent.intent if self.intent else "",
            "action_results": self.action_results,
            "elapsed_ms":     self.elapsed_ms,
        }


class Agent:

    async def process(self, user_input: str) -> AgentResponse:
        t0         = time.perf_counter()
        user_input = user_input.strip()

        if not user_input:
            return AgentResponse(text="I didn't catch that.")

        # ── Detect language first ──────────────────────────────────────────
        lang = _detect_language(user_input)
        log.info("Processing: %r [lang=%s]", user_input, lang)

        # ── Tier 1: local router ───────────────────────────────────────────
        local = _local_route(user_input, lang)
        if local is not None:
            log.info("Local route: %s", local.intent)
            conversation_store.add("user", user_input)
            response            = await self._execute_plan(local)
            response.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            response.lang       = lang
            conversation_store.add("assistant", response.text)
            self._save_memory(user_input, response.text)
            log.info("Done (local) %dms: %r", response.elapsed_ms, response.text[:80])
            return response

        # ── Tier 2: RAG context (GATED — skip if ChromaDB is empty) ─────
        # Checking an empty ChromaDB costs ~300ms per turn for nothing.
        # _rag_has_docs() is cached after first check.
        rag_context = ""
        if _is_rag_query(user_input) and _rag_has_docs():
            log.info("RAG query detected")
            rag_context = _build_rag_context(user_input)
            if rag_context:
                log.info("RAG context: %d chars", len(rag_context))

        enriched_input = user_input + rag_context if rag_context else user_input

        # ── Tier 3: LLM (language-aware) ──────────────────────────────────
        history = conversation_store.get_history(limit=10)
        try:
            llm_output = await llm_service.respond(
                enriched_input, history, detected_lang=lang
            )
        except Exception as e:
            log.exception("LLM failed: %s", e)
            return AgentResponse(text=f"LLM error: {type(e).__name__}: {e}", lang=lang)

        intent = intent_parser.parse(llm_output)
        conversation_store.add("user", user_input)

        if intent.is_action:
            response = await self._execute_plan(intent)
        else:
            response = AgentResponse(text=intent.message, intent=intent)

        response.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        response.lang       = lang
        conversation_store.add("assistant", response.text)
        self._save_memory(user_input, response.text)
        log.info("Done (llm) %dms [lang=%s]: %r", response.elapsed_ms, lang, response.text[:80])
        return response

    async def _execute_plan(self, intent: ParsedIntent) -> AgentResponse:
        results: list[str] = []
        for step in intent.plan:
            log.info("  Step %d: %s %s", step.step, step.action, step.params)
            from backend.actions.system_actions import execute_action  # lazy: Windows-only deps
            result = await execute_action(step.action, step.params)
            results.append(result)
        display = intent.spoken or (results[-1] if results else "Done.")
        return AgentResponse(
            text=display, spoken=intent.spoken or display,
            intent=intent, action_results=results,
        )

    async def stream_chat(
        self,
        user_input: str,
        detected_lang: str = 'en-IN',
    ) -> AsyncGenerator[str, None]:
        history    = conversation_store.get_history(limit=10)
        full_reply = ""
        async for token in llm_service.stream_chat(user_input, history, detected_lang):
            full_reply += token
            yield token
        conversation_store.add("user", user_input)
        conversation_store.add("assistant", full_reply)
        self._save_memory(user_input, full_reply)

    def _save_memory(self, user_msg: str, assistant_msg: str) -> None:
        """Use SmartMemory (filters before storing)."""
        try:
            from backend.memory.smart_memory import smart_memory
            smart_memory.save_async(user_msg, assistant_msg)
        except Exception as e:
            log.warning("SmartMemory unavailable — falling back to direct save: %s", e)
            import threading
            t = threading.Thread(
                target=rag_service.save_memory,
                args=(user_msg, assistant_msg),
                daemon=True,
            )
            t.start()


agent = Agent()
