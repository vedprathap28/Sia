"""
Sia AI — LLM Service v2
========================
Changes from v1:
  ✅ OpenAI is now PRIMARY when OPENAI_API_KEY is set.
  ✅ Groq is FALLBACK — used only if OpenAI fails or key is missing.
  ✅ Groq 429 rate limit → instantly retries on OpenAI, no crash.
  ✅ Provider priority: OpenAI → Groq → Gemini → Claude
     (whichever keys are present, in that order)
  ✅ Model auto-selected per provider (gpt-4o-mini for OpenAI).

To keep using Groq as primary: set LLM_PROVIDER=groq in your .env.
Default is now LLM_PROVIDER=openai.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

import httpx

from backend.core.config import settings
from backend.core.logger import get_logger
from backend.llm.prompts import build_system_prompt

log = get_logger("llm")


class LLMService:

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))

    async def respond(
        self,
        user_message: str,
        history: list[dict],
        detected_lang: str = "en-IN",
    ) -> dict:
        messages = self._build_messages(user_message, history, detected_lang)
        raw = await self._complete(messages)
        return self._parse(raw)

    async def stream_chat(
        self,
        user_message: str,
        history: list[dict] = [],
        detected_lang: str = "en-IN",
    ) -> AsyncGenerator[str, None]:
        messages = self._build_messages(user_message, history, detected_lang)
        async for token in self._stream(messages):
            yield token

    async def close(self):
        await self._client.aclose()

    def _build_messages(self, user_message, history, detected_lang="en-IN"):
        try:
            from backend.memory.smart_memory import smart_memory
            facts = smart_memory.get_facts_context()
        except Exception:
            facts = ""

        system = build_system_prompt(
            inject_os=True,
            inject_date=True,
            detected_lang=detected_lang,
            extra=facts,
        )
        msgs = [{"role": "system", "content": system}]
        for turn in history[-(settings.MAX_HISTORY):]:
            msgs.append({"role": turn["role"], "content": turn["content"]})
        msgs.append({"role": "user", "content": user_message})
        return msgs

    # ── Provider priority ─────────────────────────────────────────────────────
    # Order: OpenAI → Groq → Gemini → Claude
    # If LLM_PROVIDER is explicitly set in .env, that runs first.

    def _provider_order(self) -> list[str]:
        """
        Returns providers to try in order.
        Explicit LLM_PROVIDER from .env goes first.
        Rest are fallbacks using whichever keys exist.
        """
        explicit = settings.LLM_PROVIDER  # from .env

        all_providers = []

        # Explicit provider first
        if explicit and explicit not in all_providers:
            all_providers.append(explicit)

        # Then fill in the rest by key availability
        for provider, key_attr in [
            ("openai", "OPENAI_API_KEY"),
            ("groq",   "GROQ_API_KEY"),
            ("google", "GOOGLE_API_KEY"),
            ("claude", "ANTHROPIC_API_KEY"),
        ]:
            if getattr(settings, key_attr, "") and provider not in all_providers:
                all_providers.append(provider)

        return all_providers

    def _model_for(self, provider: str) -> str:
        """Pick a sensible default model per provider."""
        if settings.LLM_PROVIDER == provider and settings.LLM_MODEL:
            return settings.LLM_MODEL  # respect explicit setting
        return {
            "openai": "gpt-4o-mini",
            "groq":   "llama-3.3-70b-versatile",
            "google": "gemini-2.0-flash",
            "claude": "claude-sonnet-4-6",
        }.get(provider, "gpt-4o-mini")

    async def _complete(self, messages: list[dict]) -> str:
        last_err = None
        for provider in self._provider_order():
            try:
                log.debug("Trying provider: %s", provider)
                result = await self._complete_with(provider, messages)
                return result
            except _RateLimitError as e:
                log.warning("Rate limit on %s — trying next provider", provider)
                last_err = e
                continue
            except Exception as e:
                log.warning("Provider %s failed (%s) — trying next", provider, e)
                last_err = e
                continue
        raise Exception(f"All LLM providers failed. Last error: {last_err}")

    async def _stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        last_err = None
        for provider in self._provider_order():
            try:
                log.debug("Streaming with provider: %s", provider)
                async for token in self._stream_with(provider, messages):
                    yield token
                return
            except _RateLimitError as e:
                log.warning("Rate limit on %s (stream) — trying next provider", provider)
                last_err = e
                continue
            except Exception as e:
                log.warning("Provider %s stream failed (%s) — trying next", provider, e)
                last_err = e
                continue
        raise Exception(f"All LLM providers failed. Last error: {last_err}")

    async def _complete_with(self, provider: str, messages: list[dict]) -> str:
        if provider == "openai":  return await self._openai_complete(messages)
        if provider == "groq":    return await self._groq_complete(messages)
        if provider == "google":  return await self._gemini_complete(messages)
        if provider == "claude":  return await self._claude_complete(messages)
        raise ValueError(f"Unknown provider: {provider}")

    async def _stream_with(self, provider: str, messages: list[dict]) -> AsyncGenerator[str, None]:
        if provider == "openai":
            async for t in self._openai_stream(messages): yield t
        elif provider == "groq":
            async for t in self._groq_stream(messages): yield t
        elif provider == "google":
            async for t in self._gemini_stream(messages): yield t
        elif provider == "claude":
            async for t in self._claude_stream(messages): yield t

    # ── OpenAI ────────────────────────────────────────────────────────────────

    async def _openai_complete(self, messages) -> str:
        if not settings.OPENAI_API_KEY:
            raise Exception("OPENAI_API_KEY not set")
        resp = await self._client.post(
            "https://api.openai.com/v1/chat/completions",
            json={
                "model":       self._model_for("openai"),
                "messages":    messages,
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens":  settings.LLM_MAX_TOKENS,
            },
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type":  "application/json",
            },
        )
        if resp.status_code == 429:
            raise _RateLimitError("OpenAI rate limit")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def _openai_stream(self, messages) -> AsyncGenerator[str, None]:
        if not settings.OPENAI_API_KEY:
            raise Exception("OPENAI_API_KEY not set")
        async with self._client.stream(
            "POST", "https://api.openai.com/v1/chat/completions",
            json={
                "model":       self._model_for("openai"),
                "messages":    messages,
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens":  settings.LLM_MAX_TOKENS,
                "stream":      True,
            },
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type":  "application/json",
            },
        ) as resp:
            if resp.status_code == 429:
                raise _RateLimitError("OpenAI rate limit")
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    d = line[6:]
                    if d == "[DONE]": break
                    try:
                        delta = json.loads(d)["choices"][0]["delta"].get("content", "")
                        if delta: yield delta
                    except Exception:
                        pass

    # ── Groq ──────────────────────────────────────────────────────────────────

    async def _groq_complete(self, messages) -> str:
        if not settings.GROQ_API_KEY:
            raise Exception("GROQ_API_KEY not set")
        resp = await self._client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model":       self._model_for("groq"),
                "messages":    messages,
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens":  settings.LLM_MAX_TOKENS,
            },
            headers={
                "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
        )
        if resp.status_code == 429:
            raise _RateLimitError("Groq rate limit")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def _groq_stream(self, messages) -> AsyncGenerator[str, None]:
        if not settings.GROQ_API_KEY:
            raise Exception("GROQ_API_KEY not set")
        async with self._client.stream(
            "POST", "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model":       self._model_for("groq"),
                "messages":    messages,
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens":  settings.LLM_MAX_TOKENS,
                "stream":      True,
            },
            headers={
                "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
        ) as resp:
            if resp.status_code == 429:
                raise _RateLimitError("Groq rate limit")
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    d = line[6:]
                    if d == "[DONE]": break
                    try:
                        delta = json.loads(d)["choices"][0]["delta"].get("content", "")
                        if delta: yield delta
                    except Exception:
                        pass

    # ── Claude (Anthropic) ────────────────────────────────────────────────────

    def _claude_messages(self, messages):
        system, msgs = "", []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        return system, msgs

    async def _claude_complete(self, messages) -> str:
        if not settings.ANTHROPIC_API_KEY:
            raise Exception("ANTHROPIC_API_KEY not set")
        system, msgs = self._claude_messages(messages)
        resp = await self._client.post(
            "https://api.anthropic.com/v1/messages",
            json={
                "model":      self._model_for("claude"),
                "max_tokens": settings.LLM_MAX_TOKENS,
                "system":     system,
                "messages":   msgs,
            },
            headers={
                "x-api-key":         settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
        )
        if resp.status_code == 429:
            raise _RateLimitError("Claude rate limit")
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    async def _claude_stream(self, messages) -> AsyncGenerator[str, None]:
        if not settings.ANTHROPIC_API_KEY:
            raise Exception("ANTHROPIC_API_KEY not set")
        system, msgs = self._claude_messages(messages)
        async with self._client.stream(
            "POST", "https://api.anthropic.com/v1/messages",
            json={
                "model":      self._model_for("claude"),
                "max_tokens": settings.LLM_MAX_TOKENS,
                "system":     system,
                "messages":   msgs,
                "stream":     True,
            },
            headers={
                "x-api-key":         settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    raw = line[6:]
                    if raw == "[DONE]": break
                    try:
                        chunk = json.loads(raw)
                        if chunk.get("type") == "content_block_delta":
                            delta = chunk.get("delta", {}).get("text", "")
                            if delta: yield delta
                    except Exception:
                        pass

    # ── Gemini ────────────────────────────────────────────────────────────────

    def _to_gemini(self, messages):
        system, contents = "", []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            else:
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})
        return system, contents

    async def _gemini_complete(self, messages) -> str:
        if not settings.GOOGLE_API_KEY:
            raise Exception("GOOGLE_API_KEY not set")
        system, contents = self._to_gemini(messages)
        model = (settings.LLM_MODEL or "gemini-2.0-flash").replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.GOOGLE_API_KEY}"
        resp = await self._client.post(url, json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature":    settings.LLM_TEMPERATURE,
                "maxOutputTokens": settings.LLM_MAX_TOKENS,
            },
        })
        if resp.status_code == 429:
            raise _RateLimitError("Gemini rate limit")
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    async def _gemini_stream(self, messages) -> AsyncGenerator[str, None]:
        if not settings.GOOGLE_API_KEY:
            raise Exception("GOOGLE_API_KEY not set")
        system, contents = self._to_gemini(messages)
        model = self._model_for("google")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={settings.GOOGLE_API_KEY}"
        async with self._client.stream("POST", url, json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
        }) as resp:
            if resp.status_code == 429:
                raise _RateLimitError("Gemini rate limit")
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        tok = json.loads(line[6:])["candidates"][0]["content"]["parts"][0]["text"]
                        if tok: yield tok
                    except Exception:
                        pass

    # ── Response parser ───────────────────────────────────────────────────────

    def _parse(self, raw: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = [l for l in cleaned.splitlines() if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        try:
            parsed = json.loads(cleaned)
            if "type" not in parsed:
                raise ValueError("missing type")
            return parsed
        except Exception:
            log.warning("Parse failed — treating as chat. Raw: %s", raw[:80])
            return {"type": "chat", "message": raw.strip()}


class _RateLimitError(Exception):
    """Raised when a provider returns 429 — triggers fallback to next provider."""
    pass


llm_service = LLMService()