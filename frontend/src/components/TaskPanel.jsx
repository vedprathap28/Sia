/**
 * TaskPanel — live view of what SIA is doing: plan, current agent, tool activity,
 * verification marks, cancel. Shows actions and results, never hidden reasoning.
 */
import React, { memo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Square, CheckCircle2, XCircle, Loader2, ShieldCheck, ShieldAlert, ChevronDown, ChevronRight, Clock } from 'lucide-react'
import './AgentUI.css'

const STATUS = {
  in_progress:      { label: 'Working',            cls: 'st-run' },
  not_started:      { label: 'Queued',             cls: 'st-run' },
  waiting_approval: { label: 'Needs approval',     cls: 'st-wait' },
  waiting_input:    { label: 'Needs your answer',  cls: 'st-wait' },
  blocked:          { label: 'Blocked',            cls: 'st-wait' },
  completed:        { label: 'Done',               cls: 'st-ok' },
  failed:           { label: 'Failed',             cls: 'st-bad' },
  cancelled:        { label: 'Cancelled',          cls: 'st-off' },
}
const ACTIVE = new Set(['in_progress', 'not_started', 'waiting_approval', 'waiting_input', 'blocked'])

function StepIcon({ status }) {
  if (status === 'done') return <CheckCircle2 size={14} className="ic-ok" />
  if (status === 'failed') return <XCircle size={14} className="ic-bad" />
  if (status === 'running') return <Loader2 size={14} className="ic-spin" />
  return <Clock size={14} className="ic-off" />
}

const TaskCard = memo(function TaskCard({ task, onCancel, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen)
  const st = STATUS[task.status] || STATUS.in_progress
  const active = ACTIVE.has(task.status)
  return (
    <div className={`task-card ${active ? 'task-card--active' : ''}`}>
      <div className="task-head" onClick={() => setOpen(o => !o)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <div className="task-goal" title={task.goal}>{task.goal || 'Task'}</div>
        <span className={`task-status ${st.cls}`}>{st.label}</span>
      </div>
      {active && task.label && (
        <div className="task-label">{task.status === 'in_progress' && <Loader2 size={12} className="ic-spin" />} {task.label}</div>
      )}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                      style={{ overflow: 'hidden' }}>
            {task.plan?.length > 0 && (
              <ol className="task-plan">
                {task.plan.map((p, i) => (
                  <li key={i} className={`plan-step plan-${p.status || 'pending'}`}>
                    <StepIcon status={p.status} />
                    <span className="plan-agent">{p.agent}</span>
                    <span className="plan-text">{p.instruction}</span>
                  </li>
                ))}
              </ol>
            )}
            {task.activity?.length > 0 && (
              <div className="task-activity">
                {task.activity.filter(a => a.kind !== 'agent').slice(-25).map((a, i) => (
                  <div key={i} className={`act act-${a.kind}`}>
                    {a.kind === 'tool' && <Loader2 size={11} className="ic-off" />}
                    {a.kind === 'result' && (a.ok ? <CheckCircle2 size={11} className="ic-ok" /> : <XCircle size={11} className="ic-bad" />)}
                    <span className="act-tool">{a.tool}</span>
                    <span className="act-text">{a.text}</span>
                    {a.kind === 'result' && a.verified === true && <ShieldCheck size={11} className="ic-ok" title="verified" />}
                    {a.kind === 'result' && a.verified === false && <ShieldAlert size={11} className="ic-warn" title="not verified" />}
                  </div>
                ))}
              </div>
            )}
            {task.result && !active && (
              <div className="task-result">
                {task.verified ? <span className="badge-ok"><ShieldCheck size={12} /> verified</span>
                               : task.status === 'completed' && <span className="badge-warn"><ShieldAlert size={12} /> not fully verified</span>}
                <div>{task.result.slice(0, 600)}</div>
              </div>
            )}
            {(task.tokens_in > 0 || task.cost_usd > 0) && (
              <div className="task-meta">{task.llm_calls} LLM calls · {task.tokens_in + task.tokens_out} tokens · ${Number(task.cost_usd || 0).toFixed(4)}</div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
      {active && (
        <button className="task-cancel" onClick={() => onCancel(task.id)}><Square size={11} /> Cancel</button>
      )}
    </div>
  )
})

export default memo(function TaskPanel({ tasks, onCancel, onClose, connected }) {
  const active = tasks.filter(t => ACTIVE.has(t.status))
  const recent = tasks.filter(t => !ACTIVE.has(t.status)).slice(0, 15)
  return (
    <motion.aside className="agent-drawer" initial={{ x: 380 }} animate={{ x: 0 }} exit={{ x: 380 }}
                  transition={{ duration: 0.25 }}>
      <div className="drawer-head">
        <span>Tasks</span>
        <span className={`conn ${connected ? 'conn-on' : 'conn-off'}`}>{connected ? 'live' : 'offline'}</span>
        <button className="icon-btn" onClick={onClose}><X size={16} /></button>
      </div>
      <div className="drawer-body">
        {active.length === 0 && recent.length === 0 && <div className="empty">No tasks yet. Ask SIA to do something.</div>}
        {active.length > 0 && <div className="drawer-section">Active</div>}
        {active.map(t => <TaskCard key={t.id} task={t} onCancel={onCancel} defaultOpen />)}
        {recent.length > 0 && <div className="drawer-section">Recent</div>}
        {recent.map(t => <TaskCard key={t.id} task={t} onCancel={onCancel} defaultOpen={false} />)}
      </div>
    </motion.aside>
  )
})
