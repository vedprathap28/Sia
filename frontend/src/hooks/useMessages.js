/**
 * Sia AI — useMessages / useMic / useWakeWord
 * =============================================
 * Fixes:
 *  ✅ SINGLE TTS path — frontend fetches /api/voice/speak and plays it.
 *     Backend /api/chat no longer calls _speak() for voice. No double audio.
 *  ✅ Toggle mode = browser Web Speech API listens continuously.
 *     No server mic needed. Works as long as app is open.
 *  ✅ Wake words: "Sia", "Siya", "Hi Sia", "Hey Sia" + common mishears.
 *  ✅ Barge-in: if Sia is speaking and user says anything, audio stops instantly.
 *  ✅ After wake word detected → records full command → transcribes via backend
 *     Whisper → gets reply → speaks reply. One clean flow.
 *  ✅ Mic button (tap-to-record) still works exactly as before.
 */
import { useState, useCallback, useRef, useEffect } from 'react'

// '' = same origin (Vite proxies /api to the backend)
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// ── Shared audio controller — only ONE voice plays at a time ─────────────────
const _audio = {
  current: null,        // currently playing HTMLAudioElement
  endedAt: 0,           // when SIA last stopped talking (echo guard for the wake-word listener)
  _done: null,
  stop() {
    if (this.current) {
      this.current.pause()
      this.current.currentTime = 0
      this.current = null
    }
    try { window.speechSynthesis?.cancel() } catch {}
    this._finish()
  },
  _finish() {
    this.endedAt = Date.now()
    const d = this._done; this._done = null
    if (d) d()
  },
  // Resolves when playback ENDS (or fails / is interrupted), not when it starts.
  play(url) {
    this.stop()
    const a = new Audio(url)
    this.current = a
    return new Promise((resolve) => {
      this._done = resolve
      const end = () => { if (this.current === a) { this.current = null; this._finish() } else resolve() }
      a.onended = end
      a.onerror = end
      a.play().catch((e) => { console.warn('[TTS] play error:', e); end() })
    })
  },
  // Browser voice when the backend TTS is unavailable (offline, no Sarvam key, Edge blocked…)
  speakLocal(text, lang) {
    this.stop()
    const synth = window.speechSynthesis
    if (!synth) return Promise.resolve()
    return new Promise((resolve) => {
      const u = new SpeechSynthesisUtterance(text)
      u.lang = lang || 'en-IN'
      this._done = resolve
      this.current = { pause() { synth.cancel() }, paused: false, currentTime: 0 }
      const end = () => { this.current = null; this._finish() }
      u.onend = end
      u.onerror = end
      synth.speak(u)
    })
  },
  get isPlaying() {
    return !!(this.current && !this.current.paused)
  },
  // true while SIA is talking and for a moment after (her own voice must not be heard as a command)
  get recentlyPlaying() {
    return this.isPlaying || Date.now() - this.endedAt < 900
  },
}


// ── Speak helper — fetches audio from backend, plays via shared controller ────
// This is the ONLY place TTS happens on the frontend.
export async function speakText(text, lang = 'en-IN') {
  if (!text?.trim()) return
  try {
    const res = await fetch(`${API_BASE}/api/voice/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, lang }),
    })
    if (!res.ok) throw new Error(`TTS HTTP ${res.status}`)
    const blob = await res.blob()
    if (!blob.size) throw new Error('empty audio')
    const url  = URL.createObjectURL(blob)
    await _audio.play(url)
    URL.revokeObjectURL(url)
  } catch (e) {
    console.warn('[TTS] backend TTS failed, using browser voice:', e)
    await _audio.speakLocal(text, lang)
  }
}

// Approvals / questions are spoken by App's onApproval handler (with "say yes or no").
// The /api/chat reply for a paused task must not talk over it.
const _approvalSpoken = new Set()
export function markApprovalSpoken(taskId) { if (taskId) _approvalSpoken.add(taskId) }
const WAITING = new Set(['waiting_approval', 'waiting_input'])

export async function speakReply(data, text) {
  const spoken = data?.spoken || text
  if (!spoken?.trim()) return
  if (WAITING.has(data?.task_status)) {
    // give the WebSocket approval event a moment; speak the generic line only if nobody did
    await new Promise(r => setTimeout(r, 1500))
    if (_approvalSpoken.has(data.task_id)) return
  }
  await speakText(spoken, data?.lang || 'en-IN')
}

// Stop Sia speaking immediately (barge-in)
export function stopSpeaking() {
  _audio.stop()
}

export function isSpeaking() {
  return !!_audio.isPlaying
}


// ── Long-running tasks ────────────────────────────────────────────────────────
// /api/chat waits a few seconds; if the task is still running (or waiting for an
// approval) the final answer arrives later over the WebSocket (task_result event).
const _pendingTasks = new Map()   // task_id → (text, spoken, info) => void
const FINAL = new Set(['completed', 'failed', 'cancelled'])

export function registerTaskReply(data, onDone) {
  if (data?.task_id && data?.task_status && !FINAL.has(data.task_status)) {
    _pendingTasks.set(data.task_id, onDone)
  }
}

export function deliverTaskResult(taskId, text, spoken, info = {}) {
  const fn = _pendingTasks.get(taskId)
  if (!fn) return false
  _pendingTasks.delete(taskId)
  fn(text, spoken, info)
  return true
}


// ── Voice intercept (voice approvals / "stop") ───────────────────────────────
// App registers a function that gets every recognised phrase first; returning
// true means it was handled (e.g. "yes" to a pending approval).
let _voiceIntercept = null
export function setVoiceIntercept(fn) { _voiceIntercept = fn }
export function runVoiceIntercept(text) { return !!(_voiceIntercept && _voiceIntercept(text)) }


// ── useMessages ───────────────────────────────────────────────────────────────
export function useMessages() {
  const [messages, setMessages] = useState([])
  const [thinking, setThinking] = useState(false)
  const abortRef = useRef(null)

  /**
   * send(text, source)
   *   source = 'text'  → no TTS (chat box)
   *   source = 'voice' → TTS fires after reply (mic button or wake word)
   */
  const send = useCallback(async (text, source = 'text') => {
    if (!text.trim() || thinking) return

    const userMsg = { id: Date.now(), role: 'user', content: text, ts: new Date(), source }
    setMessages(prev => [...prev, userMsg])
    setThinking(true)

    const aiId = Date.now() + 1
    setMessages(prev => [...prev, {
      id: aiId, role: 'assistant', content: '', ts: new Date(), animate: true,
    }])

    const ctrl = new AbortController()
    abortRef.current = ctrl

    const patch = (updates) =>
      setMessages(prev => prev.map(m => m.id === aiId ? { ...m, ...updates } : m))

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        signal: ctrl.signal,
        headers: { 'Content-Type': 'application/json' },
        // The backend never plays audio itself; source only tags the task (chat / voice).
        body: JSON.stringify({ message: text, history: [], source }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data  = await res.json()
      const reply = String(data?.text ?? data?.response ?? data?.reply ?? data?.message ?? '(no response)')

      // Show text in UI immediately
      patch({ content: reply, animate: true, taskId: data?.task_id, taskStatus: data?.task_status })

      // Speak ONLY if voice source — single TTS call, runs after UI update
      if (source === 'voice' && reply.trim()) {
        speakReply(data, reply)  // fire-and-forget, non-blocking
      }

      // Task still running / waiting for approval → final answer replaces this message later
      registerTaskReply(data, (finalText, spoken, info) => {
        patch({ content: finalText, animate: true, taskStatus: info.status, verified: info.verified })
        if (source === 'voice' && finalText.trim()) speakText(spoken || finalText, data?.lang || 'en-IN')
      })

    } catch (err) {
      if (err.name === 'AbortError') {
        patch({ content: '(stopped)' })
      } else {
        patch({
          content: `❌ **Error:** ${err.message}\n\nMake sure the backend is running (python -m backend.main)`,
          isError: true,
        })
      }
    } finally {
      setThinking(false)
    }
  }, [thinking])

  const addMessage = useCallback((msg) => {
    setMessages(prev => [...prev, {
      id:      Date.now() + Math.random(),
      role:    msg.role,
      content: msg.content,
      ts:      new Date(),
      animate: true,
      isVoice: true,
    }])
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    stopSpeaking()
    setThinking(false)
  }, [])

  const clear = useCallback(() => {
    abortRef.current?.abort()
    stopSpeaking()
    setMessages([])
    setThinking(false)
  }, [])

  return { messages, thinking, send, stop, clear, addMessage }
}


// ── useMic — tap-to-record mic button ────────────────────────────────────────
export function useMic({ onTranscribed, onError }) {
  const [micState, setMicState] = useState('idle')
  const mediaRef  = useRef(null)
  const chunksRef = useRef([])

  const startRecording = useCallback(async () => {
    if (micState !== 'idle') return
    // Stop Sia if she's speaking — barge-in
    stopSpeaking()
    try {
      const stream   = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus' : 'audio/webm'
      const mr = new MediaRecorder(stream, { mimeType })
      mediaRef.current  = mr
      chunksRef.current = []

      mr.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data) }

      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop())
        const blob = new Blob(chunksRef.current, { type: mimeType })
        const form = new FormData()
        form.append('file', blob, 'voice.webm')
        setMicState('transcribing')
        try {
          const res  = await fetch(`${API_BASE}/api/voice/transcribe`, { method: 'POST', body: form })
          const data = await res.json()
          if (data.success && data.text) {
            onTranscribed(data.text, 'voice')
          } else {
            onError?.(data.error || "I didn't catch that.")
          }
        } catch (e) {
          console.error('[Mic] Transcription failed:', e)
          onError?.('Could not reach the backend for speech-to-text.')
        } finally {
          setMicState('idle')
        }
      }

      mr.start()
      setMicState('recording')
    } catch (e) {
      console.error('[Mic] Access denied:', e)
      onError?.('Microphone access was blocked. Allow the mic for this site and try again.')
      setMicState('idle')
    }
  }, [micState, onTranscribed, onError])

  const stopRecording = useCallback(() => {
    if (mediaRef.current && micState === 'recording') mediaRef.current.stop()
  }, [micState])

  const toggleMic = useCallback(() => {
    if (micState === 'idle') startRecording()
    else if (micState === 'recording') stopRecording()
  }, [micState, startRecording, stopRecording])

  return { micState, toggleMic }
}


// ── useWakeWord — toggle-based always-on voice mode ───────────────────────────
/**
 * How it works when toggle is ON:
 *  1. Web Speech API listens continuously in the browser (no server mic).
 *  2. User says "Hi Sia" / "Hey Sia" / "Siya" etc.
 *  3. Wake word strips out, remaining words = command.
 *  4. If command present → send to backend → get reply → speak reply.
 *  5. If only wake word → Sia says "Yeah?" and waits.
 *  6. If Sia is already speaking and user says anything → barge-in stops audio.
 *  7. Toggle OFF → everything stops.
 */
export function useWakeWord({ onMessage, onVoiceState }) {
  const recognitionRef  = useRef(null)
  const enabledRef      = useRef(false)
  const processingRef   = useRef(false)  // prevent overlapping requests
  const armedUntilRef   = useRef(0)      // after a bare "Hey Sia", the next phrase is the command

  // Wake word patterns — covers common mishears of "Sia"
  const WAKE_RE = /\b(hi\s+si[ay]a?|hey\s+si[ay]a?|ok\s+si[ay]a?|si[ay]a?|seya|see\s*ya|fia|sha)\b/i
  const STOP_RE = /^(stop|ruko|bas|aapu|thamba|enough|quiet)\b/i

  const _processCommand = useCallback(async (command) => {
    if (processingRef.current) return
    processingRef.current = true
    onVoiceState?.('processing')

    try {
      // Show user message in chat
      onMessage({ role: 'user', content: command })

      const res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: command, history: [], source: 'voice' }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data  = await res.json()
      const reply = String(data?.text ?? data?.response ?? data?.reply ?? '(no response)')

      // Show reply in chat
      onMessage({ role: 'assistant', content: reply })

      registerTaskReply(data, async (finalText, spoken) => {
        onMessage({ role: 'assistant', content: finalText })
        onVoiceState?.('speaking')
        await speakText(spoken || finalText, data?.lang || 'en-IN')
        onVoiceState?.('listening')
      })

      // Speak — single voice
      onVoiceState?.('speaking')
      await speakReply(data, reply)
      onVoiceState?.('listening')

    } catch (e) {
      console.error('[WakeWord] command failed:', e)
      onVoiceState?.('listening')
    } finally {
      processingRef.current = false
    }
  }, [onMessage, onVoiceState])

  const start = useCallback(() => {
    if (enabledRef.current) return
    enabledRef.current = true

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      console.warn('[WakeWord] Web Speech API not supported in this browser. Use Chrome.')
      onVoiceState?.('off')
      return
    }

    const recog = new SpeechRecognition()
    recog.continuous     = true
    recog.interimResults = false
    recog.lang           = 'en-IN'
    recog.maxAlternatives = 3  // try multiple to catch mishears

    recog.onstart = () => {
      console.log('[WakeWord] Listening… say "Hi Sia" or "Sia"')
      onVoiceState?.('listening')
    }

    recog.onresult = (event) => {
      // Check all alternatives for wake word
      const result = event.results[event.results.length - 1]

      const heard = (result[0]?.transcript || '').trim()
      const anyWake = Array.from(result).some(r => WAKE_RE.test(r.transcript || ''))

      // 0) Barge-in without the wake word: "stop" / "ruko" / "bas" / "aapu" while Sia is talking
      if (_audio.isPlaying && STOP_RE.test(heard)) {
        stopSpeaking()
        onVoiceState?.('listening')
        return
      }

      // Echo guard: while (and just after) Sia talks, the mic hears her own voice. Only a
      // wake word gets through then — otherwise she would answer her own question card.
      if (_audio.recentlyPlaying && !anyWake) return

      // 1) Voice approvals / answers get first look at what was said (no wake word needed)
      if (heard && _voiceIntercept && _voiceIntercept(heard)) return
      let transcript = ''
      for (let i = 0; i < result.length; i++) {
        transcript = result[i].transcript.trim()
        if (WAKE_RE.test(transcript)) break
        transcript = ''
      }
      if (!transcript) {
        // "Hey Sia" … "Yeah?" … "book a hotel in Goa" — the follow-up needs no wake word
        if (heard && Date.now() < armedUntilRef.current) {
          armedUntilRef.current = 0
          _processCommand(heard)
        }
        return
      }

      console.log('[WakeWord] Heard:', transcript)

      // Barge-in: stop Sia if she's speaking
      if (_audio.isPlaying) {
        stopSpeaking()
      }

      // Strip wake word, get the command
      const command = transcript.replace(WAKE_RE, '').replace(/^[\s,.\-!?]+/, '').trim()

      if (!command) {
        // Just the wake word — acknowledge and wait
        onMessage({ role: 'assistant', content: '👂 Yeah? Go ahead.' })
        armedUntilRef.current = Date.now() + 10000
        speakText("Yeah?").then(() => { armedUntilRef.current = Date.now() + 8000 })
        return
      }

      // Process command
      _processCommand(command)
    }

    recog.onerror = (e) => {
      if (e.error === 'no-speech') return  // normal, just restart
      if (e.error === 'not-allowed') {
        console.error('[WakeWord] Mic permission denied')
        onVoiceState?.('off')
        enabledRef.current = false
        return
      }
      console.warn('[WakeWord] error:', e.error)
    }

    recog.onend = () => {
      // Auto-restart unless explicitly stopped
      if (enabledRef.current) {
        setTimeout(() => {
          try { recog.start() } catch {}
        }, 300)
      }
    }

    recognitionRef.current = recog
    try { recog.start() } catch (e) { console.error('[WakeWord] start failed:', e) }

  }, [_processCommand, onMessage, onVoiceState])

  const stop = useCallback(() => {
    enabledRef.current = false
    stopSpeaking()
    if (recognitionRef.current) {
      try { recognitionRef.current.stop() } catch {}
      recognitionRef.current = null
    }
    onVoiceState?.('off')
    console.log('[WakeWord] Stopped')
  }, [onVoiceState])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      enabledRef.current = false
      if (recognitionRef.current) {
        try { recognitionRef.current.stop() } catch {}
      }
    }
  }, [])

  return { start, stop }
}