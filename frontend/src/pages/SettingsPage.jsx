import { useState, useEffect, useCallback, useRef } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api } from '../api'
import { fmtSize, fmtDateNice } from '../utils/formatters'
import { useI18n } from '../i18n/I18nContext'
import LoadingSpinner from '../components/common/LoadingSpinner'
import Modal from '../components/common/Modal'
import ConfirmDialog from '../components/common/ConfirmDialog'

export default function SettingsPage({ embedded = false }) {
  const { t } = useI18n()
  const [agents, setAgents] = useState([])
  const [unmatchedSessions, setUnmatchedSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedAgents, setExpandedAgents] = useState(new Set())
  const [viewDef, setViewDef] = useState(null)
  const [transcriptUuid, setTranscriptUuid] = useState(null)
  const [transcript, setTranscript] = useState(null)
  const [transcriptLoading, setTranscriptLoading] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null)
  const [confirmMessage, setConfirmMessage] = useState('')
  const transcriptEndRef = useRef(null)
  const [viewDefRaw, setViewDefRaw] = useState(false)

  // Coach Memory state
  const [memories, setMemories] = useState([])
  const [newMemory, setNewMemory] = useState('')
  const [editingMemId, setEditingMemId] = useState(null)
  const [editingMemText, setEditingMemText] = useState('')

  // Agent model setting
  const [agentModel, setAgentModel] = useState('')
  const [modelSaving, setModelSaving] = useState(false)

  const fetchMemories = useCallback(async () => {
    try {
      const data = await api('/api/memory')
      setMemories(data)
    } catch { /* ignore */ }
  }, [])

  const fetchAgents = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api('/api/agents')
      setAgents(data.agents || [])
      setUnmatchedSessions(data.unmatched_sessions || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAgents(); fetchMemories()
    api('/api/admin/settings').then(s => setAgentModel(s.agent_model || '')).catch(() => {})
  }, [fetchAgents, fetchMemories])

  // Auto-refresh agents when insight generation finishes
  useEffect(() => {
    let active = true
    let timer = null

    async function pollUntilDone() {
      while (active) {
        try {
          const r = await fetch('/api/insights/status')
          const data = await r.json()
          if (!data.running) {
            fetchAgents()
            return // done, stop polling
          }
        } catch { /* ignore */ }
        await new Promise(r => setTimeout(r, 3000))
      }
    }

    // On mount: check once, if running start polling
    fetch('/api/insights/status').then(r => r.json()).then(data => {
      if (active && data.running) pollUntilDone()
    }).catch(err => console.error('Failed to load:', err))

    // Listen for import event (fired by ImportModal)
    function onInsightsStarted() { if (active) pollUntilDone() }
    window.addEventListener('insights-started', onInsightsStarted)

    return () => {
      active = false
      if (timer) clearTimeout(timer)
      window.removeEventListener('insights-started', onInsightsStarted)
    }
  }, [fetchAgents])

  function copyText(text, e) {
    const el = e.currentTarget
    navigator.clipboard.writeText(text).then(() => {
      el.classList.add('uuid-copied')
      const orig = el.textContent
      el.textContent = 'copied!'
      setTimeout(() => { el.classList.remove('uuid-copied'); el.textContent = orig }, 1200)
    })
  }

  async function saveAgentModel(model) {
    setModelSaving(true)
    try {
      await api('/api/admin/settings', { method: 'PATCH', body: JSON.stringify({ agent_model: model }) })
      setAgentModel(model)
    } catch { /* ignore */ }
    finally { setModelSaving(false) }
  }

  async function addMemory() {
    if (!newMemory.trim()) return
    await api('/api/memory', { method: 'POST', body: JSON.stringify({ content: newMemory.trim() }) })
    setNewMemory('')
    fetchMemories()
  }

  async function saveMemoryEdit(id) {
    if (!editingMemText.trim()) return
    await api(`/api/memory/${id}`, { method: 'PUT', body: JSON.stringify({ content: editingMemText.trim() }) })
    setEditingMemId(null)
    fetchMemories()
  }

  async function deleteMemory(id) {
    await api(`/api/memory/${id}`, { method: 'DELETE' })
    fetchMemories()
  }

  async function viewTranscript(uuid) {
    setTranscriptUuid(uuid)
    setTranscriptLoading(true)
    setTranscript(null)
    try {
      const data = await api(`/api/sessions/${uuid}/transcript`)
      setTranscript(Array.isArray(data) ? data : data.messages || [])
    } catch (e) {
      setTranscript([])
    } finally {
      setTranscriptLoading(false)
      // Auto-scroll to bottom after render
      setTimeout(() => {
        transcriptEndRef.current?.scrollIntoView({ behavior: 'auto' })
      }, 100)
    }
  }

  function requestDeleteSession(uuid, filePath) {
    setConfirmMessage(`Delete session?\n\nFile: ${filePath}`)
    setConfirmAction(() => async () => {
      try {
        await api(`/api/sessions/${uuid}`, { method: 'DELETE' })
        fetchAgents()
        window.dispatchEvent(new CustomEvent('coach-data-update'))
      } catch (e) {
        alert('Error deleting session: ' + e.message)
      }
    })
    setConfirmOpen(true)
  }

  function requestDeleteAllUnmatched() {
    setConfirmMessage(`Delete all ${unmatchedSessions.length} non-agent sessions?\n\nThese are CLI development sessions not related to coaching agents.`)
    setConfirmAction(() => async () => {
      for (const s of unmatchedSessions) {
        try {
          await api(`/api/sessions/${s.session_uuid}`, { method: 'DELETE' })
        } catch (e) { /* continue */ }
      }
      fetchAgents()
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    })
    setConfirmOpen(true)
  }

  async function handleConfirm() {
    setConfirmOpen(false)
    if (confirmAction) await confirmAction()
    setConfirmAction(null)
  }

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">Error: {error}</div>

  const totalAgentSessions = agents.reduce((sum, a) => sum + a.sessions.length, 0)

  return (
    <>
      {!embedded && <h1 className="page-title">{t('page_agents')}</h1>}

      <p className="text-dim" style={{ marginBottom: 16 }}>
        {agents.length} {t('agents_defined')}, {totalAgentSessions} {t('agents_coaching_sessions', totalAgentSessions !== 1 ? 's' : '')}
        {unmatchedSessions.length > 0 && (
          <span> | {unmatchedSessions.length} {t('agents_unrelated_sessions')}</span>
        )}
      </p>

      {/* Agent Cards */}
      {agents.map((agent) => (
        <div key={agent.name} className="card mb-20" style={{ padding: 0 }}>
          <div
            className="clickable"
            style={{ padding: '12px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
            onClick={() => setExpandedAgents(prev => {
              const next = new Set(prev)
              next.has(agent.name) ? next.delete(agent.name) : next.add(agent.name)
              return next
            })}
          >
            <div>
              <strong style={{ fontSize: 15 }}>{agent.name}</strong>
              <span className="text-dim text-sm" style={{ marginLeft: 12 }}>
                {(() => {
                  const parts = []
                  if (agent.sessions.length > 0) parts.push(`${agent.sessions.length} session${agent.sessions.length !== 1 ? 's' : ''}`)
                  if (agent.subagent_transcript_count > 0) parts.push(`${agent.subagent_transcript_count} spawned sub-agents`)
                  if (agent.transcript_appearances?.length > 0 && !agent.subagent_transcript_count) parts.push(`invoked ${agent.transcript_appearances.length}x`)
                  return parts.length > 0 ? parts.join(' · ') : '0 sessions'
                })()}
              </span>
              {agent.delegates_to?.length > 0 && (
                <span className="text-dim text-xs" style={{ marginLeft: 8 }}>
                  delegates to: {agent.delegates_to.join(', ')}
                </span>
              )}
              {agent.delegated_by?.length > 0 && (
                <span className="agent-sub-badge">sub-agent</span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                className="btn btn-sm"
                onClick={(e) => { e.stopPropagation(); setViewDef(agent) }}
              >
                {t('agents_view_definition')}
              </button>
              <span style={{ color: 'var(--text-dim)', fontSize: 18 }}>
                {expandedAgents.has(agent.name) ? '\u25B2' : '\u25BC'}
              </span>
            </div>
          </div>

          {expandedAgents.has(agent.name) && (
            <div style={{ borderTop: '1px solid var(--border)', padding: '12px 16px' }}>
              <div className="text-dim text-xs" style={{ marginBottom: 8 }}>
                {t('agents_file')}:{' '}
                <span
                  className="uuid-cell"
                  onClick={(e) => copyText(agent.file_path, e)}
                  title="Click to copy path"
                  style={{ cursor: 'pointer', fontFamily: 'monospace' }}
                >
                  {agent.file_path}
                </span>
              </div>

              {/* Main sessions (own JSONL files) */}
              {agent.sessions.length > 0 && (
                <div className="table-scroll" style={{ maxHeight: 300 }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{t('agents_session_id')}</th>
                        <th>{t('agents_file_col')}</th>
                        <th>{t('agents_last_used')}</th>
                        <th>{t('agents_messages')}</th>
                        <th>{t('agents_size')}</th>
                        <th>{t('agents_actions')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {agent.sessions.map((s) => (
                        <tr key={s.session_uuid}>
                          <td>
                            <span
                              className="uuid-cell"
                              onClick={(e) => copyText(s.session_uuid, e)}
                              title={`${t('agents_click_copy')}: ${s.session_uuid}`}
                              style={{ cursor: 'pointer', fontFamily: 'monospace', fontSize: 12 }}
                            >
                              {(s.session_uuid || '').slice(0, 12)}...
                            </span>
                          </td>
                          <td>
                            <span
                              className="uuid-cell"
                              onClick={(e) => copyText(s.file_path, e)}
                              title={`${t('agents_click_copy')}: ${s.file_path}`}
                              style={{ cursor: 'pointer', fontFamily: 'monospace', fontSize: 11, color: 'var(--text-dim)' }}
                            >
                              .../{(s.session_uuid || '').slice(0, 8)}.jsonl
                            </span>
                          </td>
                          <td>{fmtDateNice(s.last_used_at)}</td>
                          <td>{s.message_count ?? '-'}</td>
                          <td>{fmtSize(s.file_size)}</td>
                          <td>
                            <div style={{ display: 'flex', gap: 4 }}>
                              <button className="btn btn-sm" onClick={() => viewTranscript(s.session_uuid)}>{t('agents_view')}</button>
                              <button className="btn btn-sm btn-red" onClick={() => requestDeleteSession(s.session_uuid, s.file_path)}>{t('delete')}</button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Sub-agent transcripts spawned by this agent */}
              {agent.subagent_sessions?.length > 0 && (
                <div style={{ marginTop: agent.sessions.length > 0 ? 12 : 0 }}>
                  <div className="text-dim text-xs" style={{ marginBottom: 6 }}>
                    {t('agents_spawned_sub')} ({agent.subagent_sessions.length})
                  </div>
                  <div className="table-scroll" style={{ maxHeight: 300 }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('agents_agent_id')}</th>
                          <th>{t('agents_type')}</th>
                          <th>{t('agents_file_col')}</th>
                          <th>{t('agents_last_used')}</th>
                          <th>{t('agents_messages')}</th>
                          <th>{t('agents_size')}</th>
                          <th>{t('agents_actions')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {agent.subagent_sessions.map((s) => (
                          <tr key={s.session_uuid}>
                            <td>
                              <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-dim)' }}>
                                {s.session_uuid.replace('agent-', '').slice(0, 12)}
                              </span>
                            </td>
                            <td>
                              <span className="agent-sub-badge">{s.agent_type || 'unknown'}</span>
                            </td>
                            <td>
                              <span
                                className="uuid-cell"
                                onClick={(e) => copyText(s.file_path, e)}
                                title={`Click to copy: ${s.file_path}`}
                                style={{ cursor: 'pointer', fontFamily: 'monospace', fontSize: 10, color: 'var(--text-dim)' }}
                              >
                                .../subagents/{s.session_uuid}.jsonl
                              </span>
                            </td>
                            <td>{fmtDateNice(s.last_used_at)}</td>
                            <td>{s.message_count ?? '-'}</td>
                            <td>{fmtSize(s.file_size)}</td>
                            <td>
                              <button className="btn btn-sm" onClick={() => viewTranscript(s.session_uuid)}>{t('agents_view')}</button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Sub-agent cards: show where this agent was invoked */}
              {agent.delegated_by?.length > 0 && agent.transcript_appearances?.length > 0 && (
                <p className="text-dim text-sm" style={{ marginTop: 8 }}>
                  Invoked {agent.transcript_appearances.length} time{agent.transcript_appearances.length !== 1 ? 's' : ''} as
                  sub-agent — transcripts stored inside {[...new Set(agent.transcript_appearances.map(s => s.parent_agent))].join(', ')}'s session directory.
                </p>
              )}

              {/* Empty state */}
              {agent.sessions.length === 0 && !agent.subagent_sessions?.length && !agent.transcript_appearances?.length && (
                <p className="text-dim text-sm">
                  {agent.delegated_by?.length > 0
                    ? `${t('agents_sub_no_transcripts')} ${agent.delegated_by.join(' or ')}.`
                    : t('agents_no_sessions')}
                </p>
              )}
            </div>
          )}
        </div>
      ))}

      {/* Unmatched sessions (non-agent) */}
      {unmatchedSessions.length > 0 && (
        <div className="card mb-20" style={{ padding: 0 }}>
          <div
            className="clickable"
            style={{ padding: '12px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
            onClick={() => setExpandedAgents(prev => {
              const next = new Set(prev)
              next.has('_unmatched') ? next.delete('_unmatched') : next.add('_unmatched')
              return next
            })}
          >
            <div>
              <strong style={{ fontSize: 15, color: 'var(--text-dim)' }}>{t('agents_other_sessions')}</strong>
              <span className="text-dim text-sm" style={{ marginLeft: 12 }}>
                {unmatchedSessions.length} {t('agents_sessions_not_coaching')}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                className="btn btn-sm btn-red"
                onClick={(e) => { e.stopPropagation(); requestDeleteAllUnmatched() }}
              >
                {t('agents_delete_all')}
              </button>
              <span style={{ color: 'var(--text-dim)', fontSize: 18 }}>
                {expandedAgents.has('_unmatched') ? '\u25B2' : '\u25BC'}
              </span>
            </div>
          </div>

          {expandedAgents.has('_unmatched') && (
            <div style={{ borderTop: '1px solid var(--border)', padding: '12px 16px' }}>
              <div className="table-scroll" style={{ maxHeight: 400 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('agents_session_id')}</th>
                      <th>{t('agents_slug')}</th>
                      <th>{t('agents_file_col')}</th>
                      <th>{t('agents_last_used')}</th>
                      <th>{t('agents_messages')}</th>
                      <th>{t('agents_size')}</th>
                      <th>{t('agents_actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {unmatchedSessions.map((s) => (
                      <tr key={s.session_uuid}>
                        <td>
                          <span
                            className="uuid-cell"
                            onClick={(e) => copyText(s.session_uuid, e)}
                            title={`Click to copy: ${s.session_uuid}`}
                            style={{ cursor: 'pointer', fontFamily: 'monospace', fontSize: 12 }}
                          >
                            {(s.session_uuid || '').slice(0, 12)}...
                          </span>
                        </td>
                        <td className="text-dim">{s.slug || '-'}</td>
                        <td>
                          <span
                            className="uuid-cell"
                            onClick={(e) => copyText(s.file_path, e)}
                            title={`Click to copy: ${s.file_path}`}
                            style={{ cursor: 'pointer', fontFamily: 'monospace', fontSize: 11, color: 'var(--text-dim)' }}
                          >
                            .../{(s.session_uuid || '').slice(0, 8)}.jsonl
                          </span>
                        </td>
                        <td>{fmtDateNice(s.last_used_at)}</td>
                        <td>{s.message_count ?? '-'}</td>
                        <td>{fmtSize(s.file_size)}</td>
                        <td>
                          <div style={{ display: 'flex', gap: 4 }}>
                            <button className="btn btn-sm" onClick={() => viewTranscript(s.session_uuid)}>{t('agents_view')}</button>
                            <button className="btn btn-sm btn-red" onClick={() => requestDeleteSession(s.session_uuid, s.file_path)}>{t('delete')}</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Agent Definition Modal */}
      <Modal
        open={viewDef != null}
        onClose={() => { setViewDef(null); setViewDefRaw(false) }}
        title={viewDef ? `Agent: ${viewDef.name}` : ''}
        wide
      >
        {viewDef && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div className="text-dim text-xs" style={{ fontFamily: 'monospace' }}>
                {viewDef.file_path}
              </div>
              <button className="btn btn-sm" onClick={() => setViewDefRaw(!viewDefRaw)}>
                {viewDefRaw ? 'Rendered' : 'Raw'}
              </button>
            </div>
            {viewDefRaw ? (
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, background: 'var(--bg-1)', padding: 12, borderRadius: 'var(--radius)', maxHeight: '70vh', overflow: 'auto' }}>
                {viewDef.definition || ''}
              </pre>
            ) : (
              <div
                className="markdown-body"
                dir="auto"
                dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(viewDef.definition || '')) }}
              />
            )}
          </div>
        )}
      </Modal>

      {/* Transcript Modal */}
      <Modal
        open={transcriptUuid != null}
        onClose={() => { setTranscriptUuid(null); setTranscript(null) }}
        title={`Session ${(transcriptUuid || '').slice(0, 12)}...`}
        wide
      >
        {transcriptLoading ? (
          <LoadingSpinner message={t('agents_loading_transcript')} />
        ) : !transcript?.length ? (
          <p className="text-dim">{t('agents_no_messages')}</p>
        ) : (
          <div className="transcript" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
            {(() => {
              const filtered = transcript.filter((msg) => {
                if (msg.role === 'assistant') {
                  if (typeof msg.content === 'string' && msg.content.trim() === '') return false
                  if (Array.isArray(msg.content) && msg.content.length === 0) return false
                }
                return true
              })
              const collapsed = []
              for (const msg of filtered) {
                if (msg.role === 'tool') {
                  const prev = collapsed[collapsed.length - 1]
                  if (prev && prev._type === 'tool_group') {
                    prev.messages.push(msg)
                  } else {
                    collapsed.push({ _type: 'tool_group', messages: [msg] })
                  }
                } else {
                  collapsed.push(msg)
                }
              }
              return collapsed.map((item, i) => {
                if (item._type === 'tool_group') {
                  return <CollapsedToolMessages key={i} messages={item.messages} />
                }
                return <TranscriptMessage key={i} msg={item} />
              })
            })()}
            <div ref={transcriptEndRef} />
          </div>
        )}
      </Modal>
      {/* Agent Model */}
      <div className="card mt-20" style={{ padding: '16px' }}>
        <h3 style={{ margin: '0 0 8px' }}>{t('agent_model_title')}</h3>
        <p className="text-sm text-dim mb-12">{t('agent_model_desc')}</p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            className="input-full"
            style={{ width: 220 }}
            value={agentModel}
            onChange={e => saveAgentModel(e.target.value)}
            disabled={modelSaving}
          >
            <option value="">{t('agent_model_default')}</option>
            <option value="claude-opus-4-6">Opus 4.6 (most capable, expensive)</option>
            <option value="claude-sonnet-4-6">Sonnet 4.6 (balanced)</option>
            <option value="claude-haiku-4-5-20251001">Haiku 4.5 (fastest, cheapest)</option>
          </select>
          {modelSaving && <span className="text-dim text-sm">{t('saving')}...</span>}
        </div>
      </div>

      {/* Coach Memory */}
      <div className="card mt-20" style={{ padding: '16px' }}>
        <h3 style={{ margin: '0 0 8px' }}>{t('coach_memory_title')}</h3>
        <p className="text-sm text-dim mb-12">{t('coach_memory_desc')}</p>
        {memories.map(m => (
          <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            {editingMemId === m.id ? (
              <>
                <input className="input-full" style={{ flex: 1 }} value={editingMemText}
                  onChange={e => setEditingMemText(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveMemoryEdit(m.id); if (e.key === 'Escape') setEditingMemId(null) }}
                  dir="auto" autoFocus />
                <button className="btn btn-sm btn-accent" onClick={() => saveMemoryEdit(m.id)}>{t('save')}</button>
                <button className="btn btn-sm" onClick={() => setEditingMemId(null)}>{t('cancel')}</button>
              </>
            ) : (
              <>
                <span dir="auto" style={{ flex: 1 }}>{m.content}</span>
                <button className="btn btn-sm" onClick={() => { setEditingMemId(m.id); setEditingMemText(m.content) }}>{t('edit')}</button>
                <button className="btn btn-sm btn-red" onClick={() => deleteMemory(m.id)}>{t('delete')}</button>
              </>
            )}
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, marginTop: memories.length ? 12 : 0 }}>
          <input className="input-full" style={{ flex: 1 }} value={newMemory}
            onChange={e => setNewMemory(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addMemory() }}
            placeholder={t('coach_memory_placeholder')}
            dir="auto" />
          <button className="btn btn-sm btn-accent" onClick={addMemory} disabled={!newMemory.trim()}>{t('add')}</button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title={t('delete_session')}
        message={confirmMessage}
        onConfirm={handleConfirm}
        onCancel={() => { setConfirmOpen(false); setConfirmAction(null) }}
      />
    </>
  )
}

function CollapsedToolMessages({ messages }) {
  if (messages.length === 1) {
    return <TranscriptMessage msg={messages[0]} />
  }
  return (
    <div className="transcript-msg tool">
      <details>
        <summary style={{ cursor: 'pointer', fontSize: 12 }}>
          Tool Results ({messages.length})
        </summary>
        <div style={{ marginTop: 8 }}>
          {messages.map((msg, i) => {
            const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)
            const truncated = content.length > 200 ? content.slice(0, 200) + '...' : content
            return (
              <pre key={i} style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all', marginBottom: 6, paddingBottom: 6, borderBottom: '1px solid var(--border)' }}>
                {truncated}
              </pre>
            )
          })}
        </div>
      </details>
    </div>
  )
}

function TranscriptMessage({ msg }) {
  const role = msg.role || 'unknown'

  if (role === 'user') {
    const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)
    return (
      <div className="transcript-msg user">
        <div className="transcript-role">User</div>
        <div dir="auto" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(content)) }} />
      </div>
    )
  }

  if (role === 'assistant') {
    let textParts = ''
    let toolCalls = []
    if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (block.type === 'text') textParts += block.text
        else if (block.type === 'tool_use') {
          const inputStr = JSON.stringify(block.input, null, 2)
          const truncInput = inputStr.length > 300 ? inputStr.slice(0, 300) + '...' : inputStr
          toolCalls.push({ name: block.name, input: truncInput })
        }
      }
    } else {
      textParts = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)
    }
    const wordCount = textParts ? textParts.trim().split(/\s+/).filter(Boolean).length : 0
    return (
      <div className="transcript-msg assistant">
        <div className="transcript-role">
          Assistant
          {wordCount > 0 && <span style={{ fontWeight: 'normal', fontSize: 11, color: 'var(--text-dim)', marginLeft: 8 }}>({wordCount} words)</span>}
        </div>
        {textParts && <div dir="auto" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(textParts)) }} />}
        {toolCalls.length > 0 && (
          <div className="transcript-tool-calls">
            {toolCalls.map((tc, i) => (
              <details key={i}>
                <summary>Tool: {tc.name}</summary>
                <pre style={{ fontSize: 11, overflowX: 'auto', padding: 8, background: 'var(--bg)', borderRadius: 4, marginTop: 4 }}>
                  {tc.input}
                </pre>
              </details>
            ))}
          </div>
        )}
      </div>
    )
  }

  if (role === 'tool') {
    const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)
    const truncated = content.length > 200 ? content.slice(0, 200) + '...' : content
    return (
      <div className="transcript-msg tool">
        <div className="transcript-role">Tool Result</div>
        <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{truncated}</pre>
      </div>
    )
  }

  return null
}
