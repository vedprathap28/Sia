"""
SIA — SQL schema (SQLite via SQLAlchemy 2)
Tasks, steps, approvals, audit trail, memory, job applications, schedules, settings.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


class TaskStatus:
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    ACTIVE = {NOT_STARTED, IN_PROGRESS, WAITING_APPROVAL, WAITING_INPUT, BLOCKED}
    FINAL = {FAILED, COMPLETED, CANCELLED}


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.NOT_STARTED, index=True)
    source: Mapped[str] = mapped_column(String(16), default="chat")      # chat | voice | schedule
    lang: Mapped[str] = mapped_column(String(8), default="en-IN")
    agent: Mapped[str] = mapped_column(String(32), default="")
    plan_json: Mapped[str] = mapped_column(Text, default="[]")
    result: Mapped[str] = mapped_column(Text, default="")
    spoken: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TaskStep(Base):
    __tablename__ = "task_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))     # plan | agent | tool | verify | message | error
    agent: Mapped[str] = mapped_column(String(32), default="")
    tool: Mapped[str] = mapped_column(String(64), default="")
    args_json: Mapped[str] = mapped_column(Text, default="{}")
    summary: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    risk: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(64))
    args_json: Mapped[str] = mapped_column(Text, default="{}")
    risk: Mapped[int] = mapped_column(Integer, default=3)
    summary: Mapped[str] = mapped_column(Text, default="")     # exact action, human readable
    kind: Mapped[str] = mapped_column(String(16), default="approval")  # approval | input
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    event: Mapped[str] = mapped_column(String(32))     # tool_call | approval | llm | error
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MemoryItem(Base):
    __tablename__ = "memory"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    category: Mapped[str] = mapped_column(String(24), index=True)   # preference|profile|project|workflow|fact
    key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="user")  # user | auto | migrated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class JobApplication(Base):
    __tablename__ = "job_applications"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    company: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="shortlisted")  # shortlisted|prepared|applied|interview|rejected|offer
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    goal: Mapped[str] = mapped_column(Text)
    cron: Mapped[str] = mapped_column(String(64))          # 5-field cron, local time
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_task_id: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
