"""
SIA — Persistent scheduler for proactive / background tasks (daily briefing, job monitoring, reports).

Schedules live in the `schedules` table (5-field cron, local time). Each firing starts a normal task
with source="schedule": the SAME permission gate applies — consequential steps wait in the approvals
queue until the user approves them in the UI.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from backend.core.logger import get_logger
from backend.database.db import session_scope
from backend.database.models import Schedule

log = get_logger("scheduler")

PRESETS = {
    "daily_briefing": ("Daily briefing", "0 8 * * *",
                       "Prepare my daily briefing: today's calendar events, important unread emails (summarise, "
                       "don't open anything risky), and pending SIA tasks. Keep it short."),
    "job_watch": ("Job watch", "0 19 * * *",
                  "Search for new backend / AI engineer jobs posted in the last 24 hours that match my resume, "
                  "score them, and track the good ones as shortlisted. Do not apply."),
}


def _local_tz():
    """Local timezone; tolerates legacy aliases like TZ=Asia/Calcutta that tzlocal rejects."""
    import os
    try:
        from tzlocal import get_localzone
        return get_localzone()
    except Exception:
        from zoneinfo import ZoneInfo
        aliases = {"Asia/Calcutta": "Asia/Kolkata"}
        name = os.environ.get("TZ", "")
        try:
            return ZoneInfo(aliases.get(name, name) or "UTC")
        except Exception:
            return ZoneInfo("UTC")


def schedule_dict(s: Schedule) -> dict:
    return {"id": s.id, "name": s.name, "goal": s.goal, "cron": s.cron, "enabled": s.enabled,
            "last_run": s.last_run.isoformat() if s.last_run else None, "last_task_id": s.last_task_id}


class SiaScheduler:
    def __init__(self) -> None:
        self._sched = None

    def start(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError:
            log.warning("APScheduler not installed — background schedules disabled")
            return
        self._sched = AsyncIOScheduler(timezone=_local_tz())
        self._sched.start()
        with session_scope() as s:
            for row in s.scalars(select(Schedule).where(Schedule.enabled.is_(True))):
                self._add_job(row.id, row.cron)
        log.info("Scheduler started")

    def shutdown(self) -> None:
        if self._sched:
            self._sched.shutdown(wait=False)

    def _add_job(self, sid: str, cron: str) -> None:
        if not self._sched:
            return
        from apscheduler.triggers.cron import CronTrigger
        try:
            self._sched.add_job(self._fire, CronTrigger.from_crontab(cron, timezone=_local_tz()), args=[sid], id=sid, replace_existing=True,
                                misfire_grace_time=3600, coalesce=True)
        except Exception as e:
            log.warning("Bad cron %r for schedule %s: %s", cron, sid, e)

    def _remove_job(self, sid: str) -> None:
        if self._sched and self._sched.get_job(sid):
            self._sched.remove_job(sid)

    async def _fire(self, sid: str) -> None:
        from backend.task_manager.manager import task_manager
        with session_scope() as s:
            row = s.get(Schedule, sid)
            if not row or not row.enabled:
                return
            goal = row.goal
        tid = task_manager.start(goal, source="schedule")
        with session_scope() as s:
            row = s.get(Schedule, sid)
            row.last_run, row.last_task_id = datetime.now(timezone.utc), tid
        log.info("Schedule %s fired → task %s", sid, tid)

    # CRUD
    def list(self) -> list[dict]:
        with session_scope() as s:
            return [schedule_dict(r) for r in s.scalars(select(Schedule).order_by(Schedule.created_at))]

    def create(self, name: str, goal: str, cron: str, enabled: bool = True) -> dict:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(cron, timezone=_local_tz())          # validate (raises ValueError)
        with session_scope() as s:
            row = Schedule(name=name, goal=goal, cron=cron, enabled=enabled)
            s.add(row)
            s.flush()
            d = schedule_dict(row)
        if enabled:
            self._add_job(d["id"], cron)
        return d

    def update(self, sid: str, **fields) -> dict | None:
        with session_scope() as s:
            row = s.get(Schedule, sid)
            if not row:
                return None
            for k in ("name", "goal", "cron", "enabled"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, fields[k])
            d = schedule_dict(row)
        self._remove_job(sid)
        if d["enabled"]:
            self._add_job(sid, d["cron"])
        return d

    def delete(self, sid: str) -> bool:
        self._remove_job(sid)
        with session_scope() as s:
            row = s.get(Schedule, sid)
            if not row:
                return False
            s.delete(row)
        return True

    async def run_now(self, sid: str) -> None:
        await self._fire(sid)


scheduler = SiaScheduler()
