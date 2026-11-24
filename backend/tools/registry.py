"""
SIA — Tool registry
===================
Every capability SIA has is a *tool*: a typed function with

  • a description + Pydantic input schema (what the LLM sees)
  • a risk level 1-4 (fixed, or computed from the arguments)
  • a timeout, optional bounded retries (idempotent tools only)
  • an optional verifier that checks the action really happened
  • a flag for untrusted output (web pages, emails, files) → wrapped before the LLM sees it

The LLM only *chooses* tools. `execute()` validates the arguments, runs ordinary
code with a timeout, verifies, and returns a structured ToolResult. Permission
checks happen in the graph's gate before `execute()` is ever called.

Adding a tool:

    @tool_def(category="files", risk=1)
    def read_text(path: Annotated[str, Field(description="File path")]) -> str:
        "Read a text file."
        ...
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Union

from pydantic import BaseModel, ValidationError, create_model

from backend.core.logger import get_logger, redact
from backend.security.injection import wrap_untrusted

log = get_logger("tools")


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict = field(default_factory=dict)
    verified: Optional[bool] = None      # None = no verifier for this tool
    evidence: str = ""                   # what the verifier observed
    needs_user: str = ""                 # e.g. "login_required", "captcha", "mfa"
    error: str = ""

    def for_llm(self, max_chars: int = 8000) -> str:
        parts = {"ok": self.ok}
        if self.output:
            parts["output"] = self.output[:max_chars]
        if self.data:
            d = json.dumps(self.data, ensure_ascii=False, default=str)
            parts["data"] = d[:max_chars] if len(d) > max_chars else self.data
        if self.verified is not None:
            parts["verified"] = self.verified
            if self.evidence:
                parts["evidence"] = self.evidence[:500]
        if self.needs_user:
            parts["needs_user"] = self.needs_user
        if self.error:
            parts["error"] = self.error[:1500]
        return json.dumps(parts, ensure_ascii=False, default=str)

    def short(self) -> str:
        if not self.ok:
            return f"Failed: {self.error or self.output}"[:200]
        return (self.output or json.dumps(self.data, default=str))[:200]


@dataclass
class ToolContext:
    task_id: str = ""
    agent: str = ""
    lang: str = "en-IN"


RiskSpec = Union[int, Callable[[dict], int]]


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[..., Any]
    category: str
    risk: RiskSpec = 1
    timeout: float = 30.0
    retries: int = 0                      # only for idempotent tools
    untrusted_output: bool = False
    verifier: Optional[Callable[[dict, ToolResult], Union[tuple[bool, str], Awaitable[tuple[bool, str]]]]] = None
    summarize: Optional[Callable[[dict], str]] = None   # human-readable exact action (approvals)
    wants_ctx: bool = False

    def risk_for(self, args: dict) -> int:
        try:
            r = self.risk(args) if callable(self.risk) else self.risk
        except Exception:
            r = 4
        return max(1, min(4, int(r)))

    def describe_call(self, args: dict) -> str:
        if self.summarize:
            try:
                return self.summarize(args)
            except Exception:
                pass
        shown = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
        return f"{self.name}({shown})"

    def openai_schema(self) -> dict:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for p in schema.get("properties", {}).values():
            p.pop("title", None)
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": schema}}


def _short(v: Any, n: int = 80) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return (s[:n] + "…") if len(s) > n else s


# ── Registry ──────────────────────────────────────────────────────────────────

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            log.debug("Tool %s re-registered", spec.name)
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def by_category(self, *categories: str) -> list[ToolSpec]:
        cats = set(categories)
        return [t for t in self._tools.values() if t.category in cats]

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def validate(self, name: str, args: dict) -> tuple[ToolSpec | None, dict, str]:
        spec = self.get(name)
        if not spec:
            return None, {}, f"Unknown tool '{name}'. Available: {', '.join(self.names()[:60])}"
        try:
            model = spec.args_model(**(args or {}))
            return spec, model.model_dump(), ""
        except ValidationError as e:
            return spec, {}, f"Invalid arguments for {name}: {e.errors(include_url=False)}"

    async def execute(self, name: str, args: dict, ctx: ToolContext | None = None) -> ToolResult:
        ctx = ctx or ToolContext()
        spec, clean, err = self.validate(name, args)
        if err:
            return ToolResult(ok=False, error=err)
        t0 = time.perf_counter()
        attempts = 1 + max(0, spec.retries)
        result: ToolResult | None = None
        for attempt in range(attempts):
            try:
                result = await asyncio.wait_for(self._call(spec, clean, ctx), timeout=spec.timeout)
                if result.ok or attempt == attempts - 1:
                    break
            except asyncio.TimeoutError:
                result = ToolResult(ok=False, error=f"Timed out after {spec.timeout:.0f}s")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # tool bug or OS error → structured failure
                log.exception("Tool %s raised", name)
                result = ToolResult(ok=False, error=f"{type(e).__name__}: {e}")
            if attempt < attempts - 1:
                await asyncio.sleep(min(8, 0.5 * 2 ** attempt))   # exponential backoff
        assert result is not None

        if spec.verifier and result.ok:
            try:
                v = spec.verifier(clean, result)
                if inspect.isawaitable(v):
                    v = await asyncio.wait_for(v, timeout=15)
                result.verified, result.evidence = bool(v[0]), str(v[1])
            except Exception as e:
                result.verified, result.evidence = False, f"verifier error: {e}"

        if spec.untrusted_output and result.output:
            result.output = wrap_untrusted(result.output, source=name)

        ms = int((time.perf_counter() - t0) * 1000)
        log.info("tool %s ok=%s verified=%s %dms args=%s", name, result.ok, result.verified, ms,
                 redact(_short(clean, 200)))
        result.data.setdefault("_duration_ms", ms)
        return result

    async def _call(self, spec: ToolSpec, args: dict, ctx: ToolContext) -> ToolResult:
        kwargs = dict(args)
        if spec.wants_ctx:
            kwargs["ctx"] = ctx
        if inspect.iscoroutinefunction(spec.func):
            out = await spec.func(**kwargs)
        else:
            out = await asyncio.to_thread(spec.func, **kwargs)
        return coerce_result(out)


def coerce_result(out: Any) -> ToolResult:
    if isinstance(out, ToolResult):
        return out
    if isinstance(out, dict):
        ok = bool(out.pop("ok", True))
        return ToolResult(ok=ok, output=str(out.pop("output", "")), error=str(out.pop("error", "")),
                          needs_user=str(out.pop("needs_user", "")), data=out)
    if out is None:
        return ToolResult(ok=True, output="Done.")
    return ToolResult(ok=True, output=str(out))


registry = ToolRegistry()


# ── Decorator ─────────────────────────────────────────────────────────────────

def tool_def(*, category: str, risk: RiskSpec = 1, name: str | None = None, timeout: float = 30.0,
             retries: int = 0, untrusted_output: bool = False, verifier=None, summarize=None,
             description: str | None = None, reg: ToolRegistry | None = None):
    """Register a function as a tool. Parameter annotations become the input schema."""

    def wrap(fn: Callable):
        sig = inspect.signature(fn)
        import typing
        try:
            hints = typing.get_type_hints(fn, include_extras=True)
        except Exception:
            hints = {}
        fields: dict[str, Any] = {}
        wants_ctx = False
        for pname, p in sig.parameters.items():
            if pname == "ctx":
                wants_ctx = True
                continue
            ann = hints.get(pname, p.annotation if p.annotation is not inspect.Parameter.empty else str)
            default = p.default if p.default is not inspect.Parameter.empty else ...
            fields[pname] = (ann, default)
        tname = name or fn.__name__
        model = create_model(f"{tname}_args", **fields)  # type: ignore[call-overload]
        spec = ToolSpec(
            name=tname,
            description=(description or inspect.getdoc(fn) or tname).strip(),
            args_model=model, func=fn, category=category, risk=risk, timeout=timeout,
            retries=retries, untrusted_output=untrusted_output, verifier=verifier,
            summarize=summarize, wants_ctx=wants_ctx,
        )
        (reg or registry).register(spec)
        fn.tool_spec = spec  # type: ignore[attr-defined]
        return fn

    return wrap


_LOADED = False


def load_all_tools() -> ToolRegistry:
    """Import every tool module once (each registers itself). Missing optional deps are tolerated."""
    global _LOADED
    if _LOADED:
        return registry
    import importlib
    for mod in (
        "backend.tools.core_tools",
        "backend.tools.legacy_actions",
        "backend.tools.knowledge.tools",
        "backend.tools.files.tools",
        "backend.tools.productivity.tools",
        "backend.tools.development.tools",
        "backend.tools.browser.tools",
        "backend.tools.computer.tools",
        "backend.tools.communication.tools",
        "backend.tools.knowledge.jobs",
    ):
        try:
            importlib.import_module(mod)
        except Exception as e:  # pragma: no cover - depends on host
            log.warning("Tool module %s not loaded: %s", mod, e)
    _LOADED = True
    log.info("Tool registry ready: %d tools", len(registry.names()))
    return registry
