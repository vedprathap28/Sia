"""
SIA — Computer-use tools (Windows desktop).

Preferred order for the agent:
  1. Windows UI Automation (uia_*)   — exact controls by name, no coordinates
  2. keyboard shortcuts (computer_hotkey)
  3. vision: computer_locate → computer_click(x, y)
Every input action takes a before/after screenshot and reports how much of the screen changed,
so the agent can tell whether the action did anything.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Annotated, Literal

from pydantic import Field

from backend.tools.computer import screen
from backend.tools.registry import ToolResult, tool_def

_RISKY = re.compile(r"\b(send|submit|pay|buy|order|delete|remove|post|publish|confirm|book|apply|transfer|uninstall)\b", re.I)
_RISKY_KEYS = {"alt+f4", "ctrl+enter", "ctrl+shift+delete", "shift+delete", "win+r", "win+x", "ctrl+alt+delete"}


def _pg():
    import pyautogui
    pyautogui.FAILSAFE = True          # slam the mouse into a corner to abort
    pyautogui.PAUSE = 0.05
    return pyautogui


async def _with_change(action) -> ToolResult:
    before = await asyncio.to_thread(screen.grab)
    await asyncio.to_thread(action)
    await asyncio.sleep(0.7)
    after = await asyncio.to_thread(screen.grab)
    ratio = screen.change_ratio(before, after)
    return ToolResult(ok=True, output=f"Done. Screen changed {ratio:.0%}.", verified=ratio > 0.002,
                      evidence=f"screen change {ratio:.1%}", data={"screen_change": round(ratio, 4)})


# ── Seeing ────────────────────────────────────────────────────────────────────

@tool_def(category="computer", risk=1, timeout=20)
def computer_screenshot() -> ToolResult:
    """Capture the whole screen to a PNG (path returned). Use computer_look to understand it."""
    img = screen.grab()
    return ToolResult(ok=True, output=f"Saved {screen.save(img)} ({img.width}x{img.height})",
                      data={"width": img.width, "height": img.height})


@tool_def(category="computer", risk=1, timeout=60, untrusted_output=True)
async def computer_look(question: Annotated[str, Field(description=(
        "What you need to know, e.g. 'Which app is in front and is there an error dialog?'"))]) -> ToolResult:
    """Look at the current screen with a vision model and answer a question about it."""
    img = await asyncio.to_thread(screen.grab)
    ans = await screen.vision(question, img)
    return ToolResult(ok=True, output=ans)


@tool_def(category="computer", risk=1, timeout=60)
async def computer_locate(target: Annotated[str, Field(description="The UI element to find, e.g. 'the blue Save button'")]
                          ) -> ToolResult:
    """Find an element on screen visually; returns screen x,y to use with computer_click."""
    img = await asyncio.to_thread(screen.grab)
    raw = await screen.vision(
        "The image has a red grid labelled 0-1000 on both axes. Find: " + target +
        '. Reply JSON {"found": true|false, "x": 0-1000, "y": 0-1000, "label": "visible text of the element"} '
        "with x,y at the centre of the element.", img, grid=True, json_mode=True)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    if not d.get("found"):
        return ToolResult(ok=False, error=f"Could not find {target} on screen.")
    x, y = int(d["x"] / 1000 * img.width), int(d["y"] / 1000 * img.height)
    return ToolResult(ok=True, output=f"Found '{d.get('label', target)}' at x={x}, y={y}",
                      data={"x": x, "y": y, "label": d.get("label", "")})


@tool_def(category="computer", risk=1, timeout=60, untrusted_output=True)
def computer_read_text() -> ToolResult:
    """OCR all text visible on screen (needs Tesseract). Prefer uia_list_controls or computer_look."""
    return ToolResult(ok=True, output=screen.ocr(screen.grab()))


# ── Windows / apps ────────────────────────────────────────────────────────────

def _windows() -> list[dict]:
    import pygetwindow as gw
    out = []
    for w in gw.getAllWindows():
        if w.title.strip() and w.width > 50:
            out.append({"title": w.title, "active": w.isActive, "minimized": w.isMinimized,
                        "rect": [w.left, w.top, w.width, w.height]})
    return out


@tool_def(category="computer", risk=1)
def list_windows() -> ToolResult:
    """List open windows (title, active, minimised) and which one is in front."""
    ws = _windows()
    active = next((w["title"] for w in ws if w["active"]), "")
    return ToolResult(ok=True, output=f"Active: {active}\n" + "\n".join(
        f"{'*' if w['active'] else ' '} {w['title']}{' (minimised)' if w['minimized'] else ''}" for w in ws),
        data={"active": active, "count": len(ws)})


def _focus_verify(args, res):
    try:
        import pygetwindow as gw
        a = gw.getActiveWindow()
        t = a.title if a else ""
        return args["title"].lower() in t.lower(), f"active window: {t}"
    except Exception as e:
        return False, str(e)


@tool_def(category="computer", risk=2, verifier=_focus_verify)
def focus_window(title: Annotated[str, Field(description="Part of the window title, e.g. 'Visual Studio Code'")]
                 ) -> ToolResult:
    """Bring a window to the front (restores it if minimised)."""
    import pygetwindow as gw
    wins = [w for w in gw.getAllWindows() if title.lower() in w.title.lower() and w.title.strip()]
    if not wins:
        return ToolResult(ok=False, error=f"No window titled like '{title}'.")
    w = wins[0]
    if w.isMinimized:
        w.restore()
    try:
        w.activate()
    except Exception:
        _pg().press("alt"); w.activate()
    return ToolResult(ok=True, output=f"Focused {w.title}")


@tool_def(category="computer", risk=2, timeout=30,
          verifier=lambda a, r: (True, "launched (check with list_windows)"))
def launch_app(name: Annotated[str, Field(description="App name, e.g. 'notepad', 'vscode', 'excel', 'chrome'")]
               ) -> ToolResult:
    """Open a desktop application by name (Start-menu search fallback)."""
    from backend.actions.app_launcher import launch_app as _launch
    out = _launch(name)
    return ToolResult(ok="fail" not in out.lower() and "not found" not in out.lower(), output=out)


# ── UI Automation (pywinauto) ─────────────────────────────────────────────────

def _uia_window(title: str):
    from pywinauto import Desktop
    wins = [w for w in Desktop(backend="uia").windows() if title.lower() in (w.window_text() or "").lower()]
    if not wins:
        raise LookupError(f"No window titled like '{title}'")
    return wins[0]


@tool_def(category="computer", risk=1, timeout=40, untrusted_output=True)
def uia_list_controls(window_title: str,
                      control_type: Annotated[str, Field(description="Filter: Button, Edit, MenuItem, ListItem, TabItem, Hyperlink… or empty")] = "",
                      limit: Annotated[int, Field(le=400)] = 150) -> ToolResult:
    """List clickable/typeable controls in a window via Windows UI Automation (name, type, automation id)."""
    if sys.platform != "win32":
        return ToolResult(ok=False, error="UI Automation is only available on Windows")
    w = _uia_window(window_title)
    out = []
    for c in w.descendants():
        try:
            ct = c.element_info.control_type
            if control_type and ct != control_type:
                continue
            name = (c.window_text() or "").strip()
            if not name and ct not in ("Edit", "Document"):
                continue
            out.append(f"{ct}: '{name[:80]}' id={c.element_info.automation_id}")
        except Exception:
            continue
        if len(out) >= limit:
            break
    return ToolResult(ok=True, output=f"Window: {w.window_text()}\n" + "\n".join(out), data={"count": len(out)})


def _uia_find(window_title: str, name: str, control_type: str):
    w = _uia_window(window_title)
    crit = {"title_re": f"(?i).*{re.escape(name)}.*"}
    if control_type:
        crit["control_type"] = control_type
    return w.child_window(**crit, found_index=0).wrapper_object()


@tool_def(category="computer", risk=lambda a: 3 if _RISKY.search(a.get("name", "")) else 2, timeout=30,
          summarize=lambda a: f"Click '{a.get('name')}' ({a.get('control_type') or 'any'}) in '{a.get('window_title')}'")
async def uia_click(window_title: str, name: Annotated[str, Field(description="Control name (from uia_list_controls)")],
                    control_type: str = "") -> ToolResult:
    """Click a control by its accessible name using UI Automation (most reliable desktop click)."""
    if sys.platform != "win32":
        return ToolResult(ok=False, error="UI Automation is only available on Windows")
    ctl = await asyncio.to_thread(_uia_find, window_title, name, control_type)

    def act():
        try:
            ctl.invoke()
        except Exception:
            ctl.click_input()
    return await _with_change(act)


@tool_def(category="computer", risk=2, timeout=30,
          summarize=lambda a: f"Type “{str(a.get('text'))[:100]}” into '{a.get('name')}' in '{a.get('window_title')}'")
async def uia_set_text(window_title: str, name: str, text: str, control_type: str = "Edit") -> ToolResult:
    """Set the text of an edit box by name using UI Automation."""
    if sys.platform != "win32":
        return ToolResult(ok=False, error="UI Automation is only available on Windows")
    ctl = await asyncio.to_thread(_uia_find, window_title, name, control_type)

    def act():
        try:
            ctl.set_edit_text(text)
        except Exception:
            ctl.click_input(); ctl.type_keys(text, with_spaces=True, with_newlines=True, pause=0.01)
    res = await _with_change(act)
    try:
        val = ctl.window_text() if hasattr(ctl, "window_text") else ""
        res.verified = text[:30] in (val or "") or res.verified
    except Exception:
        pass
    return res


# ── Mouse & keyboard ──────────────────────────────────────────────────────────

@tool_def(category="computer", risk=lambda a: 3 if _RISKY.search(a.get("target_description", "")) else 2, timeout=20,
          summarize=lambda a: f"{a.get('button','left').title()} {'double-' if a.get('double') else ''}click at "
                              f"({a.get('x')}, {a.get('y')}) — {a.get('target_description','')}")
async def computer_click(x: int, y: int,
                         target_description: Annotated[str, Field(description="What you are clicking (shown for approval)")],
                         button: Literal["left", "right", "middle"] = "left", double: bool = False) -> ToolResult:
    """Click at screen coordinates (get them from computer_locate). Describe the target honestly."""
    pg = _pg()
    return await _with_change(lambda: pg.click(x, y, clicks=2 if double else 1, button=button, interval=0.08))


@tool_def(category="computer", risk=2, timeout=60,
          summarize=lambda a: f"Type “{str(a.get('text'))[:120]}” into the focused window")
async def computer_type(text: str, press_enter: bool = False) -> ToolResult:
    """Type text into whatever has keyboard focus (uses the clipboard for non-ASCII like Hindi/Telugu)."""
    pg = _pg()

    def act():
        if text.isascii():
            pg.write(text, interval=0.01)
        else:
            import pyperclip
            old = pyperclip.paste()
            pyperclip.copy(text); pg.hotkey("ctrl", "v")
            pyperclip.copy(old)
        if press_enter:
            pg.press("enter")
    return await _with_change(act)


@tool_def(category="computer", risk=lambda a: 3 if a.get("keys", "").lower().replace(" ", "") in _RISKY_KEYS else 2,
          timeout=20, summarize=lambda a: f"Press {a.get('keys')}")
async def computer_hotkey(keys: Annotated[str, Field(description="e.g. 'ctrl+s', 'alt+tab', 'enter', 'win'")]
                          ) -> ToolResult:
    """Press a key or shortcut."""
    pg = _pg()
    parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
    parts = ["winleft" if k in ("win", "windows") else k for k in parts]
    return await _with_change(lambda: pg.hotkey(*parts) if len(parts) > 1 else pg.press(parts[0]))


@tool_def(category="computer", risk=1, timeout=20)
async def computer_scroll(amount: Annotated[int, Field(description="Positive = up, negative = down (clicks)")] = -5,
                          x: int | None = None, y: int | None = None) -> ToolResult:
    """Scroll the mouse wheel (optionally at a position)."""
    pg = _pg()
    return await _with_change(lambda: pg.scroll(amount * 100, x=x, y=y))


@tool_def(category="computer", risk=2, timeout=30,
          summarize=lambda a: f"Drag from ({a.get('from_x')},{a.get('from_y')}) to ({a.get('to_x')},{a.get('to_y')})")
async def computer_drag(from_x: int, from_y: int, to_x: int, to_y: int) -> ToolResult:
    """Drag and drop with the mouse."""
    pg = _pg()

    def act():
        pg.moveTo(from_x, from_y, duration=0.2)
        pg.dragTo(to_x, to_y, duration=0.6, button="left")
    return await _with_change(act)


@tool_def(category="computer", risk=1, timeout=40)
async def computer_wait(seconds: Annotated[float, Field(ge=0.5, le=30)] = 2.0) -> ToolResult:
    """Wait for an app to load, then report how much the screen changed."""
    before = await asyncio.to_thread(screen.grab)
    await asyncio.sleep(seconds)
    after = await asyncio.to_thread(screen.grab)
    r = screen.change_ratio(before, after)
    return ToolResult(ok=True, output=f"Waited {seconds}s; screen changed {r:.0%}.", data={"screen_change": r})
