"""
Sia AI — Window Management (Windows)
Uses pyautogui keyboard shortcuts + win32gui for window control.
"""
from __future__ import annotations
import subprocess
import time
import pyautogui
from backend.core.logger import get_logger

log = get_logger("window")


def window_minimise() -> str:
    pyautogui.hotkey("win", "down")
    return "Window minimised"

def window_maximise() -> str:
    pyautogui.hotkey("win", "up")
    return "Window maximised"

def window_close() -> str:
    pyautogui.hotkey("alt", "f4")
    return "Window closed"

def window_snap_left() -> str:
    pyautogui.hotkey("win", "left")
    return "Window snapped left"

def window_snap_right() -> str:
    pyautogui.hotkey("win", "right")
    return "Window snapped right"

def show_desktop() -> str:
    pyautogui.hotkey("win", "d")
    return "Desktop shown"

def open_task_view() -> str:
    pyautogui.hotkey("win", "tab")
    return "Task view opened"

def virtual_desktop_new() -> str:
    pyautogui.hotkey("win", "ctrl", "d")
    return "New virtual desktop created"

def virtual_desktop_switch(number: int = 2) -> str:
    # Win+Ctrl+Left/Right switches desktops
    # Go to desktop N by switching right (number-1) times from desktop 1
    # First go to desktop 1
    for _ in range(10):
        pyautogui.hotkey("win", "ctrl", "left")
        time.sleep(0.1)
    # Then go right to target
    for _ in range(number - 1):
        pyautogui.hotkey("win", "ctrl", "right")
        time.sleep(0.1)
    return f"Switched to desktop {number}"

def window_switch(app_name: str) -> str:
    """Switch to an open application window by name."""
    try:
        import win32gui
        import win32con

        def callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).lower()
                if app_name.lower() in title:
                    results.append(hwnd)

        results = []
        win32gui.EnumWindows(callback, results)

        if results:
            hwnd = results[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return f"Switched to {app_name}"
        else:
            # Fallback: Alt+Tab and hope
            pyautogui.hotkey("alt", "tab")
            return f"Could not find '{app_name}' window — used Alt+Tab"
    except ImportError:
        pyautogui.hotkey("alt", "tab")
        return f"Switched window (install pywin32 for app-specific switching)"
    except Exception as e:
        return f"Window switch failed: {e}"

def open_emoji_picker() -> str:
    pyautogui.hotkey("win", ".")
    return "Emoji picker opened"

def open_clipboard_history() -> str:
    pyautogui.hotkey("win", "v")
    return "Clipboard history opened"