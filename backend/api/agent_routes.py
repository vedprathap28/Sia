"""
SIA — API for the agent engine: tasks, approvals, live events, memory, permissions, schedules, tools.
Mounted under /api next to the original routes (which are unchanged in shape).
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.core.logger import get_logger
from backend.task_manager.events import bus
from backend.task_manager.manager import task_manager

log = get_logger("agent_routes")
router = APIRouter()


# ── Live events ───────────────────────────────────────────────────────────────

@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_json({"type": "hello", "pending_approvals": task_manager.pending_approvals(),
                            "active_tasks": task_manager.list(20, active_only=True)})
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=25)
                await ws.send_json(evt)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


@router.get("/events/recent")
async def recent_events(task_id: str = "", limit: int = 100):
    return {"events": bus.recent(task_id, limit)}


# ── Tasks ─────────────────────────────────────────────────────────────────────

class TaskIn(BaseModel):
    goal: str
    source: Literal["chat", "voice", "schedule"] = "chat"
    lang: str = ""


@router.post("/tasks")
async def create_task(body: TaskIn):
    from backend.agent.router import _detect_language
    from backend.memory.conversation_store import conversation_store
    tid = task_manager.start(body.goal, source=body.source, lang=body.lang or _detect_language(body.goal),
                             history=conversation_store.get_history(limit=10))
    return {"task_id": tid}


@router.get("/tasks")
async def list_tasks(limit: int = 50, active: bool = False):
    return {"tasks": task_manager.list(limit, active_only=active)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    t = task_manager.get(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    t["events"] = bus.recent(task_id, 200)
    return t


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    return {"cancelled": task_manager.cancel(task_id)}


# ── Approvals & answers ───────────────────────────────────────────────────────

class ApprovalIn(BaseModel):
    approved: Optional[bool] = None
    confirm_text: str = ""
    note: str = ""
    text: str = ""          # answer for input requests


@router.get("/approvals")
async def list_approvals():
    return {"approvals": task_manager.pending_approvals()}


@router.post("/approvals/{approval_id}")
async def answer_approval(approval_id: str, body: ApprovalIn):
    try:
        return task_manager.respond(approval_id, approved=body.approved, text=body.text,
                                    confirm_text=body.confirm_text, note=body.note)
    except KeyError:
        raise HTTPException(404, "approval not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


# ── Memory ────────────────────────────────────────────────────────────────────

class MemoryIn(BaseModel):
    category: Literal["preference", "profile", "project", "workflow", "fact"] = "fact"
    key: str
    value: str


@router.get("/memory/items")
async def memory_items(q: str = "", category: str = ""):
    from backend.memory.store import memory_store
    return {"items": memory_store.search(q, category or None, limit=500)}


@router.post("/memory/items")
async def memory_add(body: MemoryIn):
    from backend.memory.store import memory_store
    item = memory_store.upsert(body.category, body.key, body.value, source="user")
    if item is None:
        raise HTTPException(400, "Not stored: empty, or looks like a password/secret. Secrets belong in the "
                                 "Windows Credential Manager, not memory.")
    return item


@router.delete("/memory/items/{item_id}")
async def memory_delete(item_id: str):
    from backend.memory.store import memory_store
    return {"deleted": memory_store.delete(item_id)}


@router.delete("/memory/items")
async def memory_clear(category: str = ""):
    from backend.memory.store import memory_store
    return {"deleted": memory_store.clear(category or None)}


# ── Permissions / settings ────────────────────────────────────────────────────

class PermIn(BaseModel):
    auto_approve_level: Optional[int] = Field(default=None, ge=1, le=2)
    level4_policy: Optional[Literal["confirm", "block"]] = None


def _set_setting(key: str, value: str):
    from backend.database.db import session_scope
    from backend.database.models import Setting
    with session_scope() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))


@router.get("/settings/permissions")
async def get_permissions():
    from backend.security.permissions import RISK_LABEL, auto_approve_level, level4_policy
    from backend.security.sandbox import allowed_roots
    return {"auto_approve_level": auto_approve_level(), "level4_policy": level4_policy(),
            "levels": RISK_LABEL, "allowed_roots": [str(r) for r in allowed_roots()],
            "engine": settings.SIA_ENGINE, "planner_model": f"{settings.PLANNER_PROVIDER}/{settings.PLANNER_MODEL}",
            "chat_model": f"{settings.CHAT_PROVIDER}/{settings.CHAT_MODEL}"}


@router.put("/settings/permissions")
async def put_permissions(body: PermIn):
    if body.auto_approve_level is not None:
        _set_setting("auto_approve_level", str(body.auto_approve_level))
    if body.level4_policy is not None:
        _set_setting("level4_policy", body.level4_policy)
    return await get_permissions()


# ── Schedules ─────────────────────────────────────────────────────────────────

class ScheduleIn(BaseModel):
    name: str = ""
    goal: str = ""
    cron: str = ""
    enabled: bool = True
    preset: str = ""


@router.get("/schedules")
async def schedules():
    from backend.background_workers.scheduler import PRESETS, scheduler
    return {"schedules": scheduler.list(), "presets": {k: {"name": v[0], "cron": v[1], "goal": v[2]}
                                                       for k, v in PRESETS.items()}}


@router.post("/schedules")
async def schedule_create(body: ScheduleIn):
    from backend.background_workers.scheduler import PRESETS, scheduler
    name, cron, goal = body.name, body.cron, body.goal
    if body.preset in PRESETS:
        pn, pc, pg = PRESETS[body.preset]
        name, cron, goal = name or pn, cron or pc, goal or pg
    if not (name and cron and goal):
        raise HTTPException(400, "name, cron and goal are required")
    try:
        return scheduler.create(name, goal, cron, body.enabled)
    except ValueError as e:
        raise HTTPException(400, f"Invalid cron: {e}")


@router.patch("/schedules/{sid}")
async def schedule_update(sid: str, body: ScheduleIn):
    from backend.background_workers.scheduler import scheduler
    d = scheduler.update(sid, name=body.name or None, goal=body.goal or None, cron=body.cron or None,
                         enabled=body.enabled)
    if not d:
        raise HTTPException(404, "schedule not found")
    return d


@router.post("/schedules/{sid}/run")
async def schedule_run(sid: str):
    from backend.background_workers.scheduler import scheduler
    await scheduler.run_now(sid)
    return {"ok": True}


@router.delete("/schedules/{sid}")
async def schedule_delete(sid: str):
    from backend.background_workers.scheduler import scheduler
    return {"deleted": scheduler.delete(sid)}


# ── Introspection ─────────────────────────────────────────────────────────────

@router.get("/tools")
async def tools():
    from backend.tools.registry import load_all_tools
    reg = load_all_tools()
    out = []
    for t in reg.all():
        risk = t.risk if isinstance(t.risk, int) else "depends on arguments"
        out.append({"name": t.name, "category": t.category, "risk": risk, "description": t.description[:300]})
    return {"tools": sorted(out, key=lambda x: (x["category"], x["name"]))}


@router.get("/agents")
async def agents():
    from backend.graph.agents import AGENTS
    return {"agents": [{"name": a.name, "description": a.description} for a in AGENTS.values()]}


@router.get("/applications")
async def applications():
    from sqlalchemy import select
    from backend.database.db import session_scope
    from backend.database.models import JobApplication
    with session_scope() as s:
        rows = s.scalars(select(JobApplication).order_by(JobApplication.updated_at.desc())).all()
        return {"applications": [{"id": r.id, "company": r.company, "role": r.role, "url": r.url, "status": r.status,
                                  "match_score": r.match_score, "notes": r.notes,
                                  "updated_at": r.updated_at.isoformat()} for r in rows]}
