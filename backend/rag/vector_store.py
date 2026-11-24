"""
Sia AI — Vector Store
=======================
ChromaDB-backed persistent vector store.
Two separate collections: 'documents' and 'memory'.
Uses cosine similarity for semantic search.
"""
from __future__ import annotations

from pathlib import Path
from backend.core.logger import get_logger

log = get_logger("vector_store")


class VectorStore:
    """
    Persistent ChromaDB collection wrapper.
    Each instance wraps one named collection.
    """

    def __init__(self, collection_name: str, persist_path: str):
        self._name = collection_name
        self._path = persist_path
        self._client     = None
        self._collection = None
        self._init()

    def _init(self) -> None:
        try:
            import chromadb
            Path(self._path).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self._path)
            self._collection = self._client.get_or_create_collection(
                name=self._name,
                metadata={"hnsw:space": "cosine"},
            )
            log.info("VectorStore '%s' ready (%d items)", self._name, self._collection.count())
        except ImportError:
            log.error("chromadb not installed. Run: pip install chromadb")
            raise
        except Exception as e:
            log.error("VectorStore init failed: %s", e)
            raise

    # ── Write ──────────────────────────────────────────────────────────────────

    def add(
        self,
        texts:      list[str],
        embeddings: list[list[float]],
        metadatas:  list[dict],
        ids:        list[str],
    ) -> None:
        """Add documents with pre-computed embeddings."""
        if not texts:
            return
        try:
            self._collection.add(
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
                ids=ids,
            )
        except Exception as e:
            log.error("VectorStore.add failed: %s", e)
            raise

    def delete_by_source(self, source: str) -> None:
        """Remove all chunks from a specific source file."""
        try:
            self._collection.delete(where={"source": source})
            log.info("Deleted all chunks for source: %s", source)
        except Exception as e:
            log.warning("delete_by_source failed for '%s': %s", source, e)

    def clear_all(self) -> None:
        """Wipe the entire collection."""
        try:
            self._client.delete_collection(self._name)
            self._collection = self._client.get_or_create_collection(
                name=self._name,
                metadata={"hnsw:space": "cosine"},
            )
            log.info("Collection '%s' cleared", self._name)
        except Exception as e:
            log.warning("clear_all failed: %s", e)

    # ── Read ───────────────────────────────────────────────────────────────────

    def search(
        self,
        query_embedding: list[float],
        n_results: int = 5,
    ) -> list[str]:
        """
        Find the n most similar documents to the query embedding.
        Returns list of text strings.
        """
        try:
            count = self._collection.count()
            if count == 0:
                return []
            n = min(n_results, count)
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=n,
            )
            return results["documents"][0] if results["documents"] else []
        except Exception as e:
            log.error("VectorStore.search failed: %s", e)
            return []

    def list_sources(self) -> list[str]:
        """Return unique source filenames of all ingested documents."""
        try:
            result = self._collection.get(include=["metadatas"])
            sources = set()
            for meta in result.get("metadatas", []):
                if meta and "source" in meta:
                    sources.add(meta["source"])
            return sorted(sources)
        except Exception as e:
            log.warning("list_sources failed: %s", e)
            return []

    def has_source(self, source: str) -> bool:
        """Check if a source file is already ingested."""
        try:
            result = self._collection.get(
                where={"source": source},
                limit=1,
                include=["metadatas"],
            )
            return len(result.get("ids", [])) > 0
        except Exception:
            return False

    def count(self) -> int:
        """Total number of chunks stored."""
        try:
            return self._collection.count()
        except Exception:
            return 0
