import React, { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { FileText, File, Trash2, Upload, Loader2 } from 'lucide-react'
import './DocumentsPanel.css'
import { API_BASE } from '../hooks/useAgentEvents'

const API = `${API_BASE}/api`
const ICONS = { pdf: <FileText size={13} />, docx: <File size={13} />, txt: <File size={13} />, md: <File size={13} /> }
const ext = n => n.split('.').pop().toLowerCase()

export default function DocumentsPanel() {
  const [docs, setDocs]         = useState([])
  const [loading, setLoading]   = useState(true)
  const [uploading, setUploading] = useState(false)
  const [deleting, setDeleting] = useState(null)
  const fileRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/documents/list`)
      const d = await r.json()
      setDocs(d.documents || [])
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [])

  const handleUpload = useCallback(async e => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setUploading(true)
    const form = new FormData()
    form.append('file', file)
    try {
      await fetch(`${API}/documents/upload`, { method: 'POST', body: form })
      await load()
    } catch {}
    setUploading(false)
  }, [load])

  const handleDelete = useCallback(async name => {
    setDeleting(name)
    try {
      await fetch(`${API}/documents/${encodeURIComponent(name)}`, { method: 'DELETE' })
      await load()
    } catch {}
    setDeleting(null)
  }, [load])

  return (
    <div className="docs-panel">
      <div className="docs-header">
        <span className="docs-title">Knowledge Base</span>
        <span className="docs-count">{docs.length} files</span>
        <motion.button
          className="docs-upload-btn"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          whileHover={{ scale: 1.04 }}
          whileTap={{ scale: 0.96 }}
        >
          {uploading ? <Loader2 size={12} className="icon-spin" /> : <Upload size={12} />}
          <span>{uploading ? 'Uploading…' : 'Add File'}</span>
        </motion.button>
        <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" style={{ display:'none' }} onChange={handleUpload} />
      </div>

      <div className="docs-list">
        {loading
          ? <div className="docs-empty"><Loader2 size={13} className="icon-spin" /> Loading…</div>
          : docs.length === 0
          ? <div className="docs-empty">No files — upload a PDF, DOCX, TXT or MD to ask questions about it</div>
          : (
            <AnimatePresence initial={false}>
              {docs.map(name => (
                <motion.div
                  key={name}
                  className="doc-chip"
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  transition={{ duration: 0.15 }}
                >
                  <span className="doc-icon">{ICONS[ext(name)] || <File size={13} />}</span>
                  <span className="doc-name" title={name}>{name}</span>
                  <motion.button
                    className="doc-del"
                    onClick={() => handleDelete(name)}
                    disabled={deleting === name}
                    whileHover={{ scale: 1.2 }}
                    whileTap={{ scale: 0.9 }}
                  >
                    {deleting === name ? <Loader2 size={11} className="icon-spin" /> : <Trash2 size={11} />}
                  </motion.button>
                </motion.div>
              ))}
            </AnimatePresence>
          )
        }
      </div>
    </div>
  )
}
