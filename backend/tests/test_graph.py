"""End-to-end orchestration tests with scripted fake LLMs (no network, no Windows)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from backend.tests.conftest import FakeModel, plan, tool_call


async def _tm(checkpoint: str | None = None):
    from langgraph.checkpoint.memory import InMemorySaver
    from backend.graph.supervisor import build_graph
    from backend.task_manager.manager import TaskManager
    tm = TaskManager()
    if checkpoint:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        tm._saver_cm = AsyncSqliteSaver.from_conn_string(checkpoint)
        saver = await tm._saver_cm.__aenter__()
        await tm.startup(build_graph(saver))
    else:
        await tm.startup(build_graph(InMemorySaver()))
    return tm


def _set(planner=None, chat=None):
    from backend.llm.models import set_model_override
    if planner:
        set_model_override("planner", planner)
    if chat:
        set_model_override("chat", chat)


async def test_respond_mode_uses_chat_model():
    _set(planner=FakeModel([plan(mode="respond")]), chat=FakeModel([AIMessage(content="Hey! All good here.")]))
    tm = await _tm()
    tid = tm.start("how are you?")
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed"
    assert "All good" in t["result"]


async def test_file_task_executes_and_verifies(isolated):
    target = isolated / "allowed" / "notes.txt"
    _set(planner=FakeModel([
        plan(("productivity", "Create notes.txt with hello")),
        tool_call("write_text_file", {"path": str(target), "content": "hello"}),
        AIMessage(content=f"Created {target}"),
    ]))
    tm = await _tm()
    tid = tm.start("create a notes file saying hello")
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed", t
    assert target.read_text() == "hello"
    assert t["verified"] is True
    full = tm.get(tid)
    assert any(s["tool"] == "write_text_file" and s["verified"] for s in full["steps"])


async def test_consequential_action_waits_for_approval_then_runs(isolated):
    target = isolated / "allowed" / "a.txt"
    target.write_text("old")
    _set(planner=FakeModel([
        plan(("productivity", "overwrite a.txt")),
        tool_call("write_text_file", {"path": str(target), "content": "new", "overwrite": True}),
        AIMessage(content="Overwrote a.txt"),
    ]))
    tm = await _tm()
    tid = tm.start("overwrite a.txt with new")
    t = await tm.wait(tid, 10)
    assert t["status"] == "waiting_approval"
    assert target.read_text() == "old"              # nothing ran before approval
    ap = tm.pending_approvals()[0]
    assert ap["risk"] == 3 and "overwrite" in ap["summary"]
    tm.respond(ap["id"], approved=True)
    await asyncio.sleep(0.05)
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed"
    assert target.read_text() == "new"


async def test_denied_action_is_not_executed(isolated):
    target = isolated / "allowed" / "b.txt"
    target.write_text("keep")
    _set(planner=FakeModel([
        plan(("productivity", "overwrite b.txt")),
        tool_call("write_text_file", {"path": str(target), "content": "x", "overwrite": True}),
        AIMessage(content="FAILED: the user denied overwriting the file"),
        plan(mode="respond"),                        # replan gives up
    ]))
    tm = await _tm()
    tid = tm.start("overwrite b.txt")
    await tm.wait(tid, 10)
    ap = tm.pending_approvals()[0]
    tm.respond(ap["id"], approved=False, note="no thanks")
    await asyncio.sleep(0.05)
    t = await tm.wait(tid, 10)
    assert target.read_text() == "keep"
    assert t["status"] == "failed"
    assert "couldn't finish" in t["result"].lower()


async def test_level4_requires_typed_confirmation(isolated):
    f = isolated / "allowed" / "gone.txt"
    f.write_text("x")
    _set(planner=FakeModel([
        plan(("productivity", "delete gone.txt")),
        tool_call("delete_path", {"path": str(f)}),
        AIMessage(content="FAILED: not confirmed"),
        plan(mode="respond"),
    ]))
    tm = await _tm()
    tid = tm.start("delete gone.txt")
    await tm.wait(tid, 10)
    ap = tm.pending_approvals()[0]
    assert ap["risk"] == 4
    tm.respond(ap["id"], approved=True, confirm_text="yes")   # wrong confirmation text → treated as denied
    await asyncio.sleep(0.05)
    await tm.wait(tid, 10)
    assert f.exists()


async def test_fast_path_runs_without_llm(monkeypatch):
    calls = []

    async def fake_exec(action, params):
        calls.append((action, params))
        return "Volume up by 10"
    import backend.actions  # noqa
    import types, sys
    mod = types.ModuleType("backend.actions.system_actions")
    mod.execute_action = fake_exec
    monkeypatch.setitem(sys.modules, "backend.actions.system_actions", mod)
    planner = FakeModel([])
    _set(planner=planner)
    tm = await _tm()
    tid = tm.start("volume up")
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed", t
    assert calls == [("volume_up", {"amount": 10})]
    assert planner.calls == []                       # no LLM involved


async def test_fast_path_shutdown_needs_approval(monkeypatch):
    calls = []

    async def fake_exec(action, params):
        calls.append(action)
        return "Shutting down"
    import types, sys
    mod = types.ModuleType("backend.actions.system_actions")
    mod.execute_action = fake_exec
    monkeypatch.setitem(sys.modules, "backend.actions.system_actions", mod)
    _set(planner=FakeModel([]))
    tm = await _tm()
    tid = tm.start("shutdown the pc")
    t = await tm.wait(tid, 10)
    assert t["status"] == "waiting_approval" and calls == []
    tm.respond(tm.pending_approvals()[0]["id"], approved=False)
    await asyncio.sleep(0.05)
    t = await tm.wait(tid, 10)
    assert calls == [] and t["status"] == "failed"


async def test_ask_user_interrupt_and_answer(isolated):
    target = isolated / "allowed" / "name.txt"
    _set(planner=FakeModel([
        plan(("productivity", "write the user's name")),
        tool_call("ask_user", {"question": "What name should I write?"}),
        lambda msgs: tool_call("write_text_file", {"path": str(target),
                                                   "content": msgs[-1].content.split(": ", 1)[1]}),
        AIMessage(content="Wrote the name"),
    ]))
    tm = await _tm()
    tid = tm.start("write my name to a file")
    t = await tm.wait(tid, 10)
    assert t["status"] == "waiting_input"
    ap = tm.pending_approvals()[0]
    assert ap["kind"] == "input"
    tm.respond(ap["id"], text="Ved")
    await asyncio.sleep(0.05)
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed" and target.read_text() == "Ved"


async def test_multi_step_plan_shares_results(isolated):
    src = isolated / "allowed" / "data.csv"
    src.write_text("name,score\na,1\nb,3\n")
    out = isolated / "allowed" / "report.docx"
    seen = {}

    def step2(msgs):
        seen["system"] = msgs[0].content
        return tool_call("create_word_document", {"path": str(out), "title": "Report", "markdown": "- mean 2"})
    _set(planner=FakeModel([
        plan(("knowledge", "analyse data.csv"), ("productivity", "write report.docx with the stats")),
        tool_call("analyse_spreadsheet", {"path": str(src)}),
        AIMessage(content="score mean is 2"),
        step2,
        AIMessage(content=f"Created {out}"),
        AIMessage(content=f"Analysed the data (mean score 2) and saved the report to {out}."),
    ]))
    tm = await _tm()
    tid = tm.start("analyse data.csv and write a report")
    t = await tm.wait(tid, 15)
    assert t["status"] == "completed", t
    assert out.exists()
    assert "score mean is 2" in seen["system"]          # step 2 saw step 1's result
    assert "mean score 2" in t["result"]


async def test_unknown_tool_for_agent_is_rejected(isolated):
    _set(planner=FakeModel([
        plan(("knowledge", "do something")),
        tool_call("delete_path", {"path": "x"}),          # not in knowledge agent's toolset
        AIMessage(content="FAILED: tool unavailable"),
        plan(mode="respond"),
    ]))
    tm = await _tm()
    tid = tm.start("x")
    t = await tm.wait(tid, 10)
    assert t["status"] == "failed"
    assert tm.pending_approvals() == []


async def test_cancel_running_task():
    async def slow(msgs):
        await asyncio.sleep(5)
        return AIMessage(content="late")

    class Slow(FakeModel):
        async def ainvoke(self, messages, *a, **kw):
            await asyncio.sleep(5)
            return AIMessage(content="late")
    _set(planner=Slow())
    tm = await _tm()
    tid = tm.start("long task")
    await asyncio.sleep(0.1)
    assert tm.cancel(tid)
    await asyncio.sleep(0.1)
    assert tm.get(tid)["status"] == "cancelled"


async def test_resume_after_restart_from_sqlite_checkpoint(isolated):
    """Backend 'restarts' while waiting for approval: a new manager resumes from the checkpoint."""
    target = isolated / "allowed" / "r.txt"
    target.write_text("old")
    cp = str(isolated / "cp.db")
    _set(planner=FakeModel([
        plan(("productivity", "overwrite r.txt")),
        tool_call("write_text_file", {"path": str(target), "content": "resumed", "overwrite": True}),
    ]))
    tm1 = await _tm(cp)
    tid = tm1.start("overwrite r.txt")
    await tm1.wait(tid, 10)
    ap = tm1.pending_approvals()[0]
    await tm1.shutdown()

    _set(planner=FakeModel([AIMessage(content="Done after restart")]))
    tm2 = await _tm(cp)
    tm2.respond(ap["id"], approved=True)
    await asyncio.sleep(0.05)
    t = await tm2.wait(tid, 10)
    await tm2.shutdown()
    assert t["status"] == "completed", t
    assert target.read_text() == "resumed"


async def test_login_page_pauses_for_user(monkeypatch):
    from backend.tools.registry import ToolResult, load_all_tools
    reg = load_all_tools()
    spec = reg.get("browser_open")
    calls = {"n": 0}

    async def fake_open(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return ToolResult(ok=True, output="URL: https://accounts.google.com login", needs_user="login_required")
        return ToolResult(ok=True, output="URL: https://mail.google.com inbox")
    monkeypatch.setattr(spec, "func", fake_open)
    _set(planner=FakeModel([
        plan(("browser", "open gmail")),
        tool_call("browser_open", {"url": "gmail.com"}),
        tool_call("browser_open", {"url": "gmail.com"}),
        AIMessage(content="Gmail inbox is open"),
    ]))
    tm = await _tm()
    tid = tm.start("open gmail")
    t = await tm.wait(tid, 10)
    assert t["status"] == "waiting_input"
    ap = tm.pending_approvals()[0]
    assert "sign in" in ap["summary"]
    tm.respond(ap["id"], text="done")
    await asyncio.sleep(0.05)
    t = await tm.wait(tid, 10)
    assert t["status"] == "completed" and calls["n"] == 2
