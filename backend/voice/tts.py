"""
Sia AI — TTS Engine
====================
Priority chain (fastest → best quality):
  1. Sarvam TTS  — Indian voices, ~300ms (PRIMARY if SARVAM_API_KEY set)
  2. Edge TTS    — Microsoft neural voices, free (~500ms)
  3. pyttsx3     — Fully offline, robotic but instant (LAST RESORT)

Fix: sarvam_tts_sync() was calling asyncio.new_event_loop() which crashes
when FastAPI's event loop is already running ("Cannot run the event loop
while another loop is running").

Fix: synthesize() now uses httpx.Client (sync) directly — no event loop
needed at all. Simpler and faster.
"""
from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import httpx

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("tts")

# ── Edge TTS voice map (fallback) ─────────────────────────────────────────────
_EDGE_VOICE_MAP = {
    "en-IN": "en-IN-NeerjaNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "te-IN": "te-IN-ShrutiNeural",
    "mr-IN": "mr-IN-AarohiNeural",
}

# ── Sarvam TTS language code map ──────────────────────────────────────────────
_SARVAM_LANG_MAP = {
    "en-IN": "en-IN",
    "hi-IN": "hi-IN",
    "te-IN": "te-IN",
    "mr-IN": "mr-IN",
    "ta-IN": "ta-IN",
    "kn-IN": "kn-IN",
    "ml-IN": "ml-IN",
}

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# ── Sarvam request (bulbul:v3 contract: one `text` per request, ≤2500 chars) ─
# The old v1 shape ("inputs": [...], enable_preprocessing, loudness) is rejected with HTTP 400
# by current models. Long replies are split on sentence boundaries and the WAVs merged.
_SARVAM_MAX_CHARS = 1500


def _sarvam_payload(text: str, lang: str) -> dict:
    return {
        "text":                 text,
        "target_language_code": _SARVAM_LANG_MAP.get(lang, "en-IN"),
        "speaker":              settings.SARVAM_TTS_SPEAKER,
        "pace":                 1.05,
        "speech_sample_rate":   22050,
        "model":                settings.SARVAM_TTS_MODEL,
    }

def _sarvam_segments(text: str) -> list[str]:
    import re as _re
    text = _re.sub(r"[*_`#>]+", "", text).strip()       # markdown confuses TTS
    parts, cur = [], ""
    for sent in _re.split(r"(?<=[.!?।])\s+", text):
        while len(sent) > _SARVAM_MAX_CHARS:
            parts.append(sent[:_SARVAM_MAX_CHARS]); sent = sent[_SARVAM_MAX_CHARS:]
        if len(cur) + len(sent) + 1 > _SARVAM_MAX_CHARS and cur:
            parts.append(cur); cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts[:10] or [" "]

def _merge_wavs(chunks: list[bytes]) -> bytes:
    if len(chunks) == 1:
        return chunks[0]
    import io as _io, wave as _wave
    out = _io.BytesIO()
    with _wave.open(_io.BytesIO(chunks[0])) as first:
        params = first.getparams()
    with _wave.open(out, "wb") as w:
        w.setparams(params)
        for c in chunks:
            with _wave.open(_io.BytesIO(c)) as r:
                w.writeframes(r.readframes(r.getnframes()))
    return out.getvalue()



# ── Sarvam TTS async (used by livekit_agent.py) ───────────────────────────────

async def sarvam_tts_async(text: str, lang: str = "en-IN") -> bytes:
    """Async Sarvam TTS — returns WAV bytes. Used by LiveKit agent."""
    api_key = settings.SARVAM_API_KEY
    if not api_key:
        raise ValueError("SARVAM_API_KEY not set")

    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for seg in _sarvam_segments(text):
            resp = await client.post(SARVAM_TTS_URL, json=_sarvam_payload(seg, lang),
                                     headers={"api-subscription-key": api_key, "Content-Type": "application/json"})
            resp.raise_for_status()
            chunks += [base64.b64decode(a) for a in resp.json().get("audios", [])]
        if not chunks:
            raise ValueError("Sarvam TTS returned empty audios")
        wav_bytes = _merge_wavs(chunks)
        log.info("Sarvam TTS async: %d bytes (lang=%s)", len(wav_bytes), lang)
        return wav_bytes


# ── Sarvam TTS sync — uses httpx.Client directly, NO event loop ───────────────
# FIX: the old version called asyncio.new_event_loop() which crashes when
# FastAPI's loop is already running. This version uses the sync httpx client
# so there is no async/event loop involvement at all.

def sarvam_tts_sync(text: str, lang: str = "en-IN") -> bytes:
    """
    Sync Sarvam TTS — uses httpx.Client (blocking), safe to call from
    any thread including inside a running FastAPI event loop context.
    """
    api_key = settings.SARVAM_API_KEY
    if not api_key:
        raise ValueError("SARVAM_API_KEY not set")

    try:
        chunks: list[bytes] = []
        with httpx.Client(timeout=20) as client:
            for seg in _sarvam_segments(text):
                resp = client.post(SARVAM_TTS_URL, json=_sarvam_payload(seg, lang),
                                   headers={"api-subscription-key": api_key, "Content-Type": "application/json"})
                if resp.status_code >= 400:
                    log.warning("Sarvam TTS HTTP %s: %s", resp.status_code, resp.text[:300])
                resp.raise_for_status()
                chunks += [base64.b64decode(a) for a in resp.json().get("audios", [])]
            if not chunks:
                raise ValueError("Sarvam TTS returned empty audios")
            wav_bytes = _merge_wavs(chunks)
            log.info("Sarvam TTS sync: %d bytes (lang=%s)", len(wav_bytes), lang)
            return wav_bytes
    except Exception as e:
        log.warning("Sarvam TTS sync error: %s", e)
        return b""


# ── Edge TTS ──────────────────────────────────────────────────────────────────

def _edge_tts_to_file(text: str, voice: str, out_path: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "edge_tts",
             "--voice", voice, "--text", text, "--write-media", out_path],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0 and Path(out_path).stat().st_size > 0
    except Exception as e:
        log.warning("Edge TTS failed: %s", e)
        return False


# ── ElevenLabs (optional override) ────────────────────────────────────────────

def _elevenlabs_synthesize(text: str) -> bytes:
    import json as _json
    import urllib.request

    api_key  = settings.ELEVENLABS_API_KEY
    voice_id = settings.ELEVENLABS_VOICE_ID
    url      = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload  = _json.dumps({
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.45, "similarity_boost": 0.82,
            "style": 0.35, "use_speaker_boost": True,
        },
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


# ── Audio playback (Windows) ──────────────────────────────────────────────────

def _play_wav_bytes(wav_bytes: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        tmp = f.name
    _play_file(tmp)
    Path(tmp).unlink(missing_ok=True)


def _play_mp3_bytes(mp3_bytes: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_bytes)
        tmp = f.name
    _play_file(tmp)
    Path(tmp).unlink(missing_ok=True)


def _play_file(path: str) -> None:
    ps = f"""
Add-Type -AssemblyName presentationCore
$mp = New-Object System.Windows.Media.MediaPlayer
$mp.Open([uri]'{path}')
$mp.Play()
Start-Sleep -Milliseconds 800
$d = $mp.NaturalDuration.TimeSpan.TotalSeconds
if ($d -gt 0) {{ Start-Sleep -Seconds $d }} else {{ Start-Sleep -Seconds 15 }}
$mp.Stop(); $mp.Close()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        log.warning("PowerShell playback failed: %s", e)


# ── Unified TTSEngine ─────────────────────────────────────────────────────────

_PYTTSX3_LOCK = __import__("threading").Lock()


def _pyttsx3_to_wav(text: str) -> bytes:
    try:
        import pyttsx3
    except Exception:
        return b""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    try:
        with _PYTTSX3_LOCK:                      # pyttsx3 is not thread-safe
            eng = pyttsx3.init()
            eng.save_to_file(text, tmp)
            eng.runAndWait()
        data = Path(tmp).read_bytes()
        return data if len(data) > 44 else b""
    except Exception as e:
        log.warning("pyttsx3 fallback failed: %s", e)
        return b""
    finally:
        Path(tmp).unlink(missing_ok=True)


class TTSEngine:
    """
    Unified TTS engine — synthesize() returns audio bytes for the frontend
    to play. speak_with_lang() is for server-side playback (not used in
    the current browser-managed audio architecture).
    """

    def synthesize(self, text: str, lang: str = "en-IN") -> bytes:
        """
        Return audio bytes — called by /api/voice/speak.
        Frontend plays the audio; backend never plays anything.
        Priority: Sarvam → Edge TTS.
        """
        if not text.strip():
            return b""

        # Sarvam TTS (sync, no event loop issues)
        if settings.SARVAM_API_KEY:
            try:
                wav = sarvam_tts_sync(text, lang)
                if wav:
                    return wav
            except Exception as e:
                log.warning("Sarvam TTS synthesize failed (%s) — Edge TTS fallback", e)

        # Edge TTS fallback
        voice = _EDGE_VOICE_MAP.get(lang, "en-IN-NeerjaNeural")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        try:
            if _edge_tts_to_file(text, voice, tmp):
                return Path(tmp).read_bytes()
        except Exception as e:
            log.warning("Edge TTS fallback failed: %s", e)
        finally:
            Path(tmp).unlink(missing_ok=True)

        # Offline fallback (Windows SAPI voices via pyttsx3) — robotic but always works
        wav = _pyttsx3_to_wav(text)
        if wav:
            return wav

        log.error("All TTS engines failed for text: %r", text[:60])
        return b""

    def speak_with_lang(self, text: str, lang: str = "en-IN") -> bytes:
        """Server-side playback (legacy — not used when frontend owns audio)."""
        if not text.strip():
            return b""

        engine = settings.TTS_ENGINE

        if engine == "elevenlabs" and settings.ELEVENLABS_API_KEY:
            try:
                audio = _elevenlabs_synthesize(text)
                if audio:
                    _play_mp3_bytes(audio)
                    return audio
            except Exception as e:
                log.warning("ElevenLabs failed (%s) — Sarvam fallback", e)

        if settings.SARVAM_API_KEY:
            try:
                wav = sarvam_tts_sync(text, lang)
                if wav:
                    _play_wav_bytes(wav)
                    return wav
            except Exception as e:
                log.warning("Sarvam TTS failed (%s) — Edge TTS fallback", e)

        voice = _EDGE_VOICE_MAP.get(lang, "en-IN-NeerjaNeural")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        try:
            if _edge_tts_to_file(text, voice, tmp):
                _play_file(tmp)
                return Path(tmp).read_bytes()
        finally:
            Path(tmp).unlink(missing_ok=True)

        try:
            import pyttsx3
            engine_p = pyttsx3.init()
            engine_p.setProperty("rate", 165)
            engine_p.say(text)
            engine_p.runAndWait()
        except Exception as e:
            log.warning("All TTS failed: %s", e)
        return b""

    def speak(self, text: str) -> bytes:
        return self.speak_with_lang(text, "en-IN")

    def speak_async(self, text: str, lang: str = "en-IN") -> None:
        """Fire-and-forget speak in background thread."""
        threading.Thread(
            target=self.speak_with_lang, args=(text, lang), daemon=True
        ).start()


tts_engine = TTSEngine()