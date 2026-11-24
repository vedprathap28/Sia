/**
 * Sia AI — useAgentEvents
 * Live connection to the backend task engine (/api/ws/events).
 * Keeps: tasks (with plan, current step, tool activity), pending approvals / questions.
 * Reconnects automatically. Shows progress only — never internal reasoning.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { deliverTaskResult } from './useMessages'

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const WS_URL = API_BASE.replace(/^http/, 'ws') + '/api/ws/events'

export async function api(path, opts = {}) {
  const res = await fetch(`${API_BASE}/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`)
  return data
}

const ACTIVE = new Set(['not_started', 'in_progress', 'waiting_approval', 'waiting_input', 'blocked'])

export function useAgentEvents({ onApproval, onTaskDone } = {}) {
  const [connected, setConnected] = useState(false)
  const [tasks, setTasks] = useState({})          // id → task view model
  const [approvals, setApprovals] = useState([])  // pending approvals + questions
  const wsRef = useRef(null)
  const retryRef = useRef(0)
  const cbRef = useRef({ onApproval, onTaskDone })
  cbRef.current = { onApproval, onTaskDone }

  const upsert = useCallback((id, fn) => {
    if (!id) return
    setTasks(prev => {
      const cur = prev[id] || { id, goal: '', status: 'in_progress', plan: [], activity: [], label: '', created: Date.now() }
      return { ...prev, [id]: fn(cur) }
    })
  }, [])

  const addActivity = (t, item) => ({ ...t, activity: [...t.activity, { ...item, at: Date.now() }].slice(-80) })

  const handle = useCallback((e) => {
    const id = e.task_id
    switch (e.type) {
      case 'hello':
        setApprovals(e.pending_approvals || [])
        ;(e.active_tasks || []).forEach(t => upsert(t.id, cur => ({ ...cur, ...t, plan: (t.plan || []) })))
        break
      case 'task_created':
        upsert(id, t => ({ ...t, goal: e.goal, source: e.source, status: 'in_progress', label: 'Understanding the request' }))
        break
      case 'task_status':
        upsert(id, t => ({ ...t, status: e.status }))
        if (!ACTIVE.has(e.status)) setApprovals(prev => prev.filter(a => a.task_id !== id))
        break
      case 'status':
        upsert(id, t => ({ ...t, label: e.label || t.label }))
        break
      case 'plan':
        upsert(id, t => ({ ...t, plan: (e.steps || []).map(s => ({ ...s, status: 'pending' })), label: e.replan ? 'Replanning' : 'Plan ready' }))
        break
      case 'agent':
        upsert(id, t => addActivity({
          ...t, agent: e.agent, label: e.label, step: e.step,
          plan: t.plan.map((p, i) => i === e.step - 1 ? { ...p, status: 'running' } : p),
        }, { kind: 'agent', text: `${e.agent} agent: ${e.instruction}` }))
        break
      case 'tool_started':
        upsert(id, t => addActivity({ ...t, label: e.summary }, { kind: 'tool', tool: e.tool, text: e.summary, risk: e.risk, state: 'running' }))
        break
      case 'tool_finished':
        upsert(id, t => {
          // replace the matching "running" line with its result
          const act = [...t.activity]
          for (let i = act.length - 1; i >= 0; i--) {
            if (act[i].kind === 'tool' && act[i].tool === e.tool) { act.splice(i, 1); break }
          }
          return addActivity({ ...t, activity: act }, { kind: 'result', tool: e.tool, ok: e.ok, verified: e.verified, text: e.summary, ms: e.duration_ms })
        })
        break
      case 'tool_blocked':
        upsert(id, t => addActivity(t, { kind: 'result', tool: e.tool, ok: false, text: `Blocked by safety policy: ${e.summary}` }))
        break
      case 'step_finished':
        upsert(id, t => ({ ...t, plan: t.plan.map((p, i) => i === e.step - 1 ? { ...p, status: e.ok ? 'done' : 'failed', result: e.summary } : p) }))
        break
      case 'approval_required':
      case 'input_required': {
        const a = e.approval
        setApprovals(prev => [...prev.filter(x => x.id !== a.id), a])
        upsert(id, t => ({ ...t, status: e.type === 'input_required' ? 'waiting_input' : 'waiting_approval',
                           label: e.type === 'input_required' ? 'Waiting for your answer' : 'Waiting for your approval' }))
        cbRef.current.onApproval?.(a)
        break
      }
      case 'approval_resolved':
        setApprovals(prev => prev.filter(a => a.id !== e.approval_id))
        upsert(id, t => ({ ...t, status: 'in_progress' }))
        break
      case 'task_result':
        upsert(id, t => ({ ...t, status: e.status, result: e.text, verified: e.verified, label: e.status === 'completed' ? 'Done' : 'Could not finish' }))
        setApprovals(prev => prev.filter(a => a.task_id !== id))
        deliverTaskResult(id, e.text, e.spoken, { status: e.status, verified: e.verified })
        cbRef.current.onTaskDone?.(e)
        break
      default:
    }
  }, [upsert])

  useEffect(() => {
    let closed = false
    let timer
    const connect = () => {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws
      ws.onopen = () => { setConnected(true); retryRef.current = 0 }
      ws.onmessage = (m) => { try { handle(JSON.parse(m.data)) } catch {} }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          const wait = Math.min(15000, 1000 * 2 ** retryRef.current++)
          timer = setTimeout(connect, wait)
        }
      }
      ws.onerror = () => { try { ws.close() } catch {} }
    }
    connect()
    // load recent history once
    api('/tasks?limit=20').then(d => (d.tasks || []).forEach(t => upsert(t.id, cur => ({ ...cur, ...t, plan: t.plan || [] })))).catch(() => {})
    return () => { closed = true; clearTimeout(timer); try { wsRef.current?.close() } catch {} }
  }, [handle, upsert])

  const respond = useCallback(async (approval, payload) => {
    await api(`/approvals/${approval.id}`, { method: 'POST', body: payload })
    setApprovals(prev => prev.filter(a => a.id !== approval.id))
  }, [])

  const cancel = useCallback(async (taskId) => {
    await api(`/tasks/${taskId}/cancel`, { method: 'POST' })
    upsert(taskId, t => ({ ...t, status: 'cancelled', label: 'Cancelled' }))
    setApprovals(prev => prev.filter(a => a.task_id !== taskId))
  }, [upsert])

  const ts = (t) => Date.parse(t.created_at || '') || t.created || 0
  const taskList = Object.values(tasks).sort((a, b) => ts(b) - ts(a))
  const activeCount = taskList.filter(t => ACTIVE.has(t.status)).length

  return { connected, tasks: taskList, approvals, respond, cancel, activeCount }
}
