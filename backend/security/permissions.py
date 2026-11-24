"""
SIA — Permission levels and the approval policy.

Level 1  READ           read-only (search, read email, read files, screenshots)
Level 2  LOW            low-risk / reversible (open apps, drafts, create files in allowed folders)
Level 3  CONSEQUENTIAL  needs explicit approval (send email/message, submit forms, book, pay,
                        post, share personal info, install packages, power off the PC)
Level 4  DESTRUCTIVE    stronger safeguard or blocked (delete files, destructive commands,
                        security/system settings)

The LLM never decides whether an action is allowed — this module does, from the
tool's declared risk (and per-call escalations computed by the tool itself).
"""
from __future__ import annotations

from enum import IntEnum
from typing import Literal

from backend.core.config import settings


class Risk(IntEnum):
    READ = 1
    LOW = 2
    CONSEQUENTIAL = 3
    DESTRUCTIVE = 4


Decision = Literal["allow", "ask", "confirm", "block"]

RISK_LABEL = {
    1: "Read-only",
    2: "Low-risk",
    3: "Needs approval",
    4: "Destructive",
}


def _setting(key: str, default):
    try:
        from backend.database.db import session_scope
        from backend.database.models import Setting
        with session_scope() as s:
            row = s.get(Setting, key)
            if row is not None:
                return type(default)(row.value) if not isinstance(default, bool) else row.value == "true"
    except Exception:
        pass
    return default


def auto_approve_level() -> int:
    # Never allow auto-approving level 3+ via settings: consequential actions always ask.
    return max(0, min(2, int(_setting("auto_approve_level", settings.AUTO_APPROVE_LEVEL))))


def level4_policy() -> str:
    v = str(_setting("level4_policy", settings.LEVEL4_POLICY)).lower()
    return v if v in ("confirm", "block") else "confirm"


def decide(risk: int, *, background: bool = False) -> Decision:
    """Return what must happen before a tool call with this risk may run."""
    risk = int(risk)
    if risk >= Risk.DESTRUCTIVE:
        return "block" if level4_policy() == "block" else "confirm"
    if risk >= Risk.CONSEQUENTIAL:
        return "ask"
    if risk > auto_approve_level():
        return "ask"
    return "allow"
