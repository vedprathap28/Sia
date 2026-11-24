import React, { memo } from 'react'
import { motion } from 'framer-motion'
import './MessageBubble.css'

function parseContent(text) {
  const parts = []
  const codeRx = /```(\w+)?\n?([\s\S]*?)```/g
  let last = 0, m
  while ((m = codeRx.exec(text)) !== null) {
    if (m.index > last) parts.push({ type: 'text', content: text.slice(last, m.index) })
    parts.push({ type: 'code', lang: m[1] || '', content: m[2].trim() })
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push({ type: 'text', content: text.slice(last) })
  return parts.length ? parts : [{ type: 'text', content: text }]
}

// SIA's replies contain text taken from web pages and emails, so EVERYTHING is HTML-escaped
// first; only the small markdown subset below is turned back into tags.
function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
}

const isRow = (l) => /^\s*\|.*\|\s*$/.test(l)
const isSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l)
const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()))

export function renderText(text) {
  const lines = escapeHtml(text).split('\n')
  const out = []
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]
    if (isRow(l) && i + 1 < lines.length && isSep(lines[i + 1])) {          // table
      const head = cells(l); i += 1
      const body = []
      while (i + 1 < lines.length && isRow(lines[i + 1])) body.push(cells(lines[++i]))
      out.push('<div class="md-table-wrap"><table class="md-table"><thead><tr>'
        + head.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>'
        + body.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('')
        + '</tbody></table></div>')
      continue
    }
    if (/^\s*([-*•])\s+/.test(l) || /^\s*\d+[.)]\s+/.test(l)) {              // list
      const ordered = /^\s*\d+[.)]\s+/.test(l)
      const items = []
      i -= 1
      while (i + 1 < lines.length && (ordered ? /^\s*\d+[.)]\s+/ : /^\s*([-*•])\s+/).test(lines[i + 1])) {
        items.push(inline(lines[++i].replace(/^\s*([-*•]|\d+[.)])\s+/, '')))
      }
      const tag = ordered ? 'ol' : 'ul'
      out.push(`<${tag} class="md-list">` + items.map(it => `<li>${it}</li>`).join('') + `</${tag}>`)
      continue
    }
    const h = l.match(/^(#{1,4})\s+(.*)$/)
    if (h) { out.push(`<div class="md-h md-h${h[1].length}">${inline(h[2])}</div>`); continue }
    out.push(inline(l) + '<br/>')
  }
  return out.join('').replace(/(<br\/>)+$/, '')
}

const CodeBlock = memo(function CodeBlock({ lang, content }) {
  const [copied, setCopied] = React.useState(false)
  const copy = () => {
    navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <div className="code-block">
      <div className="code-header">
        <span className="code-lang">{lang || 'code'}</span>
        <button className="code-copy" onClick={copy}>{copied ? '✓ Copied' : 'Copy'}</button>
      </div>
      <pre className="code-pre"><code>{content}</code></pre>
    </div>
  )
})

export default memo(function MessageBubble({ message }) {
  const isUser = message.role === 'user'
  const parts  = parseContent(message.content || '')

  return (
    <motion.div
      className={`msg-row ${isUser ? 'msg-row--user' : 'msg-row--ai'}`}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16,1,0.3,1] }}
    >
      {!isUser && (
        <div className="msg-avatar msg-avatar--ai">
          <span>S</span>
        </div>
      )}

      <div className={`msg-bubble ${isUser ? 'msg-bubble--user' : 'msg-bubble--ai'}`}>
        {parts.map((p, i) =>
          p.type === 'code'
            ? <CodeBlock key={i} lang={p.lang} content={p.content} />
            : <div
                key={i}
                className="msg-text"
                dangerouslySetInnerHTML={{ __html: renderText(p.content) }}
              />
        )}

        {message.action && (
          <div className="msg-action-badge">
            <span className="action-dot" />
            {message.action}
          </div>
        )}
      </div>

      {isUser && (
        <div className="msg-avatar msg-avatar--user">
          <span>V</span>
        </div>
      )}
    </motion.div>
  )
})
