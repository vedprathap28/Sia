"""
SIA — File-system tools (sandboxed to allowed folders).
Every path goes through security.sandbox.resolve_safe; writes are verified on disk.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Annotated

from pydantic import Field

from backend.security.sandbox import allowed_roots, is_sensitive, resolve_safe
from backend.tools.registry import ToolResult, tool_def

PathArg = Annotated[str, Field(description="File or folder path. ~ and %USERPROFILE% are expanded")]

_TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".xml",
             ".yaml", ".yml", ".ini", ".toml", ".log", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".sql",
             ".sh", ".bat", ".ps1", ".env.example"}


# ── Reading any document ──────────────────────────────────────────────────────

def read_any(p: Path, max_chars: int = 20000) -> str:
    ext = p.suffix.lower()
    if ext == ".pdf":
        import fitz
        with fitz.open(p) as doc:
            text = "\n".join(page.get_text() for page in doc)
    elif ext == ".docx":
        import docx
        d = docx.Document(str(p))
        text = "\n".join(par.text for par in d.paragraphs)
        for t in d.tables:
            for row in t.rows:
                text += "\n" + " | ".join(c.text for c in row.cells)
    elif ext in {".xlsx", ".xlsm"}:
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            out.append(f"# Sheet: {ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 500:
                    out.append("…"); break
                out.append(" | ".join("" if v is None else str(v) for v in row))
        text = "\n".join(out)
    elif ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(str(p))
        out = []
        for i, slide in enumerate(prs.slides, 1):
            out.append(f"# Slide {i}")
            for sh in slide.shapes:
                if sh.has_text_frame:
                    out.append(sh.text_frame.text)
        text = "\n".join(out)
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars] + ("\n[...truncated]" if len(text) > max_chars else "")


@tool_def(category="files", risk=1, untrusted_output=True, timeout=60)
def read_document(path: PathArg, max_chars: Annotated[int, Field(ge=500, le=60000)] = 20000) -> ToolResult:
    """Read text from a PDF, Word, Excel, PowerPoint, CSV, Markdown or code file."""
    p = resolve_safe(path, must_exist=True)
    if p.is_dir():
        return ToolResult(ok=False, error="That is a folder. Use list_folder.")
    text = read_any(p, max_chars)
    return ToolResult(ok=True, output=text, data={"path": str(p), "chars": len(text)})


@tool_def(category="files", risk=1)
def list_folder(path: PathArg, pattern: str = "*", limit: Annotated[int, Field(le=500)] = 200) -> ToolResult:
    """List files in a folder (name, size, modified date), newest first."""
    p = resolve_safe(path, must_exist=True)
    items = []
    for c in p.iterdir():
        if not fnmatch.fnmatch(c.name.lower(), pattern.lower()) or is_sensitive(c):
            continue
        st = c.stat()
        items.append({"name": c.name, "dir": c.is_dir(), "size": st.st_size,
                      "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    items.sort(key=lambda x: x["modified"], reverse=True)
    items = items[:limit]
    text = "\n".join(f"{'[dir] ' if i['dir'] else ''}{i['name']}  {i['size']}B  {i['modified']}" for i in items)
    return ToolResult(ok=True, output=text or "(empty)", data={"path": str(p), "count": len(items)})


@tool_def(category="files", risk=1, timeout=60)
def search_files(query: Annotated[str, Field(description="Part of the file name, or a glob like '*resume*.pdf'")],
                 root: Annotated[str, Field(description="Folder to search; empty = all allowed folders")] = "",
                 limit: Annotated[int, Field(le=100)] = 25) -> ToolResult:
    """Find files by name across the allowed folders. Results are newest first (use for 'my latest resume')."""
    roots = [resolve_safe(root, must_exist=True)] if root else [r for r in allowed_roots() if r.exists()]
    pat = query if any(ch in query for ch in "*?[") else f"*{query}*"
    hits: list[tuple[float, Path]] = []
    for r in roots:
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in
                           {"node_modules", "__pycache__", "venv", ".venv", "appdata"}]
            for f in filenames:
                if fnmatch.fnmatch(f.lower(), pat.lower()):
                    fp = Path(dirpath) / f
                    if not is_sensitive(fp):
                        try:
                            hits.append((fp.stat().st_mtime, fp))
                        except OSError:
                            pass
            if len(hits) > 5000:
                break
    hits.sort(reverse=True)
    hits = hits[:limit]
    text = "\n".join(f"{p}  (modified {datetime.fromtimestamp(t):%Y-%m-%d})" for t, p in hits)
    return ToolResult(ok=bool(hits), output=text or "", error="" if hits else f"No files matching '{query}'.",
                      data={"paths": [str(p) for _, p in hits]})


def _exists_verifier(key: str):
    def v(args: dict, res: ToolResult):
        p = resolve_safe(args[key])
        return p.exists(), f"{p} exists={p.exists()}"
    return v


@tool_def(category="files", risk=lambda a: 3 if a.get("overwrite") else 2, verifier=_exists_verifier("path"),
          summarize=lambda a: f"Write {len(a.get('content',''))} chars to {a.get('path')}"
                              + (" (overwrite)" if a.get("overwrite") else ""))
def write_text_file(path: PathArg, content: str, overwrite: bool = False) -> ToolResult:
    """Create a text file (txt, md, csv, json, code…). Set overwrite=true to replace an existing file."""
    p = resolve_safe(path)
    if p.exists() and not overwrite:
        return ToolResult(ok=False, error=f"{p} already exists. Use overwrite=true (needs approval) or another name.")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return ToolResult(ok=True, output=f"Wrote {p}", data={"path": str(p)})


@tool_def(category="files", risk=2, verifier=_exists_verifier("path"))
def create_folder(path: PathArg) -> ToolResult:
    """Create a folder (and parents)."""
    p = resolve_safe(path)
    p.mkdir(parents=True, exist_ok=True)
    return ToolResult(ok=True, output=f"Folder ready: {p}", data={"path": str(p)})


def _dst_verifier(args: dict, res: ToolResult):
    d = Path(res.data.get("dst", ""))
    return d.exists(), f"destination exists={d.exists()}"


@tool_def(category="files", risk=2, verifier=_dst_verifier,
          summarize=lambda a: f"{'Copy' if a.get('keep_original') else 'Move'} {a.get('src')} → {a.get('dst')}")
def move_or_copy(src: PathArg, dst: PathArg, keep_original: Annotated[bool, Field(description="true = copy instead of move")] = False) -> ToolResult:
    """Move (default) or copy a file/folder. If dst is an existing folder the item goes inside it."""
    s = resolve_safe(src, must_exist=True)
    d = resolve_safe(dst)
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return ToolResult(ok=False, error=f"{d} already exists; not overwriting.")
    d.parent.mkdir(parents=True, exist_ok=True)
    if keep_original:
        shutil.copytree(s, d) if s.is_dir() else shutil.copy2(s, d)
    else:
        shutil.move(str(s), str(d))
    return ToolResult(ok=True, output=f"{'Copied' if keep_original else 'Moved'} to {d}", data={"dst": str(d)})


@tool_def(category="files", risk=2, verifier=_dst_verifier,
          summarize=lambda a: f"Rename {a.get('path')} → {a.get('new_name')}")
def rename_path(path: PathArg, new_name: Annotated[str, Field(description="New name only, no folders")]) -> ToolResult:
    """Rename a file or folder in place."""
    p = resolve_safe(path, must_exist=True)
    if "/" in new_name or "\\" in new_name:
        return ToolResult(ok=False, error="new_name must not contain folders")
    d = resolve_safe(p.parent / new_name)
    if d.exists():
        return ToolResult(ok=False, error=f"{d} already exists")
    p.rename(d)
    return ToolResult(ok=True, output=f"Renamed to {d.name}", data={"dst": str(d)})


def _gone(args, res):
    p = resolve_safe(args["path"])
    return (not p.exists()), f"still exists={p.exists()}"


@tool_def(category="files", risk=4, verifier=_gone, summarize=lambda a: f"DELETE {a.get('path')} (to Recycle Bin)")
def delete_path(path: PathArg) -> ToolResult:
    """Delete a file or folder by sending it to the Recycle Bin. Always destructive: needs confirmation."""
    p = resolve_safe(path, must_exist=True)
    try:
        import send2trash
        send2trash.send2trash(str(p))
    except ImportError:
        return ToolResult(ok=False, error="send2trash is not installed; refusing permanent delete.")
    return ToolResult(ok=True, output=f"Sent {p.name} to the Recycle Bin")


_TYPE_FOLDERS = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Presentations": {".ppt", ".pptx", ".odp"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".heic"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Audio": {".mp3", ".wav", ".m4a", ".flac", ".aac"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi"},
    "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".html", ".css", ".json", ".ipynb"},
}


@tool_def(category="files", risk=lambda a: 1 if a.get("dry_run", True) else 2, timeout=120,
          summarize=lambda a: f"Organise files in {a.get('folder')} into type sub-folders"
                              + (" (preview only)" if a.get("dry_run", True) else ""))
def organise_folder(folder: PathArg, dry_run: Annotated[bool, Field(description="true = only show the plan")] = True
                    ) -> ToolResult:
    """Sort the files directly inside a folder into sub-folders by type (Documents, Images, …).
    Run with dry_run=true first and show the user the plan."""
    root = resolve_safe(folder, must_exist=True)
    plan = []
    for f in root.iterdir():
        if f.is_file():
            target = next((k for k, exts in _TYPE_FOLDERS.items() if f.suffix.lower() in exts), "Other")
            plan.append((f, root / target / f.name))
    if dry_run:
        text = "\n".join(f"{a.name} → {b.parent.name}/" for a, b in plan)
        return ToolResult(ok=True, output=text or "Nothing to organise.", data={"moves": len(plan)})
    moved = 0
    for a, b in plan:
        if b.exists():
            continue
        b.parent.mkdir(exist_ok=True)
        shutil.move(str(a), str(b))
        moved += 1
    return ToolResult(ok=True, output=f"Moved {moved} of {len(plan)} files", verified=moved == len(plan),
                      evidence=f"{moved}/{len(plan)} moved", data={"moved": moved})


@tool_def(category="files", risk=2, timeout=300, verifier=_exists_verifier("dest_zip"))
def zip_paths(paths: list[str], dest_zip: PathArg) -> ToolResult:
    """Compress files/folders into a .zip."""
    dst = resolve_safe(dest_zip)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for raw in paths:
            p = resolve_safe(raw, must_exist=True)
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and not is_sensitive(f):
                        z.write(f, f.relative_to(p.parent))
            else:
                z.write(p, p.name)
    return ToolResult(ok=True, output=f"Created {dst}", data={"path": str(dst)})


@tool_def(category="files", risk=2, timeout=300, verifier=_exists_verifier("dest_folder"))
def unzip(zip_path: PathArg, dest_folder: PathArg) -> ToolResult:
    """Extract a .zip into a folder (blocks path traversal)."""
    src = resolve_safe(zip_path, must_exist=True)
    dst = resolve_safe(dest_folder)
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        for m in z.infolist():
            target = (dst / m.filename).resolve()
            if dst not in target.parents and target != dst:
                return ToolResult(ok=False, error=f"Unsafe path in archive: {m.filename}")
        z.extractall(dst)
    return ToolResult(ok=True, output=f"Extracted to {dst}", data={"path": str(dst)})


@tool_def(category="files", risk=1, untrusted_output=True, timeout=60)
def compare_documents(path_a: PathArg, path_b: PathArg) -> ToolResult:
    """Compare two documents (any readable type) and return a unified diff of their text."""
    a = read_any(resolve_safe(path_a, must_exist=True), 60000).splitlines()
    b = read_any(resolve_safe(path_b, must_exist=True), 60000).splitlines()
    diff = list(difflib.unified_diff(a, b, Path(path_a).name, Path(path_b).name, lineterm="", n=1))
    ratio = difflib.SequenceMatcher(None, "\n".join(a), "\n".join(b)).ratio()
    return ToolResult(ok=True, output="\n".join(diff[:800]) or "No differences.",
                      data={"similarity": round(ratio, 3), "changed_lines": sum(1 for d in diff if d[:1] in "+-")})


@tool_def(category="files", risk=2)
def open_path(path: PathArg) -> ToolResult:
    """Open a file or folder with its default Windows app (Explorer, Word, VS Code…)."""
    p = resolve_safe(path, must_exist=True)
    if hasattr(os, "startfile"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["xdg-open", str(p)])
    return ToolResult(ok=True, output=f"Opened {p}")
