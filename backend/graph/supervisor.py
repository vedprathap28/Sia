"""
SIA — LangGraph supervisor
==========================

    START → intake ─┬─(short built-in command)──────────────► agent_tool ─► step_done
                    └─► supervisor ─┬─ respond ─► chat_reply ─► END
                                    └─ act ─► dispatch ─► agent_llm ⇄ agent_tool ─► step_done
                                                             ▲            │ needs login/CAPTCHA
                                                             └─ user_wait ◄┘
                    step_done ─┬─ next step ─► dispatch
                               ├─ failed ─► replan ─► dispatch | finalize
                               └─ done ─► finalize ─► END

* The supervisor (planner model) decides: reply directly, or a plan of 1-6 steps, each owned
  by a specialised agent (graph/agents.py).
* Every agent runs the same tool loop. `agent_tool` executes ONE tool call per visit, so a
  human-in-the-loop interrupt (approval / question) never re-runs an action that already ran.
* The permission gate lives in `agent_tool`: risk ≥ 3 → interrupt for approval; risk 4 → typed
  confirmation or blocked; nothing the LLM says can skip it.
* State is checkpointed (SQLite) after every node, so tasks survive restarts and can resume.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.core.logger import get_logger, redact
from backend.graph.agents import AGENTS, agent_catalogue
from backend.llm.models import get_model
from backend.security.injection import UNTRUSTED_RULE
from backend.security.permissions import RISK_LABEL, decide
from backend.task_manager.events import bus
from backend.tools.registry import ToolContext, load_all_tools

log = get_logger("supervisor")

_LANG_RULE = {
    "en-IN": "Write the final answer in clear English.",
    "hi-IN": "Write the final answer in Hindi (Roman script is fine), like the user did.",
    "te-IN": "Write the final answer in Telugu (or Telugu-English mix), like the user did.",
    "mr-IN": "Write the final answer in Marathi (Roman script is fine), like the user did.",
}
_NEEDS_USER_TEXT = {
    "login_required": "sign in on the page that is open in SIA's browser window",
    "captcha": "complete the CAPTCHA / human check in SIA's browser window",
    "mfa": "enter the verification code (2-step / OTP) in SIA's browser window",
}


# ── State ─────────────────────────────────────────────────────────────────────

class SiaState(TypedDict, total=False):
    task_id: str
    goal: str
    lang: str
    source: str
    history: list[dict]
    plan: list[dict]                 # [{agent, instruction, status, result}]
    step: int
    agent_messages: list[BaseMessage]
    agent_turns: int
    direct: Optional[dict]           # fast path: {"spoken": "..."}
    await_user: Optional[dict]
    tool_log: list[dict]
    replans: int
    final: str
    spoken: str
    status: str
    verified: bool


# ── Planner schema ────────────────────────────────────────────────────────────

class PlanStep(BaseModel):
    agent: str = Field(description="One of the agent names")
    instruction: str = Field(description="Self-contained instruction for that agent, with every detail it needs")


class MakePlan(BaseModel):
    """Decide how to handle the user's request."""
    mode: Literal["respond", "act"] = Field(description="respond = just talk/answer from general knowledge; "
                                                        "act = needs tools, the computer, files, web, apps or memory")
    steps: list[PlanStep] = Field(default_factory=list, description="For act: 1-6 ordered steps")
    reply: str = Field(default="", description="For respond: optional short draft answer")


def _plan_tool() -> dict:
    schema = MakePlan.model_json_schema()
    return {"type": "function", "function": {"name": "make_plan", "description": MakePlan.__doc__, "parameters": schema}}


def _parse_plan(msg: AIMessage) -> MakePlan | None:
    for tc in getattr(msg, "tool_calls", None) or []:
        if tc.get("name") == "make_plan":
            try:
                p = MakePlan(**tc.get("args", {}))
                p.steps = [s for s in p.steps if s.agent in AGENTS][:6]
                if p.mode == "act" and not p.steps:
                    return None
                return p
            except Exception as e:
                log.warning("Bad plan: %s", e)
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _emit(state: SiaState, type_: str, **data):
    bus.publish(type_, state.get("task_id", ""), **data)


def _record(state: SiaState, kind: str, **kw) -> None:
    tid = state.get("task_id")
    if not tid:
        return
    try:
        from backend.database.db import session_scope, to_json
        from backend.database.models import TaskStep
        args = kw.pop("args", None)
        with session_scope() as s:
            s.add(TaskStep(task_id=tid, kind=kind, args_json=redact(to_json(args or {}))[:4000], **kw))
    except Exception as e:
        log.debug("step record failed: %s", e)


def _context_block(state: SiaState) -> str:
    try:
        from backend.memory.store import memory_store
        mem = memory_store.context_block()
    except Exception:
        mem = ""
    return (f"Now: {datetime.now():%A %d %B %Y, %I:%M %p}. OS: Windows. User: {settings.USER_NAME}.\n"
            f"Home folder: ~ (Desktop, Documents, Downloads). SIA workspace: {settings.WORKSPACE_DIR}\n{mem}")


def _history_msgs(state: SiaState, n: int = 8) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for t in (state.get("history") or [])[-n:]:
        c = str(t.get("content", ""))[:1500]
        out.append(HumanMessage(content=c) if t.get("role") == "user" else AIMessage(content=c))
    return out


def _speakable(text: str) -> str:
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    # Tables and bullet lists are for the screen, not for TTS: speak the prose around them.
    lines = t.splitlines()
    prose = [ln for ln in lines if not re.match(r"^\s*\|", ln) and not re.match(r"^\s*[-:| ]{3,}\s*$", ln)]
    had_table = len(prose) < len(lines)
    is_bullet = lambda ln: bool(re.match(r"^\s*([-*•]|\d+[.)])\s+", ln))  # noqa: E731
    prose_only = [ln for ln in prose if not is_bullet(ln)]
    has_list = had_table or len(prose_only) < len(prose)
    if any(p.strip() for p in prose_only):
        t = "\n".join(prose_only)
    else:
        t = "\n".join(prose)
    if not t.strip():
        return "I've put the details on your screen." if had_table else ""
    if has_list and any(p.strip() for p in prose_only):
        spoken = _speakable_plain(t)
        return (spoken + " The details are on your screen.").strip()[:300]
    return _speakable_plain(t)


def _speakable_plain(t: str) -> str:
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"(?m)^\s*([-*•]|\d+[.)])\s+", "", t)
    t = re.sub(r"[#*_>`|]+", "", t)
    t = re.sub(r"\s+(?:at|on|via|here:?)\s+https?://\S+?(?=[.,;!?]?(?:\s|$))", "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    sents = re.split(r"(?<=[.!?।])\s+", t)
    out = " ".join(sents[:2])
    return out[:300]


def _pending_call(msgs: list[BaseMessage]) -> dict | None:
    answered = {m.tool_call_id for m in msgs if isinstance(m, ToolMessage)}
    for m in reversed(msgs):
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                if tc["id"] not in answered:
                    return tc
            return None
    return None


_SCHEMAS: dict[str, list[dict]] = {}


def _agent_tools(agent: str) -> tuple[list[dict], set[str]]:
    if agent not in _SCHEMAS:
        specs = AGENTS[agent].tool_specs()
        _SCHEMAS[agent] = [s.openai_schema() for s in specs]
    return _SCHEMAS[agent], {s["function"]["name"] for s in _SCHEMAS[agent]}


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def intake(state: SiaState) -> dict:
    goal = state["goal"].strip()
    _emit(state, "status", status="in_progress", label="Understanding the request")
    base = {"tool_log": [], "replans": 0, "step": 0, "agent_turns": 0, "await_user": None, "direct": None}
    if len(goal.split()) <= 8:
        from backend.agent.router import _local_route
        intent = _local_route(goal, state.get("lang", "en-IN"))
        if intent and len(intent.plan) == 1:
            st = intent.plan[0]
            call = {"name": "system_action", "args": {"action": st.action, "params": st.params},
                    "id": f"fast_{uuid.uuid4().hex[:8]}", "type": "tool_call"}
            log.info("Fast path: %s", st.action)
            return {**base, "plan": [{"agent": "system", "instruction": goal, "status": "running", "result": ""}],
                    "agent_messages": [HumanMessage(content=goal), AIMessage(content="", tool_calls=[call])],
                    "direct": {"spoken": intent.spoken}}
    return base


async def supervisor(state: SiaState) -> dict:
    _emit(state, "status", status="in_progress", label="Planning the task")
    sys = SystemMessage(content=(
        "You are the supervisor of SIA, a personal AI assistant that can operate the user's Windows computer: "
        "apps, browser, files, email, code, and more, through specialised agents.\n\n"
        f"Agents:\n{agent_catalogue()}\n\n"
        "Call make_plan exactly once.\n"
        "- mode=respond for greetings, chit-chat, opinions, and questions answerable from general knowledge "
        "without looking anything up.\n"
        "- mode=act whenever the request needs the computer, web, current information, the user's files, "
        "email, documents, memory or any action. Use the FEWEST steps (usually one agent; split only when "
        "different agents are truly needed, e.g. read email → create spreadsheet). Each instruction must be "
        "self-contained: include names, file paths, recipients, dates and preferences from the conversation, "
        "and what the agent should return for the next step.\n"
        "- Short follow-ups ('reply to it', 'open the second one') refer to the recent conversation; resolve them.\n"
        + UNTRUSTED_RULE + "\n\n" + _context_block(state)))
    model = get_model("planner", tools=[_plan_tool()], tool_choice="make_plan")
    msg = await model.ainvoke([sys, *_history_msgs(state), HumanMessage(content=state["goal"])])
    plan = _parse_plan(msg)
    if plan is None:
        # A model that ignored the tool: treat its text as a direct reply
        log.warning("No valid plan from planner — replying conversationally")
        return {"plan": [], "status": "respond"}
    if plan.mode == "respond":
        return {"plan": [], "status": "respond"}
    steps = [{"agent": s.agent, "instruction": s.instruction, "status": "pending", "result": ""} for s in plan.steps]
    _emit(state, "plan", steps=[{"agent": s["agent"], "instruction": s["instruction"][:200]} for s in steps])
    _record(state, "plan", summary=" → ".join(f"{s['agent']}: {s['instruction'][:80]}" for s in steps))
    return {"plan": steps, "step": 0}


async def chat_reply(state: SiaState) -> dict:
    """Conversational reply in SIA's persona (fast chat model)."""
    from backend.llm.prompts import build_system_prompt
    from backend.llm.llm_service import llm_service
    try:
        persona = build_system_prompt(inject_os=True, inject_date=True, detected_lang=state.get("lang", "en-IN"))
    except Exception:
        persona = "You are Sia, a friendly, sharp personal AI assistant."
    sys = SystemMessage(content=persona + "\n\nIMPORTANT FOR THIS TURN: reply ONLY with plain conversational "
                        'text, or the JSON {"type":"chat","message":"..."}. Do not plan actions.\n' + _context_block(state))
    model = get_model("chat")
    msg = await model.ainvoke([sys, *_history_msgs(state), HumanMessage(content=state["goal"])])
    parsed = llm_service._parse(str(msg.content))
    if parsed.get("type") == "action" and parsed.get("plan"):
        # The persona model decided this needs a built-in action after all: run it through the
        # gate (never reply "done" without actually doing it).
        from backend.tools.legacy_actions import CATALOGUE
        calls = [{"name": "system_action", "args": {"action": st.get("action"), "params": st.get("params") or {}},
                  "id": f"chat_{uuid.uuid4().hex[:8]}", "type": "tool_call"}
                 for st in parsed["plan"] if st.get("action") in CATALOGUE][:5]
        if calls:
            log.info("chat_reply produced actions: %s", [c["args"]["action"] for c in calls])
            return {"plan": [{"agent": "system", "instruction": state["goal"], "status": "running", "result": ""}],
                    "step": 0, "agent_messages": [HumanMessage(content=state["goal"]), AIMessage(content="", tool_calls=calls)],
                    "direct": {"spoken": parsed.get("spoken") or "Done."}, "status": ""}
    text = parsed.get("message") or parsed.get("spoken") or str(msg.content)
    return {"final": text, "spoken": _speakable(text), "status": "completed", "verified": True}


async def dispatch(state: SiaState) -> dict:
    idx = state.get("step", 0)
    plan = [dict(p) for p in state["plan"]]
    p = plan[idx]
    p["status"] = "running"
    agent = AGENTS[p["agent"]]
    prev = "\n".join(f"Step {i+1} ({q['agent']}) result: {q['result'][:2500]}"
                     for i, q in enumerate(plan[:idx]) if q.get("result"))
    sys = SystemMessage(content=(
        f"{agent.prompt}\n{UNTRUSTED_RULE}\n\n{_context_block(state)}\n"
        f"Overall user goal: {state['goal']}\n" + (f"\nEarlier steps:\n{prev}\n" if prev else "")
        + _LANG_RULE.get(state.get("lang", "en-IN"), "")))
    _emit(state, "agent", agent=p["agent"], step=idx + 1, total=len(plan), instruction=p["instruction"][:300],
          label=f"{p['agent'].title()} agent working")
    _record(state, "agent", agent=p["agent"], summary=p["instruction"][:500])
    return {"plan": plan, "agent_messages": [sys, HumanMessage(content=p["instruction"])], "agent_turns": 0}


async def agent_llm(state: SiaState) -> dict:
    agent = state["plan"][state.get("step", 0)]["agent"]
    msgs = list(state["agent_messages"])
    turns = state.get("agent_turns", 0)
    if turns >= settings.AGENT_MAX_STEPS:
        msg = AIMessage(content=f"FAILED: stopped after {turns} tool rounds without finishing.")
    else:
        schemas, _ = _agent_tools(agent)
        model = get_model("planner", tools=schemas)
        msg = await model.ainvoke(msgs)
        if not isinstance(msg, AIMessage):
            msg = AIMessage(content=str(getattr(msg, "content", msg)))
    return {"agent_messages": msgs + [msg], "agent_turns": turns + 1}


async def agent_tool(state: SiaState) -> dict:
    msgs = list(state["agent_messages"])
    call = _pending_call(msgs)
    if not call:
        return {}
    agent = state["plan"][state.get("step", 0)]["agent"]
    name, args, cid = call["name"], call.get("args") or {}, call["id"]
    tool_log = list(state.get("tool_log") or [])

    def reply(text: str, extra: dict | None = None) -> dict:
        out = {"agent_messages": msgs + [ToolMessage(content=text, tool_call_id=cid, name=name)], "tool_log": tool_log}
        out.update(extra or {})
        return out

    # Human-in-the-loop question
    if name == "ask_user":
        q = str(args.get("question", ""))[:1000]
        ans = interrupt({"type": "input", "question": q, "agent": agent})
        _record(state, "message", agent=agent, tool="ask_user", summary=f"Q: {q[:200]} A: {str(ans)[:200]}")
        return reply(f"User answered: {ans}")

    reg = load_all_tools()
    _, allowed = _agent_tools(agent)
    if name not in allowed:
        return reply(json.dumps({"ok": False, "error": f"Tool '{name}' is not available to the {agent} agent."}))
    spec, clean, err = reg.validate(name, args)
    if err:
        return reply(json.dumps({"ok": False, "error": err}))

    risk = spec.risk_for(clean)
    decision = decide(risk, background=state.get("source") == "schedule")
    summary = spec.describe_call(clean)
    if decision == "block":
        _record(state, "tool", agent=agent, tool=name, args=clean, summary="BLOCKED by policy", ok=False, risk=risk)
        _emit(state, "tool_blocked", tool=name, summary=summary, risk=risk)
        return reply(json.dumps({"ok": False, "error": "Blocked by SIA's safety policy (destructive action). "
                                                       "Tell the user it was not done."}))
    if decision in ("ask", "confirm"):
        resp = interrupt({"type": "approval", "tool": name, "args": json.loads(redact(json.dumps(clean, default=str))),
                          "risk": risk, "risk_label": RISK_LABEL[risk], "summary": summary,
                          "confirm": decision == "confirm", "agent": agent})
        resp = resp if isinstance(resp, dict) else {"approved": bool(resp)}
        ok = bool(resp.get("approved"))
        if decision == "confirm":
            ok = ok and str(resp.get("confirm_text", "")).strip().upper() == "CONFIRM"
        if not ok:
            note = str(resp.get("note", "")).strip()
            _record(state, "tool", agent=agent, tool=name, args=clean, summary="DENIED by user", ok=False, risk=risk)
            return reply(json.dumps({"ok": False, "error": "The user DENIED this exact action. Do not retry it."
                                     + (f" User note: {note}" if note else "")}))

    _emit(state, "tool_started", tool=name, agent=agent, summary=summary[:300], risk=risk)
    t0 = time.perf_counter()
    res = await reg.execute(name, clean, ToolContext(task_id=state.get("task_id", ""), agent=agent,
                                                     lang=state.get("lang", "en-IN")))
    ms = int((time.perf_counter() - t0) * 1000)
    _emit(state, "tool_finished", tool=name, agent=agent, ok=res.ok, verified=res.verified, summary=res.short(),
          duration_ms=ms)
    _record(state, "tool", agent=agent, tool=name, args=clean, summary=res.short(), ok=res.ok,
            verified=bool(res.verified), risk=risk, duration_ms=ms)
    tool_log.append({"tool": name, "ok": res.ok, "verified": res.verified, "risk": risk, "output": res.short()})
    extra = {}
    if res.needs_user in _NEEDS_USER_TEXT and not state.get("direct"):
        extra["await_user"] = {"reason": res.needs_user}
    return reply(res.for_llm(), extra)


async def user_wait(state: SiaState) -> dict:
    reason = (state.get("await_user") or {}).get("reason", "login_required")
    ans = interrupt({"type": "input", "reason": reason,
                     "question": f"Please {_NEEDS_USER_TEXT.get(reason, 'finish the step')}, then reply 'done' "
                                 "(or tell me what to do instead)."})
    msgs = list(state["agent_messages"]) + [HumanMessage(
        content=f"User says: {ans}. Take a fresh snapshot to check the page before continuing.")]
    return {"agent_messages": msgs, "await_user": None}


async def step_done(state: SiaState) -> dict:
    idx = state.get("step", 0)
    plan = [dict(p) for p in state["plan"]]
    msgs = state.get("agent_messages") or []
    if state.get("direct"):
        last_tool = next((m for m in reversed(msgs) if isinstance(m, ToolMessage)), None)
        data = {}
        try:
            data = json.loads(last_tool.content) if last_tool else {}
        except Exception:
            pass
        ok = bool(data.get("ok"))
        result = (state["direct"].get("spoken") or data.get("output") or "Done.") if ok else \
            f"FAILED: {data.get('error') or data.get('output') or 'the action did not run'}"
    else:
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        result = str(last_ai.content).strip() if last_ai else "FAILED: no result"
        if not result:
            result = "FAILED: the agent returned no result"
    failed = bool(re.match(r"^\s*(FAILED|BLOCKED)\b", result))
    plan[idx]["status"] = "failed" if failed else "done"
    plan[idx]["result"] = result
    _emit(state, "step_finished", step=idx + 1, agent=plan[idx]["agent"], ok=not failed, summary=result[:300])
    return {"plan": plan, "step": idx if failed else idx + 1}


async def replan(state: SiaState) -> dict:
    _emit(state, "status", status="in_progress", label="Replanning after a problem")
    plan = state["plan"]
    done = "\n".join(f"{i+1}. [{p['status']}] {p['agent']}: {p['instruction'][:300]}\n   result: {p['result'][:1500]}"
                     for i, p in enumerate(plan) if p["status"] in ("done", "failed"))
    sys = SystemMessage(content=(
        "You are SIA's supervisor. A step failed. Decide whether a DIFFERENT approach can still achieve the goal "
        "(another agent, other site, ask the user via a step). If it cannot, or the failure was the user denying "
        "an action, a login/payment boundary, or a safety block, call make_plan with mode=respond and no steps.\n"
        f"Agents:\n{agent_catalogue()}\n" + UNTRUSTED_RULE))
    model = get_model("planner", tools=[_plan_tool()], tool_choice="make_plan")
    msg = await model.ainvoke([sys, HumanMessage(content=f"Goal: {state['goal']}\n\nSo far:\n{done}")])
    new = _parse_plan(msg)
    if not new or new.mode != "act" or not new.steps:
        return {"replans": state.get("replans", 0) + 1, "status": "give_up"}
    keep = [p for p in plan if p["status"] == "done"]
    steps = keep + [{"agent": s.agent, "instruction": s.instruction, "status": "pending", "result": ""} for s in new.steps]
    _emit(state, "plan", steps=[{"agent": s["agent"], "instruction": s["instruction"][:200]} for s in steps], replan=True)
    _record(state, "plan", summary="Replan: " + " → ".join(f"{s.agent}: {s.instruction[:80]}" for s in new.steps))
    return {"plan": steps, "step": len(keep), "replans": state.get("replans", 0) + 1, "status": ""}


async def finalize(state: SiaState) -> dict:
    plan = state.get("plan") or []
    failed = any(p["status"] == "failed" for p in plan) or any(p["status"] == "pending" for p in plan)
    tool_log = state.get("tool_log") or []
    if len(plan) == 1 or state.get("direct"):
        final = re.sub(r"^\s*(FAILED|BLOCKED):\s*", "", plan[-1]["result"]) if plan else "Done."
        if failed and plan:
            final = "I couldn't finish this: " + (final[:1].lower() + final[1:] if final else "it failed.")
    else:
        summary = "\n".join(f"Step {i+1} [{p['status']}] ({p['agent']}): {p['result'][:3000]}" for i, p in enumerate(plan))
        model = get_model("planner")
        msg = await model.ainvoke([
            SystemMessage(content="Write SIA's final reply to the user from the step results. Lead with the outcome. "
                                  "Be specific (names, numbers, file paths, links). Be honest: clearly say what was "
                                  "not done or not verified. No internal jargon about agents or steps. "
                                  + _LANG_RULE.get(state.get("lang", "en-IN"), "")),
            HumanMessage(content=f"User goal: {state['goal']}\n\n{summary}")])
        final = str(msg.content).strip()
    verifiers = [t for t in tool_log if t.get("verified") is not None]
    verified = (not failed) and all(t["verified"] for t in verifiers) and all(t["ok"] for t in tool_log[-1:])
    status = "failed" if failed else "completed"
    _emit(state, "status", status=status, label="Done" if not failed else "Could not finish", verified=verified)
    return {"final": final, "spoken": _speakable(final), "status": status, "verified": verified}


# ── Routing ───────────────────────────────────────────────────────────────────

def _after_intake(s: SiaState) -> str:
    return "agent_tool" if s.get("direct") else "supervisor"


def _after_supervisor(s: SiaState) -> str:
    return "dispatch" if s.get("plan") else "chat_reply"


def _after_llm(s: SiaState) -> str:
    last = (s.get("agent_messages") or [None])[-1]
    return "agent_tool" if isinstance(last, AIMessage) and last.tool_calls else "step_done"


def _after_tool(s: SiaState) -> str:
    # Answer every tool call of the batch first: OpenAI/Groq reject a user message that
    # arrives while some tool_calls are still unanswered.
    if _pending_call(s.get("agent_messages") or []):
        return "agent_tool"
    if s.get("await_user"):
        return "user_wait"
    return "step_done" if s.get("direct") else "agent_llm"


def _after_step(s: SiaState) -> str:
    plan, idx = s.get("plan") or [], s.get("step", 0)
    if idx < len(plan) and plan[idx]["status"] == "failed":
        if not s.get("direct") and s.get("replans", 0) < settings.TASK_MAX_REPLANS:
            return "replan"
        return "finalize"
    return "dispatch" if idx < len(plan) else "finalize"


def _after_replan(s: SiaState) -> str:
    return "finalize" if s.get("status") == "give_up" else "dispatch"


def build_graph(checkpointer=None):
    g = StateGraph(SiaState)
    for name, fn in [("intake", intake), ("supervisor", supervisor), ("chat_reply", chat_reply),
                     ("dispatch", dispatch), ("agent_llm", agent_llm), ("agent_tool", agent_tool),
                     ("user_wait", user_wait), ("step_done", step_done), ("replan", replan), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _after_intake, ["agent_tool", "supervisor"])
    g.add_conditional_edges("supervisor", _after_supervisor, ["dispatch", "chat_reply"])
    g.add_conditional_edges("chat_reply", lambda s: "agent_tool" if s.get("direct") and not s.get("final") else END,
                            ["agent_tool", END])
    g.add_edge("dispatch", "agent_llm")
    g.add_conditional_edges("agent_llm", _after_llm, ["agent_tool", "step_done"])
    g.add_conditional_edges("agent_tool", _after_tool, ["user_wait", "agent_tool", "step_done", "agent_llm"])
    g.add_edge("user_wait", "agent_llm")
    g.add_conditional_edges("step_done", _after_step, ["replan", "finalize", "dispatch"])
    g.add_conditional_edges("replan", _after_replan, ["finalize", "dispatch"])
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
