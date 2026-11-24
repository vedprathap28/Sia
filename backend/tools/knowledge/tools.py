"""
SIA — Knowledge tools: web search, RAG documents, long-term memory, SIA's own database.
"""
from __future__ import annotations

import html
import re
import sqlite3
from typing import Annotated, Literal

import httpx
from pydantic import Field

from backend.core.config import settings
from backend.tools.registry import ToolResult, tool_def


# ── Web search ────────────────────────────────────────────────────────────────

def _ddg(query: str, n: int) -> list[dict]:
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query},
                   headers={"User-Agent": "Mozilla/5.0"}, timeout=12, follow_redirects=True)
    r.raise_for_status()
    out = []
    for m in re.finditer(r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?'
                         r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S):
        url, title, snip = m.groups()
        if "uddg=" in url:
            from urllib.parse import parse_qs, unquote, urlparse
            url = unquote(parse_qs(urlparse(url).query).get("uddg", [url])[0])
        clean = lambda s: html.unescape(re.sub("<.*?>", "", s)).strip()
        out.append({"title": clean(title), "url": url, "snippet": clean(snip)})
        if len(out) >= n:
            break
    return out


@tool_def(category="knowledge", risk=1, untrusted_output=True, timeout=25, retries=1)
def web_search(query: Annotated[str, Field(description="Search query")],
               max_results: Annotated[int, Field(ge=1, le=10)] = 5) -> ToolResult:
    """Search the web and return titles, URLs and snippets. Use browser tools to open and read a result."""
    results: list[dict] = []
    if settings.TAVILY_API_KEY:
        try:
            from tavily import TavilyClient
            res = TavilyClient(api_key=settings.TAVILY_API_KEY).search(query, max_results=max_results)
            results = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:400]}
                       for r in res.get("results", [])]
        except Exception:
            results = []
    if not results:
        results = _ddg(query, max_results)
    if not results:
        return ToolResult(ok=False, error="No results. Try different words or use browser_search.")
    text = "\n".join(f"{i+1}. {r['title']} — {r['url']}\n   {r['snippet']}" for i, r in enumerate(results))
    return ToolResult(ok=True, output=text, data={"results": results})


@tool_def(category="knowledge", risk=1, timeout=15)
def get_weather(city: Annotated[str, Field(description="City name, e.g. Hyderabad")]) -> str:
    """Current weather for a city (needs OPENWEATHER_API_KEY)."""
    from backend.agent.tool_registry import get_weather as _w
    return _w.invoke({"city": city})


# ── Documents (RAG) ───────────────────────────────────────────────────────────

@tool_def(category="knowledge", risk=1, untrusted_output=True, timeout=40)
def search_my_documents(query: Annotated[str, Field(description="What to look for")],
                        top_k: Annotated[int, Field(ge=1, le=10)] = 5) -> ToolResult:
    """Semantic search over documents the user uploaded to SIA (resume, notes, PDFs)."""
    from backend.rag.rag_service import rag_service
    ctx = rag_service.search_documents(query, n=top_k)
    if not ctx:
        return ToolResult(ok=False, error="No matching documents. The user may need to upload them first.")
    return ToolResult(ok=True, output=ctx)


@tool_def(category="knowledge", risk=1)
def list_my_documents() -> ToolResult:
    """List documents in SIA's knowledge base."""
    from backend.rag.rag_service import rag_service
    docs = rag_service.list_documents()
    return ToolResult(ok=True, output=", ".join(docs) or "No documents uploaded.", data={"documents": docs})


@tool_def(category="knowledge", risk=2, timeout=180)
def add_document_to_knowledge(path: Annotated[str, Field(description="Path to a PDF/DOCX/TXT/MD file")]) -> ToolResult:
    """Ingest a local file into SIA's knowledge base so it can be searched later."""
    from backend.rag.rag_service import rag_service
    from backend.security.sandbox import resolve_safe
    p = resolve_safe(path, must_exist=True)
    msg = rag_service.ingest_file(str(p))
    ok = p.name in rag_service.list_documents()
    return ToolResult(ok=ok, output=msg, verified=ok, evidence=f"listed in knowledge base={ok}")


# ── Memory ────────────────────────────────────────────────────────────────────

MemCat = Literal["preference", "profile", "project", "workflow", "fact"]


@tool_def(category="knowledge", risk=1)
def recall_memory(query: Annotated[str, Field(description="Topic or key to recall; empty for all")] = "",
                  category: MemCat | None = None) -> ToolResult:
    """Look up what the user has told SIA to remember (preferences, approved profile details,
    projects, saved workflows)."""
    from backend.memory.store import memory_store
    items = memory_store.search(query, category=category, limit=30)
    if not items:
        return ToolResult(ok=True, output="Nothing stored for that.")
    return ToolResult(ok=True, output="\n".join(f"[{i['category']}] {i['key']}: {i['value']}" for i in items),
                      data={"items": items})


@tool_def(category="knowledge", risk=2,
          summarize=lambda a: f"Remember [{a.get('category')}] {a.get('key')} = {str(a.get('value'))[:120]}")
def remember(key: Annotated[str, Field(description="Short key, e.g. 'preferred_browser'")],
             value: Annotated[str, Field(description="Value to remember")],
             category: MemCat = "fact") -> ToolResult:
    """Store a durable fact the USER stated (never passwords, tokens or card numbers)."""
    from backend.memory.store import memory_store
    item = memory_store.upsert(category, key, value, source="auto")
    if item is None:
        return ToolResult(ok=False, error="Refused: looks like a secret. Credentials are never stored in memory.")
    return ToolResult(ok=True, output=f"Remembered {key}.", verified=True, evidence="saved to memory table")


# ── SIA's own SQL database (read-only) ────────────────────────────────────────

@tool_def(category="data", risk=1, timeout=15)
def query_sia_database(sql: Annotated[str, Field(description=(
        "A single read-only SELECT over SIA's SQLite DB. Tables: tasks(id, goal, status, source, agent, "
        "result, created_at), task_steps(task_id, kind, agent, tool, summary, ok, created_at), "
        "job_applications(company, role, url, status, match_score, notes, created_at), "
        "memory(category, key, value), schedules(name, goal, cron, enabled, last_run)"))]) -> ToolResult:
    """Answer questions about SIA's history, tasks, job applications or schedules with SQL."""
    q = sql.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", q) or ";" in q:
        return ToolResult(ok=False, error="Only a single SELECT statement is allowed.")
    con = sqlite3.connect(f"file:{settings.DB_PATH}?mode=ro", uri=True)
    try:
        cur = con.execute(q)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(200)
    except sqlite3.Error as e:
        return ToolResult(ok=False, error=f"SQL error: {e}")
    finally:
        con.close()
    table = " | ".join(cols) + "\n" + "\n".join(" | ".join(str(c) for c in r) for r in rows)
    return ToolResult(ok=True, output=table, data={"columns": cols, "row_count": len(rows)})
