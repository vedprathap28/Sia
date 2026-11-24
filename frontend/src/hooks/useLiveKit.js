/**
 * Sia AI — useLiveKit hook  (vNext)
 * ====================================
 * Manages the full LiveKit voice pipeline:
 *   — Fetch room token from /api/livekit/token
 *   — Join LiveKit room with browser mic
 *   — Push-to-talk: PTT_START / PTT_STOP data messages
 *   — Barge-in: mute agent TTS when user starts speaking
 *   — State machine: off → connecting → ready → listening → processing → speaking
 *   — Language: exposes detectedLang from agent data messages
 *   — Transcript: exposes last user + Sia transcripts
 *   — Fallback: if LiveKit not configured, falls back to old polling system
 *
 * Usage:
 *   const { state, lang, transcript, startPTT, stopPTT, connect, disconnect } = useLiveKit({ onMessage })
 */
import { useState, useRef, useCallback, useEffect } from 'react'

const API = import.meta.env.VITE_API_BASE ?? ''

// ── State machine values ──────────────────────────────────────────────────────
// 'off' | 'connecting' | 'ready' | 'listening' | 'processing' | 'speaking'

export function useLiveKit({ onMessage, onError } = {}) {
  const [state,       setState]       = useState('off')
  const [lang,        setLang]        = useState('en-IN')
  const [transcript,  setTranscript]  = useState({ user: '', sia: '' })
  const [lkAvailable, setLkAvailable] = useState(null) // null = unknown

  const roomRef      = useRef(null)
  const audioElRef   = useRef(null) // <audio> element for agent audio
  const pttActiveRef = useRef(false)
  const agentAudioRef = useRef(null) // MediaStream from agent

  // ── Check if LiveKit is configured on the backend ──────────────────────────
  useEffect(() => {
    fetch(`${API}/api/health`)
      .then(r => r.json())
      .then(d => setLkAvailable(!!d.livekit))
      .catch(() => setLkAvailable(false))
  }, [])

  // ── Connect to LiveKit room ─────────────────────────────────────────────────
  const connect = useCallback(async () => {
    if (state !== 'off') return
    setState('connecting')

    try {
      // 1. Get token from backend
      const res  = await fetch(`${API}/api/livekit/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room: 'sia-voice-room', identity: 'ved' }),
      })
      const { token, url } = await res.json()

      if (!token || !url) {
        throw new Error('LiveKit token missing — check backend config')
      }

      // 2. Dynamic import of LiveKit client SDK
      const { Room, RoomEvent, Track, ConnectionState } = await import('livekit-client')

      const room = new Room({
        adaptiveStream: true,
        dynacast:       true,
        audioCaptureDefaults: {
          echoCancellation:   true,
          noiseSuppression:   true,
          autoGainControl:    true,
          sampleRate:         16000,
          channelCount:       1,
        },
      })

      roomRef.current = room

      // 3. Event handlers
      room.on(RoomEvent.Connected, () => {
        setState('ready')
      })

      room.on(RoomEvent.Disconnected, () => {
        setState('off')
        roomRef.current = null
      })

      // Agent audio (Sia speaking)
      room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        if (track.kind === Track.Kind.Audio) {
          const el = document.createElement('audio')
          el.autoplay = true
          track.attach(el)
          audioElRef.current   = el
          agentAudioRef.current = track
        }
      })

      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        if (audioElRef.current) {
          track.detach(audioElRef.current)
        }
      })

      // Data channel messages from Sia agent
      room.on(RoomEvent.DataReceived, (data) => {
        try {
          const msg = JSON.parse(new TextDecoder().decode(data))
          _handleAgentMessage(msg)
        } catch (_) {}
      })

      // Active speaker detection (barge-in)
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const isUserSpeaking = speakers.some(
          s => s.identity === 'ved' && s.audioLevel > 0.05
        )
        if (isUserSpeaking && state === 'speaking') {
          // Barge-in: mute Sia's audio immediately
          _muteAgentAudio()
        }
      })

      // 4. Connect + publish mic
      await room.connect(url, token)
      await room.localParticipant.setMicrophoneEnabled(true)

    } catch (err) {
      console.error('[useLiveKit] connect error:', err)
      setState('off')
      onError?.(`LiveKit connection failed: ${err.message}`)
    }
  }, [state])


  // ── Disconnect ─────────────────────────────────────────────────────────────
  const disconnect = useCallback(async () => {
    if (roomRef.current) {
      await roomRef.current.disconnect()
      roomRef.current = null
    }
    setState('off')
    pttActiveRef.current = false
  }, [])


  // ── Push-to-talk ───────────────────────────────────────────────────────────
  const startPTT = useCallback(() => {
    const room = roomRef.current
    if (!room || state === 'off' || state === 'connecting') return
    if (pttActiveRef.current) return

    pttActiveRef.current = true
    setState('listening')

    // Unmute mic
    room.localParticipant.setMicrophoneEnabled(true)

    // Tell agent to start listening
    room.localParticipant.publishData(
      new TextEncoder().encode(JSON.stringify({ type: 'PTT_START' })),
      { reliable: true }
    )
  }, [state])


  const stopPTT = useCallback(() => {
    const room = roomRef.current
    if (!room || !pttActiveRef.current) return

    pttActiveRef.current = false
    setState('processing')

    // Tell agent to process audio
    room.localParticipant.publishData(
      new TextEncoder().encode(JSON.stringify({ type: 'PTT_STOP' })),
      { reliable: true }
    )

    // Mute mic while Sia processes + speaks
    room.localParticipant.setMicrophoneEnabled(false)
  }, [])


  // ── Barge-in: mute Sia's audio ─────────────────────────────────────────────
  const _muteAgentAudio = useCallback(() => {
    if (audioElRef.current) {
      audioElRef.current.pause()
    }
    // Tell agent to stop speaking
    const room = roomRef.current
    if (room) {
      room.localParticipant.publishData(
        new TextEncoder().encode(JSON.stringify({ type: 'BARGE_IN' })),
        { reliable: true }
      )
    }
  }, [])


  // ── Handle data messages from Sia agent ────────────────────────────────────
  const _handleAgentMessage = useCallback((msg) => {
    switch (msg.type) {
      case 'state':
        setState(msg.state || 'ready')
        break

      case 'agent_ready':
        setState('ready')
        break

      case 'transcript':
        // {type:'transcript', role:'user'|'sia', text:'...', lang:'en-IN'}
        if (msg.lang) setLang(msg.lang)
        setTranscript(prev => ({
          ...prev,
          [msg.role === 'user' ? 'user' : 'sia']: msg.text,
        }))
        if (msg.role !== 'user') {
          onMessage?.({ role: 'assistant', content: msg.text })
        } else {
          onMessage?.({ role: 'user', content: msg.text })
        }
        break

      case 'lang':
        setLang(msg.lang || 'en-IN')
        break

      case 'error':
        onError?.(msg.message)
        setState('ready')
        break

      default:
        break
    }
  }, [onMessage, onError])


  // ── Toggle (for VoiceOrb click) ────────────────────────────────────────────
  const toggle = useCallback(async () => {
    if (state === 'off') {
      await connect()
    } else {
      await disconnect()
    }
  }, [state, connect, disconnect])


  return {
    // State
    state,
    lang,
    transcript,
    lkAvailable,
    isConnected: state !== 'off' && state !== 'connecting',

    // Actions
    connect,
    disconnect,
    toggle,
    startPTT,
    stopPTT,
  }
}


// ── Language display helper ───────────────────────────────────────────────────
export const LANG_LABELS = {
  'en-IN': 'EN',
  'hi-IN': 'हि',
  'te-IN': 'తె',
  'mr-IN': 'म',
}

export const LANG_NAMES = {
  'en-IN': 'English',
  'hi-IN': 'Hindi',
  'te-IN': 'Telugu',
  'mr-IN': 'Marathi',
}