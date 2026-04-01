import { memo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { detectDir } from '../../utils/formatters'

function escapeHtml(s) {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML.replace(/\n/g, '<br>')
}

function fmtMsgTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) +
    ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export default memo(function ChatMessage({ role, content, timestamp }) {
  const body = role === 'assistant'
    ? DOMPurify.sanitize(marked.parse(content || ''))
    : escapeHtml(content || '')

  const dir = detectDir(content)

  return (
    <div className={`chat-msg ${role}`}>
      <div className="chat-msg-header">
        <span className="chat-msg-role">{role}</span>
        {timestamp && <span className="chat-msg-time">{fmtMsgTime(timestamp)}</span>}
      </div>
      <div className="chat-msg-body" dir={dir} dangerouslySetInnerHTML={{ __html: body }} />
    </div>
  )
})
