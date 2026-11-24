"""
Sia AI — Fast-path router + language detection
================================================
Pure functions (no OS / network imports) so they can be unit-tested anywhere.
Moved out of agent.py; agent.py re-exports them for backward compatibility.
"""
from __future__ import annotations
import re

from backend.agent.intent_parser import ParsedIntent, ActionStep


# ── Language detection (inline — no extra import needed) ─────────────────────

import re as _re

_DEVANAGARI_RE = _re.compile(r'[\u0900-\u097F]')
_TELUGU_RE     = _re.compile(r'[\u0C00-\u0C7F]')
_HINDI_ROMAN   = _re.compile(
    r'\b(kya|hai|hain|karo|bolo|batao|suno|acha|thik|nahi|haan|bhai|yaar|'
    r'kal|aaj|raat|din|subah|khana|pani|ghar|dost|matlab|samajh|mujhe|'
    r'tujhe|humko|unhe|woh|yeh|koi|kuch|abhi|phir|lekin|aur|ya|bahut|'
    r'bohot|thoda|zyada|bilkul|zarur|sahi|galat|achha|bura|kholo|bata|'
    r'sunao|dikha|laga|kar|ruk|chalo|aaja|badh|ghata|band|khol|mein|mujhko|'
    r'chahiye|kitna|kitne|kaise|kahan|kaun|liye|wala|wali|karna|karke|dena|'
    r'dijiye|kijiye|zara|accha|theek|haa)\b',
    _re.IGNORECASE,
)
_TELUGU_ROMAN  = _re.compile(
    r'\b(emi|enti|cheppu|cheyyi|neeku|naku|meeru|mee|memu|okati|rendo|'
    r'akkada|ikkada|ela|enduku|ekkuva|takkuva|manchidi|chedduga|avunu|'
    r'kaadu|undi|ledu|vachindi|poyindi|chestanu|teravantu|vesthe|chudandi)\b',
    _re.IGNORECASE,
)
# Only words that are NOT also common English words ("to", "te", "mi", "pan", "ka" used to be
# here and made "set a reminder to call mom" Marathi).
_MARATHI_ROMAN = _re.compile(
    r'\b(kasa|kashi|aho|naahi|nako|mala|tula|aapan|aamhi|tumhi|'
    r'kuthe|keva|mhanun|tyala|tila|ughad|ughda|sangas|sang|bagh|yeto|gela|ahe|aahe|'
    r'kara|kar[aā]yche|pahije|kiti|majha|maza|tumcha|lavkar|thamba)\b',
    _re.IGNORECASE,
)
# Devanagari words that are Marathi-only (Hindi uses है / नहीं / मुझे)
_MARATHI_DEVA = _re.compile(r'(आहे|नाही|मला|तुला|आम्ही|तुम्ही|करा|कसा|कशी|काय|पाहिजे)')


def _detect_language(text: str) -> str:
    if _TELUGU_RE.search(text):
        return 'te-IN'
    if _DEVANAGARI_RE.search(text):
        return 'mr-IN' if _MARATHI_DEVA.search(text) else 'hi-IN'
    te = len(_TELUGU_ROMAN.findall(text))
    mr = len(_MARATHI_ROMAN.findall(text))
    hi = len(_HINDI_ROMAN.findall(text))
    if te >= 1 and te >= max(mr, hi):
        return 'te-IN'
    if hi >= 2 and hi >= mr:
        return 'hi-IN'
    if mr >= 2 or (mr >= 1 and hi == 0 and len(text.split()) <= 4):
        return 'mr-IN'
    if hi >= 1 and len(text.split()) <= 3:   # "haan", "theek hai"
        return 'hi-IN'
    return 'en-IN'


# ── Local route spoken responses (language-aware) ─────────────────────────────

_LOCAL_SPOKEN = {
    # key → {lang: phrase}
    'volume_up': {
        'en-IN': "Volume up.",
        'hi-IN': "Volume badh gayi.",
        'te-IN': "Volume ekkuvayindi.",
        'mr-IN': "Volume vadhle.",
    },
    'volume_down': {
        'en-IN': "Volume down.",
        'hi-IN': "Volume kam ho gayi.",
        'te-IN': "Volume tagginchanu.",
        'mr-IN': "Volume kamin.",
    },
    'volume_mute': {
        'en-IN': "Muted.",
        'hi-IN': "Mute kar diya.",
        'te-IN': "Mute chesanu.",
        'mr-IN': "Mute kel.",
    },
    'volume_unmute': {
        'en-IN': "Unmuted.",
        'hi-IN': "Unmute kar diya.",
        'te-IN': "Unmute chesanu.",
        'mr-IN': "Unmute kel.",
    },
    'system_lock': {
        'en-IN': "Locking up.",
        'hi-IN': "Lock kar raha hoon.",
        'te-IN': "Lock chesanu.",
        'mr-IN': "Lock karto.",
    },
    'take_screenshot': {
        'en-IN': "Screenshot taken.",
        'hi-IN': "Screenshot le liya.",
        'te-IN': "Screenshot tesanu.",
        'mr-IN': "Screenshot ghetla.",
    },
    'system_sleep': {
        'en-IN': "Going to sleep.",
        'hi-IN': "Sleep mode mein ja raha hoon.",
        'te-IN': "Nidura povataniki veltundi.",
        'mr-IN': "Sleep mein jato.",
    },
}

def _localised_spoken(action: str, lang: str, default: str) -> str:
    return _LOCAL_SPOKEN.get(action, {}).get(lang, default)


# ── Intent builder ────────────────────────────────────────────────────────────

def _make_intent(action: str, params: dict, spoken: str) -> ParsedIntent:
    return ParsedIntent(
        type="action",
        intent=action,
        spoken=spoken,
        plan=[ActionStep(step=1, action=action, params=params)],
    )


def _local_route(text: str, lang: str = 'en-IN') -> ParsedIntent | None:
    """
    Identical local router as v1, but spoken responses are now language-aware.
    Only the spoken string changes — action logic is identical.
    """
    t = text.strip().lower()

    # ── Volume ────────────────────────────────────────────────────────────
    m = re.search(r"volume\s+up\s+(?:by\s+)?(\d+)", t)
    if m:
        amt = int(m.group(1))
        spoken = _LOCAL_SPOKEN.get('volume_up', {}).get(lang, f"Volume up by {amt}.")
        return _make_intent("volume_up", {"amount": amt}, spoken)

    m = re.search(r"volume\s+down\s+(?:by\s+)?(\d+)", t)
    if m:
        amt = int(m.group(1))
        spoken = _LOCAL_SPOKEN.get('volume_down', {}).get(lang, f"Volume down by {amt}.")
        return _make_intent("volume_down", {"amount": amt}, spoken)

    m = re.search(r"(?:set\s+)?volume\s+(?:to\s+)?(\d+)", t)
    if m:
        lvl = int(m.group(1))
        return _make_intent("volume_set", {"level": lvl}, f"Volume set to {lvl}%.")

    if re.search(r"\bunmute\b", t):
        return _make_intent("volume_unmute", {}, _localised_spoken('volume_unmute', lang, "Unmuted."))

    if re.search(r"\bmute\b", t):
        return _make_intent("volume_mute", {}, _localised_spoken('volume_mute', lang, "Muted."))

    if re.search(r"volume\s+up", t):
        return _make_intent("volume_up", {"amount": 10}, _localised_spoken('volume_up', lang, "Volume up."))

    if re.search(r"volume\s+down", t):
        return _make_intent("volume_down", {"amount": 10}, _localised_spoken('volume_down', lang, "Volume down."))

    # ── Brightness ────────────────────────────────────────────────────────
    m = re.search(r"brightness\s+up\s+(?:by\s+)?(\d+)", t)
    if m:
        return _make_intent("brightness_up", {"amount": int(m.group(1))}, f"Brightness up by {m.group(1)}.")

    m = re.search(r"brightness\s+down\s+(?:by\s+)?(\d+)", t)
    if m:
        return _make_intent("brightness_down", {"amount": int(m.group(1))}, f"Brightness down by {m.group(1)}.")

    m = re.search(r"(?:set\s+)?brightness\s+(?:to\s+)?(\d+)", t)
    if m:
        return _make_intent("brightness_set", {"level": int(m.group(1))}, f"Brightness set to {m.group(1)}%.")

    if re.search(r"brightness\s+up", t):
        return _make_intent("brightness_up", {"amount": 10}, "Brightness up.")

    if re.search(r"brightness\s+down", t):
        return _make_intent("brightness_down", {"amount": 10}, "Brightness down.")

    # ── Screenshot / Lock / Sleep / Power ─────────────────────────────────
    if re.search(r"(take\s+a?\s*screenshot|screenshot)", t):
        return _make_intent("take_screenshot", {}, _localised_spoken('take_screenshot', lang, "Screenshot taken."))

    # Power actions: anchored full-phrase matches only.
    # (Previously "\bsleep\b" / "\brestart\b" matched anywhere — "I can't sleep"
    #  put the PC to sleep and "cancel shutdown" triggered a shutdown.)
    _DEV = r"(?:the\s+)?(?:pc|computer|laptop|system|machine)"
    if re.fullmatch(r"(?:please\s+)?lock\s+(?:the\s+)?(?:screen|pc|computer|laptop|system|machine)", t.rstrip(".!")):
        return _make_intent("system_lock", {}, _localised_spoken('system_lock', lang, "Locking up."))

    if re.fullmatch(r"(?:please\s+)?cancel\s+(?:the\s+)?shut\s*down", t.rstrip(".!")):
        return _make_intent("cancel_shutdown", {}, "Shutdown cancelled.")

    if re.fullmatch(rf"(?:please\s+)?(?:put\s+{_DEV}\s+to\s+sleep|sleep\s+{_DEV}|{_DEV}\s+sleep)", t.rstrip(".!")):
        return _make_intent("system_sleep", {}, _localised_spoken('system_sleep', lang, "Going to sleep."))

    if re.fullmatch(rf"(?:please\s+)?(?:shut\s*down|power\s+off|turn\s+off)\s+{_DEV}", t.rstrip(".!")):
        return _make_intent("system_shutdown", {}, "Shutting down in 10 seconds.")

    if re.fullmatch(rf"(?:please\s+)?(?:restart|reboot)\s+{_DEV}", t.rstrip(".!")):
        return _make_intent("system_restart", {}, "Restarting in 10 seconds.")

    # ── Dark / Light mode ─────────────────────────────────────────────────
    if re.search(r"(dark\s+mode|switch\s+to\s+dark)", t):
        return _make_intent("toggle_dark_mode", {"mode": "dark"}, "Dark mode on.")

    if re.search(r"(light\s+mode|switch\s+to\s+light)", t):
        return _make_intent("toggle_dark_mode", {"mode": "light"}, "Light mode on.")

    # ── Battery / System ──────────────────────────────────────────────────
    if re.fullmatch(r"(?:check\s+)?(?:the\s+)?battery(?:\s+level|\s+status)?|how\s+much\s+battery(?:\s+is\s+left)?", t.rstrip(".?!")):
        return _make_intent("check_battery", {}, "Checking battery.")

    if re.search(r"(system\s+info|check\s+system|cpu\s+usage|ram\s+usage)", t):
        return _make_intent("check_system", {}, "Checking system.")

    if re.search(r"(task\s+manager|open\s+task\s+manager)", t):
        return _make_intent("open_task_manager", {}, "Opening Task Manager.")

    if re.fullmatch(r"(?:empty|clear)\s+(?:the\s+)?recycle\s+bin", t.rstrip(".!")):
        return _make_intent("empty_recycle_bin", {}, "Emptying recycle bin.")

    # ── Media ─────────────────────────────────────────────────────────────
    if re.search(r"next\s+(track|song)", t):
        return _make_intent("media_next", {}, "Next track.")

    if re.search(r"(previous|prev)\s+(track|song)", t):
        return _make_intent("media_prev", {}, "Previous track.")

    # ── Window management ─────────────────────────────────────────────────
    if re.search(r"(minimis|minimiz)\w+\s+(the\s+)?window", t):
        return _make_intent("window_minimise", {}, "Minimised.")

    if re.search(r"(maximis|maximiz)\w+\s+(the\s+)?window", t):
        return _make_intent("window_maximise", {}, "Maximised.")

    if re.search(r"snap\s+(window\s+)?(?:to\s+)?left", t):
        return _make_intent("window_snap_left", {}, "Snapped left.")

    if re.search(r"snap\s+(window\s+)?(?:to\s+)?right", t):
        return _make_intent("window_snap_right", {}, "Snapped right.")

    if re.search(r"show\s+desktop", t):
        return _make_intent("show_desktop", {}, "Desktop.")

    # ── Reminders / Timers ────────────────────────────────────────────────
    m = re.search(r"remind\s+me\s+in\s+(\d+)\s+min\w*\s+(?:to\s+)?(.+)", t)
    if m:
        mins = int(m.group(1)); msg = m.group(2).strip()
        return _make_intent("set_reminder", {"message": msg, "minutes": mins},
                            f"Reminder set for {mins} minutes.")

    m = re.search(r"set\s+(?:a\s+)?timer\s+(?:for\s+)?(\d+)\s+min", t)
    if m:
        mins = int(m.group(1))
        return _make_intent("set_timer", {"minutes": mins}, f"Timer set — {mins} minutes.")

    # ── Folder / File operations (unchanged from v1) ───────────────────────
    m = re.search(r"create\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?['\"]?([^'\"]+?)['\"]?\s+(?:on|in|at)\s+(?:the\s+)?desktop", t)
    if m:
        name = m.group(1).strip()
        return _make_intent("create_folder", {"path": f"~/Desktop/{name}"}, f"Folder '{name}' created.")

    m = re.search(r"create\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?['\"]?([^'\"]+?)['\"]?\s+(?:in|on|at)\s+(?:the\s+)?downloads", t)
    if m:
        name = m.group(1).strip()
        return _make_intent("create_folder", {"path": f"~/Downloads/{name}"}, f"Folder '{name}' created.")

    m = re.search(r"create\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?['\"]?([^'\"]+?)['\"]?\s+(?:in|on|at)\s+(?:the\s+)?documents", t)
    if m:
        name = m.group(1).strip()
        return _make_intent("create_folder", {"path": f"~/Documents/{name}"}, f"Folder '{name}' created.")

    m = re.search(r"delete\s+(?:the\s+)?(?:folder\s+)?['\"]?([^'\"]+?)['\"]?\s+folder\s+(?:from|on|in)\s+(?:the\s+)?desktop", t)
    if m:
        return _make_intent("delete_folder", {"path": f"~/Desktop/{m.group(1).strip()}"}, "Folder deleted.")

    m = re.search(r"delete\s+(?:the\s+)?folder\s+(?:called\s+|named\s+)?['\"]?([^'\"]+?)['\"]?\s*(?:from|on|in)?\s*(?:the\s+)?desktop", t)
    if m:
        return _make_intent("delete_folder", {"path": f"~/Desktop/{m.group(1).strip()}"}, "Folder deleted.")

    m = re.search(r"create\s+(?:a\s+)?file\s+(?:called\s+|named\s+)?['\"]?([^'\"]+?)['\"]?\s+(?:on|in|at)\s+(?:the\s+)?desktop(?:\s+with\s+content\s+(.+))?$", t)
    if m:
        name = m.group(1).strip(); content = m.group(2) or ""
        return _make_intent("create_file", {"path": f"~/Desktop/{name}", "content": content}, f"File '{name}' created.")

    m = re.search(r"delete\s+(?:the\s+)?file\s+['\"]?([^'\"]+?)['\"]?\s+(?:from|on|in)\s+(?:the\s+)?desktop", t)
    if m:
        return _make_intent("delete_file", {"path": f"~/Desktop/{m.group(1).strip()}"}, "File deleted.")

    for fname, fpath in [("downloads", "~/Downloads"), ("documents", "~/Documents"),
                         ("pictures", "~/Pictures"), ("music", "~/Music"),
                         ("videos", "~/Videos"), ("desktop", "~/Desktop")]:
        if re.search(rf"open\s+(the\s+)?{fname}\s+folder", t):
            return _make_intent("open_folder", {"path": fpath}, f"Opening {fname}.")

    # ── Known apps ────────────────────────────────────────────────────────
    _KNOWN_APPS = {
        "whatsapp", "brave", "brave browser", "notepad", "calculator",
        "vscode", "visual studio code", "code", "spotify", "discord",
        "teams", "microsoft teams", "outlook", "excel", "word", "powerpoint",
        "onenote", "paint", "snipping tool", "snip", "terminal",
        "file explorer", "sticky notes", "to do", "todo", "calendar",
        "camera", "clock", "photos", "media player", "store",
        "microsoft store", "xbox", "phone link", "clipchamp", "whiteboard",
        "power automate", "notepad++", "edge", "microsoft edge",
    }
    m = re.match(r"open\s+(.+)", t)
    if m:
        candidate = m.group(1).strip().rstrip(".")
        if candidate in _KNOWN_APPS:
            return _make_intent("open_app", {"app_name": candidate}, f"Opening {candidate}.")

    return None


