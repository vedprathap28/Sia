"""
Sia AI — Document Loader
==========================
Reads file content from PDF, DOCX, TXT, and MD files.
Returns plain text strings ready for chunking.

Fix: Added DocumentLoader class so rag_service.py can do
     loader = DocumentLoader(); loader.load_file(path)
"""
from __future__ import annotations

from pathlib import Path
from backend.core.logger import get_logger

log = get_logger("doc_loader")

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


# ── Class interface (what rag_service.py calls) ───────────────────────────────

class DocumentLoader:
    """Wrapper class so rag_service.py can call loader.load_file() and loader.load_folder()."""

    def load_file(self, path: str) -> str:
        return load_file(path)

    def load_folder(self, folder_path: str) -> list[str]:
        """Returns list of file paths (not dict) — matches rag_service.ingest_folder usage."""
        base = Path(folder_path)
        if not base.exists() or not base.is_dir():
            log.warning("Folder not found: %s", folder_path)
            return []
        paths = []
        for f in base.rglob("*"):
            if f.suffix.lower() in SUPPORTED_EXTENSIONS and f.is_file():
                paths.append(str(f))
        return paths


# ── Module-level functions ────────────────────────────────────────────────────

def load_file(path: str) -> str:
    """Load a single file and return its text content."""
    p = Path(path)
    if not p.exists():
        log.warning("File not found: %s", path)
        return ""

    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        log.debug("Unsupported file type: %s", ext)
        return ""

    try:
        if ext == ".pdf":
            return _load_pdf(p)
        elif ext == ".docx":
            return _load_docx(p)
        elif ext in (".txt", ".md"):
            return _load_text(p)
    except Exception as e:
        log.warning("Could not read '%s': %s", path, e)

    return ""


def load_folder(folder_path: str) -> dict[str, str]:
    """Recursively load all supported files in a folder. Returns {filename: text}."""
    base = Path(folder_path)
    if not base.exists() or not base.is_dir():
        log.warning("Folder not found: %s", folder_path)
        return {}

    results: dict[str, str] = {}
    for f in base.rglob("*"):
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if f.is_file():
            content = load_file(str(f))
            if content.strip():
                results[f.name] = content
                log.debug("Loaded: %s (%d chars)", f.name, len(content))

    log.info("Loaded %d files from '%s'", len(results), folder_path)
    return results


# ── Private loaders ───────────────────────────────────────────────────────────

def _load_pdf(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
        doc   = fitz.open(str(path))
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n".join(pages)
    except ImportError:
        log.warning("PyMuPDF not installed. Run: pip install pymupdf")
        return ""
    except Exception as e:
        log.warning("PDF read error '%s': %s", path.name, e)
        return ""


def _load_docx(path: Path) -> str:
    try:
        from docx import Document
        doc   = Document(str(path))
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paras)
    except ImportError:
        log.warning("python-docx not installed. Run: pip install python-docx")
        return ""
    except Exception as e:
        log.warning("DOCX read error '%s': %s", path.name, e)
        return ""


def _load_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    log.warning("Could not decode '%s' with any encoding", path.name)
    return ""
