import { useState, useRef, useEffect, useCallback } from 'react'
import { useAuth } from '../context/AuthContext'
import { useI18n } from '../i18n/I18nContext'
import { api } from '../api'
import Modal from './common/Modal'

export default function UserMenu() {
  const { user, logout, logoutKeepSession, changePassword, switchToUser, savedSessions } = useAuth()
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [view, setView] = useState('menu') // 'menu' | 'password' | 'switch' | 'memory'
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [pwMsg, setPwMsg] = useState('')
  const [pwError, setPwError] = useState(false)
  const [switchError, setSwitchError] = useState('')
  const ref = useRef()

  // Unified memory state — 'all-coaches' uses coach_memory API, others use agent_memory API
  const [memoryScope, setMemoryScope] = useState('all-coaches')
  const [memories, setMemories] = useState([])
  const [newMemory, setNewMemory] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editingText, setEditingText] = useState('')

  // Expanded memory modal
  const [memoryModal, setMemoryModal] = useState(false)
  const [allMemories, setAllMemories] = useState([])
  const [modalFilters, setModalFilters] = useState(null) // null = show all, Set of scope strings
  const [modalEditId, setModalEditId] = useState(null)
  const [modalEditText, setModalEditText] = useState('')
  const [modalNewScope, setModalNewScope] = useState('all-coaches')
  const [modalNewText, setModalNewText] = useState('')

  const fetchMemories = useCallback(async (scope) => {
    const s = scope || memoryScope
    try {
      if (s === 'all-coaches') {
        setMemories(await api('/api/memory'))
      } else {
        setMemories(await api(`/api/memory/agent/${s}`))
      }
    } catch { setMemories([]) }
  }, [memoryScope])

  useEffect(() => {
    if (!open) return
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) closeMenu()
    }
    function handleKey(e) {
      if (e.key === 'Escape') {
        // If memory modal is open, let Modal's own ESC handler close it
        if (document.querySelector('.modal')) return
        e.stopPropagation()
        closeMenu()
      }
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleKey, true) // capture phase to beat Layout's ESC
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey, true)
    }
  }, [open])

  function closeMenu() {
    setOpen(false)
    setView('menu')
    setPwMsg('')
    setSwitchError('')
    setCurrentPw('')
    setNewPw('')
  }

  if (!user) return null
  const initials = (user.display_name || user.username || '?').slice(0, 2).toUpperCase()
  const otherSessions = savedSessions.filter(s => s.username !== user.username)

  async function handleChangePw(e) {
    e.preventDefault()
    setPwMsg('')
    setPwError(false)
    try {
      await changePassword(currentPw, newPw)
      setPwMsg('Password changed')
      setPwError(false)
      setCurrentPw('')
      setNewPw('')
      setTimeout(() => closeMenu(), 1500)
    } catch (err) {
      setPwMsg(err.message)
      setPwError(true)
    }
  }

  async function handleSwitch(session) {
    setSwitchError('')
    try {
      await switchToUser(session)
      closeMenu()
      // Clear session-scoped data to prevent leaking between users
      sessionStorage.clear()
      window.location.reload()
    } catch (err) {
      setSwitchError(err.message)
    }
  }

  async function addMemory() {
    if (!newMemory.trim()) return
    if (memoryScope === 'all-coaches') {
      await api('/api/memory', { method: 'POST', body: JSON.stringify({ content: newMemory.trim() }) })
    } else {
      await api(`/api/memory/agent/${memoryScope}`, { method: 'POST', body: JSON.stringify({ content: newMemory.trim() }) })
    }
    setNewMemory('')
    fetchMemories()
  }

  async function saveEdit(id) {
    if (!editingText.trim()) return
    if (memoryScope === 'all-coaches') {
      await api(`/api/memory/${id}`, { method: 'PUT', body: JSON.stringify({ content: editingText.trim() }) })
    } else {
      await api(`/api/memory/agent/${id}`, { method: 'PUT', body: JSON.stringify({ content: editingText.trim() }) })
    }
    setEditingId(null)
    fetchMemories()
  }

  async function deleteMemory(id) {
    if (memoryScope === 'all-coaches') {
      await api(`/api/memory/${id}`, { method: 'DELETE' })
    } else {
      await api(`/api/memory/agent/${id}`, { method: 'DELETE' })
    }
    fetchMemories()
  }

  // Modal CRUD — operates on allMemories, refreshes via /api/memory/all
  const refreshAllMemories = useCallback(async () => {
    try { setAllMemories(await api('/api/memory/all')) } catch { /* ignore */ }
  }, [])

  async function modalAdd() {
    if (!modalNewText.trim()) return
    if (modalNewScope === 'all-coaches') {
      await api('/api/memory', { method: 'POST', body: JSON.stringify({ content: modalNewText.trim() }) })
    } else {
      await api(`/api/memory/agent/${modalNewScope}`, { method: 'POST', body: JSON.stringify({ content: modalNewText.trim() }) })
    }
    setModalNewText('')
    refreshAllMemories()
  }

  async function modalSaveEdit(id, scope) {
    if (!modalEditText.trim()) return
    if (scope === 'all-coaches') {
      await api(`/api/memory/${id}`, { method: 'PUT', body: JSON.stringify({ content: modalEditText.trim() }) })
    } else {
      await api(`/api/memory/agent/${id}`, { method: 'PUT', body: JSON.stringify({ content: modalEditText.trim() }) })
    }
    setModalEditId(null)
    refreshAllMemories()
  }

  async function modalDelete(id, scope) {
    if (scope === 'all-coaches') {
      await api(`/api/memory/${id}`, { method: 'DELETE' })
    } else {
      await api(`/api/memory/agent/${id}`, { method: 'DELETE' })
    }
    refreshAllMemories()
  }

  return (
    <div className="user-menu-wrapper" ref={ref}>
      <button className="user-avatar" onClick={() => setOpen(o => !o)} title={user.display_name || user.username}>
        {initials}
      </button>
      {open && (
        <div className="user-menu-dropdown" style={view === 'memory' ? { width: 360 } : undefined}>
          <div className="user-menu-name">{user.display_name || user.username}</div>
          <div className="user-menu-role">{user.role} · ID {user.id}</div>
          <hr className="user-menu-divider" />

          {view === 'password' && (
            <form onSubmit={handleChangePw} className="user-menu-pw-form">
              <input type="password" className="input-full input-sm" placeholder="Current password"
                value={currentPw} onChange={e => setCurrentPw(e.target.value)} required autoFocus />
              <input type="password" className="input-full input-sm" placeholder="New password"
                value={newPw} onChange={e => setNewPw(e.target.value)} required />
              {pwMsg && <div className={pwError ? 'text-red text-xs' : 'text-green text-xs'}>{pwMsg}</div>}
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-accent btn-xs" type="submit">{t('save')}</button>
                <button className="btn btn-xs" type="button" onClick={() => setView('menu')}>{t('cancel')}</button>
              </div>
            </form>
          )}

          {view === 'switch' && (
            <div className="user-menu-switch">
              {otherSessions.length === 0 ? (
                <div className="text-dim text-xs" style={{ padding: '4px 0' }}>No other saved sessions</div>
              ) : (
                otherSessions.map(s => (
                  <button key={s.username} className="user-switch-btn" onClick={() => handleSwitch(s)}>
                    <span className="user-switch-avatar">
                      {(s.display_name || s.username || '?').slice(0, 2).toUpperCase()}
                    </span>
                    <span>
                      <span className="user-switch-name">{s.display_name || s.username}</span>
                      <span className="user-switch-role">{s.role}</span>
                    </span>
                  </button>
                ))
              )}
              {switchError && <div className="text-red text-xs">{switchError}</div>}
              <hr className="user-menu-divider" />
              <button className="user-menu-item" onClick={() => { closeMenu(); logoutKeepSession() }}>
                Sign in as different user
              </button>
              <button className="btn btn-xs" type="button" onClick={() => setView('menu')}>Back</button>
            </div>
          )}

          {view === 'memory' && (
            <div style={{ padding: '4px 0' }}>
              <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>{t('coach_memory_title')}</div>
              <div className="text-dim text-xs" style={{ marginBottom: 8 }}>{t('coach_memory_desc')}</div>
              <select className="input-full input-sm" style={{ marginBottom: 8 }}
                value={memoryScope}
                onChange={e => { setMemoryScope(e.target.value); setEditingId(null); fetchMemories(e.target.value) }}>
                <option value="all-coaches">{t('memory_all_coaches')}</option>
                <optgroup label="Coaching">
                  <option value="main-coach">IronCoach (main)</option>
                  <option value="run-coach">Run Coach</option>
                  <option value="swim-coach">Swim Coach</option>
                  <option value="bike-coach">Bike Coach</option>
                  <option value="nutrition-coach">Nutrition Coach</option>
                </optgroup>
                <optgroup label="Development">
                  <option value="frontend-dev">Frontend Dev</option>
                  <option value="backend-dev">Backend Dev</option>
                  <option value="code-simplifier">Code Simplifier</option>
                </optgroup>
                <optgroup label="Review">
                  <option value="security-reviewer">Security Reviewer</option>
                  <option value="frontend-reviewer">Frontend Reviewer</option>
                  <option value="backend-reviewer">Backend Reviewer</option>
                  <option value="data-reviewer">Data Reviewer</option>
                </optgroup>
              </select>
              <div style={{ maxHeight: 250, overflowY: 'auto', marginBottom: 8 }}>
                {memories.map(m => (
                  <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 6, fontSize: 12 }}>
                    {editingId === m.id ? (
                      <>
                        <input className="input-full input-sm" style={{ flex: 1 }} value={editingText}
                          onChange={e => setEditingText(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') saveEdit(m.id); if (e.key === 'Escape') setEditingId(null) }}
                          dir="auto" autoFocus />
                        <button className="btn btn-xs btn-accent" onClick={() => saveEdit(m.id)}>✓</button>
                        <button className="btn btn-xs" onClick={() => setEditingId(null)}>✕</button>
                      </>
                    ) : (
                      <>
                        <span dir="auto" style={{ flex: 1 }}>{m.content}</span>
                        <button className="btn btn-xs" onClick={() => { setEditingId(m.id); setEditingText(m.content) }}>✎</button>
                        <button className="btn btn-xs" style={{ color: 'var(--red)' }} onClick={() => deleteMemory(m.id)}>✕</button>
                      </>
                    )}
                  </div>
                ))}
                {memories.length === 0 && <div className="text-dim text-xs">{t('no_memories')}</div>}
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                <input className="input-full input-sm" style={{ flex: 1 }} value={newMemory}
                  onChange={e => setNewMemory(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') addMemory() }}
                  placeholder={t('coach_memory_placeholder')} dir="auto" />
                <button className="btn btn-xs btn-accent" onClick={addMemory} disabled={!newMemory.trim()}>+</button>
              </div>
              <hr className="user-menu-divider" />
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <button className="btn btn-xs" type="button" onClick={() => setView('menu')}>Back</button>
                <button className="btn btn-xs" type="button" onClick={async () => {
                  try {
                    setAllMemories(await api('/api/memory/all'))
                  } catch { setAllMemories([]) }
                  setModalFilters(null)
                  setMemoryModal(true)
                }}>View All</button>
              </div>
            </div>
          )}

          {view === 'menu' && (
            <>
              <button className="user-menu-item" onClick={() => { setView('memory'); fetchMemories() }}>{t('coach_memory_title')}</button>
              <button className="user-menu-item" onClick={() => setView('password')}>Change Password</button>
              <button className="user-menu-item" onClick={() => setView('switch')}>
                Switch User {otherSessions.length > 0 && <span className="text-dim">({otherSessions.length})</span>}
              </button>
              <button className="user-menu-item user-menu-logout" onClick={logout}>{t('logout')}</button>
            </>
          )}
        </div>
      )}

      {memoryModal && (() => {
        const SCOPE_LABELS = {
          'all-coaches': t('memory_all_coaches'),
          'main-coach': 'IronCoach', 'run-coach': 'Run', 'swim-coach': 'Swim',
          'bike-coach': 'Bike', 'nutrition-coach': 'Nutrition',
          'frontend-dev': 'FE Dev', 'backend-dev': 'BE Dev',
          'code-simplifier': 'Simplify',
          'security-reviewer': 'Security', 'frontend-reviewer': 'FE Review',
          'backend-reviewer': 'BE Review', 'data-reviewer': 'Data Review',
        }
        const ALL_SCOPES = [
          { value: 'all-coaches', label: t('memory_all_coaches') },
          { value: 'main-coach', label: 'IronCoach (main)', group: 'Coaching' },
          { value: 'run-coach', label: 'Run Coach', group: 'Coaching' },
          { value: 'swim-coach', label: 'Swim Coach', group: 'Coaching' },
          { value: 'bike-coach', label: 'Bike Coach', group: 'Coaching' },
          { value: 'nutrition-coach', label: 'Nutrition Coach', group: 'Coaching' },
          { value: 'frontend-dev', label: 'Frontend Dev', group: 'Development' },
          { value: 'backend-dev', label: 'Backend Dev', group: 'Development' },
          { value: 'code-simplifier', label: 'Code Simplifier', group: 'Development' },
          { value: 'security-reviewer', label: 'Security Reviewer', group: 'Review' },
          { value: 'frontend-reviewer', label: 'Frontend Reviewer', group: 'Review' },
          { value: 'backend-reviewer', label: 'Backend Reviewer', group: 'Review' },
          { value: 'data-reviewer', label: 'Data Reviewer', group: 'Review' },
        ]
        const visibleGroups = allMemories.filter(g =>
          g.memories.length > 0 && (!modalFilters || modalFilters.has(g.scope))
        )
        const totalCount = allMemories.reduce((sum, g) => sum + g.memories.length, 0)
        const toggleFilter = (scope) => {
          setModalFilters(prev => {
            if (!prev) return new Set([scope])
            const next = new Set(prev)
            if (next.has(scope)) { next.delete(scope); return next.size === 0 ? null : next }
            next.add(scope)
            return next
          })
        }
        return (
          <Modal title={`${t('coach_memory_title')} (${totalCount})`} onClose={() => setMemoryModal(false)} wide>
            {/* Filter chips */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 12 }}>
              {allMemories.filter(g => g.memories.length > 0).map(g => (
                <button
                  key={g.scope}
                  className={`btn btn-xs${!modalFilters || modalFilters.has(g.scope) ? ' btn-accent' : ''}`}
                  onClick={() => toggleFilter(g.scope)}
                >
                  {SCOPE_LABELS[g.scope] || g.scope} ({g.memories.length})
                </button>
              ))}
              {modalFilters && (
                <button className="btn btn-xs" onClick={() => setModalFilters(null)}>Show All</button>
              )}
            </div>

            {/* Memory list with edit/delete */}
            <div style={{ maxHeight: '50vh', overflowY: 'auto', marginBottom: 12 }}>
              {visibleGroups.length === 0 && <div className="text-dim">{t('no_memories')}</div>}
              {visibleGroups.map(group => (
                <div key={group.scope} style={{ marginBottom: 14 }}>
                  <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4, color: 'var(--accent)' }}>
                    {SCOPE_LABELS[group.scope] || group.scope}
                  </div>
                  {group.memories.map(m => (
                    <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 5, paddingInlineStart: 8, borderInlineStart: '2px solid var(--border)' }}>
                      {modalEditId === m.id ? (
                        <>
                          <input className="input-full input-sm" style={{ flex: 1, fontSize: 12 }} value={modalEditText}
                            onChange={e => setModalEditText(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') modalSaveEdit(m.id, group.scope); if (e.key === 'Escape') setModalEditId(null) }}
                            dir="auto" autoFocus />
                          <button className="btn btn-xs btn-accent" onClick={() => modalSaveEdit(m.id, group.scope)}>✓</button>
                          <button className="btn btn-xs" onClick={() => setModalEditId(null)}>✕</button>
                        </>
                      ) : (
                        <>
                          <span dir="auto" style={{ flex: 1, fontSize: 12 }}>{m.content}</span>
                          <button className="btn btn-xs" onClick={() => { setModalEditId(m.id); setModalEditText(m.content) }}>✎</button>
                          <button className="btn btn-xs" style={{ color: 'var(--red)' }} onClick={() => modalDelete(m.id, group.scope)}>✕</button>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>

            {/* Add new memory */}
            <div style={{ display: 'flex', gap: 4, alignItems: 'center', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <select className="input-sm" style={{ width: 130, flexShrink: 0 }}
                value={modalNewScope} onChange={e => setModalNewScope(e.target.value)}>
                {ALL_SCOPES.filter(s => !s.group).map(s => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
                {['Coaching', 'Development', 'Review'].map(group => (
                  <optgroup key={group} label={group}>
                    {ALL_SCOPES.filter(s => s.group === group).map(s => (
                      <option key={s.value} value={s.value}>{s.label}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <input className="input-full input-sm" style={{ flex: 1 }} value={modalNewText}
                onChange={e => setModalNewText(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') modalAdd() }}
                placeholder={t('coach_memory_placeholder')} dir="auto" />
              <button className="btn btn-xs btn-accent" onClick={modalAdd} disabled={!modalNewText.trim()}>+</button>
            </div>
          </Modal>
        )
      })()}
    </div>
  )
}
