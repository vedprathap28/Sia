import React, { memo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, MessageSquare, Mic } from 'lucide-react'
import './Sidebar.css'

function groupByDate(items) {
  const now   = new Date()
  const today = now.toDateString()
  const yest  = new Date(now - 86400000).toDateString()
  const groups = { Today: [], Yesterday: [], 'This Week': [], Older: [] }

  items.forEach(item => {
    const d = new Date(item.date)
    if (d.toDateString() === today)       groups['Today'].push(item)
    else if (d.toDateString() === yest)   groups['Yesterday'].push(item)
    else if (now - d < 7 * 86400000)      groups['This Week'].push(item)
    else                                   groups['Older'].push(item)
  })
  return Object.entries(groups).filter(([, v]) => v.length > 0)
}

const HistItem = memo(function HistItem({ item, active, onSelect, Icon }) {
  return (
    <motion.button
      className={`hist-item ${active ? 'hist-item--active' : ''}`}
      onClick={() => onSelect(item.id, item.messages)}
      whileHover={{ x: 3 }}
      transition={{ duration: 0.12 }}
      title={item.title}
    >
      <Icon size={12} className="hist-icon" />
      <span className="hist-title">{item.title}</span>
    </motion.button>
  )
})

function HistSection({ items, activeId, onSelect, Icon }) {
  if (!items.length) return (
    <div className="hist-empty">No history yet</div>
  )
  const groups = groupByDate(items)
  return (
    <div>
      {groups.map(([label, groupItems]) => (
        <div key={label} className="hist-group">
          <div className="hist-date">{label}</div>
          {groupItems.map(item => (
            <HistItem
              key={item.id}
              item={item}
              active={item.id === activeId}
              onSelect={onSelect}
              Icon={Icon}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export default memo(function Sidebar({
  chatHistory = [], voiceHistory = [],
  activeId, onNewChat, onSelect,
}) {
  const [tab, setTab] = useState('chat')

  return (
    <aside className="sidebar">
      {/* Top */}
      <div className="sidebar-top">
        <div className="sidebar-logo">
          <div className="logo-mark">
            <div className="logo-ring" />
            <span>S</span>
          </div>
          <div className="logo-info">
            <span className="logo-name">SIA</span>
            <span className="logo-sub">Smart Intelligent Assistant</span>
          </div>
        </div>

        <motion.button
          className="new-chat-btn"
          onClick={onNewChat}
          whileHover={{ borderColor: 'var(--brand)', boxShadow: '0 0 0 3px var(--brand-light)' }}
          whileTap={{ scale: 0.98 }}
        >
          <Plus size={14} />
          <span>New chat</span>
        </motion.button>
      </div>

      {/* Tabs */}
      <div className="sidebar-tabs">
        {[
          { id: 'chat',  label: 'Chat',  Icon: MessageSquare },
          { id: 'voice', label: 'Voice', Icon: Mic },
        ].map(({ id, label, Icon }) => (
          <button
            key={id}
            className={`sidebar-tab ${tab === id ? 'sidebar-tab--active' : ''}`}
            onClick={() => setTab(id)}
          >
            <Icon size={12} />
            <span>{label}</span>
            <span className="tab-count">
              {id === 'chat' ? chatHistory.length : voiceHistory.length}
            </span>
          </button>
        ))}
      </div>

      {/* History */}
      <nav className="sidebar-nav">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, x: tab === 'chat' ? -10 : 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: tab === 'chat' ? 10 : -10 }}
            transition={{ duration: 0.18 }}
          >
            {tab === 'chat'
              ? <HistSection items={chatHistory} activeId={activeId} onSelect={onSelect} Icon={MessageSquare} />
              : <HistSection items={voiceHistory} activeId={activeId} onSelect={onSelect} Icon={Mic} />
            }
          </motion.div>
        </AnimatePresence>
      </nav>

      {/* Bottom user card */}
      <div className="sidebar-bottom">
        <div className="sidebar-divider" />
        <div className="user-card">
          <div className="user-avatar">V</div>
          <div className="user-info">
            <span className="user-name">Ved Prathap</span>
            <span className="user-role">Owner</span>
          </div>
          <div className="user-status" />
        </div>
      </div>
    </aside>
  )
})
