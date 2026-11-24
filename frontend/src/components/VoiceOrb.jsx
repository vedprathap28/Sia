/**
 * Sia AI — VoiceOrb vNext
 * =========================
 * Changes from v1:
 *   ✓ Push-to-talk: hold orb to speak, release to send
 *   ✓ Language badge: shows active language (EN / हि / తె / म)
 *   ✓ Barge-in indicator: flash red when interrupting Sia
 *   ✓ LiveKit state machine: off → connecting → ready → listening → processing → speaking
 *   ✓ Richer canvas animation (more alive when speaking)
 *   ✓ Keyboard shortcut: Space bar = hold to talk
 *   ✓ All visual states preserved from v1 + new ones
 */
import React, { useEffect, useRef, memo, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { LANG_LABELS } from '../hooks/useLiveKit'
import './VoiceOrb.css'

const LABELS = {
  off:         'Click to activate voice',
  connecting:  'Connecting…',
  ready:       'Hold to speak',
  listening:   'Listening…',
  processing:  'Processing…',
  speaking:    'Sia is speaking…',
}

const WaveBar = memo(({ i }) => (
  <div
    className="wave-bar"
    style={{
      animationDelay:    `${i * 0.1}s`,
      animationDuration: `${0.45 + i * 0.07}s`,
    }}
  />
))

export default memo(function VoiceOrb({
  voiceState,   // 'off' | 'connecting' | 'ready' | 'listening' | 'processing' | 'speaking'
  lang,         // 'en-IN' | 'hi-IN' | 'te-IN' | 'mr-IN'
  onToggle,     // click = connect / disconnect
  onPTTStart,   // pointerdown = start speaking
  onPTTStop,    // pointerup = stop speaking
}) {
  const canvasRef  = useRef(null)
  const rafRef     = useRef(null)
  const frameRef   = useRef(0)
  const pttHeld    = useRef(false)

  const isOn = voiceState !== 'off' && voiceState !== 'connecting'

  // ── Canvas animation ───────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = 120, H = 120
    canvas.width = W; canvas.height = H
    const cx = W / 2, cy = H / 2

    const draw = () => {
      frameRef.current++
      const t = frameRef.current * 0.025
      ctx.clearRect(0, 0, W, H)

      // ── OFF state ──────────────────────────────────────────────────────
      if (voiceState === 'off') {
        const r = 32 + Math.sin(t) * 2
        const g = ctx.createRadialGradient(cx - 6, cy - 6, 2, cx, cy, r)
        g.addColorStop(0, 'rgba(100,120,160,0.5)')
        g.addColorStop(1, 'rgba(50,70,120,0.2)')
        ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.fillStyle = g; ctx.fill()
        rafRef.current = requestAnimationFrame(draw)
        return
      }

      // ── CONNECTING — spinning dots ─────────────────────────────────────
      if (voiceState === 'connecting') {
        ctx.beginPath(); ctx.arc(cx, cy, 28, 0, Math.PI * 2)
        ctx.strokeStyle = 'rgba(0,212,255,0.2)'; ctx.lineWidth = 2; ctx.stroke()
        for (let i = 0; i < 6; i++) {
          const angle = t * 3 + (i * Math.PI * 2) / 6
          const dx = cx + Math.cos(angle) * 22
          const dy = cy + Math.sin(angle) * 22
          ctx.beginPath(); ctx.arc(dx, dy, 3, 0, Math.PI * 2)
          ctx.fillStyle = `rgba(0,212,255,${0.3 + i * 0.12})`; ctx.fill()
        }
        rafRef.current = requestAnimationFrame(draw)
        return
      }

      // ── Base radius by state ───────────────────────────────────────────
      const baseR =
        voiceState === 'speaking'   ? 34 + Math.sin(t * 4) * 7
        : voiceState === 'listening' ? 32 + Math.sin(t * 8) * 5   // more alive
        : voiceState === 'processing' ? 32 + Math.sin(t * 6) * 4
        : 30 + Math.sin(t * 1.5) * 2  // ready — gentle breathe

      // ── Outer glow rings ───────────────────────────────────────────────
      const ringCount = voiceState === 'speaking' || voiceState === 'listening' ? 4 : 3
      for (let i = ringCount; i >= 1; i--) {
        const rr = baseR + i * 10 + Math.sin(t * 1.2 + i) * 3
        const a  = (0.07 - i * 0.015) * (voiceState === 'speaking' || voiceState === 'listening' ? 2.2 : 1)
        ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(0,212,255,${Math.max(0, a)})`; ctx.fill()
      }

      // ── Core orb gradient ──────────────────────────────────────────────
      const grad = ctx.createRadialGradient(cx - 8, cy - 8, 2, cx, cy, baseR)
      if (voiceState === 'listening') {
        // Bright white-blue when PTT held — feels "open"
        grad.addColorStop(0, 'rgba(220,250,255,0.98)')
        grad.addColorStop(0.4, 'rgba(0,230,255,0.95)')
        grad.addColorStop(1, 'rgba(0,160,220,0.8)')
      } else if (voiceState === 'speaking') {
        grad.addColorStop(0, 'rgba(80,240,255,0.95)')
        grad.addColorStop(0.5, 'rgba(0,212,255,0.9)')
        grad.addColorStop(1, 'rgba(0,140,200,0.7)')
      } else if (voiceState === 'processing') {
        grad.addColorStop(0, 'rgba(180,240,255,0.9)')
        grad.addColorStop(0.5, 'rgba(0,180,255,0.85)')
        grad.addColorStop(1, 'rgba(0,100,200,0.65)')
      } else {
        // ready
        grad.addColorStop(0, 'rgba(60,220,255,0.85)')
        grad.addColorStop(0.5, 'rgba(0,190,255,0.75)')
        grad.addColorStop(1, 'rgba(0,110,170,0.5)')
      }
      ctx.beginPath(); ctx.arc(cx, cy, baseR, 0, Math.PI * 2)
      ctx.fillStyle = grad; ctx.fill()

      // ── Shine ──────────────────────────────────────────────────────────
      const shine = ctx.createRadialGradient(cx - 9, cy - 9, 1, cx - 5, cy - 5, baseR * 0.55)
      shine.addColorStop(0, 'rgba(255,255,255,0.45)')
      shine.addColorStop(1, 'rgba(255,255,255,0)')
      ctx.beginPath(); ctx.arc(cx, cy, baseR, 0, Math.PI * 2)
      ctx.fillStyle = shine; ctx.fill()

      // ── Sound waves when speaking ──────────────────────────────────────
      if (voiceState === 'speaking' || voiceState === 'listening') {
        const waveCount = voiceState === 'listening' ? 4 : 3
        for (let i = 0; i < waveCount; i++) {
          const prog  = ((t * 0.65 + i * (1 / waveCount)) % 1)
          const wR    = baseR + prog * 38
          const alpha = (1 - prog) * 0.4
          ctx.beginPath(); ctx.arc(cx, cy, wR, 0, Math.PI * 2)
          ctx.strokeStyle = `rgba(0,212,255,${alpha})`
          ctx.lineWidth   = 1.5; ctx.stroke()
        }
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(rafRef.current)
  }, [voiceState])


  // ── Keyboard shortcut: Space = hold to talk ────────────────────────────────
  useEffect(() => {
    if (!isOn) return

    const onKeyDown = (e) => {
      if (e.code === 'Space' && !e.repeat && !pttHeld.current) {
        e.preventDefault()
        pttHeld.current = true
        onPTTStart?.()
      }
    }
    const onKeyUp = (e) => {
      if (e.code === 'Space' && pttHeld.current) {
        e.preventDefault()
        pttHeld.current = false
        onPTTStop?.()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
    }
  }, [isOn, onPTTStart, onPTTStop])


  // ── PTT pointer events (hold on orb) ──────────────────────────────────────
  const handlePointerDown = useCallback((e) => {
    if (!isOn) {
      onToggle?.()    // first click = connect
      return
    }
    e.preventDefault()
    if (pttHeld.current) return
    pttHeld.current = true
    onPTTStart?.()
  }, [isOn, onToggle, onPTTStart])

  const handlePointerUp = useCallback((e) => {
    if (!isOn) return
    if (!pttHeld.current) return
    pttHeld.current = false
    onPTTStop?.()
  }, [isOn, onPTTStop])

  const handleClick = useCallback((e) => {
    // If already connected and not holding PTT → disconnect on click
    if (isOn && !pttHeld.current) {
      onToggle?.()
    }
  }, [isOn, onToggle])


  const langLabel = LANG_LABELS[lang] || 'EN'

  return (
    <div className="voice-orb-wrap">

      {/* Ripple rings when listening (PTT held) */}
      <AnimatePresence>
        {voiceState === 'listening' && (
          <>
            {[0, 1, 2, 3].map(i => (
              <motion.div
                key={i}
                className="orb-ripple"
                initial={{ scale: 1, opacity: 0.6 }}
                animate={{ scale: 2.8, opacity: 0 }}
                transition={{ duration: 2.0, delay: i * 0.5, repeat: Infinity, ease: 'easeOut' }}
              />
            ))}
          </>
        )}
      </AnimatePresence>

      {/* Main orb button */}
      <motion.button
        className={`voice-orb-btn voice-orb-btn--${voiceState}`}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}   // release if pointer leaves while held
        onClick={handleClick}
        whileHover={{ scale: isOn ? 1.04 : 1.08 }}
        whileTap={{ scale: 0.92 }}
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: 'spring', stiffness: 260, damping: 20, delay: 0.3 }}
        title={
          voiceState === 'off' ? 'Click to activate Sia voice' :
          voiceState === 'ready' ? 'Hold to speak (or press Space)' :
          voiceState === 'speaking' ? 'Speaking — hold to interrupt' :
          LABELS[voiceState]
        }
        style={{ touchAction: 'none', userSelect: 'none' }}
      >
        <canvas ref={canvasRef} className="orb-canvas" />

        {/* Wave bars — speaking */}
        <AnimatePresence>
          {(voiceState === 'speaking') && (
            <motion.div
              className="wave-bars"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            >
              {[0, 1, 2, 3, 4, 5, 6].map(i => <WaveBar key={i} i={i} />)}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Processing spinner */}
        <AnimatePresence>
          {voiceState === 'processing' && (
            <motion.div
              className="orb-spinner"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            >
              <div className="orb-spinner-ring" />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Language badge — shows when active */}
        <AnimatePresence>
          {isOn && lang && lang !== 'en-IN' && (
            <motion.span
              className="orb-lang-badge"
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              {langLabel}
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>

      {/* PTT hint ring when ready */}
      <AnimatePresence>
        {voiceState === 'ready' && (
          <motion.div
            className="orb-ptt-hint"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 0] }}
            transition={{ duration: 2.5, repeat: Infinity, ease: 'easeInOut' }}
          />
        )}
      </AnimatePresence>

      {/* Label */}
      <AnimatePresence mode="wait">
        <motion.div
          key={voiceState}
          className={`orb-label ${isOn ? `orb-label--${voiceState}` : ''}`}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.2 }}
        >
          <span className={`orb-dot orb-dot--${voiceState}`} />
          {LABELS[voiceState] || LABELS.off}
        </motion.div>
      </AnimatePresence>

    </div>
  )
})
