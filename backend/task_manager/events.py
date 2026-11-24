"""
SIA — In-process event bus. Graph nodes and the task manager publish progress events;
the WebSocket endpoint (/api/ws/events) fans them out to every connected UI.

Events never contain chain-of-thought — only what SIA is doing (agent, tool, status,
approval requests) and short result summaries.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from backend.core.logger import get_logger, redact

log = get_logger("events")


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._recent: list[dict] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, type_: str, task_id: str = "", **data: Any) -> None:
        evt = {"type": type_, "task_id": task_id, "ts": time.time(), **data}
        for k, v in list(evt.items()):
            if isinstance(v, str):
                evt[k] = redact(v)
        self._recent.append(evt)
        self._recent = self._recent[-300:]
        for q in list(self._subs):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass

    def recent(self, task_id: str = "", limit: int = 100) -> list[dict]:
        ev = [e for e in self._recent if not task_id or e["task_id"] == task_id]
        return ev[-limit:]


bus = EventBus()
