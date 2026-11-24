"""
Sia AI — LangGraph entry point (compatibility shim)
===================================================
The old graph in this file never executed tools (its ToolNode had no incoming edge) and
crashed because async nodes were run with the sync `invoke`. The real graph now lives in
backend/graph/supervisor.py and runs through the task manager.

`sia_graph.run()` keeps the signature livekit_agent.py uses.
"""
from __future__ import annotations

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("sia_graph")


class SiaGraph:
    async def run(self, user_input: str, lang: str = "en-IN", rag: bool = False,
                  history: list[dict] | None = None) -> str:
        from backend.task_manager.manager import task_manager
        if task_manager.graph is None:
            await task_manager.startup()
        tid = task_manager.start(user_input, source="voice", lang=lang, history=history or [])
        t = await task_manager.wait(tid, settings.CHAT_WAIT_SECONDS)
        if t.get("status") in ("completed", "failed"):
            return t.get("spoken") or t.get("result") or "Done."
        if t.get("status") == "waiting_approval":
            return "I need your approval for that. Please check the screen."
        return "Working on it. I'll tell you when it's done."


sia_graph = SiaGraph()
