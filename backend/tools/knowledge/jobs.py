"""
SIA — Job-search helpers: profile, resume ↔ JD matching, application tracker.
Searching job sites themselves is done by the browser agent with the generic browser tools.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import Field
from sqlalchemy import select

from backend.database.db import session_scope
from backend.database.models import JobApplication
from backend.security.injection import UNTRUSTED_RULE, wrap_untrusted
from backend.tools.registry import ToolResult, tool_def

AppStatus = Literal["shortlisted", "prepared", "applied", "interview", "rejected", "offer", "withdrawn"]


@tool_def(category="jobs", risk=1)
def get_approved_profile() -> ToolResult:
    """The user's approved profile details for forms (name, email, phone, links, skills…) from memory.
    Only these may be used to fill applications. Ask the user for anything missing."""
    from backend.memory.store import memory_store
    items = memory_store.search(category="profile", limit=100)
    if not items:
        return ToolResult(ok=True, output="No approved profile stored yet. Ask the user which details to use, "
                                          "and offer to save them as profile memory.")
    return ToolResult(ok=True, output="\n".join(f"{i['key']}: {i['value']}" for i in items))


@tool_def(category="jobs", risk=1, timeout=90)
async def match_resume_to_job(job_description: Annotated[str, Field(description="Full job description text")],
                              resume_query: Annotated[str, Field(description="How to find the resume in uploaded docs")]
                              = "resume skills experience projects") -> ToolResult:
    """Score how well the user's uploaded resume fits a job description (0-100) with matched/missing skills."""
    from backend.llm.models import get_model
    from backend.rag.rag_service import rag_service
    from langchain_core.messages import HumanMessage, SystemMessage
    resume = rag_service.search_documents(resume_query, n=8)
    if not resume:
        return ToolResult(ok=False, error="No resume found in the knowledge base. Ask the user to upload it "
                                          "(Documents panel) or give its path for add_document_to_knowledge.")
    model = get_model("planner")
    msg = await model.ainvoke([
        SystemMessage(content="You are a precise technical recruiter. " + UNTRUSTED_RULE +
                      ' Reply JSON only: {"score": 0-100, "matched": [...], "missing": [...], '
                      '"summary": "two sentences", "talking_points": [...]}'),
        HumanMessage(content=f"RESUME:\n{wrap_untrusted(resume, 'resume')}\n\nJOB:\n{wrap_untrusted(job_description, 'job_post')}"),
    ])
    m = re.search(r"\{.*\}", str(msg.content), re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    if "score" not in d:
        return ToolResult(ok=False, error="Could not score the match", output=str(msg.content)[:1000])
    text = (f"Match {d['score']}/100. {d.get('summary', '')}\nMatched: {', '.join(d.get('matched', []))}\n"
            f"Missing: {', '.join(d.get('missing', []))}")
    return ToolResult(ok=True, output=text, data=d)


@tool_def(category="jobs", risk=2, summarize=lambda a: f"Track job: {a.get('role')} at {a.get('company')} [{a.get('status')}]")
def track_job_application(company: str, role: str, url: str = "", status: AppStatus = "shortlisted",
                          match_score: float = 0, notes: str = "") -> ToolResult:
    """Add or update a job in SIA's application tracker (same company+role updates the row)."""
    with session_scope() as s:
        row = s.scalar(select(JobApplication).where(JobApplication.company == company, JobApplication.role == role))
        if row:
            row.status, row.url = status, url or row.url
            row.match_score = match_score or row.match_score
            row.notes = (row.notes + "\n" + notes).strip() if notes else row.notes
        else:
            row = JobApplication(company=company, role=role, url=url, status=status, match_score=match_score, notes=notes)
            s.add(row)
        s.flush()
        rid = row.id
    return ToolResult(ok=True, output=f"Tracked {role} at {company} as {status}.", data={"id": rid},
                      verified=True, evidence="row saved in job_applications")


@tool_def(category="jobs", risk=1)
def list_job_applications(status: str = "") -> ToolResult:
    """List tracked job applications (optionally by status)."""
    with session_scope() as s:
        q = select(JobApplication).order_by(JobApplication.updated_at.desc())
        if status:
            q = q.where(JobApplication.status == status)
        rows = list(s.scalars(q.limit(100)))
        text = "\n".join(f"{r.company} — {r.role} [{r.status}] score={r.match_score:.0f} {r.url}" for r in rows)
    return ToolResult(ok=True, output=text or "No applications tracked yet.", data={"count": len(rows)})
