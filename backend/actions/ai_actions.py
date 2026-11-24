"""
Sia AI — AI-Powered Clipboard Actions
Summarise, fix spelling, translate — using the LLM on clipboard content.
"""
from __future__ import annotations
import pyperclip
from backend.core.logger import get_logger

log = get_logger("ai_actions")


async def _llm_on_clipboard(prompt_prefix: str) -> str:
    """Run LLM on current clipboard content."""
    try:
        text = pyperclip.paste()
        if not text.strip():
            return "Clipboard is empty."
    except Exception:
        return "Could not read clipboard."

    from backend.llm.llm_service import llm_service
    result = await llm_service.respond(
        f"{prompt_prefix}:\n\n{text[:3000]}",
        history=[],
    )
    if result.get("type") == "chat":
        output = result.get("message", "")
    else:
        output = str(result)

    # Copy result back to clipboard
    pyperclip.copy(output)
    return output


async def summarise_clipboard() -> str:
    result = await _llm_on_clipboard("Summarise this text concisely in 3-5 sentences")
    return f"Summary (copied to clipboard): {result[:200]}..."

async def fix_spelling_clipboard() -> str:
    result = await _llm_on_clipboard("Fix all spelling and grammar errors in this text. Return only the corrected text")
    return "Fixed text copied to clipboard."

async def translate_clipboard(target_language: str = "Hindi") -> str:
    result = await _llm_on_clipboard(f"Translate this text to {target_language}. Return only the translation")
    return f"Translation to {target_language} copied to clipboard."