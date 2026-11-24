"""
Sia Voice Test v3
Tests full pipeline: TTS → STT → keyword detection
Run: python scripts/voice_check.py   (manual check: speakers + microphone + internet)
"""
import asyncio, io, os, re, tempfile, wave, sys
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000

_SIA_RE = re.compile(
    r'\b(sia|siya|seya|see\s?ya|fia|sya|zya|cya|shia|asia)\b',
    re.IGNORECASE,
)

def numpy_to_wav(audio):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    buf.seek(0)
    return buf.read()

def gen_mp3(text):
    async def _gen():
        import edge_tts
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        await edge_tts.Communicate(text, "en-US-AriaNeural").save(path)
        return path
    return asyncio.run(_gen())

def speak(text):
    print(f"[TTS] Speaking: {text!r}")
    mp3 = gen_mp3(text)
    from playsound import playsound
    playsound(mp3, block=True)
    os.unlink(mp3)
    print("[TTS] Done speaking.")

def record_and_transcribe(seconds=6):
    print(f"\n[STT] Recording {seconds}s — say 'Sia open YouTube'...")
    audio = sd.rec(SAMPLE_RATE * seconds, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    print("[STT] Sending to Google STT...")
    import speech_recognition as sr
    wav  = numpy_to_wav(audio.flatten())
    data = sr.AudioData(wav, SAMPLE_RATE, 2)
    r    = sr.Recognizer()
    try:
        text = r.recognize_google(data, language="en-IN")
        print(f"[STT] Heard: {text!r}")
        has_sia = bool(_SIA_RE.search(text))
        print(f"[STT] Sia keyword detected: {has_sia}")
        return text, has_sia
    except sr.UnknownValueError:
        print("[STT] Could not understand audio")
        return "", False
    except sr.RequestError as e:
        print(f"[STT] Google error: {e}")
        return "", False

print("=" * 50)
print("  SIA VOICE TEST v3")
print("=" * 50)

# Test 1: TTS full sentence
print("\n--- TEST 1: TTS ---")
try:
    speak("Evening Ved. Sia at your service. How can I help you today?")
    print("PASS: Full sentence spoken")
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

# Test 2: STT + keyword
print("\n--- TEST 2: STT + Keyword ---")
text, has_sia = record_and_transcribe(6)
if has_sia:
    speak(f"Keyword detected! You said: {text}. Sia is working!")
    print("\nALL TESTS PASSED - Voice pipeline working!")
elif text:
    print(f"\nHeard text but no Sia keyword: {text!r}")
    print("Try saying 'Sia' more clearly or add your variant to the keyword list")
    speak(f"I heard {text} but didn't catch Sia. Try again.")
else:
    print("\nNo speech detected. Check mic.")
print("=" * 50)
