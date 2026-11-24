"""
Sia AI — Microphone Recorder
===============================
Records from the default microphone until silence is detected (VAD).

Algorithm:
  1. Open mic stream at 16kHz mono float32
  2. Read 100ms chunks
  3. Calculate RMS energy of each chunk
  4. Start collecting frames once speech is detected (RMS > threshold)
  5. Stop collecting after SILENCE_DURATION seconds of continuous silence
  6. Return concatenated numpy array

The recorder is stateless — call record() repeatedly.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

import numpy as np

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("recorder")

# ── Constants ──────────────────────────────────────────────────────────────
_CHUNK_SECS  = 0.1    # 100ms chunks
_PRE_BUFFER  = 5      # keep this many chunks BEFORE speech starts (for clipping prevention)


class Recorder:
    """
    Voice-activity-detection-based microphone recorder.
    Blocks until a complete utterance is captured.
    """

    def __init__(self):
        self.sr        = settings.SAMPLE_RATE
        self.threshold = settings.SILENCE_THRESHOLD
        self.silence_s = settings.SILENCE_DURATION
        self.max_secs  = settings.MAX_RECORD_SECONDS

    # ── Sync API ──────────────────────────────────────────────────────────

    def record(
        self,
        on_speech_start: Callable | None = None,
        on_silence:      Callable | None = None,
    ) -> np.ndarray:
        """
        Block until an utterance is captured.

        Callbacks:
          on_speech_start() — called when voice is first detected
          on_silence()      — called when silence ends the recording

        Returns float32 numpy array at settings.SAMPLE_RATE.
        """
        try:
            import sounddevice as sd
        except ImportError:
            log.error("sounddevice not installed — run: pip install sounddevice")
            return np.zeros(self.sr, dtype=np.float32)

        chunk_size   = int(self.sr * _CHUNK_SECS)
        max_chunks   = int(self.max_secs / _CHUNK_SECS)
        silent_limit = int(self.silence_s / _CHUNK_SECS)

        frames:          list[np.ndarray] = []
        pre_buffer:      list[np.ndarray] = []
        speaking         = False
        silent_count     = 0
        total_chunks     = 0

        log.debug(
            "Recording — threshold=%.4f  silence=%.1fs  max=%ds",
            self.threshold, self.silence_s, self.max_secs,
        )

        with sd.InputStream(
            samplerate=self.sr,
            channels=1,
            dtype="float32",
            blocksize=chunk_size,
        ) as stream:
            while total_chunks < max_chunks:
                chunk, _ = stream.read(chunk_size)
                chunk     = chunk.flatten()
                rms       = float(np.sqrt(np.mean(chunk ** 2)))
                total_chunks += 1

                if rms > self.threshold:
                    if not speaking:
                        speaking = True
                        log.debug("Speech detected (RMS=%.4f)", rms)
                        if on_speech_start:
                            on_speech_start()
                        # Add pre-buffer to avoid clipping the start
                        frames.extend(pre_buffer)
                        pre_buffer.clear()

                    silent_count = 0
                    frames.append(chunk)

                elif speaking:
                    frames.append(chunk)  # keep silence frames (natural cadence)
                    silent_count += 1

                    if silent_count >= silent_limit:
                        log.debug("Silence detected — stopping")
                        if on_silence:
                            on_silence()
                        break

                else:
                    # Pre-speech: maintain rolling buffer
                    pre_buffer.append(chunk)
                    if len(pre_buffer) > _PRE_BUFFER:
                        pre_buffer.pop(0)

        if not frames:
            log.warning("No speech captured")
            return np.zeros(self.sr, dtype=np.float32)

        audio    = np.concatenate(frames).astype(np.float32)
        duration = len(audio) / self.sr
        log.info("Captured %.2fs of audio", duration)
        return audio

    # ── Async API ─────────────────────────────────────────────────────────

    async def record_async(
        self,
        on_speech_start: Callable | None = None,
        on_silence:      Callable | None = None,
    ) -> np.ndarray:
        """
        Async wrapper — runs recording in a thread-pool executor
        so it doesn't block the FastAPI event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.record(
                on_speech_start=on_speech_start,
                on_silence=on_silence,
            ),
        )


# ── Singleton ──────────────────────────────────────────────────────────────
recorder = Recorder()