"""
Sia AI — WhatsApp Automation (Windows Desktop App)
====================================================
Uses pyautogui to control the WhatsApp Desktop app.

Supported actions:
  whatsapp_send(contact, message)   — open chat, send message
  whatsapp_read(contact, n)         — open chat, read last N messages via clipboard
  whatsapp_reply(contact, message)  — open chat, reply to last message

Flow for all actions:
  1. Launch WhatsApp Desktop (if not already open) via app_launcher
  2. Wait for app to be ready
  3. Press Ctrl+F to open search
  4. Type contact name
  5. Wait for results, press Enter to open first match
  6. Perform the action (send / read / reply)

Limitations:
  - Contact name must match exactly enough for WhatsApp search to find them
  - Read uses clipboard (Ctrl+A + Ctrl+C) on the chat area — may grab extra UI text
  - Works on WhatsApp Desktop (Windows Store version)
"""
from __future__ import annotations
import time
import pyautogui
import pyperclip

from backend.core.logger import get_logger

log = get_logger("whatsapp")

# Timing constants — adjust if WhatsApp is slow to respond on your machine
_APP_LAUNCH_WAIT  = 3.0   # seconds to wait after launching WhatsApp
_SEARCH_WAIT      = 1.5   # seconds to wait after typing contact name
_CHAT_OPEN_WAIT   = 1.0   # seconds to wait after opening a chat
_TYPE_INTERVAL    = 0.04  # seconds between each keystroke when typing


def _ensure_whatsapp_open() -> bool:
    """
    Check if WhatsApp Desktop window is already open.
    If not, launch it and wait for it to load.
    Returns True if WhatsApp is ready.
    """
    try:
        import win32gui

        found: list[int] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if "WhatsApp" in t:
                    found.append(hwnd)

        win32gui.EnumWindows(_cb, None)

        if found:
            # Already open — bring to front
            import win32con
            hwnd = found[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            log.info("WhatsApp already open (hwnd=%s)", hwnd)
            return True

    except Exception as e:
        log.warning("win32gui check failed: %s", e)

    # Not open — launch it
    log.info("WhatsApp not open — launching...")
    from backend.actions.app_launcher import launch_app
    launch_app("whatsapp")
    time.sleep(_APP_LAUNCH_WAIT)

    # Try to bring to front after launch
    try:
        import win32gui
        import win32con

        found = []
        win32gui.EnumWindows(
            lambda hwnd, _: found.append(hwnd)
            if win32gui.IsWindowVisible(hwnd) and "WhatsApp" in win32gui.GetWindowText(hwnd)
            else None,
            None,
        )
        if found:
            win32gui.ShowWindow(found[0], win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(found[0])
            time.sleep(0.5)
            return True
    except Exception:
        pass

    return True  # Assume launched even if we can't verify


def _open_chat(contact: str) -> bool:
    """
    Open a WhatsApp chat by contact name using Ctrl+F search.
    Returns True if chat was opened successfully.
    """
    log.info("Opening WhatsApp chat: '%s'", contact)

    # Ctrl+F opens the search panel in WhatsApp Desktop
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.6)

    # Clear any existing search text and type contact name
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.write(contact, interval=_TYPE_INTERVAL)
    time.sleep(_SEARCH_WAIT)

    # Press Enter or Down+Enter to select the first result
    pyautogui.press("enter")
    time.sleep(_CHAT_OPEN_WAIT)

    log.info("Chat opened for: '%s'", contact)
    return True


def _type_and_send(message: str) -> None:
    """Type a message in the active chat input and press Enter to send."""
    # Click the message input area — WhatsApp input is at the bottom
    # Use pyperclip to paste instead of typing char by char (handles Unicode/Hindi etc.)
    pyperclip.copy(message)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(0.3)
    log.info("Message sent: '%s'", message[:60])


# ── Public API ─────────────────────────────────────────────────────────────────

def whatsapp_send(contact: str, message: str) -> str:
    """
    Open WhatsApp Desktop, find the contact, and send a message.

    Example: whatsapp_send("Karthik", "Hey! How are you?")
    """
    log.info("whatsapp_send: contact='%s' message='%s'", contact, message[:60])

    if not contact.strip():
        return "Contact name cannot be empty"
    if not message.strip():
        return "Message cannot be empty"

    try:
        _ensure_whatsapp_open()
        _open_chat(contact)
        _type_and_send(message)
        return f"Message sent to {contact}: '{message[:50]}{'...' if len(message) > 50 else ''}'"

    except Exception as e:
        log.error("whatsapp_send failed: %s", e)
        return f"Failed to send WhatsApp message: {e}"


def whatsapp_read(contact: str, n: int = 5) -> str:
    """
    Open WhatsApp Desktop, find the contact, and read the last N messages.

    Uses clipboard trick:
      - Click the chat area
      - Ctrl+A to select all visible text
      - Ctrl+C to copy
      - Read from clipboard
      - Return last N non-empty lines as messages

    Note: This grabs all visible text including timestamps and contact names.
    The LLM will parse and make sense of it.
    """
    log.info("whatsapp_read: contact='%s' n=%d", contact, n)

    if not contact.strip():
        return "Contact name cannot be empty"

    try:
        _ensure_whatsapp_open()
        _open_chat(contact)

        # Click roughly in the middle of the chat area to focus it
        # WhatsApp Desktop chat area is approximately center-right of the window
        screen_w, screen_h = pyautogui.size()
        chat_x = int(screen_w * 0.60)
        chat_y = int(screen_h * 0.50)
        pyautogui.click(chat_x, chat_y)
        time.sleep(0.3)

        # Select all visible text and copy
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.5)

        # Read from clipboard
        raw = pyperclip.paste()

        if not raw.strip():
            return f"No messages found in chat with {contact}"

        # Filter to last N non-empty lines
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        last_n = lines[-n:] if len(lines) >= n else lines

        result = "\n".join(last_n)
        log.info("whatsapp_read: got %d lines from %s", len(lines), contact)
        return f"Last {len(last_n)} messages from {contact}:\n{result}"

    except Exception as e:
        log.error("whatsapp_read failed: %s", e)
        return f"Failed to read WhatsApp messages: {e}"


def whatsapp_reply(contact: str, message: str) -> str:
    """
    Open WhatsApp Desktop, find the contact, and reply to their last message.

    'Reply' here means: open the chat and send a new message.
    (Replying to a specific bubble requires right-click → Reply,
    which is more fragile. This sends a fresh message in the chat.)
    """
    log.info("whatsapp_reply: contact='%s' message='%s'", contact, message[:60])

    if not contact.strip():
        return "Contact name cannot be empty"
    if not message.strip():
        return "Reply message cannot be empty"

    try:
        _ensure_whatsapp_open()
        _open_chat(contact)
        _type_and_send(message)
        return f"Replied to {contact}: '{message[:50]}{'...' if len(message) > 50 else ''}'"

    except Exception as e:
        log.error("whatsapp_reply failed: %s", e)
        return f"Failed to reply on WhatsApp: {e}"