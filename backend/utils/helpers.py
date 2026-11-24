"""
Sia AI — Shared Utilities
===========================
Small, reusable helper functions used across the backend.
Nothing domain-specific here — pure utility code.
"""

from __future__ import annotations

import re
import time
import unicodedata
from functools import wraps
from typing import Callable, Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


# ══════════════════════════════════════════════════════════════════════════════
#  Timing
# ══════════════════════════════════════════════════════════════════════════════

def timer(fn: F) -> F:
    """Decorator — prints execution time of the wrapped function."""
    @wraps(fn)
    def wrapper(*args, **kwargs) -> Any:
        t0     = time.perf_counter()
        result = fn(*args, **kwargs)
        ms     = (time.perf_counter() - t0) * 1000
        print(f"[timer] {fn.__qualname__}  {ms:.1f} ms")
        return result
    return wrapper  # type: ignore[return-value]


def now_ms() -> int:
    """Current epoch time in milliseconds."""
    return int(time.time() * 1000)


# ══════════════════════════════════════════════════════════════════════════════
#  Strings
# ══════════════════════════════════════════════════════════════════════════════

def truncate(text: str, max_chars: int = 100, suffix: str = "…") -> str:
    """Truncate text to max_chars, appending suffix if needed."""
    return text if len(text) <= max_chars else text[:max_chars] + suffix


def sanitise_filename(name: str) -> str:
    """
    Remove/replace characters that are unsafe in filenames.
    Works cross-platform (Windows is the most restrictive).
    """
    # Normalize unicode
    name = unicodedata.normalize("NFKD", name)
    # Remove control chars and forbidden chars
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Strip leading/trailing dots and spaces (Windows)
    name = name.strip(". ")
    return name or "file"


def is_url(text: str) -> bool:
    """Quick check: does text look like a URL?"""
    return bool(re.match(r"https?://|www\.", text.strip(), re.IGNORECASE))


def normalise_query(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for fuzzy matching."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_quoted(text: str) -> list[str]:
    """Extract all substrings inside double or single quotes."""
    return re.findall(r'"([^"]+)"|\'([^\']+)\'', text)


# ══════════════════════════════════════════════════════════════════════════════
#  Collections
# ══════════════════════════════════════════════════════════════════════════════

def chunk_list(lst: list, size: int) -> list[list]:
    """Split list into chunks of `size`."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge override into base.
    Override values take precedence.
    """
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Validation
# ══════════════════════════════════════════════════════════════════════════════

def is_empty(value: Any) -> bool:
    """True if value is None, empty string, empty list/dict, etc."""
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) == 0
    return False


def coerce_bool(value: Any) -> bool:
    """Convert common truthy string values to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")