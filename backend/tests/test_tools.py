"""Tool registry + file/productivity/dev tools against a temp sandbox."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from backend.tools.registry import ToolRegistry, ToolResult, load_all_tools, tool_def


@pytest.fixture
def reg():
    return load_all_tools()


def test_every_tool_has_valid_schema_and_description(reg):
    for t in reg.all():
        s = t.openai_schema()
        assert s["function"]["name"] == t.name and len(t.description) > 15, t.name
        assert s["function"]["parameters"]["type"] == "object"


def test_every_agent_tool_exists():
    from backend.graph.agents import AGENTS
    reg = load_all_tools()
    for a in AGENTS.values():
        for n in a.extra_tools:
            assert reg.get(n), f"{a.name} references missing tool {n}"
        assert any(t.name == "ask_user" for t in a.tool_specs())


async def test_validation_timeout_and_retry():
    r = ToolRegistry()
    n = {"c": 0}

    @tool_def(category="t", reg=r, retries=2)
    def flaky(x: int) -> ToolResult:
        "flaky test tool"
        n["c"] += 1
        return ToolResult(ok=n["c"] >= 2, output="ok")

    @tool_def(category="t", reg=r, timeout=0.2)
    async def slow() -> str:
        "slow test tool"
        await asyncio.sleep(2)
        return "x"
    assert not (await r.execute("flaky", {"x": "nope"})).ok          # validation error
    res = await r.execute("flaky", {"x": 1})
    assert res.ok and n["c"] == 2                                   # retried once
    res = await r.execute("slow", {})
    assert not res.ok and "Timed out" in res.error
    assert not (await r.execute("missing", {})).ok


async def test_file_tools_roundtrip(reg, isolated):
    d = isolated / "allowed"
    (d / "a.pdf").write_bytes(b"%PDF")
    (d / "b.jpg").write_bytes(b"x")
    (d / "resume_2024.docx").write_bytes(b"x")
    r = await reg.execute("search_files", {"query": "resume", "root": str(d)})
    assert r.ok and "resume_2024.docx" in r.output
    r = await reg.execute("organise_folder", {"folder": str(d), "dry_run": True})
    assert "a.pdf → Documents/" in r.output and (d / "a.pdf").exists()
    assert reg.get("organise_folder").risk_for({"dry_run": False}) == 2
    r = await reg.execute("organise_folder", {"folder": str(d), "dry_run": False})
    assert r.ok and (d / "Images" / "b.jpg").exists() and r.verified
    r = await reg.execute("move_or_copy", {"src": str(d / "Images" / "b.jpg"), "dst": str(d), "keep_original": True})
    assert r.ok and r.verified and (d / "b.jpg").exists()
    r = await reg.execute("write_text_file", {"path": str(isolated / "elsewhere.txt"), "content": "x"})
    assert not r.ok and "outside the allowed folders" in r.error


async def test_productivity_documents(reg, isolated):
    d = isolated / "allowed"
    r = await reg.execute("create_word_document", {"path": str(d / "r"), "title": "Report",
                                                   "markdown": "# Intro\n- **one**\n| a | b |\n|---|---|\n| 1 | 2 |"})
    assert r.ok and r.verified and (d / "r.docx").exists()
    r = await reg.execute("create_spreadsheet", {"path": str(d / "s.xlsx"), "sheets": [
        {"name": "Data", "headers": ["n", "v"], "rows": [["a", 1], ["b", 3], ["total", "=SUM(B2:B3)"]]}]})
    assert r.ok and r.verified
    r = await reg.execute("analyse_spreadsheet", {"path": str(d / "s.xlsx")})
    assert r.ok and "3 rows" in r.output
    r = await reg.execute("create_presentation", {"path": str(d / "p.pptx"), "title": "T",
                                                  "slides": [{"title": "S1", "bullets": ["x", "y"], "notes": "n"}]})
    assert r.ok and r.verified
    r = await reg.execute("read_document", {"path": str(d / "p.pptx")})
    assert "S1" in r.output
    (d / "a.txt").write_text("one\ntwo\n")
    (d / "b.txt").write_text("one\nthree\n")
    r = await reg.execute("compare_documents", {"path_a": str(d / "a.txt"), "path_b": str(d / "b.txt")})
    assert "-two" in r.output and "+three" in r.output


async def test_dev_tools(reg, isolated):
    proj = isolated / "allowed" / "proj"
    proj.mkdir()
    (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (proj / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    r = await reg.execute("inspect_project", {"path": str(proj)})
    assert "calc.py" in r.output
    r = await reg.execute("search_code", {"path": str(proj), "pattern": r"return a - b"})
    assert "calc.py:2" in r.output
    r = await reg.execute("run_tests", {"project": str(proj)})
    assert not r.ok and r.data["failed"] == 1
    spec = reg.get("edit_code")
    args = {"path": str(proj / "calc.py"), "old_text": "a - b", "new_text": "a + b"}
    assert spec.risk_for(args) == 3 and "+    return a + b" in spec.describe_call(args)
    r = await reg.execute("edit_code", args)
    assert r.ok and r.verified
    r = await reg.execute("run_tests", {"project": str(proj)})
    assert r.ok and r.data["passed"] == 1
    r = await reg.execute("run_command", {"command": "rm -rf x", "cwd": str(proj)})
    assert not r.ok and "blocked" in r.error


async def test_query_sia_database_is_read_only(reg):
    r = await reg.execute("query_sia_database", {"sql": "SELECT count(*) FROM tasks"})
    assert r.ok
    r = await reg.execute("query_sia_database", {"sql": "DELETE FROM tasks"})
    assert not r.ok


async def test_legacy_system_action_sandbox_and_failure_detection(reg, monkeypatch, isolated):
    import types
    mod = types.ModuleType("backend.actions.system_actions")

    async def fake(action, params):
        return "Action 'volume_up' failed: no audio device" if action == "volume_up" else "ok"
    mod.execute_action = fake
    monkeypatch.setitem(sys.modules, "backend.actions.system_actions", mod)
    r = await reg.execute("system_action", {"action": "volume_up", "params": {}})
    assert not r.ok
    r = await reg.execute("system_action", {"action": "delete_file", "params": {"path": "/etc/passwd"}})
    assert not r.ok and "outside" in r.error
    assert reg.get("system_action").risk_for({"action": "whatsapp_send"}) == 3
