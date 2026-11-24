"""
SIA — File-system and command sandbox.

* Files: every path a tool touches is resolved and must sit inside an allowed root.
  Secrets (.ssh, .env, browser credential stores, keys) are never readable.
* Commands: no shell. The first word must be on an allowlist; shell metacharacters
  are rejected; each command gets a risk level (git push / pip install → 3, etc.).
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from backend.core.config import settings


class SandboxError(PermissionError):
    pass


# ── Files ─────────────────────────────────────────────────────────────────────

_SENSITIVE_PARTS = {
    ".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker",
    "login data", "cookies", "local state",          # browser credential stores
    "credentials", "secrets",
}
_SENSITIVE_NAMES = re.compile(
    r"(^\.env(\..*)?$|\.pem$|\.key$|\.pfx$|\.p12$|id_rsa|id_ed25519|\.kdbx$|credentials\.json$|token\.json$)",
    re.IGNORECASE,
)


def allowed_roots() -> list[Path]:
    raw = settings.ALLOWED_ROOTS or getattr(settings, "_ALLOWED_ROOTS_OVERRIDE", "")
    if raw:
        roots = [Path(os.path.expandvars(r)).expanduser() for r in raw.split(";") if r.strip()]
    else:
        home = Path.home()
        roots = [home / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")]
        roots += [home / "OneDrive" / d for d in ("Desktop", "Documents")]
    roots.append(Path(settings.WORKSPACE_DIR))
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except Exception:
            pass
    return out


def is_sensitive(p: Path) -> bool:
    parts = {x.lower() for x in p.parts}
    return bool(parts & _SENSITIVE_PARTS) or bool(_SENSITIVE_NAMES.search(p.name))


def resolve_safe(path: str | os.PathLike, *, must_exist: bool = False) -> Path:
    """Resolve a user/LLM-supplied path and enforce the sandbox."""
    if not str(path).strip():
        raise SandboxError("Empty path")
    s = os.path.expandvars(str(path)).strip().strip('"')
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = Path(settings.WORKSPACE_DIR) / p
    p = p.resolve()
    if is_sensitive(p):
        raise SandboxError(f"Access to sensitive file is not allowed: {p.name}")
    if not any(p == r or r in p.parents for r in allowed_roots()):
        raise SandboxError(
            f"{p} is outside the allowed folders. Allowed: " + ", ".join(str(r) for r in allowed_roots()))
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p


# ── Commands ──────────────────────────────────────────────────────────────────

_METACHARS = re.compile(r"[;&|`<>]|\$\(|\$\{|\n")

# first word → base risk
_BASE_ALLOW = {
    "python": 2, "python3": 2, "py": 2, "pytest": 2, "node": 2, "npm": 2, "pnpm": 2, "yarn": 2,
    "npx": 3, "pip": 2, "pip3": 2, "git": 2, "uvicorn": 2, "ruff": 1, "black": 2, "mypy": 1,
    "tsc": 2, "go": 2, "cargo": 2, "mvn": 2, "gradle": 2, "dotnet": 2, "java": 2, "javac": 2,
    "ls": 1, "dir": 1, "pwd": 1, "echo": 1, "where": 1, "which": 1, "type": 1, "cat": 1,
    "code": 2,
}
_BLOCKED = {"rm", "del", "rmdir", "rd", "format", "shutdown", "reg", "regedit", "powershell", "pwsh",
            "cmd", "bash", "sh", "sudo", "su", "chmod", "chown", "mkfs", "dd", "diskpart", "bcdedit",
            "netsh", "sc", "taskkill", "curl", "wget", "certutil", "bitsadmin", "schtasks", "wmic"}

_GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "stash list"}
_GIT_WRITE = {"add", "commit", "checkout", "switch", "pull", "fetch", "merge", "stash", "restore", "init", "clone"}
_GIT_DESTRUCTIVE = {"push", "reset", "clean", "rebase", "filter-branch", "gc", "prune"}


@dataclass
class CommandCheck:
    ok: bool
    argv: list[str]
    risk: int
    reason: str = ""


def check_command(command: str) -> CommandCheck:
    cmd = command.strip()
    if not cmd:
        return CommandCheck(False, [], 4, "Empty command")
    if _METACHARS.search(cmd):
        return CommandCheck(False, [], 4, "Shell operators (; & | > < ` $()) are not allowed. Run one command at a time.")
    try:
        argv = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError as e:
        return CommandCheck(False, [], 4, f"Could not parse command: {e}")
    argv = [a.strip('"') for a in argv]
    exe = Path(argv[0]).name.lower().removesuffix(".exe")
    extra = {c.strip().lower() for c in settings.EXTRA_ALLOWED_COMMANDS.split(";") if c.strip()}
    if exe in _BLOCKED and exe not in extra:
        return CommandCheck(False, argv, 4, f"'{exe}' is blocked for safety.")
    if exe not in _BASE_ALLOW and exe not in extra:
        return CommandCheck(False, argv, 4, f"'{exe}' is not on the allowed command list. "
                                            "Add it to EXTRA_ALLOWED_COMMANDS in .env if you trust it.")
    risk = _BASE_ALLOW.get(exe, 3)
    sub = argv[1].lower() if len(argv) > 1 else ""
    joined = " ".join(a.lower() for a in argv[1:])
    if exe == "git":
        forced = "--force" in joined or "-f" in argv[2:] or "--hard" in joined
        if sub in _GIT_DESTRUCTIVE:
            risk = 4 if (sub in {"reset", "clean", "filter-branch"} or forced) else 3
        elif sub in _GIT_WRITE:
            risk = 2 if sub in {"add", "fetch"} else 3
        elif sub in _GIT_READ:
            risk = 1
        else:
            risk = 3
    elif exe in {"pip", "pip3"} or (exe in {"python", "python3", "py"} and "-m pip" in joined):
        if "install" in joined or "uninstall" in joined:
            risk = 3
    elif exe in {"npm", "pnpm", "yarn"}:
        if sub in {"install", "i", "add", "remove", "uninstall", "publish", "update"}:
            risk = 3
    elif exe in {"python", "python3", "py", "node"} and len(argv) > 1 and argv[1] in {"-c", "-e"}:
        risk = 3   # inline code: always ask
    return CommandCheck(True, argv, risk)
