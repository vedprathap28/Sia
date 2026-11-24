"""
Sia AI — Volume, Brightness & Media Controls (Windows)

Volume up/down/mute: keyboard keys (each press = 2 units on Windows 0-100 scale)
Volume set:         pycaw (Windows Core Audio API) — precise, instant, no key spam
Brightness:         screen-brightness-control with PowerShell WMI fallback
                    (HP Pavilion x360 supports WMI brightness control)
"""
from __future__ import annotations
import subprocess
import time
import pyautogui
from backend.core.logger import get_logger

log = get_logger("volume")


def _press(key: str, times: int):
    for _ in range(times):
        pyautogui.press(key)
        time.sleep(0.03)


# ── Volume up / down / mute ────────────────────────────────────────────────────

def volume_up(amount: int = 10) -> str:
    amount = max(1, min(100, amount))
    steps  = max(1, round(amount / 2))
    _press("volumeup", steps)
    log.info("Volume up: %d steps (~%d units)", steps, amount)
    return f"Volume increased by {amount}"


def volume_down(amount: int = 10) -> str:
    amount = max(1, min(100, amount))
    steps  = max(1, round(amount / 2))
    _press("volumedown", steps)
    log.info("Volume down: %d steps (~%d units)", steps, amount)
    return f"Volume decreased by {amount}"


def volume_mute() -> str:
    pyautogui.press("volumemute")
    return "Muted"


def volume_unmute() -> str:
    pyautogui.press("volumemute")
    return "Unmuted"


def volume_set(level: int) -> str:
    """
    Set volume to an exact level (0-100) using pycaw (Windows Core Audio API).
    Instant and precise — no key spam.
    Fallback: keyboard method if pycaw is not installed.
    """
    level = max(0, min(100, level))

    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        import ctypes

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_ctrl = ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))

        # pycaw uses scalar 0.0–1.0
        volume_ctrl.SetMasterVolumeLevelScalar(level / 100.0, None)
        log.info("Volume set to %d%% via pycaw", level)
        return f"Volume set to {level}%"

    except ImportError:
        log.warning("pycaw not installed — using keyboard fallback. Run: pip install pycaw comtypes")
        return _volume_set_keyboard(level)
    except Exception as e:
        log.warning("pycaw failed (%s) — using keyboard fallback", e)
        return _volume_set_keyboard(level)


def _volume_set_keyboard(level: int) -> str:
    """Keyboard fallback for volume_set: go to 0, then press up to level."""
    _press("volumedown", 50)
    time.sleep(0.2)
    steps = round(level / 2)
    if steps > 0:
        _press("volumeup", steps)
    log.info("Volume set to ~%d via keyboard", level)
    return f"Volume set to approximately {level}%"


# ── Media controls ─────────────────────────────────────────────────────────────

def media_play_pause() -> str:
    pyautogui.press("playpause")
    return "Play/Pause toggled"

def media_next() -> str:
    pyautogui.press("nexttrack")
    return "Next track"

def media_prev() -> str:
    pyautogui.press("prevtrack")
    return "Previous track"

def media_stop() -> str:
    pyautogui.press("stop")
    return "Stopped"


# ── Brightness ────────────────────────────────────────────────────────────────
# HP Pavilion x360 supports WMI brightness control via PowerShell.
# screen-brightness-control is tried first; PowerShell WMI is the fallback.

def _get_brightness_wmi() -> int:
    """Get current brightness via PowerShell WMI."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"],
            capture_output=True, text=True, timeout=5,
        )
        return int(result.stdout.strip())
    except Exception:
        return 50  # assume 50% if we can't read it


def _set_brightness_wmi(level: int) -> bool:
    """Set brightness via PowerShell WMI (works on HP Pavilion x360)."""
    try:
        ps_cmd = (
            f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1,{level})"
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception as e:
        log.warning("WMI brightness set failed: %s", e)
        return False


def brightness_up(amount: int = 10) -> str:
    try:
        import screen_brightness_control as sbc
        cur = sbc.get_brightness(display=0)
        cur = cur[0] if isinstance(cur, list) else cur
        new = min(100, cur + amount)
        sbc.set_brightness(new, display=0)
        return f"Brightness increased to {new}%"
    except Exception:
        cur = _get_brightness_wmi()
        new = min(100, cur + amount)
        if _set_brightness_wmi(new):
            return f"Brightness increased to {new}%"
        return "Brightness control not supported on this display."


def brightness_down(amount: int = 10) -> str:
    try:
        import screen_brightness_control as sbc
        cur = sbc.get_brightness(display=0)
        cur = cur[0] if isinstance(cur, list) else cur
        new = max(0, cur - amount)
        sbc.set_brightness(new, display=0)
        return f"Brightness decreased to {new}%"
    except Exception:
        cur = _get_brightness_wmi()
        new = max(0, cur - amount)
        if _set_brightness_wmi(new):
            return f"Brightness decreased to {new}%"
        return "Brightness control not supported on this display."


def brightness_set(level: int) -> str:
    level = max(0, min(100, level))
    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(level, display=0)
        return f"Brightness set to {level}%"
    except Exception:
        if _set_brightness_wmi(level):
            return f"Brightness set to {level}%"
        return "Brightness control not supported on this display."


# ── Night light ────────────────────────────────────────────────────────────────

def night_light_on() -> str:
    subprocess.Popen("start ms-settings:nightlight", shell=True)
    return "Opening Night Light settings — toggle it on"

def night_light_off() -> str:
    subprocess.Popen("start ms-settings:nightlight", shell=True)
    return "Opening Night Light settings — toggle it off"