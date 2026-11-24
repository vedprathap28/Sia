import React, { useEffect, useRef, memo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import MessageBubble from './MessageBubble'
import ThinkingIndicator from './ThinkingIndicator'
import './ChatWindow.css'

// Floating particle
const Particle = memo(function Particle({ style }) {
  return <div className="particle" style={style} />
})

// Generate particles
function makeParticles(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: i,
    style: {
      left:              `${Math.random() * 90 + 5}%`,
      top:               `${Math.random() * 80 + 10}%`,
      width:             `${Math.random() * 4 + 2}px`,
      height:            `${Math.random() * 4 + 2}px`,
      animationDelay:    `${Math.random() * 4}s`,
      animationDuration: `${3 + Math.random() * 4}s`,
      opacity:           Math.random() * 0.6 + 0.2,
    },
  }))
}

const PARTICLES = makeParticles(18)

// Holographic SIA logo
const SiaLogo = memo(function SiaLogo() {
  return (
    <div className="sia-logo-wrap">
      <div className="sia-logo-ring sia-logo-ring--1" />
      <div className="sia-logo-ring sia-logo-ring--2" />
      <div className="sia-logo-core">
        <span className="sia-logo-text">S</span>
      </div>
      <div className="sia-logo-glow" />
    </div>
  )
})

// Futuristic suggestion cards
const FutureSuggestions = [
  { icon: '⚡', text: 'Play Brown Munde on YouTube' },
  { icon: '🌐', text: 'Open GitHub in my profile' },
  { icon: '📸', text: 'Take a screenshot' },
  { icon: '🔊', text: 'Set volume to 60' },
  { icon: '📁', text: 'Open my Downloads folder' },
  { icon: '💡', text: 'What can you do?' },
]

const WelcomeScreen = memo(function WelcomeScreen({ onSuggestion }) {
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'

  return (
    <motion.div
      className="welcome"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.5 }}
    >
      {/* Floating particles */}
      {PARTICLES.map(p => <Particle key={p.id} style={p.style} />)}

      {/* Logo */}
      <motion.div
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ delay: 0.1, duration: 0.6, ease: [0.34,1.56,0.64,1] }}
      >
        <SiaLogo />
      </motion.div>

      {/* Greeting */}
      <motion.div
        className="welcome-greeting"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3, duration: 0.5 }}
      >
        <span className="welcome-time">{greeting}, Ved</span>
        <h1 className="welcome-title">How can I help you today?</h1>
        <p className="welcome-sub">
          Your intelligent AI assistant — ask anything, say <span className="keyword-highlight">"Sia"</span> to activate voice
        </p>
      </motion.div>

      {/* Futuristic suggestion grid */}
      <motion.div
        className="suggestions-grid"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5, duration: 0.5 }}
      >
        {FutureSuggestions.map((s, i) => (
          <motion.button
            key={i}
            className="suggestion-card"
            onClick={() => onSuggestion(s.text)}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.55 + i * 0.06, duration: 0.3 }}
            whileHover={{ y: -3, scale: 1.02 }}
            whileTap={{ scale: 0.97 }}
          >
            <span className="suggestion-icon">{s.icon}</span>
            <span className="suggestion-text">{s.text}</span>
            <span className="suggestion-arrow">→</span>
          </motion.button>
        ))}
      </motion.div>
    </motion.div>
  )
})

export default memo(function ChatWindow({ messages, thinking, onSuggestion }) {
  const bottomRef  = useRef(null)
  const hasMessages = messages.length > 0

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  return (
    <div className="chat-window">
      <AnimatePresence mode="wait">
        {!hasMessages ? (
          <WelcomeScreen key="welcome" onSuggestion={onSuggestion} />
        ) : (
          <motion.div
            key="messages"
            className="messages-wrap"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.25 }}
          >
            <div className="messages-inner">
              <AnimatePresence initial={false}>
                {messages.map(msg => (
                  <MessageBubble key={msg.id} message={msg} />
                ))}
              </AnimatePresence>
              <AnimatePresence>
                {thinking && <ThinkingIndicator key="thinking" />}
              </AnimatePresence>
              <div ref={bottomRef} style={{ height: 20 }} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
})
