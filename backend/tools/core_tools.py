"""SIA — core tools available to every agent."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field

from backend.tools.registry import tool_def


@tool_def(category="core", risk=1)
def ask_user(question: Annotated[str, Field(description="One clear question for the user")]) -> str:
    """Ask the user for information you cannot find yourself (a choice, a missing detail, or to
    finish a login / OTP / CAPTCHA in the browser). The task pauses until they answer."""
    # Never executed directly: the graph turns this call into a human-in-the-loop interrupt.
    return "ask_user is handled by the orchestrator"


@tool_def(category="core", risk=1)
def get_current_time() -> str:
    """Get the current local date and time."""
    return datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
