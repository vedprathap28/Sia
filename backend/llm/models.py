"""
SIA — Model factory (LangChain chat models) with per-role routing and provider fallback.

Roles:
  chat     fast conversational replies (default Groq Llama 3.3 70B)
  planner  planning + tool calling        (default OpenAI gpt-4o-mini)
  vision   screenshots                    (default OpenAI gpt-4o-mini)

If the preferred provider has no key or fails (429, outage), the next configured provider
is tried automatically. Token usage is reported to the active task via `usage_tracker`.
"""
from __future__ import annotations

import contextvars
from typing import Any, Sequence

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("models")

# $ per 1M tokens (input, output) — estimates for the cost column
PRICES = {
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.0), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.0, 8.0),
    "llama-3.3-70b-versatile": (0.59, 0.79), "llama-3.1-8b-instant": (0.05, 0.08),
    "meta-llama/llama-4-scout-17b-16e-instruct": (0.11, 0.34),
}

_DEFAULTS = {"openai": "gpt-4o-mini", "groq": "llama-3.3-70b-versatile", "anthropic": "claude-sonnet-4-5"}


def _has_key(provider: str) -> bool:
    return bool({"openai": settings.OPENAI_API_KEY, "groq": settings.GROQ_API_KEY,
                 "anthropic": settings.ANTHROPIC_API_KEY}.get(provider))


def _build(provider: str, model: str, role: str) -> BaseChatModel:
    temp = 0.6 if role == "chat" else 0.1
    max_tokens = settings.LLM_MAX_TOKENS if role == "chat" else settings.PLANNER_MAX_TOKENS
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, api_key=settings.OPENAI_API_KEY, temperature=temp, max_tokens=max_tokens,
                          timeout=60, max_retries=2)
    if provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, api_key=settings.GROQ_API_KEY, temperature=temp, max_tokens=max_tokens,
                        timeout=60, max_retries=2)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # optional dependency
        return ChatAnthropic(model=model, api_key=settings.ANTHROPIC_API_KEY, temperature=temp, max_tokens=max_tokens)
    raise ValueError(f"Unknown provider {provider}")


def _chain(role: str) -> list[tuple[str, str]]:
    pref = {"chat": (settings.CHAT_PROVIDER, settings.CHAT_MODEL),
            "planner": (settings.PLANNER_PROVIDER, settings.PLANNER_MODEL),
            "vision": (settings.VISION_PROVIDER, settings.VISION_MODEL)}[role]
    order = [pref] + [(p, _DEFAULTS[p]) for p in ("openai", "groq", "anthropic") if p != pref[0]]
    if role == "vision":   # Groq/Llama text models can't see images
        order = [o for o in order if o[0] != "groq"]
    return [o for o in order if _has_key(o[0])]


_OVERRIDE: dict[str, Any] = {}     # tests inject fake models here


def set_model_override(role: str, model: Any) -> None:
    _OVERRIDE[role] = model


def get_model(role: str = "planner", tools: Sequence[dict] | None = None, **bind_kw):
    """A runnable chat model for the role, with tools bound and provider fallback."""
    if role in _OVERRIDE:
        m = _OVERRIDE[role]
        return m.bind_tools(list(tools), **bind_kw) if tools and hasattr(m, "bind_tools") else m
    chain = _chain(role)
    if not chain:
        raise RuntimeError(f"No API key for any provider usable as '{role}'. Set OPENAI_API_KEY or GROQ_API_KEY.")
    runnables = []
    for provider, model in chain:
        try:
            m = _build(provider, model, role)
            m = m.bind_tools(list(tools), **bind_kw) if tools else m
            runnables.append(m.with_config(callbacks=[usage_tracker], metadata={"model_name": model}))
        except Exception as e:
            log.warning("Model %s/%s unavailable: %s", provider, model, e)
    if not runnables:
        raise RuntimeError("No chat model could be created")
    return runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]


# ── Usage tracking ────────────────────────────────────────────────────────────

current_task_id: contextvars.ContextVar[str] = contextvars.ContextVar("sia_task_id", default="")


class _UsageTracker(BaseCallbackHandler):
    def on_llm_end(self, response, **kw) -> None:
        tid = current_task_id.get()
        if not tid:
            return
        tin = tout = 0
        model = ""
        try:
            for gens in response.generations:
                for g in gens:
                    um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                    tin += um.get("input_tokens", 0)
                    tout += um.get("output_tokens", 0)
                    model = (getattr(g, "message", None).response_metadata or {}).get("model_name", "") or model
        except Exception:
            pass
        price = next((v for k, v in PRICES.items() if model.startswith(k)), (0.0, 0.0))
        cost = tin / 1e6 * price[0] + tout / 1e6 * price[1]
        try:
            from backend.database.db import session_scope
            from backend.database.models import Task
            with session_scope() as s:
                t = s.get(Task, tid)
                if t:
                    t.tokens_in += tin
                    t.tokens_out += tout
                    t.llm_calls += 1
                    t.cost_usd += cost
        except Exception as e:
            log.debug("usage tracking failed: %s", e)


usage_tracker = _UsageTracker()
