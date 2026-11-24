"""
SIA — Development tools: inspect repos, search code, edit with diffs, run allow-listed commands/tests, git.
Commands never go through a shell; see security/sandbox.check_command for the allowlist and risk levels.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import Field

from backend.security.sandbox import check_command, is_sensitive, resolve_safe
from backend.tools.registry import ToolResult, tool_def

_SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build", ".next", ".idea",
              ".vscode", ".pytest_cache", "target", "chroma_db"}
_MAX_OUT = 12000


@tool_def(category="dev", risk=1, untrusted_output=True, timeout=60)
def inspect_project(path: Annotated[str, Field(description="Project root folder")]) -> ToolResult:
    """Overview of a code project: folder tree (3 levels), detected stack, entry points, README start."""
    root = resolve_safe(path, must_exist=True)
    tree, count = [], 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth > 3:
            dirs[:] = []
            continue
        tree.append("  " * depth + Path(dirpath).name + "/")
        for f in sorted(files)[:40]:
            tree.append("  " * (depth + 1) + f)
            count += 1
        if len(tree) > 400:
            tree.append("…"); break
    markers = {"package.json": "Node/JS", "requirements.txt": "Python", "pyproject.toml": "Python",
               "pom.xml": "Java/Maven", "build.gradle": "Java/Gradle", "go.mod": "Go", "Cargo.toml": "Rust",
               "vite.config.js": "Vite", "manage.py": "Django", "Dockerfile": "Docker"}
    stack = sorted({v for k, v in markers.items() if list(root.rglob(k))[:1]})
    readme = next((p for p in root.glob("README*")), None)
    head = readme.read_text(encoding="utf-8", errors="replace")[:3000] if readme else "(no README)"
    out = f"Stack: {', '.join(stack) or 'unknown'}\n\nTree:\n" + "\n".join(tree) + f"\n\nREADME:\n{head}"
    return ToolResult(ok=True, output=out, data={"stack": stack, "files_listed": count})


@tool_def(category="dev", risk=1, untrusted_output=True, timeout=60)
def search_code(path: Annotated[str, Field(description="Folder to search")],
                pattern: Annotated[str, Field(description="Regular expression")],
                glob: Annotated[str, Field(description="File glob, e.g. '*.py'")] = "*",
                max_hits: Annotated[int, Field(le=300)] = 80) -> ToolResult:
    """Search code with a regex; returns file:line: text."""
    root = resolve_safe(path, must_exist=True)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return ToolResult(ok=False, error=f"Bad regex: {e}")
    hits = []
    for f in root.rglob(glob):
        if not f.is_file() or any(p in _SKIP_DIRS for p in f.parts) or is_sensitive(f) or f.stat().st_size > 2_000_000:
            continue
        try:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= max_hits:
                        break
        except OSError:
            continue
        if len(hits) >= max_hits:
            break
    return ToolResult(ok=True, output="\n".join(hits) or "No matches.", data={"hits": len(hits)})


@tool_def(category="dev", risk=1, untrusted_output=True)
def read_code(path: Annotated[str, Field(description="File path")], start_line: int = 1,
              end_line: Annotated[int, Field(description="0 = to the end (max 600 lines)")] = 0) -> ToolResult:
    """Read a source file with line numbers."""
    p = resolve_safe(path, must_exist=True)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    end = len(lines) if end_line <= 0 else min(end_line, len(lines))
    end = min(end, start_line - 1 + 600)
    body = "\n".join(f"{i:>5}  {lines[i-1]}" for i in range(max(1, start_line), end + 1))
    return ToolResult(ok=True, output=body, data={"total_lines": len(lines)})


def _drop_stale_bytecode(p: Path) -> None:
    """Python caches bytecode by mtime+size; an edit of equal length within the same second would
    otherwise run the OLD code in the next test run."""
    if p.suffix == ".py":
        for pyc in (p.parent / "__pycache__").glob(f"{p.stem}.*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass


def _edit_diff(a: dict) -> str:
    try:
        p = resolve_safe(a["path"], must_exist=True)
        old = p.read_text(encoding="utf-8")
        new = old.replace(a["old_text"], a["new_text"], 1)
        d = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), p.name, p.name, lineterm="", n=2))
        return f"Edit {p}:\n{d[:3000]}"
    except Exception as e:
        return f"Edit {a.get('path')} (diff unavailable: {e})"


def _edit_verify(args, res):
    p = resolve_safe(args["path"])
    return args["new_text"] in p.read_text(encoding="utf-8"), "new text present in file"


@tool_def(category="dev", risk=3, summarize=_edit_diff, verifier=_edit_verify)
def edit_code(path: Annotated[str, Field(description="File to edit")],
              old_text: Annotated[str, Field(description="Exact existing text to replace (must be unique)")],
              new_text: Annotated[str, Field(description="Replacement text")]) -> ToolResult:
    """Replace one exact snippet in a file. The user approves the diff before it is applied."""
    p = resolve_safe(path, must_exist=True)
    src = p.read_text(encoding="utf-8")
    n = src.count(old_text)
    if n != 1:
        return ToolResult(ok=False, error=f"old_text found {n} times; it must match exactly once. Read the file again.")
    shutil.copy2(p, p.with_suffix(p.suffix + ".sia.bak"))
    p.write_text(src.replace(old_text, new_text, 1), encoding="utf-8")
    _drop_stale_bytecode(p)
    return ToolResult(ok=True, output=f"Edited {p} (backup: {p.name}.sia.bak)")


@tool_def(category="dev", risk=lambda a: 3 if resolve_safe(a["path"]).exists() else 2,
          summarize=lambda a: f"Write {len(a.get('content',''))} chars to {a.get('path')}",
          verifier=lambda a, r: (resolve_safe(a["path"]).exists(), "file exists"))
def write_code_file(path: Annotated[str, Field(description="File to create (overwriting needs approval)")],
                    content: str) -> ToolResult:
    """Create a new source file, or overwrite one (approval required when it already exists)."""
    p = resolve_safe(path)
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + ".sia.bak"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _drop_stale_bytecode(p)
    return ToolResult(ok=True, output=f"Wrote {p}")


def _run(argv: list[str], cwd: Path, timeout: int) -> ToolResult:
    exe = shutil.which(argv[0]) or argv[0]
    env = {k: v for k, v in os.environ.items()
           if not re.search(r"(KEY|TOKEN|SECRET|PASSWORD)", k, re.I)}   # don't leak SIA's keys to child processes
    try:
        proc = subprocess.run([exe, *argv[1:]], cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, env=env, shell=False, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "")[-_MAX_OUT:] if isinstance(e.stdout, str) else ""
        return ToolResult(ok=False, output=out, error=f"Timed out after {timeout}s")
    except FileNotFoundError:
        return ToolResult(ok=False, error=f"'{argv[0]}' is not installed or not on PATH")
    out = (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")
    if len(out) > _MAX_OUT:
        out = out[:3000] + "\n…\n" + out[-(_MAX_OUT - 3000):]
    return ToolResult(ok=proc.returncode == 0, output=out, error="" if proc.returncode == 0 else f"exit code {proc.returncode}",
                      data={"exit_code": proc.returncode})


def _cmd_risk(a: dict) -> int:
    c = check_command(a.get("command", ""))
    return c.risk if c.ok else 4


@tool_def(category="dev", risk=_cmd_risk, timeout=660, untrusted_output=True,
          summarize=lambda a: f"Run `{a.get('command')}` in {a.get('cwd')}")
def run_command(command: Annotated[str, Field(description="One command, no pipes/&&, e.g. 'npm run build'")],
                cwd: Annotated[str, Field(description="Working directory (project folder)")],
                timeout_s: Annotated[int, Field(ge=5, le=600)] = 120) -> ToolResult:
    """Run one allow-listed command (python, pytest, npm, node, git, pip…) and return its output.
    Installs, pushes and inline code need approval; dangerous commands are blocked."""
    chk = check_command(command)
    if not chk.ok:
        return ToolResult(ok=False, error=chk.reason)
    return _run(chk.argv, resolve_safe(cwd, must_exist=True), timeout_s)


@tool_def(category="dev", risk=2, timeout=660, untrusted_output=True)
def run_tests(project: Annotated[str, Field(description="Project root")],
              target: Annotated[str, Field(description="Optional test path / name filter")] = "") -> ToolResult:
    """Run the project's tests (pytest for Python, `npm test` for Node) and report pass/fail."""
    root = resolve_safe(project, must_exist=True)
    if (root / "package.json").exists() and not list(root.glob("**/test_*.py"))[:1]:
        argv = ["npm", "test", "--", target] if target else ["npm", "test"]
        env_note = "npm test"
    else:
        argv = ["python", "-m", "pytest", "-q", "--no-header", "-x"] + ([target] if target else [])
        env_note = "pytest"
    res = _run(argv, root, 600)
    m = re.search(r"(\d+) passed", res.output)
    f = re.search(r"(\d+) failed", res.output)
    res.data.update({"runner": env_note, "passed": int(m.group(1)) if m else None, "failed": int(f.group(1)) if f else 0})
    res.verified, res.evidence = res.ok, f"exit code {res.data.get('exit_code')}"
    return res


@tool_def(category="dev", risk=1, timeout=30)
def git_overview(repo: Annotated[str, Field(description="Repository folder")]) -> ToolResult:
    """git status, current branch, last 10 commits and diff stat (read-only)."""
    root = resolve_safe(repo, must_exist=True)
    parts = []
    for argv in (["git", "status", "--short", "--branch"], ["git", "log", "--oneline", "-10"], ["git", "diff", "--stat"]):
        r = _run(argv, root, 20)
        parts.append(f"$ {' '.join(argv)}\n{r.output or r.error}")
    return ToolResult(ok=True, output="\n\n".join(parts))
