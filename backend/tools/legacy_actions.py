"""
SIA — Existing action library exposed as ONE typed tool: `system_action`.

All ~105 actions in backend/actions/system_actions.py keep working unchanged.
This wrapper adds what they lacked:
  • a risk level per action (power off → 3, delete → 4, volume → 2 …)
  • sandbox checks on file paths
  • failure detection (the old actions return strings like "Action failed: …")
  • verification for file operations (the path really exists / is gone)

The catalogue (action names + their params) is parsed from system_actions.py
source, so newly added `case` branches appear automatically.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from backend.security.permissions import Risk
from backend.security.sandbox import SandboxError, resolve_safe
from backend.tools.registry import ToolResult, tool_def

_SRC = Path(__file__).resolve().parents[1] / "actions" / "system_actions.py"


def _parse_catalogue() -> dict[str, list[str]]:
    src = _SRC.read_text(encoding="utf-8")
    cat: dict[str, list[str]] = {}
    for m in re.finditer(r'case\s+"([a-z_]+)"\s*:(.*)', src):
        cat[m.group(1)] = sorted(set(re.findall(r'params\.get\("([a-z_]+)"', m.group(2))))
    return cat


CATALOGUE: dict[str, list[str]] = _parse_catalogue()

_L1 = {
    "check_battery", "check_system", "what_is_playing", "read_file", "search_file", "list_files",
    "list_open_apps", "read_clipboard", "what_time_in", "coin_flip", "roll_dice", "tell_joke",
    "search_documents", "list_documents", "take_screenshot", "whatsapp_read", "ai_summarise_clipboard",
    "chrome_history", "chrome_downloads",
}
_L3 = {
    "whatsapp_send", "whatsapp_reply", "system_shutdown", "system_restart", "system_sleep",
    "close_all_except", "window_close", "delete_document", "type_signature",
}
_L4 = {"delete_file", "delete_folder", "empty_recycle_bin"}

# Actions the regex fast path may run instantly (no LLM, no approval)
FAST_PATH_SAFE = {a for a in CATALOGUE if a not in _L3 and a not in _L4}


def action_risk(action: str) -> int:
    if action in _L4:
        return Risk.DESTRUCTIVE
    if action in _L3:
        return Risk.CONSEQUENTIAL
    if action in _L1:
        return Risk.READ
    return Risk.LOW


_PATH_KEYS = ("path", "src", "dst", "location")
_FILE_ACTIONS = {"create_file", "read_file", "delete_file", "move_file", "copy_file", "rename_file",
                 "search_file", "list_files", "open_folder", "create_folder", "delete_folder",
                 "move_folder", "copy_folder", "ingest_file", "ingest_folder", "change_wallpaper"}
_FAIL_RE = re.compile(r"(\bfailed\b|\berror\b|unknown action|not found|couldn'?t|could not|cannot|unable to)", re.I)


def _norm(p: str) -> str:
    low = p.strip().replace("\\", "/").lower()
    for alias in ("desktop", "downloads", "documents", "pictures", "music", "videos"):
        if low == alias or low.startswith(alias + "/"):
            return "~/" + p.strip()
    return p


def _check_paths(action: str, params: dict) -> None:
    if action not in _FILE_ACTIONS:
        return
    for k in _PATH_KEYS:
        v = params.get(k)
        if not v or (action == "rename_file" and k == "dst"):
            continue
        resolve_safe(_norm(str(v)))


def _summary(args: dict) -> str:
    a, p = args.get("action", ""), args.get("params") or {}
    shown = ", ".join(f"{k}={str(v)[:80]}" for k, v in p.items())
    return f"{a}({shown})"


def _verify(args: dict, res: ToolResult) -> tuple[bool, str]:
    a, p = args["action"], args.get("params") or {}
    try:
        if a in {"create_file", "create_folder"}:
            path = resolve_safe(_norm(p.get("path", "")))
            return path.exists(), f"exists={path.exists()} {path}"
        if a in {"move_file", "move_folder", "copy_file", "copy_folder"}:
            dst = resolve_safe(_norm(p.get("dst", "")))
            return dst.exists(), f"destination exists={dst.exists()}"
        if a in {"delete_file", "delete_folder"}:
            path = resolve_safe(_norm(p.get("path", "")))
            return not path.exists(), f"still exists={path.exists()}"
    except Exception as e:
        return False, str(e)
    return True, "no verifier for this action (result string only)"


def _catalogue_text() -> str:
    lines = []
    for name, ps in CATALOGUE.items():
        lines.append(f"{name}({', '.join(ps)})" if ps else name)
    return "; ".join(lines)


@tool_def(
    category="system",
    risk=lambda a: action_risk(a.get("action", "")),
    summarize=_summary,
    verifier=_verify,
    timeout=45,
    description=(
        "Run one of SIA's built-in Windows desktop actions (apps, volume, brightness, media, windows, "
        "Chrome tab shortcuts, YouTube, files, power, reminders, clipboard AI, WhatsApp desktop, RAG). "
        "Pass the action name and its params. Available actions with params: " + _catalogue_text()
    ),
)
async def system_action(
    action: Annotated[str, Field(description="Action name from the catalogue, e.g. 'volume_up'")],
    params: Annotated[dict[str, Any], Field(description="Action parameters")] = {},
) -> ToolResult:
    params = params or {}
    if action not in CATALOGUE:
        return ToolResult(ok=False, error=f"Unknown action '{action}'.")
    try:
        _check_paths(action, params)
    except (SandboxError, FileNotFoundError) as e:
        return ToolResult(ok=False, error=str(e))
    from backend.actions.system_actions import execute_action   # Windows-only deps, import lazily
    out = await execute_action(action, params)
    out = str(out or "Done.")
    if _FAIL_RE.search(out) and not out.lower().startswith(("found", "here")):
        return ToolResult(ok=False, output=out, error=out)
    untrusted = action in {"read_file", "whatsapp_read", "read_clipboard", "search_documents"}
    if untrusted:
        from backend.security.injection import wrap_untrusted
        out = wrap_untrusted(out, source=action)
    return ToolResult(ok=True, output=out)
