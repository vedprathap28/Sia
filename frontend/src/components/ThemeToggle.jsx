import React, { memo } from 'react'
import { motion } from 'framer-motion'
import { Sun, Moon } from 'lucide-react'
import { useTheme } from '../context/ThemeContext'
import './ThemeToggle.css'

export default memo(function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const isDark = theme === 'dark'

  return (
    <motion.button
      className="theme-toggle sidebar-action-btn"
      onClick={toggle}
      whileHover={{ backgroundColor: 'var(--bg-sidebar-hover)' }}
      whileTap={{ scale: 0.96 }}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      <div className="toggle-track">
        <motion.div
          className="toggle-thumb"
          animate={{ x: isDark ? 18 : 0 }}
          transition={{ type: 'spring', stiffness: 400, damping: 28 }}
        />
      </div>
      <span>{isDark ? 'Dark mode' : 'Light mode'}</span>
      <motion.div
        className="toggle-icon"
        animate={{ rotate: isDark ? 180 : 0, opacity: 1 }}
        transition={{ duration: 0.35 }}
      >
        {isDark ? <Moon size={14} /> : <Sun size={14} />}
      </motion.div>
    </motion.button>
  )
})
