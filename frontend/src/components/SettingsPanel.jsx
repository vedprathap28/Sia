/**
 * SettingsPanel — permission level, destructive-action policy, models in use, allowed folders,
 * background schedules (daily briefing, job watch, custom), and the job-application tracker.
 */
import React, { memo, useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { X, Play, Trash2, Plus } from 'lucide-react'
import { api } from '../hooks/useAgentEvents'
import './AgentUI.css'

export default memo(function SettingsPanel({ onClose }) {
  const [perm, setPerm] = useState(null)
  const [sched, setSched] = useState({ schedules: [], presets: {} })
  const [apps, setApps] = useState([])
  const [form, setForm] = useState({ name: '', cron: '0 9 * * 1-5', goal: '' })
  const [err, setErr] = useState('')
  const [tab, setTab] = useState('safety')

  const load = useCallback(() => {
    api('/settings/permissions').then(setPerm).catch(e => setErr(e.message))
    api('/schedules').then(setSched).catch(() => {})
    api('/applications').then(d => setApps(d.applications || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const savePerm = async (patch) => { try { setPerm(await api('/settings/permissions', { method: 'PUT', body: patch })) } catch (e) { setErr(e.message) } }
  const addSched = async (body) => { setErr(''); try { await api('/schedules', { method: 'POST', body }); load() } catch (e) { setErr(e.message) } }
  const toggle = async (s) => { await api(`/schedules/${s.id}`, { method: 'PATCH', body: { enabled: !s.enabled } }); load() }
  const del = async (s) => { await api(`/schedules/${s.id}`, { method: 'DELETE' }); load() }
  const run = async (s) => { await api(`/schedules/${s.id}/run`, { method: 'POST' }) }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <motion.div className="modal" onClick={e => e.stopPropagation()} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <div className="drawer-head"><span>Settings</span><button className="icon-btn" onClick={onClose}><X size={16} /></button></div>
        <div className="tabs">
          {['safety', 'schedules', 'jobs'].map(t => <button key={t} className={`tab ${tab === t ? 'tab-on' : ''}`} onClick={() => setTab(t)}>{t}</button>)}
        </div>
        {err && <div className="appr-err">{err}</div>}

        {tab === 'safety' && perm && (
          <div className="set-section">
            <div className="set-row">
              <div><b>Run without asking</b><div className="muted small">Sending, submitting, booking, paying, posting and installs always ask.</div></div>
              <select value={perm.auto_approve_level} onChange={e => savePerm({ auto_approve_level: Number(e.target.value) })}>
                <option value={2}>Read-only + low-risk (open apps, drafts, files in allowed folders)</option>
                <option value={1}>Read-only only (ask before everything else)</option>
              </select>
            </div>
            <div className="set-row">
              <div><b>Destructive actions</b><div className="muted small">Deleting files, git reset/force-push…</div></div>
              <select value={perm.level4_policy} onChange={e => savePerm({ level4_policy: e.target.value })}>
                <option value="confirm">Allow after typing CONFIRM</option>
                <option value="block">Always block</option>
              </select>
            </div>
            <div className="set-row"><div><b>Models</b></div><div className="muted">chat: {perm.chat_model} · planning/vision: {perm.planner_model} · engine: {perm.engine}</div></div>
            <div className="set-row col"><b>Folders SIA may access</b>
              <ul className="muted small">{perm.allowed_roots.map(r => <li key={r}>{r}</li>)}</ul>
              <div className="muted small">Change with ALLOWED_ROOTS in .env (separate with ;).</div>
            </div>
          </div>
        )}

        {tab === 'schedules' && (
          <div className="set-section">
            <div className="muted small">Background tasks run on schedule; anything consequential still waits for your approval.</div>
            <div className="preset-row">
              {Object.entries(sched.presets || {}).map(([k, p]) => (
                <button key={k} className="btn" onClick={() => addSched({ preset: k })}><Plus size={12} /> {p.name} ({p.cron})</button>
              ))}
            </div>
            <div className="mem-add">
              <input placeholder="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
              <input placeholder="cron e.g. 0 9 * * 1-5" value={form.cron} onChange={e => setForm({ ...form, cron: e.target.value })} />
              <input placeholder="What SIA should do" value={form.goal} onChange={e => setForm({ ...form, goal: e.target.value })} />
              <button className="btn btn-primary" onClick={() => addSched(form)} disabled={!form.name || !form.goal}><Plus size={13} /></button>
            </div>
            <div className="mem-list">
              {sched.schedules.length === 0 && <div className="empty">No schedules.</div>}
              {sched.schedules.map(s => (
                <div key={s.id} className="mem-item">
                  <input type="checkbox" checked={s.enabled} onChange={() => toggle(s)} title="enabled" />
                  <span className="mem-key">{s.name}</span>
                  <span className="mem-cat">{s.cron}</span>
                  <span className="mem-val">{s.goal}</span>
                  <button className="icon-btn" title="Run now" onClick={() => run(s)}><Play size={13} /></button>
                  <button className="icon-btn" title="Delete" onClick={() => del(s)}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === 'jobs' && (
          <div className="set-section">
            <div className="muted small">Jobs SIA found or prepared. Ask: "find backend jobs that match my resume".</div>
            <div className="mem-list">
              {apps.length === 0 && <div className="empty">No tracked applications yet.</div>}
              {apps.map(a => (
                <div key={a.id} className="mem-item">
                  <span className="mem-cat">{a.status}</span>
                  <span className="mem-key">{a.company}</span>
                  <span className="mem-val">{a.role}{a.match_score ? ` · match ${Math.round(a.match_score)}` : ''}</span>
                  {a.url && <a className="link-btn" href={a.url} target="_blank" rel="noreferrer">open</a>}
                </div>
              ))}
            </div>
          </div>
        )}
      </motion.div>
    </div>
  )
})
