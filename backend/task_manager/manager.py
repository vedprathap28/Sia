"""
SIA — Task manager: runs graph executions as background tasks with persistent state.

* start()   create a Task row, run the graph in an asyncio task (thread_id = task id)
* resume()  answer an approval / question → Command(resume=…) continues from the checkpoint
* cancel()  stop a running task, expire its pending approvals
* wait()    let /api/chat wait briefly for a quick answer, else return the task id
* recover() on startup, continue tasks that were running when the backend stopped
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.types import Command
from sqlalchemy import select

from backend.core.config import settings
from backend.core.logger import get_logger
from backend.database.db import session_scope, to_json
from backend.database.models import Approval, Task, TaskStatus, TaskStep
from backend.llm.models import current_task_id
from backend.task_manager.events import bus

log = get_logger("task_manager")


def _now():
    return datetime.now(timezone.utc)


def task_dict(t: Task, with_steps: bool = False) -> dict:
    d = {"id": t.id, "goal": t.goal, "status": t.status, "source": t.source, "lang": t.lang, "agent": t.agent,
         "plan": json.loads(t.plan_json or "[]"), "result": t.result, "spoken": t.spoken, "error": t.error,
         "verified": t.verified, "tokens_in": t.tokens_in, "tokens_out": t.tokens_out, "llm_calls": t.llm_calls,
         "cost_usd": round(t.cost_usd, 5), "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat()}
    if with_steps:
        with session_scope() as s:
            steps = s.scalars(select(TaskStep).where(TaskStep.task_id == t.id).order_by(TaskStep.id)).all()
            d["steps"] = [{"kind": x.kind, "agent": x.agent, "tool": x.tool, "summary": x.summary, "ok": x.ok,
                           "verified": x.verified, "risk": x.risk, "duration_ms": x.duration_ms,
                           "at": x.created_at.isoformat()} for x in steps]
            aps = s.scalars(select(Approval).where(Approval.task_id == t.id).order_by(Approval.created_at)).all()
            d["approvals"] = [approval_dict(a) for a in aps]
    return d


def approval_dict(a: Approval) -> dict:
    return {"id": a.id, "task_id": a.task_id, "tool": a.tool, "args": json.loads(a.args_json or "{}"), "risk": a.risk,
            "summary": a.summary, "kind": a.kind, "status": a.status, "response": a.response,
            "created_at": a.created_at.isoformat()}


class TaskManager:
    def __init__(self) -> None:
        self.graph = None
        self._saver_cm = None
        self._running: dict[str, asyncio.Task] = {}
        self._waiters: dict[str, list[asyncio.Future]] = {}
        self._shutting_down = False

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def startup(self, graph=None) -> None:
        if graph is not None:
            self.graph = graph
            return
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from backend.graph.supervisor import build_graph
        from pathlib import Path
        Path(settings.CHECKPOINT_PATH).parent.mkdir(parents=True, exist_ok=True)
        self._saver_cm = AsyncSqliteSaver.from_conn_string(settings.CHECKPOINT_PATH)
        saver = await self._saver_cm.__aenter__()
        self.graph = build_graph(checkpointer=saver)
        log.info("Task manager ready (checkpoints: %s)", settings.CHECKPOINT_PATH)

    async def shutdown(self) -> None:
        # Running tasks stay "in_progress" so recover() continues them on the next start.
        self._shutting_down = True
        running = list(self._running.values())
        for t in running:
            t.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        if self._saver_cm is not None:
            try:
                await self._saver_cm.__aexit__(None, None, None)
            except Exception:
                pass

    # ── DB helpers ────────────────────────────────────────────────────────
    def _update(self, task_id: str, **fields) -> None:
        with session_scope() as s:
            t = s.get(Task, task_id)
            if t:
                for k, v in fields.items():
                    setattr(t, k, v)
        if "status" in fields:
            bus.publish("task_status", task_id, status=fields["status"])

    def get(self, task_id: str, with_steps: bool = True) -> dict | None:
        with session_scope() as s:
            t = s.get(Task, task_id)
            return task_dict(t, with_steps) if t else None

    def list(self, limit: int = 50, active_only: bool = False) -> list[dict]:
        with session_scope() as s:
            q = select(Task).order_by(Task.created_at.desc()).limit(limit)
            if active_only:
                q = q.where(Task.status.in_(TaskStatus.ACTIVE))
            return [task_dict(t) for t in s.scalars(q)]

    def pending_approvals(self) -> list[dict]:
        with session_scope() as s:
            rows = s.scalars(select(Approval).where(Approval.status == "pending").order_by(Approval.created_at)).all()
            return [approval_dict(a) for a in rows]

    # ── run ───────────────────────────────────────────────────────────────
    def start(self, goal: str, *, source: str = "chat", lang: str = "en-IN", history: list[dict] | None = None) -> str:
        with session_scope() as s:
            t = Task(goal=goal, source=source, lang=lang, status=TaskStatus.IN_PROGRESS)
            s.add(t)
            s.flush()
            tid = t.id
        bus.publish("task_created", tid, goal=goal[:300], source=source)
        if source in ("chat", "voice"):
            self._conv("user", goal)
        state = {"task_id": tid, "goal": goal, "lang": lang, "source": source, "history": history or []}
        self._spawn(tid, state)
        return tid

    def _spawn(self, tid: str, payload: Any) -> None:
        task = asyncio.create_task(self._run(tid, payload), name=f"sia-task-{tid}")
        self._running[tid] = task
        # Only forget THIS run: after a fast approval the resumed run may already be registered
        # under the same id before the previous (interrupted) run's callback fires.
        task.add_done_callback(lambda t, tid=tid: self._running.pop(tid, None) if self._running.get(tid) is t else None)

    async def _run(self, tid: str, payload: Any) -> None:
        token = current_task_id.set(tid)
        cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 400}
        interrupted = None
        try:
            self._update(tid, status=TaskStatus.IN_PROGRESS)
            async for chunk in self.graph.astream(payload, config=cfg, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    # keep consuming: breaking early can drop the final checkpoint write
                    interrupted = chunk["__interrupt__"][0]
                    continue
                for node, upd in chunk.items():
                    if isinstance(upd, dict) and "plan" in upd and upd["plan"]:
                        agents = ",".join(dict.fromkeys(p["agent"] for p in upd["plan"]))
                        self._update(tid, plan_json=to_json(upd["plan"]), agent=agents)
            if interrupted is not None:
                self._on_interrupt(tid, interrupted)
                return
            snap = await self.graph.aget_state(cfg)
            st = snap.values
            status = TaskStatus.COMPLETED if st.get("status") == "completed" else TaskStatus.FAILED
            self._update(tid, status=status, result=st.get("final", ""), spoken=st.get("spoken", ""),
                         verified=bool(st.get("verified")), plan_json=to_json(st.get("plan") or []))
            bus.publish("task_result", tid, status=status, text=st.get("final", ""), spoken=st.get("spoken", ""),
                        verified=bool(st.get("verified")))
            if st.get("source") in ("chat", "voice") and st.get("final"):
                self._conv("assistant", st["final"], user_msg=st.get("goal", ""))
        except asyncio.CancelledError:
            if not self._shutting_down:
                self._mark_cancelled(tid)
            raise
        except Exception as e:
            log.exception("Task %s crashed", tid)
            msg = f"{type(e).__name__}: {e}"
            self._update(tid, status=TaskStatus.FAILED, error=msg[:2000],
                         result=f"Something went wrong while working on that: {msg[:300]}")
            bus.publish("task_result", tid, status=TaskStatus.FAILED, text=f"Something went wrong: {msg[:300]}",
                        spoken="Sorry, something went wrong with that task.", verified=False)
        finally:
            current_task_id.reset(token)
            self._notify(tid)

    @staticmethod
    def _conv(role: str, text: str, user_msg: str = "") -> None:
        """Keep the original conversation history + smart memory working for follow-up commands."""
        try:
            from backend.memory.conversation_store import conversation_store
            conversation_store.add(role, text)
            if role == "assistant" and user_msg:
                from backend.memory.smart_memory import smart_memory
                smart_memory.save_async(user_msg, text)
        except Exception as e:
            log.debug("conversation store update skipped: %s", e)

    def _on_interrupt(self, tid: str, intr) -> None:
        v = intr.value if hasattr(intr, "value") else intr
        kind = v.get("type", "approval")
        with session_scope() as s:
            a = Approval(task_id=tid, tool=v.get("tool", "ask_user" if kind == "input" else ""),
                         args_json=to_json(v.get("args", {})), risk=int(v.get("risk", 1)),
                         summary=v.get("summary") or v.get("question", ""), kind=kind)
            s.add(a)
            s.flush()
            ad = approval_dict(a)
        ad["confirm"] = bool(v.get("confirm"))
        ad["risk_label"] = v.get("risk_label", "")
        ad["reason"] = v.get("reason", "")
        status = TaskStatus.WAITING_INPUT if kind == "input" else TaskStatus.WAITING_APPROVAL
        self._update(tid, status=status)
        bus.publish("approval_required" if kind == "approval" else "input_required", tid, approval=ad)
        log.info("Task %s waiting (%s): %s", tid, kind, ad["summary"][:120])

    def _expire_approvals(self, tid: str) -> None:
        with session_scope() as s:
            for a in s.scalars(select(Approval).where(Approval.task_id == tid, Approval.status == "pending")):
                a.status, a.decided_at = "expired", _now()

    # ── user responses ────────────────────────────────────────────────────
    def respond(self, approval_id: str, *, approved: bool | None = None, text: str = "",
                confirm_text: str = "", note: str = "") -> dict:
        with session_scope() as s:
            a = s.get(Approval, approval_id)
            if not a:
                raise KeyError("approval not found")
            if a.status != "pending":
                raise ValueError(f"approval already {a.status}")
            created = a.created_at if a.created_at.tzinfo else a.created_at.replace(tzinfo=timezone.utc)
            if _now() - created > timedelta(minutes=settings.APPROVAL_TIMEOUT_MIN) and a.kind == "approval":
                a.status = "expired"
                raise ValueError("approval expired — ask SIA again")
            tid, kind = a.task_id, a.kind
            if kind == "input":
                a.status, a.response = "answered", text
                value: Any = text or "(no answer)"
            else:
                a.status = "approved" if approved else "denied"
                a.response = note
                value = {"approved": bool(approved), "confirm_text": confirm_text, "note": note}
            a.decided_at = _now()
        bus.publish("approval_resolved", tid, approval_id=approval_id, status="approved" if approved else
                    ("answered" if kind == "input" else "denied"))
        self._spawn(tid, Command(resume=value))
        return {"task_id": tid, "resumed": True}

    def cancel(self, task_id: str) -> bool:
        t = self._running.get(task_id)
        if t:
            t.cancel()
        else:
            with session_scope() as s:
                row = s.get(Task, task_id)
                if not row or row.status in TaskStatus.FINAL:
                    return False
            self._mark_cancelled(task_id)
            self._notify(task_id)
        return True

    def _mark_cancelled(self, tid: str) -> None:
        self._update(tid, status=TaskStatus.CANCELLED, error="Cancelled by user", result="Cancelled.")
        self._expire_approvals(tid)
        # lets the UI replace "working on it…" bubbles
        bus.publish("task_result", tid, status=TaskStatus.CANCELLED, text="Okay, I stopped that task.",
                    spoken="Okay, stopped.", verified=False)

    # ── waiting (for /api/chat) ───────────────────────────────────────────
    def _notify(self, tid: str) -> None:
        for f in self._waiters.pop(tid, []):
            if not f.done():
                f.set_result(True)

    async def wait(self, tid: str, timeout: float) -> dict:
        """Wait until the task finishes or pauses (approval / question), or timeout."""
        running = self._running.get(tid)
        if running is not None and not running.done():
            fut = asyncio.get_running_loop().create_future()
            self._waiters.setdefault(tid, []).append(fut)
            try:
                await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                pass
        return self.get(tid, with_steps=False) or {}

    # ── recovery ──────────────────────────────────────────────────────────
    async def recover(self) -> int:
        """Continue tasks that were mid-run when the backend stopped (from their last checkpoint)."""
        with session_scope() as s:
            rows = s.scalars(select(Task).where(Task.status == TaskStatus.IN_PROGRESS)).all()
            ids = [r.id for r in rows]
        n = 0
        for tid in ids:
            snap = await self.graph.aget_state({"configurable": {"thread_id": tid}})
            if snap and snap.next:
                log.info("Resuming task %s at %s", tid, snap.next)
                self._spawn(tid, None)
                n += 1
            else:
                self._update(tid, status=TaskStatus.FAILED, error="Interrupted before it started; please ask again.")
        return n


task_manager = TaskManager()
