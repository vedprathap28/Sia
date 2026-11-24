"""Voice pipeline: language detection, spoken text, TTS/STT endpoints and fallbacks, voice-sourced tasks."""
from __future__ import annotations

import asyncio
import base64
import io
import wave

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from backend.tests.conftest import FakeModel, plan, tool_call


# ── Language detection (drives the reply language and the TTS voice) ─────────

@pytest.mark.parametrize("text,lang", [
    ("book tickets for bus to vizag", "en-IN"),          # used to be Marathi because of "to"
    ("set a reminder to call mom at 5", "en-IN"),
    ("book a hotel in Goa for 2 nights", "en-IN"),
    ("I want to go to Mumbai", "en-IN"),
    ("Goa mein hotel book karo", "hi-IN"),
    ("mujhe kal ke liye hotel chahiye", "hi-IN"),
    ("kya haal hai", "hi-IN"),
    ("haan", "hi-IN"),
    ("goa lo hotel book cheyyi", "te-IN"),
    ("avunu", "te-IN"),
    ("mala hotel book karayche ahe", "mr-IN"),
    ("nako", "mr-IN"),
    ("मुझे होटल चाहिए", "hi-IN"),
    ("मला हॉटेल पाहिजे", "mr-IN"),
    ("హోటల్ బుక్ చేయి", "te-IN"),
])
def test_language_detection(text, lang):
    from backend.agent.router import _detect_language
    assert _detect_language(text) == lang


# ── What gets spoken ──────────────────────────────────────────────────────────

def test_spoken_text_skips_tables_lists_and_urls():
    from backend.graph.supervisor import _speakable
    s = _speakable("I found 3 hotels in Goa.\n\n| Hotel | Price |\n|---|---|\n| Taj | ₹9,000 |\n\nWhich one?")
    assert "|" not in s and "---" not in s and s.startswith("I found 3 hotels") and "screen" in s
    assert _speakable("| a | b |\n|---|---|\n| 1 | 2 |") == "I've put the details on your screen."
    assert "http" not in _speakable("Booking ready. Pay at https://example.com/pay now.")
    assert _speakable("- Taj\n- Ibis") == "Taj Ibis"
    assert len(_speakable("Word. " * 200)) <= 300


# ── TTS ───────────────────────────────────────────────────────────────────────

def _wav(n=1600) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * n)
    return buf.getvalue()


def test_sarvam_request_uses_current_api_shape(monkeypatch):
    """bulbul:v3 takes one `text` per request; the old `inputs` list returned HTTP 400."""
    from backend.core.config import settings
    from backend.voice import tts
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "k")
    sent = []

    class Resp:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self): return {"audios": [base64.b64encode(_wav()).decode()]}

    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, json=None, headers=None):
            sent.append(json)
            return Resp()

    monkeypatch.setattr(tts.httpx, "Client", Client)
    long_text = ("This sentence is about forty characters. " * 60).strip()     # ~2500 chars → split
    audio = tts.sarvam_tts_sync(long_text, "hi-IN")
    assert audio[:4] == b"RIFF" and len(sent) >= 2
    for body in sent:
        assert set(body) >= {"text", "target_language_code", "speaker", "model"} and "inputs" not in body
        assert body["target_language_code"] == "hi-IN" and len(body["text"]) <= 1500
        assert body["model"] == settings.SARVAM_TTS_MODEL


def test_tts_falls_back_offline(monkeypatch):
    from backend.core.config import settings
    from backend.voice import tts
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "")
    monkeypatch.setattr(tts, "_edge_tts_to_file", lambda *a: False)      # no internet
    monkeypatch.setattr(tts, "_pyttsx3_to_wav", lambda text: _wav())
    assert tts.tts_engine.synthesize("hello", "en-IN")[:4] == b"RIFF"


@pytest.fixture
def client(isolated, monkeypatch):
    from backend.core.config import settings
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test")
    monkeypatch.setattr(settings, "CHAT_WAIT_SECONDS", 5)
    import backend.memory.conversation_store as cs
    monkeypatch.setattr(cs.conversation_store, "_path", isolated / "history.json", raising=False)
    from backend.main import app
    with TestClient(app) as c:
        yield c


def test_speak_endpoint_returns_audio_without_blocking(client, monkeypatch):
    import threading
    from backend.voice import tts
    seen = {}

    def synth(text, lang):
        seen["thread"] = threading.current_thread().name
        seen["lang"] = lang
        return _wav()
    monkeypatch.setattr(tts.tts_engine, "synthesize", synth)
    r = client.post("/api/voice/speak", json={"text": "Namaste", "lang": "hi-IN"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert seen["lang"] == "hi-IN" and seen["thread"] != threading.main_thread().name   # ran off the event loop
    assert client.post("/api/voice/speak", json={"text": " "}).status_code == 400


def test_speak_endpoint_reports_failure(client, monkeypatch):
    from backend.voice import tts
    monkeypatch.setattr(tts.tts_engine, "synthesize", lambda t, l: b"")
    assert client.post("/api/voice/speak", json={"text": "hi"}).status_code == 500   # frontend → browser voice


# ── STT ───────────────────────────────────────────────────────────────────────

def test_transcribe_success_and_empty(client, monkeypatch):
    from backend.core.config import settings
    from backend.voice.stt import stt
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "")
    monkeypatch.setattr(stt, "transcribe_file", lambda p: "book a hotel in Goa")
    r = client.post("/api/voice/transcribe", files={"file": ("v.webm", b"xx", "audio/webm")}).json()
    assert r == {"text": "book a hotel in Goa", "success": True, "engine": "whisper"}
    monkeypatch.setattr(stt, "transcribe_file", lambda p: "  ")
    r = client.post("/api/voice/transcribe", files={"file": ("v.webm", b"xx", "audio/webm")}).json()
    assert r["success"] is False and r["error"]


def test_transcribe_reports_missing_whisper(client, monkeypatch):
    from backend.core.config import settings
    from backend.voice.stt import stt
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "")

    def boom(p):
        raise ModuleNotFoundError("No module named 'whisper'")
    monkeypatch.setattr(stt, "transcribe_file", boom)
    r = client.post("/api/voice/transcribe", files={"file": ("v.webm", b"xx", "audio/webm")}).json()
    assert r["success"] is False and "whisper" in r["error"].lower()


def test_sarvam_stt_auto_detects_language(client, monkeypatch):
    import httpx
    from backend.core.config import settings
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "k")
    sent = {}

    class AC:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers=None, files=None, data=None):
            sent.update(data)
            return httpx.Response(200, json={"transcript": "goa mein hotel book karo"})
    monkeypatch.setattr(httpx, "AsyncClient", AC)
    r = client.post("/api/voice/transcribe", files={"file": ("v.webm", b"xx", "audio/webm")}).json()
    assert r["engine"] == "sarvam" and r["text"].startswith("goa")
    assert sent["language_code"] == "unknown" and sent["model"] == settings.SARVAM_STT_MODEL


def test_whisper_settings_are_used(monkeypatch):
    from backend.core.config import settings
    from backend.voice import stt
    monkeypatch.setattr(settings, "WHISPER_LANGUAGE", "auto")
    monkeypatch.setattr(settings, "WHISPER_MODEL", "small")
    assert stt._language() is None and stt._model_name() == "small"
    monkeypatch.setattr(settings, "WHISPER_LANGUAGE", "hi")
    assert stt._language() == "hi"


# ── Voice-sourced task through /api/chat ──────────────────────────────────────

def test_voice_chat_hindi_reply_and_approval_status(client, isolated):
    from backend.llm.models import set_model_override
    out = isolated / "allowed" / "x.txt"
    out.write_text("old")
    set_model_override("planner", FakeModel([
        plan(("productivity", "overwrite x.txt")),
        tool_call("write_text_file", {"path": str(out), "content": "new", "overwrite": True}),
        AIMessage(content="Ho gaya."),
    ]))
    r = client.post("/api/chat", json={"message": "x.txt file ko overwrite karo, abhi", "source": "voice"}).json()
    assert r["lang"] == "hi-IN" and r["task_status"] == "waiting_approval"
    assert "(e" not in r["text"] and r["spoken"]
    t = client.get(f"/api/tasks/{r['task_id']}").json()
    assert t["source"] == "voice"


# ── Task manager: resume right after an approval keeps the run tracked ───────

async def test_fast_resume_after_approval_can_still_be_cancelled(isolated):
    from backend.tests.test_graph import _set, _tm
    a = isolated / "allowed" / "a.txt"
    a.write_text("old")
    started = asyncio.Event()

    async def slow(msgs):
        started.set()
        await asyncio.sleep(30)
        return AIMessage(content="never")

    class Slow(FakeModel):
        async def ainvoke(self, messages, *aa, **kw):
            if not self.script:
                await slow(messages)
            return await super().ainvoke(messages)

    _set(planner=Slow([plan(("productivity", "overwrite a")),
                       tool_call("write_text_file", {"path": str(a), "content": "B", "overwrite": True})]))
    tm = await _tm()
    tid = tm.start("overwrite a")
    t = await tm.wait(tid, 10)
    assert t["status"] == "waiting_approval"
    tm.respond(tm.pending_approvals()[0]["id"], approved=True)      # immediately, no sleep
    await asyncio.sleep(0.3)
    assert tid in tm._running, "resumed run must stay registered (cancel / wait depend on it)"
    assert tm.cancel(tid)
    await asyncio.sleep(0.1)
    assert tm.get(tid)["status"] == "cancelled"
