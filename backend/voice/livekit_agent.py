"""
Sia AI — LiveKit Voice Agent (Speed-optimized)
===============================================
What changed from previous version:
  ✅ Groq llama-3.3-70b-versatile as LLM  (~300ms vs ~2000ms Gemini)
  ✅ Sarvam TTS as primary                 (~300ms vs ~900ms ElevenLabs)
  ✅ Filler phrase fires INSTANTLY         user hears "On it." while Groq thinks
  ✅ RAG check gated                       skipped when ChromaDB is empty (saves 300ms)
  ✅ LangGraph agent used for tool calls   (future-proof: web search etc plug in here)
  ✅ Parallel: filler audio + LLM call run at same time

End-to-end latency target:
  STT (Sarvam)   ~400ms
  Filler to ear  ~200ms  ← fires immediately after STT, before LLM
  LLM (Groq)     ~300ms  ← runs in parallel with filler playback
  TTS (Sarvam)   ~350ms
  ─────────────────────
  Total perceived ~950ms  (user hears filler at ~600ms, full reply at ~1.3s)

Architecture:
  Browser mic (LiveKit WebRTC)
      ↓
  Sarvam Saaras v3 STT
      ↓  (transcript ready)
  ┌─── Filler TTS fires instantly (parallel) ───┐
  │                                              │
  └─── LangGraph SIA agent processes query ──────┘
      ↓  (reply ready)
  Sarvam TTS → LiveKit audio sink → browser speaker
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tempfile
import wave
from pathlib import Path
from typing import Optional

from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, JobContext, RoomInputOptions
from livekit.plugins import silero

import httpx

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("livekit_agent")


# ── Language detection ─────────────────────────────────────────────────────────

_DEVANAGARI_RE = re.compile(r'[\u0900-\u097F]')
_TELUGU_RE     = re.compile(r'[\u0C00-\u0C7F]')
_HINDI_ROMAN   = re.compile(
    r'\b(kya|hai|hain|karo|bolo|batao|suno|acha|thik|nahi|haan|bhai|yaar|'
    r'kal|aaj|raat|din|subah|khana|pani|ghar|dost|pyaar|matlab|samajh|'
    r'mujhe|tujhe|humko|unhe|woh|yeh|koi|kuch|abhi|phir|lekin|aur|ya|'
    r'bahut|bohot|thoda|zyada|bilkul|zarur|sahi|galat|achha|bura)\b',
    re.IGNORECASE,
)
_TELUGU_ROMAN  = re.compile(
    r'\b(emi|enti|cheppu|cheyyi|neeku|naku|meeru|mee|memu|okati|rendo|'
    r'akkada|ikkada|ela|enduku|ekkuva|takkuva|manchidi|chedduga|avunu|'
    r'kaadu|undi|ledu|vachindi|poyindi|chestanu)\b',
    re.IGNORECASE,
)
_MARATHI_ROMAN = re.compile(
    r'\b(kasa|kashi|aho|naahi|hoy|nako|mala|tula|aapan|aamhi|tumhi|'
    r'mi|tu|to|ti|te|kay|kuth|keva|ka|ani|pan|mhanun|tyala|tila)\b',
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    if _TELUGU_RE.search(text):   return 'te-IN'
    if _DEVANAGARI_RE.search(text):
        return 'mr-IN' if _MARATHI_ROMAN.search(text) else 'hi-IN'
    te = len(_TELUGU_ROMAN.findall(text))
    mr = len(_MARATHI_ROMAN.findall(text))
    hi = len(_HINDI_ROMAN.findall(text))
    best = max(te, mr, hi)
    if best == 0: return 'en-IN'
    if te == best: return 'te-IN'
    if mr == best: return 'mr-IN'
    if hi >= 2:    return 'hi-IN'
    return 'en-IN'


# ── Filler phrases — instant acknowledgement ──────────────────────────────────

import random

_FILLERS = {
    'en-IN': ["Mmm.", "Right.", "Got it.", "Okay.", "Sure.", "On it.", "Yeah."],
    'hi-IN': ["Haan.", "Theek hai.", "Achha.", "Samjha.", "Bilkul.", "Suno."],
    'te-IN': ["Sare.", "Avunu.", "Okay.", "Chestanu.", "Emi chepparu?"],
    'mr-IN': ["Hoy.", "Theek aahe.", "Samjhlo.", "Okay.", "Aalo."],
}

def random_filler(lang: str = 'en-IN') -> str:
    return random.choice(_FILLERS.get(lang, _FILLERS['en-IN']))


# ── Sarvam STT ────────────────────────────────────────────────────────────────

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


async def sarvam_transcribe(audio_bytes: bytes, lang_hint: str = 'en-IN') -> str:
    api_key = settings.SARVAM_API_KEY
    if not api_key:
        log.warning("SARVAM_API_KEY not set")
        return ''
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                SARVAM_STT_URL,
                headers={'api-subscription-key': api_key},
                files={'file': ('audio.wav', audio_bytes, 'audio/wav')},
                data={'model': 'saaras:v3', 'language_code': lang_hint, 'with_timestamps': 'false'},
            )
            resp.raise_for_status()
            transcript = resp.json().get('transcript', '')
            log.info("STT: %r", transcript)
            return transcript
    except Exception as e:
        log.warning("Sarvam STT failed: %s", e)
        return ''


# ── Sarvam TTS ────────────────────────────────────────────────────────────────

_SARVAM_LANG_MAP = {
    'en-IN': 'en-IN', 'hi-IN': 'hi-IN',
    'te-IN': 'te-IN', 'mr-IN': 'mr-IN',
}

async def sarvam_tts(text: str, lang: str = 'en-IN') -> bytes:
    """
    Returns WAV bytes from Sarvam TTS.
    Falls back to Edge TTS if Sarvam fails.
    """
    api_key = settings.SARVAM_API_KEY
    if not api_key:
        return await _edge_tts_fallback(text, lang)

    try:
        import base64
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                SARVAM_TTS_URL,
                json={
                    "inputs":               [text],
                    "target_language_code": _SARVAM_LANG_MAP.get(lang, 'en-IN'),
                    "speaker":              settings.SARVAM_TTS_SPEAKER,
                    "pitch":                0,
                    "pace":                 1.1,
                    "loudness":             1.5,
                    "speech_sample_rate":   22050,
                    "enable_preprocessing": True,
                    "model":                settings.SARVAM_TTS_MODEL,
                },
                headers={
                    'api-subscription-key': api_key,
                    'Content-Type': 'application/json',
                },
            )
            resp.raise_for_status()
            audios = resp.json().get('audios', [])
            if not audios:
                raise ValueError("Empty audios from Sarvam TTS")
            wav_bytes = base64.b64decode(audios[0])
            log.info("Sarvam TTS: %d bytes (lang=%s)", len(wav_bytes), lang)
            return wav_bytes
    except Exception as e:
        log.warning("Sarvam TTS failed (%s) — Edge TTS fallback", e)
        return await _edge_tts_fallback(text, lang)


async def _edge_tts_fallback(text: str, lang: str) -> bytes:
    """Async Edge TTS fallback."""
    import edge_tts
    _VOICES = {
        'en-IN': 'en-IN-NeerjaNeural',
        'hi-IN': 'hi-IN-SwaraNeural',
        'te-IN': 'te-IN-ShrutiNeural',
        'mr-IN': 'mr-IN-AarohiNeural',
    }
    voice = _VOICES.get(lang, 'en-IN-NeerjaNeural')
    buf   = io.BytesIO()
    try:
        communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                buf.write(chunk['data'])
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.error("Edge TTS also failed: %s", e)
        return b''


# ── RAG guard — skip if ChromaDB is empty ────────────────────────────────────

def _chroma_has_documents() -> bool:
    """
    Returns True only if the ChromaDB documents collection has at least 1 doc.
    This saves ~300ms per turn when no documents have been uploaded.
    Cached after first check — ChromaDB doesn't shrink at runtime.
    """
    try:
        import chromadb
        client = chromadb.PersistentClient(path=settings.CHROMA_DOCS_PATH)
        col    = client.get_or_create_collection("documents")
        count  = col.count()
        log.info("ChromaDB docs count: %d", count)
        return count > 0
    except Exception as e:
        log.warning("ChromaDB check failed: %s", e)
        return False

# Module-level cache — checked once per agent startup
_RAG_AVAILABLE: Optional[bool] = None

def rag_available() -> bool:
    global _RAG_AVAILABLE
    if _RAG_AVAILABLE is None:
        _RAG_AVAILABLE = _chroma_has_documents()
        if not _RAG_AVAILABLE:
            log.info("RAG disabled — ChromaDB is empty (saves ~300ms/turn)")
    return _RAG_AVAILABLE


# ── Core voice turn processing ────────────────────────────────────────────────

async def process_turn(
    transcript: str,
    publish_audio_fn,    # async fn(bytes) → sends audio to LiveKit room
    publish_state_fn,    # async fn(str)   → sends state to frontend
) -> str:
    """
    Handles one full voice turn:
      1. Detect language
      2. Fire filler phrase instantly (parallel with LLM call)
      3. Run LangGraph agent (Groq LLM + tools)
      4. Sarvam TTS on reply
      5. Publish audio back

    The key speed trick: filler TTS + LLM call run as parallel asyncio tasks.
    User hears "On it." while Groq is still thinking.
    """
    if not transcript.strip():
        return ''

    lang = detect_language(transcript)
    log.info("Turn | lang=%s | %r", lang, transcript)

    # ── Step 1: Filler + LLM in PARALLEL ─────────────────────────────────
    filler_text = random_filler(lang)

    async def _filler_task():
        """TTS the filler and publish it — fires immediately."""
        try:
            filler_audio = await sarvam_tts(filler_text, lang)
            if filler_audio:
                await publish_audio_fn(filler_audio)
        except Exception as e:
            log.warning("Filler TTS failed: %s", e)

    async def _llm_task():
        """Run Sia agent — returns reply string."""
        try:
            # Use LangGraph agent if available, else fallback to existing agent
            try:
                from backend.agent.sia_graph import sia_graph
                result = await sia_graph.run(transcript, lang=lang, rag=rag_available())
                return result
            except ImportError:
                from backend.agent.agent import agent
                result = await agent.process(transcript)
                return result.spoken or result.text or "Done."
        except Exception as e:
            log.exception("Agent error: %s", e)
            return _error_msg(lang)

    # Fire both simultaneously
    await publish_state_fn('processing')
    filler_task = asyncio.create_task(_filler_task())
    llm_task    = asyncio.create_task(_llm_task())

    # Wait for LLM (filler runs independently)
    reply = await llm_task
    await filler_task   # ensure filler is done before we publish reply audio

    if not reply:
        reply = _error_msg(lang)

    # ── Step 2: TTS the real reply ─────────────────────────────────────────
    await publish_state_fn('speaking')
    reply_lang  = detect_language(reply) if detect_language(reply) != 'en-IN' else lang
    reply_audio = await sarvam_tts(reply, reply_lang)
    if reply_audio:
        await publish_audio_fn(reply_audio)

    await publish_state_fn('ready')
    return reply


def _error_msg(lang: str) -> str:
    return {
        'en-IN': "Something went wrong. Try again.",
        'hi-IN': "Kuch gadbad ho gayi. Dobara try karo.",
        'te-IN': "Emi jarigindo. Malli try cheyyandi.",
        'mr-IN': "Kahi chukle. Parat try kara.",
    }.get(lang, "Something went wrong.")


# ── LiveKit agent entrypoint ──────────────────────────────────────────────────

class SiaVoiceAgent:
    """
    LiveKit voice agent for Sia.
    Connects to a LiveKit room and handles push-to-talk voice turns.
    """

    def __init__(self):
        self._lang: str   = 'en-IN'
        self._speaking:   bool = False

    async def entrypoint(self, ctx: JobContext):
        await ctx.connect()
        log.info("Sia voice agent connected: room=%s", ctx.room.name)

        # Announce readiness to frontend
        await ctx.room.local_participant.publish_data(
            json.dumps({"type": "agent_ready", "message": "Sia is online."}).encode(),
            reliable=True,
        )

        # Check RAG once at startup
        _rag = rag_available()
        log.info("RAG available: %s", _rag)

        # Audio buffer for PTT recording
        _audio_buffer: list[bytes] = []
        _ptt_active   = False

        async def publish_audio(audio_bytes: bytes):
            """Send audio bytes back to the LiveKit room."""
            if not audio_bytes:
                return
            # Publish as data message for the frontend to play
            # (The frontend's LiveKit hook handles audio track publishing)
            await ctx.room.local_participant.publish_data(
                json.dumps({
                    "type": "audio_response",
                    "data": __import__('base64').b64encode(audio_bytes).decode(),
                    "format": "wav",
                }).encode(),
                reliable=True,
            )

        async def publish_state(state: str):
            await ctx.room.local_participant.publish_data(
                json.dumps({"type": "state", "state": state}).encode(),
                reliable=True,
            )

        @ctx.room.on('data_received')
        def on_data(packet):
            asyncio.create_task(_handle_packet(packet))

        async def _handle_packet(packet):
            nonlocal _ptt_active, _audio_buffer
            try:
                msg = json.loads(packet.data.decode())
                kind = msg.get('type')

                if kind == 'PTT_START':
                    _ptt_active   = True
                    _audio_buffer = []
                    await publish_state('listening')
                    log.info("PTT started")

                elif kind == 'PTT_STOP':
                    _ptt_active = False
                    log.info("PTT stopped — %d audio chunks", len(_audio_buffer))
                    await publish_state('processing')

                    if _audio_buffer:
                        # Combine buffered audio chunks → WAV
                        raw_pcm = b''.join(_audio_buffer)
                        wav_bytes = _pcm_to_wav(raw_pcm)

                        # Transcribe
                        transcript = await sarvam_transcribe(wav_bytes, self._lang)
                        if transcript:
                            # Update language for next turn
                            self._lang = detect_language(transcript)
                            # Process the full turn
                            await process_turn(transcript, publish_audio, publish_state)
                        else:
                            await publish_state('ready')
                    else:
                        await publish_state('ready')

                elif kind == 'AUDIO_CHUNK':
                    # Raw PCM bytes sent from frontend mic
                    if _ptt_active:
                        import base64
                        chunk = base64.b64decode(msg.get('data', ''))
                        if chunk:
                            _audio_buffer.append(chunk)

                elif kind == 'TEXT_INPUT':
                    # Direct text input (from chat box)
                    text = msg.get('text', '').strip()
                    if text:
                        lang = detect_language(text)
                        await process_turn(text, publish_audio, publish_state)

            except Exception as e:
                log.warning("Packet handler error: %s", e)

        # Keep alive
        await asyncio.sleep(float('inf'))


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)          # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf.read()


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    """
    Start Sia LiveKit voice agent.
    Run from Sia/ root:
        python -m backend.voice.livekit_agent

    Required .env vars:
        LIVEKIT_URL=wss://your-project.livekit.cloud
        LIVEKIT_API_KEY=APIxxxxxxxx
        LIVEKIT_API_SECRET=your_secret
        SARVAM_API_KEY=your_key
        GROQ_API_KEY=your_key
    """
    sia_agent = SiaVoiceAgent()
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=sia_agent.entrypoint,
            agent_name='sia-voice-agent',
        )
    )
