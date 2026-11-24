"""Edge-case scenarios real models produce: parallel tool calls, pauses mid-batch, replans, limits, errors."""
from __future__ import annotations

import asyncio
import sys
import types

from langchain_core.messages import AIMessage

from backend.tests.conftest import FakeModel, plan, tool_call
from backend.tests.test_graph import _set, _tm


def multi_call(*calls):
    import uuid
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c_{uuid.uuid4().hex[:6]}", "type": "tool_call"}
                                            for n, a in calls])


async def test_parallel_calls_with_login_pause_keep_valid_message_order(monkeypatch):
    """Model asks for 2 tools at once; the first hits a login wall. All calls must be answered
    before the user-wait message, otherwise OpenAI rejects the next request."""
    from backend.tools.registry import ToolResult, load_all_tools
    reg = load_all_tools()
    n = {"open": 0}

    async def fake_open(url):
        n["open"] += 1
        return ToolResult(ok=True, output="login page", needs_user="login_required") if n["open"] == 1 else \
            ToolResult(ok=True, output="inbox")

    async def fake_snap():
        return ToolResult(ok=True, output="snapshot")
    monkeypatch.setattr(reg.get("browser_open"), "func", fake_open)
    monkeypatch.setattr(reg.get("browser_snapshot"), "func", fake_snap)
    model = FakeModel([
        plan(("browser", "open gmail")),
        multi_call(("browser_open", {"url": "gmail.com"}), ("browser_snapshot", {})),
        tool_call("browser_open", {"url": "gmail.com"}),
        AIMessage(content="Inbox open"),
    ])
    _set(planner=model)
    tm = await _tm()
    tid = tm.start("open gmail")
    t = await tm.wait(tid, 10)
    assert t["status"] == "waiting_input"
    tm.respond(tm.pending_approvals()[0]["id"], text="done")
    await asyncio.sleep(0.05)
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed", t


async def test_parallel_calls_where_second_needs_approval(isolated):
    a, b = isolated / "allowed" / "a.txt", isolated / "allowed" / "b.txt"
    b.write_text("old")
    _set(planner=FakeModel([
        plan(("productivity", "write two files")),
        multi_call(("write_text_file", {"path": str(a), "content": "A"}),
                   ("write_text_file", {"path": str(b), "content": "B", "overwrite": True})),
        AIMessage(content="Both written"),
    ]))
    tm = await _tm()
    tid = tm.start("write files")
    t = await tm.wait(tid, 10)
    assert t["status"] == "waiting_approval" and a.read_text() == "A" and b.read_text() == "old"
    tm.respond(tm.pending_approvals()[0]["id"], approved=True)
    await asyncio.sleep(0.05)
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed" and b.read_text() == "B"
    steps = [s for s in tm.get(tid)["steps"] if s["tool"] == "write_text_file"]
    assert len(steps) == 2                                  # first call did NOT run twice after resume


async def test_replan_recovers_with_another_agent(isolated):
    out = isolated / "allowed" / "ok.txt"
    _set(planner=FakeModel([
        plan(("knowledge", "find the answer")),
        AIMessage(content="FAILED: no documents uploaded"),
        plan(("productivity", "write ok.txt")),
        tool_call("write_text_file", {"path": str(out), "content": "ok"}),
        AIMessage(content="Wrote ok.txt"),
    ]))
    tm = await _tm()
    tid = tm.start("do it")
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed" and out.exists(), t


async def test_agent_step_limit(monkeypatch):
    from backend.core.config import settings
    monkeypatch.setattr(settings, "AGENT_MAX_STEPS", 3)
    monkeypatch.setattr(settings, "TASK_MAX_REPLANS", 0)
    loop = [tool_call("get_current_time", {}) for _ in range(10)]
    _set(planner=FakeModel([plan(("knowledge", "loop"))] + loop))
    tm = await _tm()
    tid = tm.start("loop forever")
    t = await tm.wait(tid, 10)
    assert t["status"] == "failed" and "stopped after 3" in t["result"]


async def test_missing_api_keys_fail_gracefully(monkeypatch):
    from backend.core.config import settings
    for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setattr(settings, k, "")
    tm = await _tm()
    tid = tm.start("search the web for news")
    t = await tm.wait(tid, 10)
    assert t["status"] == "failed" and "API key" in t["result"]


async def test_bad_tool_arguments_are_reported_to_the_model(isolated):
    seen = {}

    def after(msgs):
        seen["tool_reply"] = msgs[-1].content
        return AIMessage(content="FAILED: could not")
    _set(planner=FakeModel([
        plan(("productivity", "x")),
        tool_call("write_text_file", {"content": "no path"}),
        after,
        plan(mode="respond"),
    ]))
    tm = await _tm()
    tid = tm.start("x")
    await tm.wait(tid, 10)
    assert "Invalid arguments" in seen["tool_reply"] and "path" in seen["tool_reply"]


async def test_follow_up_sees_conversation_history():
    seen = {}

    def capture(msgs):
        seen["texts"] = [m.content for m in msgs]
        return plan(mode="respond")
    _set(planner=FakeModel([capture]), chat=FakeModel([AIMessage(content="Replying to Priya.")]))
    tm = await _tm()
    tid = tm.start("reply to her saying I will attend",
                   history=[{"role": "user", "content": "summarise the email from Priya"},
                            {"role": "assistant", "content": "Priya invited you to the Friday interview."}])
    await tm.wait(tid, 10)
    assert any("Priya invited" in str(t) for t in seen["texts"])


async def test_expired_approval_cannot_be_used(isolated, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import pytest
    from backend.database.db import session_scope
    from backend.database.models import Approval
    f = isolated / "allowed" / "e.txt"
    f.write_text("x")
    _set(planner=FakeModel([plan(("productivity", "o")),
                            tool_call("write_text_file", {"path": str(f), "content": "y", "overwrite": True})]))
    tm = await _tm()
    tid = tm.start("o")
    await tm.wait(tid, 10)
    aid = tm.pending_approvals()[0]["id"]
    with session_scope() as s:
        s.get(Approval, aid).created_at = datetime.now(timezone.utc) - timedelta(hours=5)
    with pytest.raises(ValueError):
        tm.respond(aid, approved=True)
    assert f.read_text() == "x"


async def test_legacy_engine_still_works(monkeypatch):
    """SIA_ENGINE=legacy keeps the original agent.process path (regex + JSON plan)."""
    calls = []

    async def fake_exec(action, params):
        calls.append(action)
        return "ok"
    mod = types.ModuleType("backend.actions.system_actions")
    mod.execute_action = fake_exec
    monkeypatch.setitem(sys.modules, "backend.actions.system_actions", mod)
    from backend.agent.agent import agent
    import backend.agent.agent as ag
    monkeypatch.setattr(ag.agent, "_save_memory", lambda *a: None)
    r = await agent.process("volume up")
    assert calls == ["volume_up"] and r.text

    async def fake_respond(text, history, detected_lang="en-IN"):
        return {"type": "action", "intent": "open", "spoken": "Opening.",
                "plan": [{"step": 1, "action": "open_app", "params": {"app_name": "notepad"}}]}
    monkeypatch.setattr(ag.llm_service, "respond", fake_respond)
    r = await agent.process("could you launch the notepad application for me")
    assert calls[-1] == "open_app" and r.spoken == "Opening."


async def test_chat_reply_that_turns_into_an_action_actually_runs_it(monkeypatch):
    calls = []

    async def fake_exec(action, params):
        calls.append(action)
        return "YouTube playing"
    mod = types.ModuleType("backend.actions.system_actions")
    mod.execute_action = fake_exec
    monkeypatch.setitem(sys.modules, "backend.actions.system_actions", mod)
    _set(planner=FakeModel([plan(mode="respond")]),
         chat=FakeModel([AIMessage(content='{"type":"action","intent":"yt","spoken":"Playing it, Boss!",'
                                           '"plan":[{"step":1,"action":"youtube_play","params":{"query":"lofi"}}]}')]))
    tm = await _tm()
    tid = tm.start("put on some lofi")
    t = await tm.wait(tid, 10)
    assert calls == ["youtube_play"] and t["status"] == "completed" and "Playing" in t["result"]


async def test_task_running_at_shutdown_resumes_on_next_start(isolated):
    out = isolated / "allowed" / "resume.txt"
    cp = str(isolated / "cp2.db")
    gate = asyncio.Event()

    class Blocking(FakeModel):
        async def ainvoke(self, messages, *a, **kw):
            if len(self.calls) == 1:          # 2nd LLM call (agent) blocks → backend "stops" here
                self.calls.append(messages)
                await gate.wait()
            return await super().ainvoke(messages, *a, **kw)
    _set(planner=Blocking([plan(("productivity", "write resume.txt"))]))
    tm1 = await _tm(cp)
    tid = tm1.start("write resume.txt")
    await asyncio.sleep(0.3)
    await tm1.shutdown()
    assert tm1.get(tid)["status"] == "in_progress"
    _set(planner=FakeModel([tool_call("write_text_file", {"path": str(out), "content": "back"}),
                            AIMessage(content="Wrote it")]))
    tm2 = await _tm(cp)
    assert await tm2.recover() == 1
    await asyncio.sleep(0.05)
    t = await tm2.wait(tid, 10)
    await tm2.shutdown()
    assert t["status"] == "completed" and out.read_text() == "back"


async def test_scheduled_task_uses_same_approval_gate(isolated):
    from backend.background_workers.scheduler import scheduler
    import backend.task_manager.manager as mgr
    f = isolated / "allowed" / "s.txt"
    f.write_text("x")
    _set(planner=FakeModel([plan(("productivity", "o")),
                            tool_call("write_text_file", {"path": str(f), "content": "y", "overwrite": True})]))
    tm = await _tm()
    mgr_tm = mgr.task_manager
    mgr.task_manager = tm
    try:
        sched = scheduler.create("t", "overwrite s.txt", "0 8 * * *")
        await scheduler.run_now(sched["id"])
        await asyncio.sleep(0.3)
        tasks = tm.list()
        assert tasks[0]["source"] == "schedule" and tasks[0]["status"] == "waiting_approval"
        assert f.read_text() == "x"
    finally:
        mgr.task_manager = mgr_tm


async def test_cancel_publishes_result_for_ui():
    from backend.task_manager.events import bus

    class Slow(FakeModel):
        async def ainvoke(self, messages, *a, **kw):
            await asyncio.sleep(5)
    _set(planner=Slow())
    tm = await _tm()
    tid = tm.start("long")
    await asyncio.sleep(0.1)
    tm.cancel(tid)
    await asyncio.sleep(0.1)
    ev = [e for e in bus.recent(tid) if e["type"] == "task_result"]
    assert ev and ev[-1]["status"] == "cancelled"
