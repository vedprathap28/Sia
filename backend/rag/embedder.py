"""
Sia AI — Embedder
==================
Converts text to vector embeddings using sentence-transformers.
Model: all-MiniLM-L6-v2 (80MB, CPU-friendly, fully offline)
Loaded once as a singleton — subsequent calls are instant.
"""
from __future__ import annotations

import threading
from backend.core.logger import get_logger

log = get_logger("embedder")


class Embedder:
    """Lazy-loading sentence-transformers wrapper."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model      = None
        self._lock       = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            log.info("Loading embedding model '%s'...", self._model_name)
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
                log.info("Embedding model ready")
            except ImportError:
                log.error("sentence-transformers not installed. Run: pip install sentence-transformers")
                raise

    def embed(self, text: str) -> list[float]:
        """Convert a single string to a float32 embedding vector."""
        self._ensure_loaded()
        vec = self._model.encode(text, convert_to_numpy=True)
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Convert a list of strings to embedding vectors (batched, faster)."""
        if not texts:
            return []
        self._ensure_loaded()
        vecs = self._model.encode(texts, convert_to_numpy=True, batch_size=32, show_progress_bar=False)
        return [v.tolist() for v in vecs]


# Singleton
embedder = Embedder()
