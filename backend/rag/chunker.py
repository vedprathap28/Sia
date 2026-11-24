"""
Sia AI — Text Chunker
=======================
Splits large documents into overlapping word-based chunks.
Overlap prevents loss of context at chunk boundaries.
"""
from __future__ import annotations


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    min_chunk_size: int = 50,
) -> list[str]:
    """
    Split text into overlapping chunks by word count.

    Args:
        text:           Input text to split
        chunk_size:     Target words per chunk
        overlap:        Words to repeat between adjacent chunks
        min_chunk_size: Skip chunks smaller than this (avoids tiny trailing chunks)

    Returns:
        List of text chunks as strings
    """
    if not text.strip():
        return []

    words  = text.split()
    total  = len(words)

    if total <= chunk_size:
        # Document is small enough to be one chunk
        return [text.strip()] if total >= min_chunk_size else []

    chunks: list[str] = []
    step   = max(1, chunk_size - overlap)
    i      = 0

    while i < total:
        end   = min(i + chunk_size, total)
        chunk = " ".join(words[i:end])

        if len(words[i:end]) >= min_chunk_size:
            chunks.append(chunk)

        if end >= total:
            break
        i += step

    return chunks
