"""
Sia AI — Intent Parser
========================
Converts the raw dict from LLMService into a clean, typed ParsedIntent.

NO logic here — all intelligence is in the LLM.
This module only normalises shape and provides a clean interface
for the Agent to work with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.core.logger import get_logger

log = get_logger("intent_parser")


# ══════════════════════════════════════════════════════════════════════════════
#  Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ActionStep:
    """A single step in an action plan."""
    step:   int
    action: str
    params: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Step({self.step}: {self.action} {self.params})"


@dataclass
class ParsedIntent:
    """
    Normalised output of the intent parser.

    type == "action"  → plan contains steps to execute
    type == "chat"    → message contains the assistant reply
    """
    type:    str                    # "action" | "chat"
    intent:  str         = ""      # short label, e.g. "youtube_play_song"
    spoken:  str         = ""      # what Sia says aloud when executing
    message: str         = ""      # chat reply (only for type=="chat")
    plan:    list[ActionStep] = field(default_factory=list)
    raw:     dict             = field(default_factory=dict)

    # ── Convenience properties ─────────────────────────────────────────────

    @property
    def is_action(self) -> bool:
        return self.type == "action"

    @property
    def is_chat(self) -> bool:
        return self.type == "chat"

    @property
    def step_count(self) -> int:
        return len(self.plan)

    def to_dict(self) -> dict:
        return {
            "type":    self.type,
            "intent":  self.intent,
            "spoken":  self.spoken,
            "message": self.message,
            "plan": [
                {"step": s.step, "action": s.action, "params": s.params}
                for s in self.plan
            ],
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Parser
# ══════════════════════════════════════════════════════════════════════════════

class IntentParser:
    """
    Converts raw LLM dict → ParsedIntent.

    The parser is deliberately thin:
    - Validates the shape of the LLM output
    - Fills in defaults for missing optional fields
    - Logs what it received for debugging
    """

    def parse(self, llm_output: dict) -> ParsedIntent:
        intent_type = llm_output.get("type", "chat")

        if intent_type == "action":
            return self._parse_action(llm_output)
        return self._parse_chat(llm_output)

    # ── Private ───────────────────────────────────────────────────────────

    def _parse_action(self, data: dict) -> ParsedIntent:
        raw_plan = data.get("plan", [])

        steps: list[ActionStep] = []
        for i, s in enumerate(raw_plan):
            action = s.get("action", "").strip()
            if not action:
                log.warning("Step %d has no action name — skipping", i + 1)
                continue
            steps.append(ActionStep(
                step=s.get("step", i + 1),
                action=action,
                params=s.get("params", {}),
            ))

        intent = ParsedIntent(
            type="action",
            intent=data.get("intent", "unknown_action"),
            spoken=data.get("spoken", ""),
            plan=steps,
            raw=data,
        )

        log.info(
            "Intent → ACTION  label=%s  steps=%d  spoken='%s'",
            intent.intent, intent.step_count, intent.spoken[:60],
        )
        for s in steps:
            log.debug("  %r", s)

        return intent

    def _parse_chat(self, data: dict) -> ParsedIntent:
        message = data.get("message", "").strip()

        # Graceful fallback if message is missing
        if not message:
            message = "I'm here. What do you need?"
            log.warning("LLM returned empty chat message — using fallback")

        intent = ParsedIntent(
            type="chat",
            message=message,
            raw=data,
        )

        log.info("Intent → CHAT  '%s'", message[:80])
        return intent


# ── Singleton ──────────────────────────────────────────────────────────────
intent_parser = IntentParser()