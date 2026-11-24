"""
Sia AI — Utility Actions
=========================
Miscellaneous smart actions:

  read_clipboard          — read and return clipboard content
  save_clipboard_to_file  — dump clipboard to a timestamped txt on Desktop
  google_selected_text    — Ctrl+C selected text, then Google it
  list_open_apps          — list all visible window titles (running apps)
  close_all_except        — close all windows except the named one
  type_signature          — type a predefined email signature
  what_time_in            — return current time in a given timezone
  coin_flip               — heads or tails
  roll_dice               — roll an N-sided dice
  tell_joke               — ask LLM to tell a joke (async)
"""
from __future__ import annotations
import random
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyautogui
import pyperclip

from backend.core.logger import get_logger

log = get_logger("utility")

# ── Email signature — edit this to your own ───────────────────────────────────
_EMAIL_SIGNATURE = """
Best regards,
Ved Prathap Chawhan
Backend Engineer | NxtWave
LinkedIn: linkedin.com/in/vedprathap
""".strip()

# ── Timezone aliases — common city names → IANA timezone strings ──────────────
_TZ_ALIASES: dict[str, str] = {
    # India
    "india":          "Asia/Kolkata",
    "mumbai":         "Asia/Kolkata",
    "delhi":          "Asia/Kolkata",
    "hyderabad":      "Asia/Kolkata",
    "bangalore":      "Asia/Kolkata",
    "kolkata":        "Asia/Kolkata",
    "chennai":        "Asia/Kolkata",
    "ist":            "Asia/Kolkata",

    # USA
    "new york":       "America/New_York",
    "los angeles":    "America/Los_Angeles",
    "chicago":        "America/Chicago",
    "san francisco":  "America/Los_Angeles",
    "seattle":        "America/Los_Angeles",
    "est":            "America/New_York",
    "pst":            "America/Los_Angeles",
    "cst":            "America/Chicago",

    # Europe
    "london":         "Europe/London",
    "paris":          "Europe/Paris",
    "berlin":         "Europe/Berlin",
    "amsterdam":      "Europe/Amsterdam",
    "gmt":            "Etc/GMT",
    "utc":            "UTC",

    # Asia
    "tokyo":          "Asia/Tokyo",
    "beijing":        "Asia/Shanghai",
    "shanghai":       "Asia/Shanghai",
    "singapore":      "Asia/Singapore",
    "dubai":          "Asia/Dubai",
    "hong kong":      "Asia/Hong_Kong",
    "seoul":          "Asia/Seoul",

    # Australia
    "sydney":         "Australia/Sydney",
    "melbourne":      "Australia/Melbourne",

    # Americas
    "toronto":        "America/Toronto",
    "vancouver":      "America/Vancouver",
    "sao paulo":      "America/Sao_Paulo",
    "mexico city":    "America/Mexico_City",
}


# ── Clipboard ──────────────────────────────────────────────────────────────────

def read_clipboard() -> str:
    """Read and return current clipboard content."""
    try:
        text = pyperclip.paste()
        if not text.strip():
            return "Clipboard is empty"
        preview = text[:500]
        log.info("read_clipboard: %d chars", len(text))
        return f"Clipboard contains: {preview}{'...' if len(text) > 500 else ''}"
    except Exception as e:
        return f"Could not read clipboard: {e}"


def save_clipboard_to_file() -> str:
    """Save clipboard content to a timestamped .txt file on Desktop."""
    try:
        text = pyperclip.paste()
        if not text.strip():
            return "Clipboard is empty — nothing to save"

        from backend.actions.system_ops import resolve_path
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"clipboard_{ts}.txt"
        path     = resolve_path(f"~/Desktop/{filename}")
        path.write_text(text, encoding="utf-8")
        log.info("Clipboard saved to: %s", path)
        return f"Clipboard saved to Desktop as '{filename}'"

    except Exception as e:
        return f"Failed to save clipboard: {e}"


def google_selected_text() -> str:
    """
    Copy currently selected/highlighted text, then Google it.
    Simulates Ctrl+C to grab selection, then opens Google search.
    """
    try:
        # Clear clipboard first so we can detect if copy worked
        pyperclip.copy("")
        time.sleep(0.1)

        # Copy selected text
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.4)

        text = pyperclip.paste().strip()
        if not text:
            return "No text was selected — highlight some text first, then ask me to Google it"

        from backend.actions.browser_actions import search_web
        result = search_web(text, "google")
        log.info("google_selected_text: '%s'", text[:60])
        return f"Googling: '{text[:60]}{'...' if len(text) > 60 else ''}'"

    except Exception as e:
        return f"Failed to Google selected text: {e}"


# ── Window management ──────────────────────────────────────────────────────────

def list_open_apps() -> str:
    """
    List all visible windows (running apps) by their title.
    Filters out empty titles and system tray items.
    """
    try:
        import win32gui

        titles: list[str] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd).strip()
                if t and len(t) > 1:
                    titles.append(t)

        win32gui.EnumWindows(_cb, None)

        if not titles:
            return "No open windows found"

        # Deduplicate and sort
        unique = sorted(set(titles))
        log.info("list_open_apps: %d windows", len(unique))
        return "Open apps:\n" + "\n".join(f"• {t}" for t in unique)

    except ImportError:
        return "pywin32 not installed — pip install pywin32"
    except Exception as e:
        return f"Failed to list apps: {e}"


def close_all_except(keep: str) -> str:
    """
    Close all visible windows except the one whose title contains `keep`.
    Uses Alt+F4 on each window after bringing it to front.
    Skips: taskbar, desktop, Start menu, system windows.
    """
    keep_lower = keep.lower().strip()
    log.info("close_all_except: keeping '%s'", keep)

    _SYSTEM_SKIP = [
        "program manager", "windows input experience", "settings",
        "microsoft text", "taskbar", "start", "search",
    ]

    try:
        import win32gui
        import win32con

        to_close: list[tuple[int, str]] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd).strip()
                if not t:
                    return
                t_lower = t.lower()
                # Skip the window we want to keep
                if keep_lower in t_lower:
                    return
                # Skip system windows
                if any(skip in t_lower for skip in _SYSTEM_SKIP):
                    return
                to_close.append((hwnd, t))

        win32gui.EnumWindows(_cb, None)

        if not to_close:
            return f"Nothing to close — only '{keep}' is open"

        closed = []
        for hwnd, title in to_close:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.2)
                pyautogui.hotkey("alt", "f4")
                time.sleep(0.3)
                closed.append(title)
                log.info("Closed: %s", title)
            except Exception as e:
                log.warning("Could not close '%s': %s", title, e)

        return f"Closed {len(closed)} window(s), kept '{keep}'"

    except ImportError:
        return "pywin32 not installed — pip install pywin32"
    except Exception as e:
        return f"Failed to close windows: {e}"


# ── Productivity ───────────────────────────────────────────────────────────────

def type_signature() -> str:
    """Type the predefined email signature at current cursor position."""
    try:
        pyperclip.copy(_EMAIL_SIGNATURE)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        log.info("Typed email signature")
        return "Email signature typed"
    except Exception as e:
        return f"Failed to type signature: {e}"


def what_time_in(city: str) -> str:
    """
    Return the current time in a given city/timezone.
    Supports common city names and IANA timezone strings.
    """
    city_lower = city.lower().strip()

    # Try alias lookup first
    tz_name = _TZ_ALIASES.get(city_lower)

    # If not in aliases, try it directly as an IANA string
    if not tz_name:
        tz_name = city  # e.g. "America/New_York" passed directly

    try:
        tz      = ZoneInfo(tz_name)
        now     = datetime.now(tz)
        time_str = now.strftime("%I:%M %p, %A %d %B %Y")
        log.info("what_time_in: %s → %s (%s)", city, time_str, tz_name)
        return f"Current time in {city.title()}: {time_str}"

    except ZoneInfoNotFoundError:
        # List suggestions
        suggestions = [k for k in _TZ_ALIASES if city_lower in k]
        hint = f" Did you mean: {', '.join(suggestions[:3])}?" if suggestions else ""
        return f"Unknown timezone '{city}'.{hint} Try: Tokyo, London, New York, Dubai"
    except Exception as e:
        return f"Could not get time for '{city}': {e}"


# ── Fun ────────────────────────────────────────────────────────────────────────

def coin_flip() -> str:
    result = random.choice(["Heads", "Tails"])
    log.info("coin_flip: %s", result)
    return f"🪙 {result}!"


def roll_dice(sides: int = 6) -> str:
    sides  = max(2, min(100, int(sides)))
    result = random.randint(1, sides)
    log.info("roll_dice: d%d → %d", sides, result)
    return f"🎲 Rolled a d{sides}: {result}"


async def tell_joke() -> str:
    """Ask the LLM to tell a short, clean joke."""
    try:
        from backend.llm.llm_service import llm_service
        result = await llm_service.respond(
            "Tell me one short, clean, funny joke. Just the joke, no preamble.",
            history=[],
        )
        if result.get("type") == "chat":
            return result.get("message", "Why did the AI fail to tell a joke? Null pointer exception.")
        return str(result)
    except Exception as e:
        return f"Couldn't fetch a joke: {e}"