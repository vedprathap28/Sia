"""
SIA — Gmail and Google Calendar through the browser (Playwright, the user's own logged-in session).

No passwords or tokens pass through SIA: if Gmail asks for a login, the tool returns
needs_user=login_required and the agent asks the user to sign in in the browser window.
Gmail's HTML changes from time to time; when a selector fails the tools return a page
snapshot so the agent can continue with the generic browser tools.

Drafting is risk 2 (reversible, stays in Drafts). Sending and creating events are risk 3.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated
from urllib.parse import quote, urlencode

from pydantic import Field

from backend.security.injection import wrap_untrusted
from backend.tools.browser.service import browser_service as B, format_snapshot
from backend.tools.registry import ToolResult, tool_def

GMAIL = "https://mail.google.com/mail/u/0/"

_LIST_JS = r"""
(limit) => Array.from(document.querySelectorAll('tr.zA')).slice(0, limit).map((r, i) => ({
  index: i,
  unread: r.classList.contains('zE'),
  from: (r.querySelector('.yX .yW span[email], .yW span[email]') || {}).getAttribute?.('email') || (r.querySelector('.yW') || {}).innerText || '',
  name: ((r.querySelector('.yW') || {}).innerText || '').trim(),
  subject: ((r.querySelector('.bog') || {}).innerText || '').trim(),
  snippet: ((r.querySelector('.y2') || {}).innerText || '').replace(/^\s*-\s*/, '').trim(),
  date: ((r.querySelector('.xW span') || {}).getAttribute?.('title') || (r.querySelector('.xW') || {}).innerText || '').trim(),
}))
"""

_OPEN_JS = r"""
() => {
  const subj = (document.querySelector('h2.hP') || {}).innerText || '';
  const msgs = Array.from(document.querySelectorAll('div.adn')).map(m => ({
    from: (m.querySelector('.gD') || {}).getAttribute?.('email') || '',
    name: ((m.querySelector('.gD') || {}).innerText || '').trim(),
    date: ((m.querySelector('.g3') || {}).getAttribute?.('title') || '').trim(),
    body: ((m.querySelector('.a3s') || {}).innerText || '').trim().slice(0, 8000),
  }));
  return { subject: subj, messages: msgs, url: location.href };
}
"""


async def _gmail_ready(url: str) -> tuple[bool, dict]:
    snap = await B.run("goto", url)
    if snap.get("needs_user"):
        return False, snap
    ok = await B.run("wait_selector", "tr.zA, div[role=main] table, h2.hP", 20)
    return ok, snap


def _login_result(snap: dict) -> ToolResult:
    return ToolResult(ok=False, needs_user=snap.get("needs_user") or "login_required",
                      error="Gmail needs you to sign in. Ask the user to log in in SIA's browser window, then retry.",
                      output=format_snapshot(snap, 30, 500))


async def _list(url: str, limit: int) -> ToolResult:
    ok, snap = await _gmail_ready(url)
    if not ok:
        return _login_result(snap) if snap.get("needs_user") else ToolResult(
            ok=False, error="Gmail list did not load; use browser_snapshot to inspect.", output=format_snapshot(snap))
    rows = await B.run("eval", _LIST_JS, limit)
    text = "\n".join(f"[{r['index']}] {'(unread) ' if r['unread'] else ''}{r['name']} <{r['from']}> — "
                     f"{r['subject']} — {r['snippet'][:140]} ({r['date']})" for r in rows)
    return ToolResult(ok=True, output=wrap_untrusted(text or "No emails found.", "gmail"), data={"count": len(rows)})


@tool_def(category="communication", risk=1, timeout=90)
async def gmail_list_unread(limit: Annotated[int, Field(ge=1, le=50)] = 10) -> ToolResult:
    """List unread emails in the Gmail inbox (index, sender, subject, snippet, date)."""
    return await _list(GMAIL + "#search/" + quote("is:unread in:inbox"), limit)


@tool_def(category="communication", risk=1, timeout=90)
async def gmail_search(query: Annotated[str, Field(description="Gmail search, e.g. 'from:recruiter interview newer_than:30d'")],
                       limit: Annotated[int, Field(ge=1, le=50)] = 15) -> ToolResult:
    """Search Gmail with Gmail's query syntax (from:, subject:, newer_than:, has:attachment …)."""
    return await _list(GMAIL + "#search/" + quote(query), limit)


@tool_def(category="communication", risk=1, timeout=90)
async def gmail_open(index: Annotated[int, Field(description="Row index from the last gmail list/search")] = 0) -> ToolResult:
    """Open an email from the current Gmail list and read the whole thread. (Opening marks it as read.)"""
    clicked = await B.run("click_selector", f"tr.zA >> nth={index}")
    if not clicked:
        return ToolResult(ok=False, error="No email at that index — run gmail_list_unread or gmail_search first.")
    await B.run("wait_selector", "h2.hP", 15)
    d = await B.run("eval", _OPEN_JS)
    if not d.get("subject"):
        snap = await B.run("snapshot")
        return ToolResult(ok=False, error="Could not read the email view.", output=format_snapshot(snap))
    text = f"Subject: {d['subject']}\n" + "\n\n".join(
        f"From: {m['name']} <{m['from']}>  {m['date']}\n{m['body']}" for m in d["messages"])
    return ToolResult(ok=True, output=wrap_untrusted(text, "gmail"), data={"subject": d["subject"], "url": d["url"],
                                                                            "messages": len(d["messages"])})


def _compose_url(to: str, subject: str, body: str, cc: str = "") -> str:
    q = {"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}
    if cc:
        q["cc"] = cc
    return GMAIL + "?" + urlencode(q, quote_via=quote)


async def _open_compose(to, subject, body, cc) -> tuple[bool, dict]:
    snap = await B.run("goto", _compose_url(to, subject, body, cc))
    if snap.get("needs_user"):
        return False, snap
    ok = await B.run("wait_selector", "div[aria-label='Message Body'], div[role=textbox][aria-label]", 20)
    return ok, snap


@tool_def(category="communication", risk=2, timeout=90,
          summarize=lambda a: f"Save Gmail DRAFT to {a.get('to')}: “{a.get('subject')}”\n\n{str(a.get('body'))[:600]}")
async def gmail_create_draft(to: str, subject: str, body: str, cc: str = "") -> ToolResult:
    """Write an email and leave it in Gmail Drafts (nothing is sent). Use for replies too (subject 'Re: …')."""
    ok, snap = await _open_compose(to, subject, body, cc)
    if not ok:
        return _login_result(snap) if snap.get("needs_user") else ToolResult(ok=False, error="Compose window did not open.")
    await asyncio.sleep(2.5)                       # Gmail autosaves drafts
    closed = await B.run("click_selector", "img[aria-label='Save & close'], [data-tooltip^='Save & close']")
    return ToolResult(ok=True, output=f"Draft saved for {to} — '{subject}'. It has NOT been sent.",
                      verified=True if closed else None, evidence="compose closed via Save & close" if closed else "")


@tool_def(category="communication", risk=3, timeout=90,
          summarize=lambda a: f"SEND email to {a.get('to')}{' cc '+a['cc'] if a.get('cc') else ''}: "
                              f"“{a.get('subject')}”\n\n{str(a.get('body'))[:1500]}")
async def gmail_send(to: str, subject: str, body: str, cc: str = "") -> ToolResult:
    """Send an email from the user's Gmail. Always needs the user's explicit approval of the exact text."""
    ok, snap = await _open_compose(to, subject, body, cc)
    if not ok:
        return _login_result(snap) if snap.get("needs_user") else ToolResult(ok=False, error="Compose window did not open.")
    sent = await B.run("click_selector", "div[role=button][aria-label^='Send'], div[role=button][data-tooltip^='Send']")
    if not sent:
        return ToolResult(ok=False, error="Send button not found; the draft is still open in the browser.")
    try:
        confirmed = await B.run("wait_text", "Message sent", 15)
    except Exception:
        confirmed = None
    return ToolResult(ok=True, output=f"Email sent to {to}.", verified=bool(confirmed),
                      evidence="Gmail showed 'Message sent'" if confirmed else "no confirmation toast seen")


# ── Calendar ──────────────────────────────────────────────────────────────────

@tool_def(category="communication", risk=1, timeout=90)
async def calendar_view(date: Annotated[str, Field(description="YYYY-MM-DD; empty = today")] = "",
                        view: Annotated[str, Field(description="day | week")] = "day") -> ToolResult:
    """Read the user's Google Calendar for a day or week."""
    d = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
    url = f"https://calendar.google.com/calendar/u/0/r/{'week' if view == 'week' else 'day'}/{d:%Y/%m/%d}"
    snap = await B.run("goto", url)
    if snap.get("needs_user"):
        return _login_result(snap)
    await B.run("wait_selector", "[role=main]", 15)
    text = await B.run("eval", "() => (document.querySelector('[role=main]') || document.body).innerText.slice(0, 8000)")
    return ToolResult(ok=True, output=wrap_untrusted(text, "google_calendar"), data={"date": f"{d:%Y-%m-%d}"})


@tool_def(category="communication", risk=3, timeout=90,
          summarize=lambda a: f"CREATE calendar event “{a.get('title')}” {a.get('start')} → {a.get('end')}"
                              f"{' with '+a['guests'] if a.get('guests') else ''}")
async def calendar_create_event(title: str,
                                start: Annotated[str, Field(description="Local start, 'YYYY-MM-DD HH:MM'")],
                                end: Annotated[str, Field(description="Local end, 'YYYY-MM-DD HH:MM'")],
                                details: str = "", location: str = "",
                                guests: Annotated[str, Field(description="Comma-separated emails (they get invites)")] = ""
                                ) -> ToolResult:
    """Create a Google Calendar event (needs approval — guests receive invitations)."""
    fmt = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").strftime("%Y%m%dT%H%M%S")
    q = {"action": "TEMPLATE", "text": title, "dates": f"{fmt(start)}/{fmt(end)}", "details": details, "location": location}
    if guests:
        q["add"] = guests
    snap = await B.run("goto", "https://calendar.google.com/calendar/render?" + urlencode(q, quote_via=quote))
    if snap.get("needs_user"):
        return _login_result(snap)
    await B.run("wait_selector", "button, [role=button]", 15)
    saved = await B.run("click_selector", "button:has-text('Save'), [role=button]:has-text('Save')")
    if not saved:
        return ToolResult(ok=False, error="Save button not found; the event form is open in the browser.")
    if guests:
        await asyncio.sleep(1)
        await B.run("click_selector", "button:has-text('Send'), [role=button]:has-text('Send')")
    return ToolResult(ok=True, output=f"Event '{title}' created for {start}.", verified=None)
