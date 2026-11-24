import React, { useState, useCallback, useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ThemeProvider } from './context/ThemeContext'
import { useMessages, useMic, useWakeWord, speakText, setVoiceIntercept, runVoiceIntercept, markApprovalSpoken } from './hooks/useMessages'
import { useAgentEvents } from './hooks/useAgentEvents'
import TaskPanel from './components/TaskPanel'
import ApprovalStack from './components/ApprovalStack'
import MemoryPanel from './components/MemoryPanel'
import SettingsPanel from './components/SettingsPanel'
import Sidebar from './components/Sidebar'
import ChatWindow from './components/ChatWindow'
import InputBox from './components/InputBox'
import Header from './components/Header'
import VoiceOrb from './components/VoiceOrb'
import DocumentsPanel from './components/DocumentsPanel'
import './styles/global.css'
import './styles/app.css'
import { API_BASE } from './hooks/useAgentEvents'

const API = `${API_BASE}/api`

function AppInner() {
  const [sidebarOpen, setSidebarOpen]   = useState(true)
  const [isMobile, setIsMobile]         = useState(false)
  const [chatHistory, setChatHistory]   = useState([])
  const [voiceHistory, setVoiceHistory] = useState([])
  const [activeId, setActiveId]         = useState(null)
  const [showDocs, setShowDocs]         = useState(false)

  /**
   * voiceState drives the VoiceOrb visual + label:
   *   'off'        → toggle is OFF, orb is dim
   *   'listening'  → toggle ON, waiting for "Sia..."
   *   'processing' → heard command, waiting for backend reply
   *   'speaking'   → Sia is playing audio
   */
  const [voiceState, setVoiceState] = useState('off')
  const sessionMsgs = useRef([])

  const { messages, thinking, send, stop, clear, addMessage } = useMessages()

  // ── Agent engine: live tasks + approvals ────────────────────────────────
  const [showTasks, setShowTasks]       = useState(false)
  const [showMemory, setShowMemory]     = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const voiceStateRef   = useRef('off')
  const voiceApproval   = useRef(null)     // approval SIA is waiting to hear yes/no for
  const lastVoiceAt     = useRef(0)        // last time the user spoke a command (mic button or wake word)

  const agent = useAgentEvents({
    onApproval: useCallback((a) => {
      // Speak approvals when hands-free mode is on OR the user just used the mic button,
      // so "yes"/"no" by voice also works for tap-to-talk users.
      const recentVoice = Date.now() - lastVoiceAt.current < 10 * 60 * 1000
      if (voiceStateRef.current === 'off' && !recentVoice) return
      voiceApproval.current = a
      markApprovalSpoken(a.task_id)
      if (a.kind === 'input') {
        speakText(a.summary)
      } else if (a.risk >= 4) {
        speakText('This is a destructive action. Please confirm it on the screen.')
      } else {
        // "Click button “Reserve” (e12) on the current page" → "Click button Reserve"
        const what = String(a.summary).split('\n')[0].replace(/\s*\(e\d+\)/g, '')
          .replace(/\s+on the current page/i, '').replace(/[“”"]/g, '').slice(0, 160)
        speakText(`I need your approval: ${what}. Say yes or no.`)
      }
    }, []),
  })

  const respondApproval = useCallback(async (a, payload) => {
    if (voiceApproval.current?.id === a.id) voiceApproval.current = null
    await agent.respond(a, payload)
  }, [agent.respond])

  // Voice approvals: "yes / haan / avunu / ho" approves, "no / nahi / vaddu / nako" denies.
  // Level-4 actions are never approved by voice. Answers to questions are taken verbatim.
  useEffect(() => {
    const YES = /^(yes|yeah|yep|yup|approve[d]?|go ahead|do it|send it|ok(ay)?|sure|haan?|haa|ji haan|theek hai|avunu|sare|ho|hoy|chalel)\b/i
    const NO  = /^(no|nope|deny|don'?t|cancel|stop|nahi?n?|mat karo|vaddu|ledu|nako|naahi)\b/i
    const WAKE = /^(hi|hey|ok)?\s*si[ay]a?[\s,]*/i
    setVoiceIntercept((heard) => {
      const a = voiceApproval.current
      if (!a) return false
      const text = heard.replace(WAKE, '').trim()
      if (!text) return false
      if (a.kind === 'input') {
        respondApproval(a, { text }); speakText('Got it.'); return true
      }
      if (a.risk >= 4) return false
      if (YES.test(text)) { respondApproval(a, { approved: true }); speakText('Okay, doing it.'); return true }
      if (NO.test(text))  { respondApproval(a, { approved: false, note: 'Denied by voice' }); speakText('Okay, I won\'t.'); return true }
      return false
    })
    return () => setVoiceIntercept(null)
  }, [respondApproval])

  // ── Wake word toggle mode ────────────────────────────────────────────────
  const { start: startWake, stop: stopWake } = useWakeWord({
    // Messages from wake word pipeline go into chat
    onMessage: useCallback((msg) => {
      if (msg.role === 'user') lastVoiceAt.current = Date.now()
      addMessage(msg)
    }, [addMessage]),
    // Wake word hook drives voiceState directly — no polling needed
    onVoiceState: useCallback((state) => {
      voiceStateRef.current = state
      setVoiceState(state)
    }, []),
  })

  // ── Toggle: click orb = turn wake word mode ON or OFF ───────────────────
  const handleVoiceToggle = useCallback(() => {
    if (voiceState === 'off') {
      startWake()
      // startWake() calls onVoiceState('listening') internally on recog.onstart
    } else {
      stopWake()
      // stopWake() calls onVoiceState('off') internally
    }
  }, [voiceState, startWake, stopWake])

  // ── Mic button (tap-to-record) — separate from wake word ────────────────
  const handleSendRef = useRef(null)      // always the latest handleSend (avoids a stale closure)
  const { micState, toggleMic } = useMic({
    onTranscribed: useCallback((text, source = 'voice') => {
      handleSendRef.current?.(text, source)
    }, []),
    onError: useCallback((err) => {
      addMessage({ role: 'assistant', content: `🎤 ${err}` })
    }, [addMessage]),
  })

  // ── Responsive ──────────────────────────────────────────────────────────
  useEffect(() => {
    const check = () => {
      const m = window.innerWidth < 768
      setIsMobile(m)
      if (!m) setSidebarOpen(true)
    }
    check()
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [])

  // ── Load history ─────────────────────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/history/chat`)
      .then(r => r.json())
      .then(d => setChatHistory(d.history || []))
      .catch(() => {})
    fetch(`${API}/history/voice`)
      .then(r => r.json())
      .then(d => setVoiceHistory(d.history || []))
      .catch(() => {})
  }, [])

  async function handleSend(text, source = 'text') {
    if (source === 'voice') lastVoiceAt.current = Date.now()
    // Tap-to-talk answers ("yes", "no", or an answer) to a pending voice approval
    if (source === 'voice' && voiceApproval.current && runVoiceIntercept(text)) return
    sessionMsgs.current.push({ role: 'user', content: text, id: Date.now(), source })
    await send(text, source)
    if (isMobile) setSidebarOpen(false)
  }

  handleSendRef.current = handleSend

  const saveSession = useCallback(async () => {
    const msgs = sessionMsgs.current
    if (!msgs.length) return
    const title = msgs[0]?.content?.slice(0, 50) || 'Chat'
    try {
      await fetch(`${API}/history/chat/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, messages: msgs }),
      })
      const r = await fetch(`${API}/history/chat`)
      const d = await r.json()
      setChatHistory(d.history || [])
    } catch {}
  }, [])

  const handleNewChat = useCallback(async () => {
    await saveSession()
    sessionMsgs.current = []
    clear()
    setActiveId(null)
    if (isMobile) setSidebarOpen(false)
  }, [clear, saveSession, isMobile])

  const handleSelectChat = useCallback((id) => {
    clear()
    setActiveId(id)
    if (isMobile) setSidebarOpen(false)
  }, [clear, isMobile])

  return (
    <div className="app-root">
      <div className="ambient-bg">
        <div className="scan-line" />
        <div className="ambient-orb ambient-orb-1" />
        <div className="ambient-orb ambient-orb-2" />
      </div>

      <AnimatePresence>
        {sidebarOpen && isMobile && (
          <motion.div
            className="sidebar-overlay"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {sidebarOpen && (
          <motion.div
            className="sidebar-slot"
            initial={{ x: -270, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: -270, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.25, 0.46, 0.45, 0.94] }}
          >
            <Sidebar
              chatHistory={chatHistory}
              voiceHistory={voiceHistory}
              activeId={activeId}
              onNewChat={handleNewChat}
              onSelect={handleSelectChat}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="main-slot">
        <Header
          sidebarOpen={sidebarOpen}
          onToggle={() => setSidebarOpen(v => !v)}
          onNewChat={handleNewChat}
          voiceOn={voiceState !== 'off'}
          voiceState={voiceState}
          onDocsToggle={() => setShowDocs(v => !v)}
          showDocs={showDocs}
          onTasksToggle={() => setShowTasks(v => !v)}
          showTasks={showTasks}
          activeTasks={agent.activeCount}
          pendingApprovals={agent.approvals.length}
          onMemory={() => setShowMemory(true)}
          onSettings={() => setShowSettings(true)}
        />

        <ChatWindow
          messages={messages}
          thinking={thinking}
          onSuggestion={(text) => handleSend(text, 'text')}
          voiceState={voiceState}
          voiceOn={voiceState !== 'off'}
        />

        <AnimatePresence>
          {showDocs && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.22 }}
              style={{ overflow: 'hidden' }}
            >
              <DocumentsPanel />
            </motion.div>
          )}
        </AnimatePresence>

        <InputBox
          onSend={(text) => handleSend(text, 'text')}
          onStop={stop}
          thinking={thinking}
          micState={micState}
          onToggleMic={toggleMic}
          onDocsToggle={() => setShowDocs(v => !v)}
          showDocs={showDocs}
        />
      </div>

      {/*
        VoiceOrb:
          OFF  → click = turn on wake word mode (Sia listens for "Hi Sia…")
          ON   → orb pulses, label = "Listening… say Hi Sia"
          Click again = turn off
      */}
      <VoiceOrb
        voiceState={voiceState}
        onToggle={handleVoiceToggle}
      />

      <ApprovalStack approvals={agent.approvals} onRespond={respondApproval} />

      <AnimatePresence>
        {showTasks && (
          <TaskPanel tasks={agent.tasks} connected={agent.connected}
                     onCancel={agent.cancel} onClose={() => setShowTasks(false)} />
        )}
      </AnimatePresence>
      {showMemory && <MemoryPanel onClose={() => setShowMemory(false)} />}
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}
    </div>
  )
}

export default function App() {
  return <ThemeProvider><AppInner /></ThemeProvider>
}
