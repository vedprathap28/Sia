"""
SIA — Browser tools (Playwright, DOM-first). Page content is untrusted and wrapped before the LLM sees it.
Workflow for the agent: browser_open → read the snapshot → browser_click / browser_fill using refs
→ check the returned snapshot (it says whether the page changed) → repeat.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote_plus

from pydantic import Field

from backend.core.config import settings
from backend.security.sandbox import resolve_safe
from backend.tools.browser.service import browser_service as B, format_snapshot
from backend.tools.registry import ToolResult, tool_def

Ref = Annotated[str, Field(description="Element ref from the latest snapshot, e.g. 'e12'")]


def _snap_result(s: dict, ok: bool = True, note: str = "") -> ToolResult:
    out = (note + "\n" if note else "") + format_snapshot(s)
    return ToolResult(ok=ok, output=out, needs_user=s.get("needs_user", ""),
                      data={"url": s.get("url"), "title": s.get("title")},
                      verified=s.get("changed") if "changed" in s else None,
                      evidence=f"url={s.get('url')}" if "changed" in s else "")


@tool_def(category="browser", risk=1, untrusted_output=True, timeout=60, retries=1)
async def browser_open(url: Annotated[str, Field(description="Full URL or domain, e.g. gmail.com")]) -> ToolResult:
    """Open a URL in SIA's browser (persistent, logged-in profile) and return a page snapshot."""
    s = await B.run("goto", url)
    return _snap_result(s)


_ENGINES = {"google": "https://www.google.com/search?q={}", "bing": "https://www.bing.com/search?q={}",
            "duckduckgo": "https://duckduckgo.com/?q={}", "youtube": "https://www.youtube.com/results?search_query={}"}


@tool_def(category="browser", risk=1, untrusted_output=True, timeout=60, retries=1)
async def browser_search(query: str, engine: Literal["google", "bing", "duckduckgo", "youtube"] = "google") -> ToolResult:
    """Search the web in the browser and return the results page snapshot (click results by ref)."""
    s = await B.run("goto", _ENGINES[engine].format(quote_plus(query)))
    return _snap_result(s)


@tool_def(category="browser", risk=1, untrusted_output=True, timeout=30)
async def browser_snapshot() -> ToolResult:
    """Re-read the current page: URL, title, interactive elements with refs, visible text."""
    return _snap_result(await B.run("snapshot"))


@tool_def(category="browser", risk=lambda a: B.click_risk(a.get("ref", "")), untrusted_output=True, timeout=45,
          summarize=lambda a: f"Click {B.describe_ref(a.get('ref',''))} on the current page")
async def browser_click(ref: Ref, double_click: bool = False) -> ToolResult:
    """Click an element by ref. Clicking submit/pay/send/book/apply/delete buttons needs approval."""
    s = await B.run("click", ref, double_click)
    return _snap_result(s, note="" if s.get("changed") else "Note: page did not visibly change.")


@tool_def(category="browser", risk=lambda a: 3 if a.get("submit") else 2, untrusted_output=True, timeout=45,
          summarize=lambda a: f"Type “{str(a.get('text'))[:120]}” into {B.describe_ref(a.get('ref',''))}"
                              + (" and press Enter (submit)" if a.get("submit") else ""))
async def browser_fill(ref: Ref, text: str, submit: Annotated[bool, Field(description="Press Enter after typing")] = False
                       ) -> ToolResult:
    """Type into an input/textarea/editable element. Never use for passwords or OTP codes."""
    try:
        s = await B.run("fill", ref, text, submit)
    except PermissionError as e:
        if str(e) == "payment_field":
            return ToolResult(ok=False, error="That is a payment field (card/UPI/bank). SIA never enters payment "
                                              "details. Stop here and tell the user the booking is ready to pay "
                                              "in the browser window.")
        return ToolResult(ok=False, needs_user="login_required",
                          error="That is a password/OTP field. Ask the user to type it themselves in the browser window.")
    return _snap_result(s, ok=bool(s.get("filled_value_matches", True)))


@tool_def(category="browser", risk=2, untrusted_output=True, timeout=30)
async def browser_select(ref: Ref, option: Annotated[str, Field(description="Visible option text or value")]) -> ToolResult:
    """Choose an option in a <select> dropdown."""
    return _snap_result(await B.run("select", ref, option))


@tool_def(category="browser", risk=lambda a: 3 if a.get("key", "").lower() == "enter" else 2, untrusted_output=True,
          timeout=30)
async def browser_press_key(key: Annotated[str, Field(description="e.g. Enter, Escape, Tab, ArrowDown, Control+F")]
                            ) -> ToolResult:
    """Press a key in the browser page."""
    return _snap_result(await B.run("press", key))


@tool_def(category="browser", risk=1, untrusted_output=True, timeout=30)
async def browser_scroll(direction: Literal["down", "up", "top", "bottom"] = "down") -> ToolResult:
    """Scroll the page and return the new snapshot."""
    return _snap_result(await B.run("scroll", direction))


@tool_def(category="browser", risk=1, untrusted_output=True, timeout=40)
async def browser_read_page(max_chars: Annotated[int, Field(ge=1000, le=40000)] = 15000) -> ToolResult:
    """Extract the main readable text and links of the current page (for reading articles, job descriptions…)."""
    d = await B.run("extract", max_chars)
    links = "\n".join(f"- {t} → {u}" for t, u in d["links"][:40])
    return ToolResult(ok=True, output=f"URL: {d['url']}\nTitle: {d['title']}\n\n{d['text']}\n\nLinks:\n{links}",
                      data={"url": d["url"], "title": d["title"]})


@tool_def(category="browser", risk=1, timeout=40)
async def browser_tabs(action: Literal["list", "new", "switch", "close"] = "list", index: int = 0,
                       url: Annotated[str, Field(description="URL for action=new")] = "") -> ToolResult:
    """List, open, switch to or close browser tabs (for comparing several pages)."""
    d = await B.run("tabs", action, index, url)
    if "tabs" not in d:      # 'new' with url returns a snapshot
        return _snap_result(d)
    return ToolResult(ok=True, output="\n".join(
        f"[{t['index']}]{'*' if t['active'] else ' '} {t['title']} — {t['url']}" for t in d["tabs"]), data=d)


@tool_def(category="browser", risk=1, untrusted_output=True, timeout=30)
async def browser_back() -> ToolResult:
    """Go back to the previous page."""
    return _snap_result(await B.run("back"))


@tool_def(category="browser", risk=2, timeout=90,
          verifier=lambda a, r: (Path(r.data.get("path", "")).exists(), f"saved {r.data.get('size', 0)} bytes"))
async def browser_download(ref: Annotated[str, Field(description="Ref of the link/button that starts the download")]
                           ) -> ToolResult:
    """Click a download link/button and save the file to the Downloads folder."""
    d = await B.run("download", ref, settings.DOWNLOAD_DIR)
    return ToolResult(ok=True, output=f"Downloaded {d['path']}", data=d)


@tool_def(category="browser", risk=3, untrusted_output=True, timeout=60,
          summarize=lambda a: f"Upload {', '.join(a.get('paths', []))} via {B.describe_ref(a.get('ref',''))}")
async def browser_upload(ref: Annotated[str, Field(description="Ref of the file input or upload button")],
                         paths: list[str]) -> ToolResult:
    """Upload local file(s) to a page (e.g. a resume to a job form). Needs approval: it shares your files."""
    safe = [str(resolve_safe(p, must_exist=True)) for p in paths]
    return _snap_result(await B.run("upload", ref, safe))


@tool_def(category="browser", risk=1, timeout=30)
async def browser_screenshot(full_page: bool = False) -> ToolResult:
    """Save a screenshot of the current page (path returned) — use computer tools to *analyse* it visually."""
    out = Path(settings.WORKSPACE_DIR) / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    path = str(out / f"browser_{int(time.time())}.png")
    d = await B.run("screenshot", path, full_page)
    return ToolResult(ok=True, output=f"Saved {d['path']}", data=d)


@tool_def(category="browser", risk=1, untrusted_output=True, timeout=40)
async def browser_wait_for_text(text: str, timeout_s: Annotated[int, Field(le=60)] = 20) -> ToolResult:
    """Wait until some text appears on the page (after a slow action or once the user has logged in)."""
    try:
        return _snap_result(await B.run("wait_text", text, timeout_s))
    except Exception:
        return ToolResult(ok=False, error=f"'{text}' did not appear within {timeout_s}s")
