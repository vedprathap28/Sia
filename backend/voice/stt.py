"""
Sia AI — Speech-to-Text
=========================
Uses OpenAI Whisper 'small' model for accurate Indian English transcription.
'small' is much more accurate than 'base' for non-native accents.
One-time download ~460MB, cached locally after that.
"""
from __future__ import annotations

import io
import numpy as np
from pathlib import Path
from backend.core.logger import get_logger

log = get_logger("stt")

# WHISPER_MODEL / WHISPER_LANGUAGE come from .env ('small' is much better than 'base' for Indian
# English; WHISPER_LANGUAGE=auto lets Whisper detect Hindi/Telugu/Marathi instead of forcing English).
from backend.core.config import settings


def _model_name() -> str:
    return (settings.WHISPER_MODEL or "small").strip()


def _language():
    lang = (settings.WHISPER_LANGUAGE or "auto").strip().lower()
    return None if lang in ("", "auto", "none") else lang


class STT:
    def __init__(self):
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        import whisper
        log.info("Loading Whisper model '%s' (first run downloads it)", _model_name())
        self._model = whisper.load_model(_model_name())
        log.info("Whisper ready")

    def transcribe_array(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe a numpy float32 audio array."""
        self._load()
        if audio is None or len(audio) == 0:
            return ""
        try:
            import whisper
            # Whisper expects float32 at 16kHz
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            # Normalize
            if audio.max() > 1.0:
                audio = audio / 32768.0

            result = self._model.transcribe(
                audio,
                language=_language(),
                fp16=False,
                condition_on_previous_text=False,
                # Suppress common false positives when room is quiet
                no_speech_threshold=0.6,
                logprob_threshold=-1.0,
            )
            text = result.get("text", "").strip()
            log.info("Transcribed: %r", text)
            return text
        except Exception as e:
            log.error("Transcription failed: %s", e)
            return ""

    def transcribe_file(self, path: str) -> str:
        """Transcribe an audio file (wav, mp3, webm etc.)."""
        self._load()
        try:
            result = self._model.transcribe(
                path,
                language=_language(),
                fp16=False,
                no_speech_threshold=0.6,
                logprob_threshold=-1.0,
            )
            text = result.get("text", "").strip()
            log.info("Transcribed file: %r", text)
            return text
        except FileNotFoundError as e:
            # whisper shells out to ffmpeg to decode webm/mp3
            raise RuntimeError("ffmpeg is not installed or not on PATH (needed by Whisper)") from e


stt = STT()
