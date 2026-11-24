/**
 * MemoryPanel — see, add and delete what SIA remembers. Secrets are refused by the backend.
 */
import React, { memo, useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { X, Trash2, Plus, Search } from 'lucide-react'
import { api } from '../hooks/useAgentEvents'
import './AgentUI.css'

const CATS = ['preference', 'profile', 'project', 'workflow', 'fact']
const HELP = {
  preference: 'How you like things done (e.g. preferred browser, tone of emails)',
  profile: 'Approved details SIA may use in forms (name, email, phone, LinkedIn, skills)',
  project: 'Project context (where your backend repo lives, what stack it uses)',
  workflow: 'Saved procedures you approved',
  fact: 'Anything else you asked SIA to remember',
}

export default memo(function MemoryPanel({ onClose }) {
  const [items, setItems] = useState([])
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('')
  const [form, setForm] = useState({ category: 'profile', key: '', value: '' })
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api(`/memory/items?q=${encodeURIComponent(q)}&category=${cat}`).then(d => setItems(d.items || [])).catch(e => setErr(e.message))
  }, [q, cat])
  useEffect(() => { load() }, [load])

  const add = async () => {
    setErr('')
    try { await api('/memory/items', { method: 'POST', body: form }); setForm(f => ({ ...f, key: '', value: '' })); load() }
    catch (e) { setErr(e.message) }
  }
  const del = async (id) => { await api(`/memory/items/${id}`, { method: 'DELETE' }); load() }
  const clearCat = async () => {
    if (!cat || !window.confirm(`Delete everything in "${cat}"?`)) return
    await api(`/memory/items?category=${cat}`, { method: 'DELETE' }); load()
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <motion.div className="modal" onClick={e => e.stopPropagation()} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <div className="drawer-head"><span>Memory</span><button className="icon-btn" onClick={onClose}><X size={16} /></button></div>
        <p className="muted">SIA only remembers what you tell it. Passwords, OTPs, card numbers and keys are never stored here.</p>
        <div className="mem-add">
          <select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>
            {CATS.map(c => <option key={c}>{c}</option>)}
          </select>
          <input placeholder="key (e.g. email)" value={form.key} onChange={e => setForm({ ...form, key: e.target.value })} />
          <input placeholder="value" value={form.value} onChange={e => setForm({ ...form, value: e.target.value })}
                 onKeyDown={e => e.key === 'Enter' && add()} />
          <button className="btn btn-primary" onClick={add} disabled={!form.key || !form.value}><Plus size={13} /> Add</button>
        </div>
        <div className="muted small">{HELP[form.category]}</div>
        {err && <div className="appr-err">{err}</div>}
        <div className="mem-filter">
          <Search size={14} />
          <input placeholder="Search" value={q} onChange={e => setQ(e.target.value)} />
          <select value={cat} onChange={e => setCat(e.target.value)}>
            <option value="">all</option>{CATS.map(c => <option key={c}>{c}</option>)}
          </select>
          {cat && <button className="btn btn-deny" onClick={clearCat}>Clear {cat}</button>}
        </div>
        <div className="mem-list">
          {items.length === 0 && <div className="empty">Nothing stored.</div>}
          {items.map(i => (
            <div key={i.id} className="mem-item">
              <span className={`mem-cat cat-${i.category}`}>{i.category}</span>
              <span className="mem-key">{i.key}</span>
              <span className="mem-val">{i.value}</span>
              {i.source !== 'user' && <span className="mem-src" title="saved automatically — review it">{i.source}</span>}
              <button className="icon-btn" onClick={() => del(i.id)} title="Delete"><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      </motion.div>
    </div>
  )
})
