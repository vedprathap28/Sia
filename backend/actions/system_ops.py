# Sia AI - System Operations (Windows)
from __future__ import annotations
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pyautogui
import pyperclip

from backend.core.logger import get_logger

log = get_logger("system_ops")

# Confirmed paths
_HOME             = Path("C:/Users/sonuc")
_ONEDRIVE_DESKTOP = Path("C:/Users/sonuc/OneDrive/Desktop")

if not _ONEDRIVE_DESKTOP.exists():
    log.warning("OneDrive Desktop not found, falling back")
    _ONEDRIVE_DESKTOP = _HOME / "Desktop"


def _get_desktop() -> Path:
    return _ONEDRIVE_DESKTOP


def resolve_path(path: str) -> Path:
    if not path or not path.strip():
        return _HOME

    p = path.strip()
    p_norm = p.replace("\\", "/").lower().rstrip("/")

    if p_norm in ("desktop", "~/desktop", "~\\desktop"):
        return _ONEDRIVE_DESKTOP

    for prefix in ("~/desktop/", "~\\desktop\\", "~/desktop\\", "~\\desktop/"):
        if p_norm.startswith(prefix):
            rest = p[len(prefix):]
            return _ONEDRIVE_DESKTOP / rest

    alias_map = {
        "~/downloads":  _HOME / "Downloads",
        "~/documents":  _HOME / "Documents",
        "~/pictures":   _HOME / "Pictures",
        "~/music":      _HOME / "Music",
        "~/videos":     _HOME / "Videos",
        "~":            _HOME,
    }
    for alias, resolved in alias_map.items():
        if p_norm == alias:
            return resolved
        if p_norm.startswith(alias + "/") or p_norm.startswith(alias + "\\"):
            rest = p[len(alias):].lstrip("/\\")
            return resolved / rest

    return Path(p)


# File operations

def create_file(path: str, content: str = "") -> str:
    p = resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    log.info("Created file: %s", p)
    return f"Created '{p.name}' at {p.parent}"


def read_file(path: str) -> str:
    p = resolve_path(path)
    if not p.exists():
        return f"File not found: {p}"
    return p.read_text(encoding="utf-8", errors="replace")[:2000]


def delete_path(path: str) -> str:
    """
    Delete a file or folder.
    Uses send2trash (moves to Recycle Bin) to avoid OneDrive access denied errors.
    Falls back to force delete if send2trash is not installed.
    """
    p = resolve_path(path)
    if not p.exists():
        return f"Not found: {p}"

    try:
        import send2trash
        send2trash.send2trash(str(p))
        log.info("Sent to trash: %s", p)
        return f"Deleted '{p.name}' (moved to Recycle Bin)"
    except ImportError:
        # send2trash not installed — try force delete
        log.warning("send2trash not installed, trying force delete")
        try:
            if p.is_dir():
                # Use PowerShell Remove-Item which handles OneDrive locks better
                result = subprocess.run(
                    ["powershell", "-Command",
                     f'Remove-Item -Path "{p}" -Recurse -Force'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    return f"Deleted folder '{p.name}'"
                else:
                    return f"Could not delete '{p.name}': {result.stderr.strip()}"
            else:
                p.unlink()
                return f"Deleted '{p.name}'"
        except Exception as e:
            return f"Delete failed: {e}"
    except Exception as e:
        return f"Delete failed: {e}"


def move_path(src: str, dst: str) -> str:
    s = resolve_path(src)
    d = resolve_path(dst)
    if not s.exists():
        return f"Source not found: {s}"
    if d.is_dir():
        d = d / s.name
    shutil.move(str(s), str(d))
    return f"Moved '{s.name}' to {d.parent}"


def copy_path(src: str, dst: str) -> str:
    s = resolve_path(src)
    d = resolve_path(dst)
    if not s.exists():
        return f"Source not found: {s}"
    if s.is_dir():
        if d.exists():
            d = d / s.name
        shutil.copytree(str(s), str(d))
    else:
        if d.is_dir():
            d = d / s.name
        shutil.copy2(str(s), str(d))
    return f"Copied '{s.name}' to {d.parent}"


def rename_path(src: str, new_name: str) -> str:
    s = resolve_path(src)
    if not s.exists():
        return f"Not found: {s}"
    d = s.parent / new_name
    s.rename(d)
    return f"Renamed to '{d.name}'"


def open_folder(path: str) -> str:
    p = resolve_path(path)
    subprocess.Popen(f'explorer "{p}"', shell=True)
    return f"Opened '{p.name}'"


def create_folder(path: str) -> str:
    p = resolve_path(path)
    p.mkdir(parents=True, exist_ok=True)
    log.info("Created folder: %s", p)
    return f"Created folder '{p.name}' at {p.parent}"


def search_file(name: str, location: str = "~") -> str:
    base = resolve_path(location)
    try:
        matches = list(base.rglob(f"*{name}*"))[:10]
    except Exception as e:
        return f"Search failed: {e}"
    if not matches:
        return f"No files found matching '{name}'"
    return "\n".join(str(m) for m in matches)


def list_files(path: str) -> str:
    p = resolve_path(path)
    if not p.exists():
        return f"Folder not found: {p}"
    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    lines = [f"{'📁' if i.is_dir() else '📄'} {i.name}" for i in items[:30]]
    return "\n".join(lines) if lines else "Empty folder"


# Power

def system_shutdown() -> str:
    subprocess.run(["shutdown", "/s", "/t", "10"])
    return "Shutting down in 10 seconds."

def system_restart() -> str:
    subprocess.run(["shutdown", "/r", "/t", "10"])
    return "Restarting in 10 seconds."

def system_sleep() -> str:
    subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    return "Going to sleep."

def system_lock() -> str:
    subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
    return "Screen locked."

def cancel_shutdown() -> str:
    subprocess.run(["shutdown", "/a"])
    return "Shutdown cancelled."


# Recycle bin

def empty_recycle_bin() -> str:
    try:
        import winshell
        winshell.recycle_bin().empty(confirm=False, show_progress=False, sound=False)
        return "Recycle bin emptied."
    except ImportError:
        subprocess.run(["powershell", "-c", "Clear-RecycleBin -Force"], capture_output=True)
        return "Recycle bin emptied."
    except Exception as e:
        return f"Could not empty recycle bin: {e}"


# Screenshot

def take_screenshot() -> str:
    ts   = int(time.time())
    path = _ONEDRIVE_DESKTOP / f"sia_{ts}.png"
    pyautogui.screenshot(str(path))
    log.info("Screenshot saved: %s", path)
    return f"Screenshot saved to Desktop as sia_{ts}.png"


# System info

def check_battery() -> str:
    try:
        import psutil
        b = psutil.sensors_battery()
        if not b:
            return "No battery detected."
        status = "charging" if b.power_plugged else "discharging"
        return f"Battery at {b.percent:.0f}% — {status}."
    except ImportError:
        return "Install psutil: pip install psutil"

def check_system() -> str:
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=1)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (
            f"CPU: {cpu}% | "
            f"RAM: {ram.percent:.0f}% ({ram.used//(1024**3):.1f}GB of "
            f"{ram.total//(1024**3):.1f}GB) | Disk: {disk.percent}% used"
        )
    except ImportError:
        return "Install psutil: pip install psutil"

def open_task_manager() -> str:
    subprocess.Popen("taskmgr", shell=True)
    return "Task Manager opened."


# Keyboard and clipboard

def type_text(text: str) -> str:
    pyautogui.write(text, interval=0.04)
    return "Typed text"

def press_key(key: str) -> str:
    key = key.lower().strip()
    if "+" in key:
        parts = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*parts)
    else:
        pyautogui.press(key)
    return f"Pressed {key}"

def clipboard_copy(text: str) -> str:
    pyperclip.copy(text)
    return "Copied to clipboard"

def clipboard_paste() -> str:
    pyautogui.hotkey("ctrl", "v")
    return "Pasted"

def clipboard_get() -> str:
    try:
        text = pyperclip.paste()
        return text[:500] if text else "(clipboard empty)"
    except Exception:
        return "(could not read clipboard)"


# Display

def change_wallpaper(path: str = "") -> str:
    if not path:
        subprocess.Popen("start ms-settings:personalization-background", shell=True)
        return "Opening wallpaper settings."
    try:
        import ctypes
        ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3)
        return "Wallpaper changed."
    except Exception as e:
        return f"Wallpaper change failed: {e}"

def toggle_dark_mode(mode: str = "dark") -> str:
    value = 0 if mode.lower() == "dark" else 1
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "AppsUseLightTheme",    0, winreg.REG_DWORD, value)
            winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)
        return f"Switched to {mode} mode."
    except Exception as e:
        return f"Could not change theme: {e}"

def open_emoji_picker() -> str:
    pyautogui.hotkey("win", ".")
    return "Emoji picker opened"

def open_clipboard_history() -> str:
    pyautogui.hotkey("win", "v")
    return "Clipboard history opened"


# Reminders and timers

def set_reminder(message: str, minutes: int) -> str:
    def _remind():
        time.sleep(minutes * 60)
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;"
            "$n.Visible=$true;"
            f"$n.BalloonTipTitle='Sia Reminder';"
            f"$n.BalloonTipText='{message}';"
            "$n.ShowBalloonTip(5000);"
            "Start-Sleep 6;$n.Dispose()"
        )
        subprocess.run(["powershell", "-c", ps_script], capture_output=True)
    threading.Thread(target=_remind, daemon=True).start()
    return f"Reminder set for {minutes} minute{'s' if minutes != 1 else ''}: '{message}'"

def set_timer(minutes: int) -> str:
    return set_reminder(f"Timer done! {minutes} minutes are up.", minutes)