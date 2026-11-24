
"""
Sia AI — Routes v3
===================
Key change from v2:
  ✅ Backend NEVER calls _speak() / TTS for any request.
     Frontend owns 100% of audio via /api/voice/speak.
     This eliminates the double-voice bug completely.
  ✅ /api/voice/speak returns raw audio bytes — frontend plays them.
  ✅ Document upload error fixed (rag_service + document_loader fixes handle this).
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from backend.agent.agent import agent, _local_route, _detect_language
from backend.core.config import settings
from backend.core.logger import get_logger
from backend.memory.conversation_store import conversation_store

log    = get_logger("routes")
router = APIRouter()

_ROOT            = Path(__file__).resolve().parents[2]
_CHAT_HIST_FILE  = _ROOT / "data" / "chat_history.json"
_VOICE_HIST_FILE = _ROOT / "data" / "voice_history.json"
_UPLOAD_DIR      = _ROOT / "data" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".pdf", ".docx", ".txt", ".md"}

# ── Time-sensitive question detector ──────────────────────────────────────────
_LIVE_PATTERNS = re.compile(
    r'\b(today|yesterday|tonight|just now|right now|latest|current|live|score|'
    r'match|won|lost|result|weather|price|stock|news|happened|trending|'
    r'aaj|kal|abhi|score|cricket|ipl|t20|odi|fifa|nba|premier|league)\b',
    re.IGNORECASE,
)

def _needs_web_search(text: str) -> bool:
    return bool(_LIVE_PATTERNS.search(text))


def _load(path: Path) -> list:
    try:
        if path.exists():
            return json.loads(path.read_text("utf-8"))
    except Exception:
        pass
    return []


def _save(path: Path, data: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    source:  str = "text"   # kept for compatibility but backend ignores for TTS


# ── CHAT ──────────────────────────────────────────────────────────────────────

async def _graph_chat(message: str, source: str = "text") -> dict:
    """v4 engine: run the request as a task; wait briefly; same response shape as before plus task fields."""
    from backend.task_manager.manager import task_manager
    lang = _detect_language(message)
    src = "voice" if source == "voice" else "chat"
    tid = task_manager.start(message, source=src, lang=lang, history=conversation_store.get_history(limit=10))
    t = await task_manager.wait(tid, settings.CHAT_WAIT_SECONDS)
    status = t.get("status", "")
    if status in ("completed", "failed"):
        text, spoken = t.get("result") or "Done.", t.get("spoken") or ""
    elif status == "waiting_approval":
        ap = next((a for a in task_manager.pending_approvals() if a["task_id"] == tid), {})
        what = re.sub(r"\s*\(e\d+\)", "", str(ap.get("summary", "action")))
        text = f"I need your approval before I continue:\n\n**{what}**"
        spoken = "I need your approval for the next step. Please check the approval card."
    elif status == "waiting_input":
        ap = next((a for a in task_manager.pending_approvals() if a["task_id"] == tid), {})
        text = ap.get("summary") or "I need some input from you."
        spoken = text
    elif status == "cancelled":
        text = spoken = "Cancelled."
    else:
        text = "I'm working on it — you can follow the progress in the task panel."
        spoken = "Working on it. I'll let you know when it's done."
    return {"text": text, "spoken": spoken or text[:300], "lang": lang, "type": "task", "intent": "",
            "action_results": [], "elapsed_ms": 0, "task_id": tid, "task_status": status,
            "verified": bool(t.get("verified"))}


@router.post("/chat")
async def chat(req: ChatRequest):
    log.info("POST /chat  msg=%r  source=%s", req.message[:80], req.source)
    try:
        if settings.SIA_ENGINE == "graph":
            return await _graph_chat(req.message, req.source)
        result = await agent.process(req.message)
        # ✅ No _speak() call here — frontend handles all TTS via /api/voice/speak
        return result.to_dict()
    except Exception as e:
        log.exception("Chat error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    lang = _detect_language(req.message)

    async def _gen() -> AsyncGenerator[str, None]:
        full_reply = ""
        try:
            msg = req.message.strip()
            if settings.SIA_ENGINE == "graph":
                res = await _graph_chat(msg, req.source)
                yield f"data: {res['text'].replace(chr(10), chr(92) + 'n')}\n\n"
                yield "data: [DONE]\n\n"
                return
            if _local_route(msg, lang) is not None:
                result = await agent.process(msg)
                full_reply = result.spoken or result.text or ""
                yield f"data: {result.text.replace(chr(10), ' ')}\n\n"
                yield "data: [DONE]\n\n"
                # ✅ No _speak() — frontend owns TTS
                return

            from backend.llm.llm_service import llm_service
            history    = conversation_store.get_history(limit=10)
            llm_output = await llm_service.respond(msg, history, detected_lang=lang)

            if llm_output.get("type") == "action":
                result = await agent.process(msg)
                full_reply = result.spoken or result.text or ""
                yield f"data: {result.text.replace(chr(10), ' ')}\n\n"
                yield "data: [DONE]\n\n"
                return

            async for token in agent.stream_chat(msg, detected_lang=lang):
                if token:
                    full_reply += token
                    yield f"data: {token.replace(chr(10), chr(92) + 'n')}\n\n"

            yield "data: [DONE]\n\n"
            # ✅ No _speak() — frontend owns TTS

        except Exception as e:
            log.exception("Stream error: %s", e)
            yield f"data: Error: {str(e)[:100]}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── LIVEKIT TOKEN ─────────────────────────────────────────────────────────────

@router.post("/livekit/token")
async def livekit_token(body: dict = {}):
    if not settings.LIVEKIT_ENABLED:
        raise HTTPException(status_code=503, detail="LiveKit voice is disabled (set LIVEKIT_ENABLED=true).")
    try:
        from livekit.api import AccessToken, VideoGrants
        lk_url     = settings.LIVEKIT_URL
        api_key    = settings.LIVEKIT_API_KEY
        api_secret = settings.LIVEKIT_API_SECRET
        if not all([lk_url, api_key, api_secret]):
            raise HTTPException(status_code=503, detail="LiveKit not configured.")
        room_name = body.get("room", "sia-voice-room")
        identity  = body.get("identity", settings.USER_NAME.lower() or "user")
        token = (
            AccessToken(api_key, api_secret)
            .with_identity(identity).with_name(settings.USER_NAME)
            .with_grants(VideoGrants(room_join=True, room=room_name,
                can_publish=True, can_subscribe=True, can_publish_data=True))
            .to_jwt()
        )
        return {"token": token, "url": lk_url, "room": room_name}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("LiveKit token error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── VOICE ENDPOINTS ───────────────────────────────────────────────────────────

@router.post("/voice/speak")
async def voice_speak(body: dict):
    """
    Frontend calls this to get TTS audio bytes.
    Returns audio/mpeg (or audio/wav from Sarvam).
    Frontend plays the audio — backend never plays anything.
    """
    text = body.get("text", "").strip()
    lang = body.get("lang", "en-IN")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        from backend.voice.tts import tts_engine
        audio = await asyncio.to_thread(tts_engine.synthesize, text, lang)
        if not audio:
            raise HTTPException(status_code=500, detail="TTS synthesis failed")
        # Sarvam returns WAV, Edge TTS returns MP3
        content_type = "audio/wav" if audio[:4] == b'RIFF' else "audio/mpeg"
        return Response(
            content=audio,
            media_type=content_type,
            headers={"Cache-Control": "no-cache"},
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("voice/speak error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...)):
    try:
        suffix      = Path(file.filename or "audio.webm").suffix or ".webm"
        audio_bytes = await file.read()

        if settings.SARVAM_API_KEY:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        "https://api.sarvam.ai/speech-to-text",
                        headers={"api-subscription-key": settings.SARVAM_API_KEY},
                        files={"file": ("audio.webm", audio_bytes, "audio/webm")},
                        data={"model": settings.SARVAM_STT_MODEL, "language_code": "unknown",
                              "mode": "transcribe", "with_timestamps": "false"},
                    )
                    if resp.status_code == 200:
                        text = resp.json().get("transcript", "")
                        if text:
                            return {"text": text, "success": True, "engine": "sarvam"}
            except Exception as e:
                log.warning("Sarvam STT failed (%s) — Whisper fallback", e)

        from backend.voice.stt import stt
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            text = await asyncio.to_thread(stt.transcribe_file, tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if not text.strip():
            return {"text": "", "success": False, "engine": "whisper", "error": "I didn't catch anything."}
        return {"text": text, "success": True, "engine": "whisper"}

    except ModuleNotFoundError as e:
        log.error("Transcription unavailable: %s", e)
        return {"text": "", "success": False,
                "error": "Speech-to-text is not installed (pip install openai-whisper) and SARVAM_API_KEY is not set."}
    except Exception as e:
        log.exception("Transcription error: %s", e)
        return {"text": "", "success": False, "error": str(e)}


# These endpoints kept for compatibility but voice controller is no longer
# started automatically — wake word is handled by the browser now.
@router.post("/voice/start")
async def voice_start():
    return {"success": True, "active": False, "state": "off",
            "message": "Voice is now browser-managed. No server mic needed."}

@router.post("/voice/stop")
async def voice_stop():
    return {"success": True, "active": False, "state": "off"}

@router.get("/voice/status")
async def voice_status():
    return {"active": False, "state": "off"}

@router.get("/voice/message")
async def voice_message():
    return {"has_message": False}


# ── MEMORY ────────────────────────────────────────────────────────────────────

@router.get("/memory/facts")
async def get_facts():
    try:
        from backend.memory.smart_memory import ved_facts
        return {"facts": ved_facts.all()}
    except Exception:
        return {"facts": {}}


@router.post("/memory/facts")
async def set_fact(body: dict):
    key   = body.get("key", "").strip()
    value = body.get("value", "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    try:
        from backend.memory.smart_memory import ved_facts
        ved_facts.set(key, value)
        return {"success": True, "key": key, "value": value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── DOCUMENTS ─────────────────────────────────────────────────────────────────

@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    filename = file.filename or "upload"
    ext      = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400,
            detail=f"Unsupported type '{ext}'. Allowed: {', '.join(ALLOWED_EXT)}")
    temp_path = _UPLOAD_DIR / filename
    try:
        temp_path.write_bytes(await file.read())
        from backend.rag.rag_service import rag_service
        result = rag_service.ingest_file(str(temp_path))
        return {"success": True, "message": result or f"Ingested {filename}", "filename": filename}
    except Exception as e:
        log.exception("Document upload error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("/documents/list")
async def list_documents():
    try:
        from backend.rag.rag_service import rag_service
        return {"documents": rag_service.list_documents()}
    except Exception:
        return {"documents": []}


@router.delete("/documents/{name}")
async def delete_document(name: str):
    try:
        from backend.rag.rag_service import rag_service
        rag_service.delete_document(name)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── HISTORY ───────────────────────────────────────────────────────────────────

@router.get("/history")
async def get_history():
    h = conversation_store.get_history(limit=100)
    return {"history": h, "count": len(h)}


@router.get("/history/chat")
async def get_chat_history():
    data = _load(_CHAT_HIST_FILE)
    return {"history": data, "count": len(data)}


@router.post("/history/chat/save")
async def save_chat_session(body: dict):
    title = body.get("title", "Chat")
    messages = body.get("messages", [])
    if messages:
        data = _load(_CHAT_HIST_FILE)
        data.insert(0, {
            "id":       str(int(datetime.now().timestamp() * 1000)),
            "title":    title[:60],
            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
            "messages": messages,
        })
        _save(_CHAT_HIST_FILE, data[:100])
    return {"success": True}


@router.get("/history/voice")
async def get_voice_history():
    return {"history": _load(_VOICE_HIST_FILE), "count": 0}


@router.post("/history/voice/save")
async def save_voice_session(body: dict):
    return {"success": True}


@router.delete("/history")
async def clear_history():
    conversation_store.clear()
    return {"success": True}


# ── HEALTH ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {
        "status":   "ok",
        "provider": settings.LLM_PROVIDER,
        "model":    settings.LLM_MODEL,
        "platform": settings.PLATFORM,
        "livekit":  bool(settings.LIVEKIT_URL),
        "sarvam":   bool(settings.SARVAM_API_KEY),
        "engine":   settings.SIA_ENGINE,
        "planner":  f"{settings.PLANNER_PROVIDER}/{settings.PLANNER_MODEL}",
        "openai":   bool(settings.OPENAI_API_KEY),
    }


@router.get("/debug/llm")
async def debug_llm():
    try:
        from backend.llm.llm_service import llm_service
        result = await llm_service.respond("Reply with only: pong", history=[])
        return {"success": True, "response": result}
    except Exception as e:
        return {"success": False, "error": str(e)}