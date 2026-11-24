# Sia AI - Browser Actions
#
# open_url / search_web   -> subprocess -> new tab in existing Chrome (instant)
# youtube_play            -> YouTube Data API v3 -> get first non-ad video ID
#                            -> open youtube.com/watch?v=ID directly in existing Chrome
#                            -> NO clicking, NO Tab nav, NO Playwright, NO new window
#                            -> NO bring-to-front (preserves fullscreen)
# search_in_site          -> site-specific search URL, existing Chrome new tab
#                            -> bring-to-front only here (no fullscreen concern)
# close_tab_by_name       -> find Chrome tab by name, Ctrl+Tab cycle to it, Ctrl+W
# what_is_playing         -> read active Chrome window title, return song/page name
# youtube_pause_resume    -> bring Chrome to front, press K
# youtube_seek_forward    -> press L once per 10s (30s = 3 presses)
# youtube_seek_back       -> press J once per 10s
# youtube_fullscreen      -> press F to toggle fullscreen
# youtube_volume_up/down  -> press Up/Down arrow on focused video

from __future__ import annotations
import subprocess
import time
import urllib.parse
import urllib.request
import json
import re

import pyautogui

from backend.core.logger import get_logger

log = get_logger("browser")

import os as _os
def _find_chrome() -> str:
    cands = [_os.getenv("CHROME_PATH", ""),
             "C:/Program Files/Google/Chrome/Application/chrome.exe",
             "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
             _os.path.expandvars("%LOCALAPPDATA%/Google/Chrome/Application/chrome.exe")]
    for c in cands:
        if c and _os.path.exists(c):
            return c
    return cands[1]
_CHROME_EXE   = _find_chrome()
_SONU_PROFILE = "Default"
_VED_PROFILE  = "Profile 3"

_CHROME_PROFILES = {
    "sonu":        _SONU_PROFILE,
    "default":     _SONU_PROFILE,
    "ved":         _VED_PROFILE,
    "ved prathap": _VED_PROFILE,
    "vedprathap":  _VED_PROFILE,
}

# ── Site-specific search URL patterns ─────────────────────────────────────────
_SITE_SEARCH_URLS: dict[str, str] = {
    "flipkart.com":   "https://www.flipkart.com/search?q={q}",
    "amazon.in":      "https://www.amazon.in/s?k={q}",
    "amazon.com":     "https://www.amazon.com/s?k={q}",
    "myntra.com":     "https://www.myntra.com/{q}",
    "meesho.com":     "https://www.meesho.com/search?q={q}",
    "nykaa.com":      "https://www.nykaa.com/search/result/?q={q}",
    "swiggy.com":     "https://www.swiggy.com/search?query={q}",
    "zomato.com":     "https://www.zomato.com/search?q={q}",
    "pinterest.com":  "https://www.pinterest.com/search/pins/?q={q}",
    "instagram.com":  "https://www.instagram.com/explore/search/keyword/?q={q}",
    "twitter.com":    "https://twitter.com/search?q={q}",
    "x.com":          "https://x.com/search?q={q}",
    "linkedin.com":   "https://www.linkedin.com/search/results/all/?keywords={q}",
    "github.com":     "https://github.com/search?q={q}",
    "youtube.com":    "https://www.youtube.com/results?search_query={q}",
    "google.com":     "https://www.google.com/search?q={q}",
    "netflix.com":    "https://www.netflix.com/search?q={q}",
    "hotstar.com":    "https://www.hotstar.com/in/search?q={q}",
    "bookmyshow.com": "https://in.bookmyshow.com/explore/movies-all/{q}",
}

# Minimum video duration to filter out ads and Shorts
_MIN_VIDEO_SECONDS = 60


def _normalise_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _bring_chrome_to_front() -> None:
    """
    Bring Chrome to foreground.
    Only called from search_in_site — NOT from youtube_play (avoids fullscreen break).
    """
    try:
        import win32gui
        import win32con

        found: list[int] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                if "Chrome" in win32gui.GetWindowText(hwnd):
                    found.append(hwnd)

        win32gui.EnumWindows(_cb, None)

        if found:
            hwnd = found[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            log.info("Chrome brought to foreground (hwnd=%s)", hwnd)
        else:
            log.warning("No visible Chrome window found")

    except ImportError:
        log.warning("pywin32 not installed — pip install pywin32")
    except Exception as e:
        log.warning("_bring_chrome_to_front error: %s", e)


# ── YouTube Data API v3 ────────────────────────────────────────────────────────

def _iso8601_to_seconds(duration: str) -> int:
    """Convert ISO 8601 duration string (PT4M13S) to total seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s


def _get_first_video_id(query: str) -> tuple[str, str] | None:
    """
    Use YouTube Data API v3 to find the first non-ad, non-Short video.

    Two API calls:
      1. search.list  — top 10 results (type=video)
      2. videos.list  — batch fetch durations for all 10

    Returns (video_id, title) of first video >= 60 seconds, or None on failure.
    """
    # Read API key via settings so dotenv is guaranteed loaded
    from backend.core.config import settings
    api_key = settings.YOUTUBE_API_KEY.strip()

    if not api_key:
        log.warning("YOUTUBE_API_KEY not set in .env — falling back to search URL")
        return None

    try:
        # ── Step 1: search.list ────────────────────────────────────────────
        search_params = urllib.parse.urlencode({
            "part":       "snippet",
            "q":          query,
            "type":       "video",
            "maxResults": 10,
            "key":        api_key,
        })
        search_url = f"https://www.googleapis.com/youtube/v3/search?{search_params}"
        log.debug("YT API search → %s", search_url)

        with urllib.request.urlopen(search_url, timeout=8) as resp:
            search_data = json.loads(resp.read().decode())

        items = search_data.get("items", [])
        if not items:
            log.warning("YT API: no results for '%s'", query)
            return None

        video_ids = [item["id"]["videoId"] for item in items]
        title_map = {item["id"]["videoId"]: item["snippet"]["title"] for item in items}

        log.debug("YT API: found %d videos — %s", len(video_ids), video_ids)

        # ── Step 2: videos.list (batch duration fetch) ─────────────────────
        detail_params = urllib.parse.urlencode({
            "part": "contentDetails",
            "id":   ",".join(video_ids),
            "key":  api_key,
        })
        detail_url = f"https://www.googleapis.com/youtube/v3/videos?{detail_params}"
        log.debug("YT API details → %s", detail_url)

        with urllib.request.urlopen(detail_url, timeout=8) as resp:
            detail_data = json.loads(resp.read().decode())

        duration_map: dict[str, int] = {
            item["id"]: _iso8601_to_seconds(item["contentDetails"]["duration"])
            for item in detail_data.get("items", [])
        }

        # ── Step 3: Pick first video >= _MIN_VIDEO_SECONDS ─────────────────
        for vid_id in video_ids:
            duration = duration_map.get(vid_id, 0)
            title    = title_map.get(vid_id, "Unknown")
            log.debug("  %s | %ds | %s", vid_id, duration, title)

            if duration >= _MIN_VIDEO_SECONDS:
                log.info("✅ Selected: '%s' (%ds) id=%s", title, duration, vid_id)
                return vid_id, title

        log.warning("YT API: all %d results under %ds", len(video_ids), _MIN_VIDEO_SECONDS)
        return None

    except Exception as e:
        log.error("YT API failed: %s", e)
        return None


# ── Simple URL open ────────────────────────────────────────────────────────────

def open_url(url: str, profile: str = "default") -> str:
    url            = _normalise_url(url)
    profile_folder = _CHROME_PROFILES.get(profile.lower(), _SONU_PROFILE)
    log.info("Opening %s [%s]", url, profile_folder)
    subprocess.Popen([_CHROME_EXE, f"--profile-directory={profile_folder}", url])
    return f"Opened {url}"


def search_web(query: str, engine: str = "google") -> str:
    engines = {
        "google":     "https://www.google.com/search?q=",
        "youtube":    "https://www.youtube.com/results?search_query=",
        "bing":       "https://www.bing.com/search?q=",
        "duckduckgo": "https://duckduckgo.com/?q=",
    }
    base = engines.get(engine.lower(), engines["google"])
    return open_url(base + urllib.parse.quote_plus(query))


# ── YouTube play ───────────────────────────────────────────────────────────────
#
# Flow:
#   1. Call YouTube Data API v3 → get first valid video ID (duration >= 60s)
#   2. Open youtube.com/watch?v=VIDEO_ID directly in existing Chrome (new tab)
#   3. NO bring-to-front — fullscreen of any playing video is never disturbed
#   4. Fallback: if API fails → open search results page

def youtube_play(query: str) -> str:
    log.info("youtube_play: '%s'", query)

    result = _get_first_video_id(query)

    if result:
        video_id, title = result
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        log.info("Opening direct URL: %s", watch_url)
        subprocess.Popen([_CHROME_EXE, f"--profile-directory={_SONU_PROFILE}", watch_url])
        return f"Playing '{title}' on YouTube"

    # Fallback — API key missing or request failed
    log.warning("youtube_play: API unavailable — opening search results")
    fallback_url = (
        "https://www.youtube.com/results?search_query="
        + urllib.parse.quote_plus(query)
    )
    subprocess.Popen([_CHROME_EXE, f"--profile-directory={_SONU_PROFILE}", fallback_url])
    return f"Opened YouTube search for '{query}' — click the first video to play"


# ── Search in site ─────────────────────────────────────────────────────────────

def search_in_site(url: str, query: str) -> str:
    url    = _normalise_url(url)
    q      = urllib.parse.quote_plus(query)
    domain = next((d for d in _SITE_SEARCH_URLS if d in url), None)

    if domain:
        search_url = _SITE_SEARCH_URLS[domain].format(q=q)
        log.info("search_in_site: %s — '%s' → %s", domain, query, search_url)
    else:
        search_url = url.rstrip("/") + f"/search?q={q}"
        log.warning("search_in_site: unknown domain '%s' — using fallback", url)

    subprocess.Popen([_CHROME_EXE, f"--profile-directory={_SONU_PROFILE}", search_url])

    time.sleep(0.8)
    _bring_chrome_to_front()

    return f"Searched '{query}' on {domain or url}"


# ── Tab control ───────────────────────────────────────────────────────────────
#
# close_tab_by_name:
#   1. Get all Chrome window titles via win32gui
#   2. Find which tab title contains the target name
#   3. Chrome window title = "<active tab title> - Google Chrome"
#      So we cycle through tabs with Ctrl+Tab until the window title matches,
#      then close with Ctrl+W
#
# what_is_playing:
#   Read the active Chrome window title and strip " - Google Chrome" suffix.

def close_tab_by_name(name: str) -> str:
    """
    Cycle through Chrome tabs until one matching `name` is active, then close it.
    Uses Ctrl+Tab to cycle and reads window title after each switch.
    Max 20 tab switches before giving up.
    """
    name_lower = name.lower().strip()
    log.info("close_tab_by_name: '%s'", name)

    try:
        import win32gui
        import win32con

        # Find Chrome window handle
        found: list[int] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if "Google Chrome" in t or "- Chrome" in t:
                    found.append(hwnd)

        win32gui.EnumWindows(_cb, None)

        if not found:
            return "Chrome window not found"

        hwnd = found[0]
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.4)

        # Cycle through tabs — max 20 attempts
        for i in range(20):
            title = win32gui.GetWindowText(hwnd).lower()
            log.debug("Tab check %d: %s", i, title)

            # Check if current tab matches the name
            if name_lower in title:
                pyautogui.hotkey("ctrl", "w")
                log.info("Closed tab matching '%s' (title: %s)", name, title)
                return f"Closed '{name}' tab"

            # Move to next tab
            pyautogui.hotkey("ctrl", "tab")
            time.sleep(0.3)

        return f"Could not find a tab matching '{name}' — checked 20 tabs"

    except ImportError:
        log.warning("pywin32 not installed")
        return "pywin32 not installed — pip install pywin32"
    except Exception as e:
        log.error("close_tab_by_name failed: %s", e)
        return f"Failed to close tab: {e}"


def what_is_playing() -> str:
    """
    Read the active Chrome tab title.
    Chrome sets window title to '<page title> - Google Chrome'.
    We strip the suffix and return the page title.
    """
    try:
        import win32gui

        titles: list[str] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if "Google Chrome" in t or "- Chrome" in t:
                    titles.append(t)

        win32gui.EnumWindows(_cb, None)

        if not titles:
            return "Chrome is not open"

        raw   = titles[0]
        # Strip " - Google Chrome" suffix
        title = raw.replace(" - Google Chrome", "").replace(" - Chrome", "").strip()

        if not title:
            return "Could not read current tab"

        log.info("what_is_playing: '%s'", title)
        return f"Currently playing: {title}"

    except ImportError:
        return "pywin32 not installed — pip install pywin32"
    except Exception as e:
        return f"Could not read tab: {e}"


# ── YouTube controls ───────────────────────────────────────────────────────────
#
# All YouTube keyboard shortcuts work when the video player has focus.
# We bring Chrome to front first, then send the key.
# YouTube shortcuts:
#   K          — pause / resume
#   F          — toggle fullscreen
#   J          — seek back 10 seconds
#   L          — seek forward 10 seconds
#   Up arrow   — volume up 5%
#   Down arrow — volume down 5%
#
# For seeking by arbitrary seconds:
#   We calculate number of J/L presses needed (each = 10s).
#   Remainder < 5s is ignored; >= 5s rounds up to one extra press.

def _focus_chrome_for_yt() -> bool:
    """
    Bring Chrome to foreground for YouTube keyboard control.
    Returns True if successful.
    NOTE: This WILL exit fullscreen if Chrome is fullscreen.
    User accepts this trade-off for YouTube control commands.
    """
    try:
        import win32gui
        import win32con

        found: list[int] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if "Google Chrome" in t or "- Chrome" in t:
                    found.append(hwnd)

        win32gui.EnumWindows(_cb, None)

        if found:
            hwnd = found[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.4)  # wait for focus to settle
            return True
        return False

    except Exception as e:
        log.warning("_focus_chrome_for_yt: %s", e)
        return False


def youtube_pause_resume() -> str:
    """Toggle pause/resume on YouTube — press K."""
    log.info("youtube_pause_resume")
    _focus_chrome_for_yt()
    pyautogui.press("k")
    return "Toggled play/pause"


def youtube_seek_forward(seconds: int = 10) -> str:
    """
    Seek forward by `seconds` on YouTube.
    Each L press = +10 seconds. Rounds to nearest 10.
    """
    seconds = max(10, int(seconds))
    presses = round(seconds / 10)
    log.info("youtube_seek_forward: %ds (%d presses)", seconds, presses)
    _focus_chrome_for_yt()
    for _ in range(presses):
        pyautogui.press("l")
        time.sleep(0.1)
    return f"Seeked forward {presses * 10} seconds"


def youtube_seek_back(seconds: int = 10) -> str:
    """
    Seek back by `seconds` on YouTube.
    Each J press = -10 seconds. Rounds to nearest 10.
    """
    seconds = max(10, int(seconds))
    presses = round(seconds / 10)
    log.info("youtube_seek_back: %ds (%d presses)", seconds, presses)
    _focus_chrome_for_yt()
    for _ in range(presses):
        pyautogui.press("j")
        time.sleep(0.1)
    return f"Seeked back {presses * 10} seconds"


def youtube_fullscreen() -> str:
    """Toggle fullscreen on YouTube — press F."""
    log.info("youtube_fullscreen")
    _focus_chrome_for_yt()
    pyautogui.press("f")
    return "Toggled fullscreen"


def youtube_volume_up(amount: int = 10) -> str:
    """
    Increase YouTube video volume.
    Each Up arrow press = +5%. Default amount=10 → 2 presses.
    """
    presses = max(1, round(amount / 5))
    log.info("youtube_volume_up: %d presses", presses)
    _focus_chrome_for_yt()
    for _ in range(presses):
        pyautogui.press("up")
        time.sleep(0.05)
    return f"YouTube volume up by {presses * 5}%"


def youtube_volume_down(amount: int = 10) -> str:
    """
    Decrease YouTube video volume.
    Each Down arrow press = -5%. Default amount=10 → 2 presses.
    """
    presses = max(1, round(amount / 5))
    log.info("youtube_volume_down: %d presses", presses)
    _focus_chrome_for_yt()
    for _ in range(presses):
        pyautogui.press("down")
        time.sleep(0.05)
    return f"YouTube volume down by {presses * 5}%"


# ── Chrome keyboard shortcuts ──────────────────────────────────────────────────

def _hotkey(*keys):
    pyautogui.hotkey(*keys)
    time.sleep(0.1)

def chrome_new_tab()    -> str: _hotkey("ctrl", "t");            return "New tab opened"
def chrome_close_tab()  -> str: _hotkey("ctrl", "w");            return "Tab closed"
def chrome_next_tab()   -> str: _hotkey("ctrl", "tab");          return "Next tab"
def chrome_prev_tab()   -> str: _hotkey("ctrl", "shift", "tab"); return "Previous tab"
def chrome_refresh()    -> str: _hotkey("ctrl", "r");            return "Page refreshed"
def chrome_back()       -> str: _hotkey("alt", "left");          return "Went back"
def chrome_forward()    -> str: _hotkey("alt", "right");         return "Went forward"
def chrome_zoom_in()    -> str: _hotkey("ctrl", "=");            return "Zoomed in"
def chrome_zoom_out()   -> str: _hotkey("ctrl", "-");            return "Zoomed out"
def chrome_zoom_reset() -> str: _hotkey("ctrl", "0");            return "Zoom reset"
def chrome_incognito()  -> str: _hotkey("ctrl", "shift", "n");   return "Incognito window opened"
def chrome_history()    -> str: _hotkey("ctrl", "h");            return "History opened"
def chrome_downloads()  -> str: _hotkey("ctrl", "j");            return "Downloads opened"

def chrome_open_profile(profile: str) -> str:
    folder = _CHROME_PROFILES.get(profile.lower(), _SONU_PROFILE)
    subprocess.Popen([_CHROME_EXE, f"--profile-directory={folder}"])
    return f"Opened Chrome with {profile} profile"