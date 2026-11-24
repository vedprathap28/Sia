import React, { useState, useRef, useCallback, memo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowUp, Paperclip, Mic, Square, Loader2 } from 'lucide-react'
import './InputBox.css'
import { API_BASE } from '../hooks/useAgentEvents'

export default memo(function InputBox({
  onSend, onStop, thinking,
  micState = 'idle', onToggleMic,
  onDocsToggle, showDocs,
}) {
  const [value, setValue]     = useState('')
  const [focused, setFocused] = useState(false)
  const [uploadStatus, setUploadStatus] = useState(null)
  const textRef = useRef(null)
  const fileRef = useRef(null)

  const resize = () => {
    const el = textRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }

  const handleChange = e => { setValue(e.target.value); resize() }

  const handleSend = useCallback(() => {
    const txt = value.trim()
    if (!txt || thinking) return
    onSend(txt)
    setValue('')
    if (textRef.current) textRef.current.style.height = 'auto'
    textRef.current?.focus()
  }, [value, thinking, onSend])

  const handleKey = e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleFileChange = useCallback(async e => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setUploadStatus({ type: 'loading', msg: `Uploading ${file.name}…` })
    const form = new FormData()
    form.append('file', file)
    try {
      const r = await fetch(`${API_BASE}/api/documents/upload`, { method: 'POST', body: form })
      const d = await r.json()
      if (d.success) setUploadStatus({ type: 'ok', msg: `✅ ${file.name} added to Sia's knowledge` })
      else setUploadStatus({ type: 'err', msg: `❌ ${d.detail || 'Upload failed'}` })
    } catch {
      setUploadStatus({ type: 'err', msg: '❌ Upload failed — backend not running?' })
    }
    setTimeout(() => setUploadStatus(null), 4000)
  }, [])

  const canSend       = value.trim().length > 0 && !thinking
  const isRecording   = micState === 'recording'
  const isTranscribing = micState === 'transcribing'

  return (
    <div className="input-area">
      {/* Upload status */}
      <AnimatePresence>
        {uploadStatus && (
          <motion.div
            className={`upload-bar upload-bar--${uploadStatus.type}`}
            initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
            transition={{ duration: 0.18 }}
          >
            {uploadStatus.type === 'loading' && <Loader2 size={13} className="icon-spin" />}
            <span>{uploadStatus.msg}</span>
          </motion.div>
        )}
      </AnimatePresence>

      <div className={`input-box ${focused ? 'focused' : ''} ${isRecording ? 'recording' : ''}`}>
        {/* Paperclip */}
        <motion.button
          className="input-icon-btn"
          type="button"
          title="Upload document"
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          onClick={() => fileRef.current?.click()}
        >
          <Paperclip size={17} />
        </motion.button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />

        {/* Textarea */}
        <textarea
          ref={textRef}
          className="input-field"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKey}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={
            isRecording    ? '🎤 Listening…' :
            isTranscribing ? '⏳ Transcribing…' :
            thinking       ? 'Sia is thinking…' :
                             'Message SIA'
          }
          rows={1}
          disabled={thinking || isTranscribing}
        />

        {/* Right buttons */}
        <div className="input-right">
          <motion.button
            className={`input-icon-btn mic-btn ${isRecording ? 'mic-on' : ''}`}
            type="button"
            title={isRecording ? 'Stop' : 'Voice input'}
            onClick={onToggleMic}
            whileHover={{ scale: isTranscribing ? 1 : 1.1 }}
            whileTap={{ scale: 0.9 }}
            disabled={isTranscribing}
          >
            {isTranscribing
              ? <Loader2 size={17} className="icon-spin" />
              : <Mic size={17} />
            }
          </motion.button>

          <motion.button
            className={`send-btn ${canSend ? 'send-btn--active' : ''} ${thinking ? 'send-btn--stop' : ''}`}
            onClick={thinking ? onStop : handleSend}
            whileHover={canSend || thinking ? { scale: 1.06 } : {}}
            whileTap={canSend || thinking ? { scale: 0.92 } : {}}
            disabled={!canSend && !thinking}
            type="button"
            title={thinking ? 'Stop' : 'Send'}
          >
            {thinking
              ? <Square size={13} fill="currentColor" />
              : <ArrowUp size={17} strokeWidth={2.5} />
            }
          </motion.button>
        </div>
      </div>

      {/* Recording indicator */}
      <AnimatePresence>
        {isRecording && (
          <motion.div
            className="rec-bar"
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
          >
            <span className="rec-dot" />
            <span className="rec-text">Recording — tap mic to send</span>
          </motion.div>
        )}
      </AnimatePresence>

      <p className="input-footer">
        SIA can make mistakes. Consider checking important info.
        <span className="hint-keys">
          <kbd>Enter</kbd> send · <kbd>Shift+Enter</kbd> new line
        </span>
      </p>
    </div>
  )
})
