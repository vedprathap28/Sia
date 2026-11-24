import React, { memo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Menu, PenSquare, FileText, ListChecks, Brain, Settings } from 'lucide-react'
import ThemeToggle from './ThemeToggle'
import './Header.css'

const STATE_LABEL = {
  listening:  'LISTENING',
  processing: 'PROCESSING',
  speaking:   'SPEAKING',
}

export default memo(function Header({
  sidebarOpen, onToggle, onNewChat,
  voiceOn, voiceState,
  onDocsToggle, showDocs,
  onTasksToggle, showTasks, activeTasks = 0, pendingApprovals = 0,
  onMemory, onSettings,
}) {
  const label = voiceOn ? STATE_LABEL[voiceState] : null

  return (
    <header className="header">
      <div className="header-left">
        <motion.button
          className="header-btn"
          onClick={onToggle}
          whileHover={{ backgroundColor: 'var(--bg-user-msg)' }}
          whileTap={{ scale: 0.92 }}
          aria-label="Toggle sidebar"
        >
          <Menu size={18} />
        </motion.button>
      </div>

      <div className="header-center">
        {!sidebarOpen && (
          <motion.span
            className="header-title"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
          >
            SIA
          </motion.span>
        )}
      </div>

      <div className="header-right">
        {/* Voice status pill */}
        <AnimatePresence>
          {label && (
            <motion.div
              className={`voice-status voice-status--${voiceState}`}
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.85 }}
              transition={{ duration: 0.2 }}
            >
              <span className="vs-dot" />
              <span className="vs-text">{label}</span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Tasks drawer */}
        <motion.button
          className={`header-btn ${showTasks ? 'header-btn--active' : ''}`}
          onClick={onTasksToggle}
          whileHover={{ backgroundColor: 'var(--bg-user-msg)' }}
          whileTap={{ scale: 0.92 }}
          title="Tasks"
          style={{ position: 'relative' }}
        >
          <ListChecks size={17} />
          {pendingApprovals > 0
            ? <span className="hdr-badge">{pendingApprovals}</span>
            : activeTasks > 0 && <span className="hdr-badge run">{activeTasks}</span>}
        </motion.button>

        <motion.button className="header-btn" onClick={onMemory} whileHover={{ backgroundColor: 'var(--bg-user-msg)' }}
                       whileTap={{ scale: 0.92 }} title="Memory">
          <Brain size={17} />
        </motion.button>

        <motion.button className="header-btn" onClick={onSettings} whileHover={{ backgroundColor: 'var(--bg-user-msg)' }}
                       whileTap={{ scale: 0.92 }} title="Settings">
          <Settings size={17} />
        </motion.button>

        {/* Docs toggle */}
        <motion.button
          className={`header-btn ${showDocs ? 'header-btn--active' : ''}`}
          onClick={onDocsToggle}
          whileHover={{ backgroundColor: 'var(--bg-user-msg)' }}
          whileTap={{ scale: 0.92 }}
          title="Documents"
        >
          <FileText size={17} />
        </motion.button>

        <ThemeToggle />

        {/* New chat */}
        <motion.button
          className="header-btn"
          onClick={onNewChat}
          whileHover={{ backgroundColor: 'var(--bg-user-msg)' }}
          whileTap={{ scale: 0.92 }}
          aria-label="New chat"
          title="New chat"
        >
          <PenSquare size={17} />
        </motion.button>
      </div>
    </header>
  )
})
