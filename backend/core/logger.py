"""
Sia AI — Logger
================
Structured, coloured console output + rotating file log.
Single get_logger() call gives any module a named logger.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────
_ROOT    = Path(__file__).resolve().parents[2]
_LOG_DIR = _ROOT / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "sia.log"

# ── Colour codes (terminal only) ──────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREY   = "\033[38;5;240m"
_CYAN   = "\033[38;5;45m"
_YELLOW = "\033[38;5;220m"
_RED    = "\033[38;5;196m"
_GREEN  = "\033[38;5;82m"

_LEVEL_COLOURS = {
    "DEBUG":    _GREY,
    "INFO":     _GREEN,
    "WARNING":  _YELLOW,
    "ERROR":    _RED,
    "CRITICAL": _BOLD + _RED,
}


class _ColourFormatter(logging.Formatter):
    """Console formatter with ANSI colours."""

    _FMT = "{colour}[{level:<8}]{reset} {grey}{time}{reset}  {cyan}{name}{reset}  {msg}"

    def format(self, record: logging.LogRecord) -> str:
        colour = _LEVEL_COLOURS.get(record.levelname, _RESET)
        time   = self.formatTime(record, "%H:%M:%S")
        return self._FMT.format(
            colour=colour,
            level=record.levelname,
            reset=_RESET,
            grey=_GREY,
            time=time,
            cyan=_CYAN,
            name=record.name,
            msg=record.getMessage(),
        )


class _PlainFormatter(logging.Formatter):
    """Plain formatter for log file (no ANSI codes)."""
    _FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    _DATE = "%Y-%m-%d %H:%M:%S"

    def __init__(self):
        super().__init__(fmt=self._FMT, datefmt=self._DATE)


# ── Secret redaction (applied to every SIA logger) ──────────────────────────
import re as _re
_SECRET_RE = _re.compile(
    r"(gsk_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9_\-]{12,}|sk_[A-Za-z0-9]{12,}|AIza[0-9A-Za-z_\-]{20,}"
    r"|(?i:(?:api[_-]?key|token|secret|password|passwd|authorization)\s*[=:]\s*)[^\s,'\"&]+)"
)

def redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(0)[:4] + "***", str(text))

class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            red = redact(msg)
            if red != msg:
                record.msg, record.args = red, ()
        except Exception:
            pass
        return True


# ── Registry ──────────────────────────────────────────────────────────────
_configured: set[str] = set()


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Return (and configure on first call) a named logger.

    Usage:
        from backend.core.logger import get_logger
        log = get_logger(__name__)
        log.info("hello")
    """
    logger = logging.getLogger(f"sia.{name}")

    if name in _configured:
        return logger

    _configured.add(name)
    logger.setLevel(level)
    logger.propagate = False  # prevent double-logging via root
    logger.addFilter(_RedactFilter())

    # ── Console handler ───────────────────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(_ColourFormatter())
    logger.addHandler(ch)

    # ── Rotating file handler (10 MB × 3 backups) ─────────────────────────
    fh = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_PlainFormatter())
    logger.addHandler(fh)

    return logger


# ── Root app logger (optional convenience) ────────────────────────────────
log = get_logger("app")