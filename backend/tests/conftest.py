"""Shared fixtures: isolated DB/workspace per test, fake LLMs, no Windows deps needed."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage  # noqa: E402


def validate_sequence(messages):
    """Enforce the OpenAI/Groq rule: every assistant tool_call must be answered by a ToolMessage
    before any other message, and ToolMessages must answer an outstanding call."""
    from langchain_core.messages import ToolMessage
    pending: set = set()
    for m in messages:
        if isinstance(m, ToolMessage):
            assert m.tool_call_id in pending, f"orphan ToolMessage {m.tool_call_id}"
            pending.discard(m.tool_call_id)
            continue
        assert not pending, f"tool_calls {pending} not answered before {type(m).__name__}"
        if isinstance(m, AIMessage):
            pending = {tc["id"] for tc in (m.tool_calls or [])}
    assert not pending, f"tool_calls {pending} never answered"


class FakeModel:
    """Scripted chat model: returns the queued AIMessages in order; records calls."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []

    def bind_tools(self, tools, **kw):
        return self

    def with_config(self, **kw):
        return self

    async def ainvoke(self, messages, *a, **kw):
        validate_sequence(messages)
        self.calls.append(messages)
        if not self.script:
            return AIMessage(content="FAILED: fake model script exhausted")
        nxt = self.script.pop(0)
        return nxt(messages) if callable(nxt) else nxt

    def invoke(self, messages, *a, **kw):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self.ainvoke(messages))


def tool_call(name, args, cid=None):
    import uuid
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid or f"c_{uuid.uuid4().hex[:6]}",
                                              "type": "tool_call"}])


def plan(*steps, mode="act"):
    return tool_call("make_plan", {"mode": mode, "steps": [{"agent": a, "instruction": i} for a, i in steps]})


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from backend.core.config import settings
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(settings, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(settings, "ALLOWED_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "sia.db"))
    monkeypatch.setattr(settings, "CHECKPOINT_PATH", str(tmp_path / "cp.db"))
    from backend.database.db import init_db
    init_db(str(tmp_path / "sia.db"))
    # Never write test conversations / facts into the user's real data/ folder
    import backend.memory.conversation_store as cs
    monkeypatch.setattr(cs.conversation_store, "_path", tmp_path / "history.json")
    monkeypatch.setattr(cs.conversation_store, "_history", [])
    try:
        import backend.memory.smart_memory as sm
        monkeypatch.setattr(sm, "_FACTS_PATH", tmp_path / "facts.json")
        if hasattr(sm, "ved_facts"):
            monkeypatch.setattr(sm.ved_facts, "_path", tmp_path / "facts.json")
        if hasattr(sm, "smart_memory"):
            monkeypatch.setattr(sm.smart_memory, "save_async", lambda *a, **k: None)
    except Exception:
        pass
    from backend.llm import models
    models._OVERRIDE.clear()
    yield tmp_path
    models._OVERRIDE.clear()
