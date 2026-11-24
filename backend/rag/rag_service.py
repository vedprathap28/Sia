"""
Sia AI — RAG Service v3
=========================
Fixes from v2:
  ✓ embedder.embed(str) → embedder.embed_batch(list) for multi-chunk ingest
  ✓ embedder.embed([query]) → embedder.embed_batch([query])[0] for search
  ✓ Better error messages so missing deps are obvious
  ✓ chromadb ImportError caught early with a clear install instruction
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from backend.core.logger import get_logger

log = get_logger("rag")


class RAGService:
    def __init__(self):
        self._vs_docs  = None
        self._vs_mem   = None
        self._embedder = None
        self._ready    = False

    def _ensure_init(self):
        if self._ready:
            return
        try:
            import chromadb  # noqa: F401 — import check; raises ImportError if missing
        except ImportError:
            raise RuntimeError(
                "chromadb is not installed. Run: pip install chromadb>=0.4.0"
            )

        from backend.rag.vector_store import VectorStore
        from backend.rag.embedder     import embedder
        from backend.core.config      import settings

        self._embedder = embedder
        self._vs_docs  = VectorStore("documents", settings.CHROMA_DOCS_PATH)
        self._vs_mem   = VectorStore("memory",    settings.CHROMA_MEMORY_PATH)
        self._ready    = True

    # ── Ingest ────────────────────────────────────────────────────────────────

    def ingest_file(self, path: str) -> str:
        self._ensure_init()
        from backend.rag.document_loader import DocumentLoader
        from backend.rag.chunker         import chunk_text

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")

        loader = DocumentLoader()
        text   = loader.load_file(str(p))
        if not text.strip():
            return f"No text extracted from {p.name}"

        chunks = chunk_text(text)
        if not chunks:
            return f"No chunks produced from {p.name}"

        # FIX: embed_batch() accepts a list; embed() only accepts a single str
        embeddings = self._embedder.embed_batch(chunks)

        doc_id    = hashlib.md5(p.name.encode()).hexdigest()[:8]
        ids       = [f"{doc_id}-{i}" for i in range(len(chunks))]
        metadatas = [{"source": p.name, "chunk": str(i)} for i in range(len(chunks))]

        # Replace any previous version of this file so no stale chunks remain
        self._vs_docs.delete_by_source(p.name)
        self._vs_docs.add(
            texts=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )
        log.info("Ingested %d chunks from %s", len(chunks), p.name)
        return f"Ingested {len(chunks)} chunks from {p.name}"

    def ingest_folder(self, path: str) -> str:
        from backend.rag.document_loader import DocumentLoader
        loader = DocumentLoader()
        files  = loader.load_folder(path)
        count  = 0
        for f in files:
            try:
                self.ingest_file(f)
                count += 1
            except Exception as e:
                log.warning("Failed to ingest %s: %s", f, e)
        return f"Ingested {count} files from {path}"

    # ── Search (called by agent.py) ───────────────────────────────────────────

    def search_documents(self, query: str, n: int = 5) -> str:
        """Search ingested documents. Returns joined text."""
        self._ensure_init()
        try:
            # FIX: embed_batch returns list-of-vectors; take [0] for the single query
            emb     = self._embedder.embed_batch([query])[0]
            results = self._vs_docs.search(emb, n_results=n)
            return "\n\n".join(results) if results else ""
        except Exception as e:
            log.warning("search_documents error: %s", e)
            return ""

    def recall_memory(self, query: str, n: int = 3) -> str:
        """Search past conversation memory. Returns joined text."""
        self._ensure_init()
        try:
            emb     = self._embedder.embed_batch([query])[0]
            results = self._vs_mem.search(emb, n_results=n)
            return "\n\n".join(results) if results else ""
        except Exception as e:
            log.warning("recall_memory error: %s", e)
            return ""

    # ── Memory (called by agent._save_memory_async) ───────────────────────────

    def save_memory(self, user_msg: str, assistant_msg: str) -> None:
        """
        Save a conversation turn to vector memory.
        Called from agent.py as: save_memory(user_msg, assistant_msg)
        """
        self._ensure_init()
        try:
            combined = f"User: {user_msg}\nSia: {assistant_msg}"
            uid      = hashlib.md5(f"{datetime.now().isoformat()}{combined}".encode()).hexdigest()[:12]
            emb      = self._embedder.embed_batch([combined])[0]
            meta     = {"ts": datetime.now().isoformat(), "type": "conversation"}

            self._vs_mem.add(
                texts=[combined],
                embeddings=[emb],
                metadatas=[meta],
                ids=[uid],
            )
        except Exception as e:
            log.warning("save_memory failed: %s", e)

    # ── List / Delete ─────────────────────────────────────────────────────────

    def list_documents(self) -> list[str]:
        self._ensure_init()
        try:
            return self._vs_docs.list_sources()
        except Exception as e:
            log.warning("list_documents error: %s", e)
            return []

    def delete_document(self, name: str) -> str:
        self._ensure_init()
        try:
            self._vs_docs.delete_by_source(name)
            return f"Deleted {name}"
        except Exception as e:
            log.warning("delete_document error: %s", e)
            return f"Could not delete {name}: {e}"

    # ── Alias (called from system_actions.py) ─────────────────────────────────

    def ingest(self, path: str) -> str:
        return self.ingest_file(path)


# Singleton
rag_service = RAGService()
