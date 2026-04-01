import { useState, useEffect, useCallback, useRef } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api } from '../api'
import { fmtSize } from '../utils/formatters'
import LoadingSpinner from '../components/common/LoadingSpinner'
import Modal from '../components/common/Modal'
import { useI18n } from '../i18n/I18nContext'

function fmtDate(iso) {
  if (!iso) return '--'
  return iso.slice(0, 16).replace('T', ' ')
}

// Only coaching agents that serve the app's users
const COACHING_AGENTS = new Set(['main-coach', 'run-coach', 'swim-coach', 'bike-coach', 'nutrition-coach', 'data-pipeline'])
const AGENT_LABELS = {
  'main-coach': 'Main Coach', 'run-coach': 'Running Coach', 'swim-coach': 'Swimming Coach',
  'bike-coach': 'Cycling Coach', 'nutrition-coach': 'Nutrition Coach', 'data-pipeline': 'Data Pipeline',
}
const AGENT_ICONS = {
  'main-coach': '\uD83C\uDFC6', 'run-coach': '\uD83C\uDFC3', 'swim-coach': '\uD83C\uDFCA',
  'bike-coach': '\uD83D\uDEB4', 'nutrition-coach': '\uD83C\uDF4E', 'data-pipeline': '\uD83D\uDD27',
}

/** Get a display name for a session */
function sessionName(s) {
  if (s.chat_title) return s.chat_title
  if (s.context_key) {
    const text = s.context_key.length > 55 ? s.context_key.slice(0, 55) + '...' : s.context_key
    return text
  }
  return s.agent_name || s.session_uuid?.slice(0, 12)
}

function PathCell({ path }) {
  const ref = useRef(null)
  if (!path) return null
  const filename = path.split('/').pop() || path
  const display = filename.length > 24 ? filename.slice(0, 8) + '\u2026' + filename.slice(-12) : filename
  return (
    <span ref={ref} title={path}
      style={{ fontFamily: 'monospace', fontSize: 10, color: 'var(--text-dim)', cursor: 'pointer', whiteSpace: 'nowrap' }}
      onClick={() => {
        navigator.clipboard.writeText(path)
        const el = ref.current
        if (el) { const orig = el.textContent; el.textContent = 'copied!'; el.style.color = 'var(--green)'; setTimeout(() => { el.textContent = orig; el.style.color = 'var(--text-dim)' }, 1200) }
      }}
    >{display}</span>
  )
}

export default function SessionsPage() {
  const { t } = useI18n()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [transcript, setTranscript] = useState(null)
  const [transcriptLoading, setTranscriptLoading] = useState(false)
  const [viewUuid, setViewUuid] = useState(null)
  const [viewTitle, setViewTitle] = useState('')
  const [expanded, setExpanded] = useState(new Set())

  const load = useCallback(async () => {
    try {
      const data = await api('/api/sessions')
      // Include coaching agents + sub-agents whose parent is a coaching session
      const coachingUuids = new Set(data.filter(s => COACHING_AGENTS.has(s.agent_name) && !s.parent_session).map(s => s.session_uuid))
      const filtered = data.filter(s =>
        COACHING_AGENTS.has(s.agent_name) ||
        (s.parent_session && coachingUuids.has(s.parent_session))
      )
      setSessions(filtered)
    } catch { setSessions([]) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    window.addEventListener('coach-data-update', load)
    return () => window.removeEventListener('coach-data-update', load)
  }, [load])

  async function openTranscript(uuid, title) {
    setViewUuid(uuid)
    setViewTitle(title || '')
    setTranscriptLoading(true)
    try {
      const data = await api(`/api/sessions/${uuid}/transcript`)
      setTranscript(Array.isArray(data) ? data : data.messages || [])
    } catch { setTranscript([]) }
    setTranscriptLoading(false)
  }

  function toggleExpand(uuid) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(uuid) ? next.delete(uuid) : next.add(uuid)
      return next
    })
  }

  const [confirmDeleteId, setConfirmDeleteId] = useState(null)

  async function handleDelete(uuid) {
    try {
      const res = await api(`/api/sessions/${uuid}`, { method: 'DELETE' })
      const deletedSubs = new Set(res.deleted_subagents || [])
      setSessions(prev => prev.filter(s =>
        s.session_uuid !== uuid && !deletedSubs.has(s.session_uuid) && s.parent_session !== uuid
      ))
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch (err) { alert(err.message) }
    setConfirmDeleteId(null)
  }

  if (loading) return <LoadingSpinner />

  // Build parent→children map
  const childrenOf = {}
  sessions.forEach(s => {
    if (s.parent_session) {
      if (!childrenOf[s.parent_session]) childrenOf[s.parent_session] = []
      childrenOf[s.parent_session].push(s)
    }
  })

  // UUID → session map for parent lookups
  const byUuid = {}
  sessions.forEach(s => { byUuid[s.session_uuid] = s })

  // Group: parents as top-level, sub-agents also in their specialist section
  const grouped = {}
  sessions.forEach(s => {
    if (s.parent_session) {
      const key = s.agent_name || 'other'
      if (!grouped[key]) grouped[key] = []
      grouped[key].push({ ...s, _isSub: true })
    } else {
      const key = s.agent_name || 'other'
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(s)
    }
  })
  const agentOrder = ['main-coach', 'run-coach', 'bike-coach', 'swim-coach', 'nutrition-coach', 'data-pipeline']
  const sortedGroups = Object.entries(grouped).sort(([a], [b]) => {
    const ia = agentOrder.indexOf(a), ib = agentOrder.indexOf(b)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })

  const totalCount = sessions.length

  return (
    <div className="page-sessions">
      <h2 style={{ margin: '0 0 4px' }}>Coaching Sessions</h2>
      <p className="text-dim text-sm" style={{ margin: '0 0 16px' }}>
        {totalCount} session{totalCount !== 1 ? 's' : ''} from coaching agents
      </p>

      {totalCount === 0 && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <p className="text-dim">No coaching sessions yet.</p>
          <p className="text-dim text-sm">Sessions are created when insights are generated or the coach analyzes your data.</p>
        </div>
      )}

      {sortedGroups.map(([agentName, agentSessions]) => {
        const allMsgs = agentSessions.reduce((n, s) => n + (s.message_count || 0), 0)

        return (
          <div key={agentName} className="card" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <span style={{ fontSize: 16 }}>{AGENT_ICONS[agentName] || '\uD83E\uDD16'}</span>
              <span style={{ fontWeight: 600, fontSize: 14 }}>{AGENT_LABELS[agentName] || agentName}</span>
              <span className="text-dim text-xs">
                {agentSessions.length} session{agentSessions.length !== 1 ? 's' : ''}
                {' \u00B7 '}
                {allMsgs} messages
              </span>
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Session</th>
                    <th>Source</th>
                    <th>Messages</th>
                    <th>Size</th>
                    <th>Last Active</th>
                    <th>File</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {agentSessions.map(s => {
                    const isSub = s._isSub
                    const children = childrenOf[s.session_uuid] || []
                    const hasChildren = children.length > 0
                    const isExpanded = expanded.has(s.session_uuid)
                    const name = sessionName(s)

                    // Source label + parent name for tooltip
                    let sourceLabel = 'direct'
                    let sourceTitle = ''
                    if (isSub) {
                      sourceLabel = s.source === 'chat' ? '\uD83D\uDCAC chat' : '\uD83D\uDCA1 insight'
                      const parentName = s.parent_name || sessionName(byUuid[s.parent_session] || {})
                      sourceTitle = parentName ? `From: ${parentName}` : 'View parent session'
                    } else if (s.agent_name === 'main-coach' && s.chat_title) {
                      sourceLabel = '\uD83D\uDCAC chat'
                    }

                    return [
                      <tr key={s.session_uuid} className="clickable"
                        style={{ cursor: 'pointer', background: isExpanded ? 'var(--bg-3)' : undefined }}
                        onClick={() => hasChildren ? toggleExpand(s.session_uuid) : openTranscript(s.session_uuid, name)}>
                        <td style={{ width: 20, fontSize: 11, color: 'var(--text-dim)' }}>
                          {hasChildren ? (isExpanded ? '\u25BC' : '\u25B6') : '\u25B6'}
                        </td>
                        <td style={{ fontSize: 12, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'pointer' }}
                          dir="auto" title={name}>
                          {isSub && <span title="Delegated sub-agent" style={{ marginInlineEnd: 4, fontSize: 10, opacity: 0.6 }}>{'\u21B3'}</span>}
                          {name}
                          {hasChildren && (
                            <span className="text-dim text-xs" style={{ marginInlineStart: 6 }}>
                              +{children.length} sub
                            </span>
                          )}
                        </td>
                        <td style={{ fontSize: 10, whiteSpace: 'nowrap', position: 'relative' }}
                          onClick={isSub && s.parent_session ? (e) => {
                            e.stopPropagation()
                            const parent = byUuid[s.parent_session]
                            openTranscript(s.parent_session, parent ? sessionName(parent) : '')
                          } : undefined}>
                          <span className={isSub && s.parent_session ? 'source-link' : 'text-dim'}
                            style={isSub && s.parent_session ? { cursor: 'pointer', padding: '4px 8px', borderRadius: 4, display: 'inline-block' } : undefined}>
                            {sourceLabel}
                            {isSub && sourceTitle && (
                              <span className="source-tooltip">{sourceTitle}</span>
                            )}
                          </span>
                        </td>
                        <td>{s.message_count || '--'}</td>
                        <td className="text-dim" style={{ whiteSpace: 'nowrap' }}>{s.file_size ? fmtSize(s.file_size) : '--'}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(s.last_used_at || s.created_at)}</td>
                        <td onClick={e => e.stopPropagation()}><PathCell path={s.file_path} /></td>
                        <td onClick={e => e.stopPropagation()}>
                          <button
                            className={`btn btn-sm btn-red${confirmDeleteId === s.session_uuid ? ' btn-confirm' : ''}`}
                            onClick={() => {
                              if (confirmDeleteId === s.session_uuid) {
                                handleDelete(s.session_uuid)
                              } else {
                                setConfirmDeleteId(s.session_uuid)
                                setTimeout(() => setConfirmDeleteId(prev => prev === s.session_uuid ? null : prev), 3000)
                              }
                            }}
                          >
                            {confirmDeleteId === s.session_uuid ? t('confirm') + '?' : t('del')}
                          </button>
                        </td>
                      </tr>,
                      // Expanded children (for parent sessions with sub-agents)
                      ...(isExpanded ? [
                        <tr key={`${s.session_uuid}-main`} className="clickable"
                          style={{ cursor: 'pointer', background: 'var(--bg-1)' }}
                          onClick={() => openTranscript(s.session_uuid, name)}>
                          <td></td>
                          <td colSpan={2} style={{ fontSize: 11, paddingInlineStart: 16 }}>
                            <span style={{ opacity: 0.5, marginInlineEnd: 6 }}>{'\u251C'}</span>
                            <span style={{ fontSize: 12 }}>{AGENT_ICONS[agentName] || '\uD83E\uDD16'}</span>
                            {' '}
                            <span>{AGENT_LABELS[agentName] || agentName}</span>
                            <span className="text-dim" style={{ marginInlineStart: 6 }}>({s.message_count || 0} msgs)</span>
                          </td>
                          <td colSpan={4} style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                            {'\u25B6'} view transcript
                          </td>
                        </tr>,
                        ...children.map((c, ci) => {
                          const isLast = ci === children.length - 1
                          const childName = sessionName(c)
                          return (
                            <tr key={c.session_uuid} className="clickable"
                              style={{ cursor: 'pointer', background: 'var(--bg-1)' }}
                              onClick={() => openTranscript(c.session_uuid, childName)}>
                              <td></td>
                              <td colSpan={2} style={{ fontSize: 11, paddingInlineStart: 16 }} dir="auto">
                                <span style={{ opacity: 0.5, marginInlineEnd: 6 }}>{isLast ? '\u2514' : '\u251C'}</span>
                                <span style={{ fontSize: 12 }}>{AGENT_ICONS[c.agent_name] || '\uD83E\uDD16'}</span>
                                {' '}
                                <span>{AGENT_LABELS[c.agent_name] || c.agent_name}</span>
                                <span className="text-dim" style={{ marginInlineStart: 6 }}>{childName}</span>
                                <span className="text-dim" style={{ marginInlineStart: 4 }}>({c.message_count || 0} msgs)</span>
                              </td>
                              <td colSpan={4} style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                                {'\u25B6'} view transcript
                              </td>
                            </tr>
                          )
                        })
                      ] : [])
                    ]
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}

      <Modal open={viewUuid != null} onClose={() => { setViewUuid(null); setTranscript(null); setViewTitle('') }}
        title={viewTitle || 'Session Transcript'} wide>
        {transcriptLoading ? <LoadingSpinner /> : (
          <div className="transcript" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
            {(!transcript || transcript.length === 0) && <p className="text-dim text-sm">No messages in this session.</p>}
            {transcript?.filter(msg => {
              if (msg.role === 'tool') return false
              if (msg.role === 'assistant') {
                if (typeof msg.content === 'string' && !msg.content.trim()) return false
                if (Array.isArray(msg.content) && msg.content.length === 0) return false
              }
              return true
            }).map((msg, i) => {
              const role = msg.role || msg.type || 'system'
              const cssRole = role === 'human' ? 'user' : role
              let text = typeof msg.content === 'string' ? msg.content
                : Array.isArray(msg.content) ? msg.content.filter(b => b.type === 'text').map(b => b.text).join('\n')
                : JSON.stringify(msg.content, null, 2)
              if (!text.trim()) return null
              return (
                <div key={i} className={`transcript-msg ${cssRole}`}>
                  <div className="transcript-role">{role === 'human' ? 'user' : role}</div>
                  <div dir="auto" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(text)) }} />
                </div>
              )
            })}
          </div>
        )}
      </Modal>
    </div>
  )
}
