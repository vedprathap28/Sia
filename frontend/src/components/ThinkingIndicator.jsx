import React, { memo } from 'react'
import { motion } from 'framer-motion'
import './ThinkingIndicator.css'

export default memo(function ThinkingIndicator() {
  return (
    <motion.div
      className="thinking-row"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.2 }}
    >
      <div className="thinking-avatar">S</div>
      <div className="thinking-bubble">
        <div className="thinking-dots">
          {[0, 1, 2].map(i => (
            <motion.span
              key={i}
              className="t-dot"
              animate={{ y: [0, -6, 0] }}
              transition={{ duration: 0.7, delay: i * 0.15, repeat: Infinity, ease: 'easeInOut' }}
            />
          ))}
        </div>
        <span className="thinking-text">Thinking…</span>
      </div>
    </motion.div>
  )
})
