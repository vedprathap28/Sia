"""
Sia AI — Wake Word Detector
=============================
Continuously monitors the microphone for the wake phrase ("hey sia").

Strategy:
  1. Read 500ms audio chunks in a loop
  2. Gate on energy — skip chunks below silence threshold
  3. Accumulate chunks until we have ~1.5s of audio
  4. Transcribe with Whisper (fast, tiny/base model)
  5. Check if transcript contains the wake phrase
  6. If yes → fire the on_wake callback, then pause for cooldown

This runs entirely in a background daemon thread and does not
block the FastAPI event loop.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("wakeword")

# ── Tuning constants ──────────────────────────────────────────────────────
_CHUNK_SECS    = 0.5     # seconds per audio chunk
_ACCUMULATE_S  = 1.5     # accumulate this many seconds before transcribing
_COOLDOWN_S    = 2.0     # seconds to ignore input after wake word fires
_ENERGY_GATE   = 0.008   # RMS below this → skip (no speech)


class WakeWordDetector:
    """
    Always-on wake word listener.

    Usage:
        detector = WakeWordDetector()
        detector.start(on_wake=my_callback)
        ...
        detector.stop()
    """

    def __init__(self):
        self._wake_phrase = settings.WAKE_WORD.lower().strip()
        self._sr          = settings.SAMPLE_RATE
        self._running     = False
        self._thread:  threading.Thread | None = None
        self._last_wake: float = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self, on_wake: Callable[[], None]) -> None:
        """Start the listening loop in a background daemon thread."""
        if self._running:
            log.warning("Wake word detector already running")
            return

        if not settings.WAKEWORD_ENABLED:
            log.info("Wake word detection disabled (WAKEWORD_ENABLED=false)")
            return

        self._running = True
        self._thread  = threading.Thread(
            target=self._loop,
            args=(on_wake,),
            name="sia-wakeword",
            daemon=True,
        )
        self._thread.start()
        log.info("Wake word detector started — listening for: %r", self._wake_phrase)

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        log.info("Wake word detector stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Main loop ─────────────────────────────────────────────────────────

    def _loop(self, on_wake: Callable[[], None]) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            log.error("sounddevice not installed — wake word detection unavailable")
            self._running = False
            return

        chunk_size = int(self._sr * _CHUNK_SECS)
        accumulate_chunks = max(1, int(_ACCUMULATE_S / _CHUNK_SECS))
        buffer: list[np.ndarray] = []

        try:
            with sd.InputStream(
                samplerate=self._sr,
                channels=1,
                dtype="float32",
                blocksize=chunk_size,
            ) as stream:
                while self._running:
                    chunk, _ = stream.read(chunk_size)
                    chunk     = chunk.flatten()
                    rms       = float(np.sqrt(np.mean(chunk ** 2)))

                    # Skip silent chunks
                    if rms < _ENERGY_GATE:
                        buffer.clear()
                        continue

                    buffer.append(chunk)

                    # Once we have enough audio, transcribe and check
                    if len(buffer) >= accumulate_chunks:
                        audio = np.concatenate(buffer)
                        buffer.clear()

                        text = self._transcribe(audio)

                        if self._wake_phrase in text:
                            now = time.time()
                            if now - self._last_wake > _COOLDOWN_S:
                                self._last_wake = now
                                log.info("🟢 Wake word detected in: %r", text)
                                try:
                                    on_wake()
                                except Exception as e:
                                    log.exception("on_wake callback error: %s", e)
                            else:
                                log.debug("Wake word in cooldown, ignoring")
                        else:
                            log.debug("Wake check: %r", text[:60])

        except Exception as e:
            log.exception("Wake word loop crashed: %s", e)
            self._running = False

    # ── Transcription ──────────────────────────────────────────────────────

    def _transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio for wake word detection — errors are non-fatal."""
        try:
            from backend.voice.stt import stt
            return stt.transcribe_array(audio, self._sr).lower()
        except Exception as e:
            log.debug("Wake transcription failed: %s", e)
            return ""


# ── Singleton ──────────────────────────────────────────────────────────────
wake_detector = WakeWordDetector()