/**
 * ApprovalStack — cards for actions waiting on the user.
 *  • approval (risk 3): exact action + details → Approve / Deny
 *  • destructive (risk 4): must type CONFIRM
 *  • question: free-text answer (also used for "log in, then say done")
 */
import React, { memo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ShieldAlert, Check, X, Send, HelpCircle, Trash2 } from 'lucide-react'
import './AgentUI.css'

function Card({ a, onRespond }) {
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [showArgs, setShowArgs] = useState(false)
  const isInput = a.kind === 'input'
  const isL4 = a.risk >= 4 || a.confirm

  const go = async (payload) => {
    setBusy(true); setErr('')
    try { await onRespond(a, payload) } catch (e) { setErr(e.message); setBusy(false) }
  }

  if (isInput) {
    return (
      <div className="appr-card appr-input">
        <div className="appr-title"><HelpCircle size={15} /> SIA needs your input</div>
        <div className="appr-summary">{a.summary}</div>
        <div className="appr-row">
          <input className="appr-field" autoFocus value={text} placeholder={a.reason ? "Type 'done' when finished" : 'Your answer'}
                 onChange={e => setText(e.target.value)} onKeyDown={e => e.key === 'Enter' && text.trim() && go({ text })} />
          <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={() => go({ text })}><Send size={13} /></button>
        </div>
        {err && <div className="appr-err">{err}</div>}
      </div>
    )
  }
  return (
    <div className={`appr-card ${isL4 ? 'appr-l4' : 'appr-l3'}`}>
      <div className="appr-title">
        {isL4 ? <Trash2 size={15} /> : <ShieldAlert size={15} />}
        {isL4 ? 'Destructive action — confirm' : 'Approval needed'}
        <span className="appr-risk">Level {a.risk}</span>
      </div>
      <pre className="appr-summary">{a.summary}</pre>
      <button className="link-btn" onClick={() => setShowArgs(v => !v)}>{showArgs ? 'Hide' : 'Show'} exact details ({a.tool})</button>
      {showArgs && <pre className="appr-args">{JSON.stringify(a.args, null, 2)}</pre>}
      {isL4 && (
        <input className="appr-field" value={text} placeholder="Type CONFIRM to allow" onChange={e => setText(e.target.value)} />
      )}
      <input className="appr-field appr-note" value={note} placeholder="Optional note for SIA (e.g. 'use my work email')"
             onChange={e => setNote(e.target.value)} />
      <div className="appr-row">
        <button className="btn btn-deny" disabled={busy} onClick={() => go({ approved: false, note })}><X size={13} /> Deny</button>
        <button className="btn btn-primary" disabled={busy || (isL4 && text.trim().toUpperCase() !== 'CONFIRM')}
                onClick={() => go({ approved: true, confirm_text: text, note })}><Check size={13} /> Approve</button>
      </div>
      {err && <div className="appr-err">{err}</div>}
    </div>
  )
}

export default memo(function ApprovalStack({ approvals, onRespond }) {
  return (
    <div className="appr-stack">
      <AnimatePresence>
        {approvals.map(a => (
          <motion.div key={a.id} initial={{ opacity: 0, y: 16, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: 10, scale: 0.97 }} transition={{ duration: 0.18 }}>
            <Card a={a} onRespond={onRespond} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
})
