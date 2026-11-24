"""API smoke tests: lifespan boots, /chat keeps its response shape, tasks/approvals/memory endpoints work."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from backend.tests.conftest import FakeModel, plan, tool_call


@pytest.fixture
def client(isolated, monkeypatch):
    from backend.core.config import settings
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test")
    monkeypatch.setattr(settings, "CHAT_WAIT_SECONDS", 5)
    import backend.memory.conversation_store as cs
    monkeypatch.setattr(cs.conversation_store, "_path", isolated / "history.json", raising=False)
    from backend.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["engine"] == "graph"


def test_chat_shape_and_task(client):
    from backend.llm.models import set_model_override
    set_model_override("planner", FakeModel([plan(mode="respond")]))
    set_model_override("chat", FakeModel([AIMessage(content='{"type":"chat","message":"Hello Boss!"}')]))
    r = client.post("/api/chat", json={"message": "hi"}).json()
    for k in ("text", "spoken", "lang", "type", "intent", "action_results", "elapsed_ms"):
        assert k in r
    assert r["text"] == "Hello Boss!" and r["task_status"] == "completed"
    t = client.get(f"/api/tasks/{r['task_id']}").json()
    assert t["status"] == "completed"


def test_approval_flow_over_http(client, isolated):
    f = isolated / "allowed" / "x.txt"
    f.write_text("1")
    from backend.llm.models import set_model_override
    set_model_override("planner", FakeModel([
        plan(("productivity", "overwrite")),
        tool_call("write_text_file", {"path": str(f), "content": "2", "overwrite": True}),
        AIMessage(content="overwritten"),
    ]))
    r = client.post("/api/chat", json={"message": "overwrite x"}).json()
    assert r["task_status"] == "waiting_approval"
    aps = client.get("/api/approvals").json()["approvals"]
    assert len(aps) == 1
    client.post(f"/api/approvals/{aps[0]['id']}", json={"approved": True})
    for _ in range(50):
        t = client.get(f"/api/tasks/{r['task_id']}").json()
        if t["status"] == "completed":
            break
        time.sleep(0.05)
    assert t["status"] == "completed" and f.read_text() == "2"
    assert client.post(f"/api/approvals/{aps[0]['id']}", json={"approved": True}).status_code == 409


def test_memory_crud_refuses_secrets(client):
    ok = client.post("/api/memory/items", json={"category": "profile", "key": "email", "value": "a@b.com"})
    assert ok.status_code == 200
    bad = client.post("/api/memory/items", json={"category": "fact", "key": "gmail password", "value": "hunter2"})
    assert bad.status_code == 400
    items = client.get("/api/memory/items").json()["items"]
    assert [i["key"] for i in items] == ["email"]
    assert client.delete(f"/api/memory/items/{items[0]['id']}").json()["deleted"]


def test_permissions_cannot_auto_approve_level3(client):
    r = client.put("/api/settings/permissions", json={"auto_approve_level": 1}).json()
    assert r["auto_approve_level"] == 1
    assert client.put("/api/settings/permissions", json={"auto_approve_level": 3}).status_code == 422


def test_schedules_crud(client):
    r = client.post("/api/schedules", json={"preset": "daily_briefing"}).json()
    assert r["cron"] == "0 8 * * *"
    assert client.post("/api/schedules", json={"name": "x", "goal": "y", "cron": "bad"}).status_code == 400
    assert len(client.get("/api/schedules").json()["schedules"]) == 1
    assert client.delete(f"/api/schedules/{r['id']}").json()["deleted"]


def test_tools_and_agents_listed(client):
    names = {t["name"] for t in client.get("/api/tools").json()["tools"]}
    assert {"browser_open", "system_action", "gmail_send", "run_command"} <= names
    assert len(client.get("/api/agents").json()["agents"]) == 10


def test_websocket_hello(client):
    with client.websocket_connect("/api/ws/events") as ws:
        assert ws.receive_json()["type"] == "hello"


def test_stream_tasks_and_cancel_endpoints(client):
    from backend.llm.models import set_model_override
    set_model_override("planner", FakeModel([plan(mode="respond"), plan(mode="respond")]))
    set_model_override("chat", FakeModel([AIMessage(content="streamed hi"), AIMessage(content="second")]))
    r = client.post("/api/chat/stream", json={"message": "hello"})
    assert "streamed hi" in r.text and "[DONE]" in r.text
    tid = client.post("/api/tasks", json={"goal": "hello again"}).json()["task_id"]
    for _ in range(50):
        if client.get(f"/api/tasks/{tid}").json()["status"] == "completed":
            break
        time.sleep(0.05)
    assert client.get("/api/tasks").json()["tasks"][0]["id"] == tid
    assert client.post(f"/api/tasks/{tid}/cancel").json()["cancelled"] is False   # already finished
    assert client.get("/api/tasks/nope").status_code == 404


def test_legacy_engine_switch(client, monkeypatch):
    from backend.core.config import settings
    monkeypatch.setattr(settings, "SIA_ENGINE", "legacy")
    import types, sys
    mod = types.ModuleType("backend.actions.system_actions")

    async def fake(action, params):
        return "ok"
    mod.execute_action = fake
    monkeypatch.setitem(sys.modules, "backend.actions.system_actions", mod)
    import backend.agent.agent as ag
    monkeypatch.setattr(ag.agent, "_save_memory", lambda *a: None)
    r = client.post("/api/chat", json={"message": "volume up"}).json()
    assert r["intent"] == "volume_up" and "task_id" not in r
