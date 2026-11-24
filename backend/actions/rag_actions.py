"""
Sia AI — RAG Actions
======================
Action functions for document ingestion and semantic search.
Called via system_actions.py dispatcher.
"""
from __future__ import annotations
from backend.core.logger import get_logger

log = get_logger("rag_actions")


def ingest_file(path: str) -> str:
    """Ingest a single file into the knowledge base."""
    from backend.rag.rag_service import rag_service
    from backend.actions.system_ops import resolve_path
    resolved = str(resolve_path(path))
    return rag_service.ingest_file(resolved)


def ingest_folder(path: str) -> str:
    """Ingest all supported files in a folder recursively."""
    from backend.rag.rag_service import rag_service
    from backend.actions.system_ops import resolve_path
    resolved = str(resolve_path(path))
    return rag_service.ingest_folder(resolved)


def search_documents(query: str) -> str:
    """Search ingested documents for relevant content."""
    from backend.rag.rag_service import rag_service
    result = rag_service.search_documents(query, n=5)
    if not result:
        return "No relevant documents found. Try ingesting your files first."
    # Return a trimmed preview for the UI
    preview = result[:800]
    return f"Found relevant content:\n\n{preview}{'...' if len(result) > 800 else ''}"


def list_documents() -> str:
    """List all files currently in the knowledge base."""
    from backend.rag.rag_service import rag_service
    return rag_service.list_documents()


def delete_document(name: str) -> str:
    """Remove a document from the knowledge base."""
    from backend.rag.rag_service import rag_service
    return rag_service.delete_document(name)
