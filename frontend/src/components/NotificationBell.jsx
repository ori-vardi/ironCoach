import { useState, useEffect, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChat } from '../context/ChatContext'
import { useI18n } from '../i18n/I18nContext'
import Modal from './common/Modal'

// Render status badge with appropriate color
function StatusBadge({ status, t }) {
  if (status === 'error') return <span className="notification-done-badge" style={{ color: '#ff5370', background: 'rgba(255,83,112,0.12)' }}>{t('failed')}</span>
  if (status === 'cancelled') return <span className="notification-done-badge" style={{ color: '#ff966c', background: 'rgba(255,150,108,0.12)' }}>{t('cancelled')}</span>
  if (status === 'warning') return <span className="notification-done-badge" style={{ color: '#ffc777', background: 'rgba(255,199,119,0.12)' }}>{t('partial')}</span>
  return <span className="notification-done-badge">{t('done')}</span>
}

// Categorize notification by label pattern
function getNotifType(label) {
  if (!label) return 'default'
  const l = label.toLowerCase()
  if (l.includes('session rotated') || l.includes('chat session rotated')) return 'rotation'
  if (l.includes('batch') || l.includes('period')) return 'batch'
  if (l.includes('insight') || l.includes('workout')) return 'insight'
  if (l.includes('nutrition') || l.includes('meal')) return 'nutrition'
  return 'default'
}

const NOTIF_ICONS = {
  insight: { color: 'var(--accent)', icon: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
  )},
  rotation: { color: 'var(--yellow)', icon: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
  )},
  batch: { color: 'var(--purple)', icon: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
  )},
  nutrition: { color: 'var(--green)', icon: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/></svg>
  )},
  default: { color: 'var(--text-dim)', icon: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
  )},
}

function groupConsecutive(items) {
  const groups = []
  for (const item of items) {
    const last = groups[groups.length - 1]
    if (last && last.label === item.label && last.status === item.status) {
      last.items.push(item)
    } else {
      groups.push({ label: item.label, status: item.status, items: [item] })
    }
  }
  return groups
}

export default function NotificationBell() {
  const { t } = useI18n()
  const [events, setEvents] = useState([])
  const [localTasks, setLocalTasks] = useState({})
  const [serverHistory, setServerHistory] = useState([])
  const [dismissed, setDismissed] = useState(new Set())
  const [open, setOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const ref = useRef(null)
  const navigate = useNavigate()
  const { setChatOpen, switchSession, switchToSession, setPendingScrollIndex } = useChat()

  // Listen for local LLM task events (from any component)
  useEffect(() => {
    function onStart(e) {
      const { id, label, link } = e.detail
      setLocalTasks(prev => ({ ...prev, [id]: { label, link, startedAt: Date.now() } }))
    }
    function onEnd(e) {
      const { id, error } = e.detail
      setLocalTasks(prev => {
        const task = prev[id]
        if (task) {
          const elapsed = Math.round((Date.now() - task.startedAt) / 1000)
          const timeStr = elapsed > 60 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`
          const status = error ? 'error' : 'done'
          const detail = error ? `Failed in ${timeStr} — ${error}` : `Completed in ${timeStr}`
          // Persist to server
          fetch('/api/insights/notifications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: task.label, detail, status, link: task.link || '' }),
          }).catch(err => console.error('Failed to save notification:', err))
        }
        const copy = { ...prev }
        delete copy[id]
        return copy
      })
    }
    window.addEventListener('llm-task-start', onStart)
    window.addEventListener('llm-task-end', onEnd)
    return () => {
      window.removeEventListener('llm-task-start', onStart)
      window.removeEventListener('llm-task-end', onEnd)
    }
  }, [])

  // Server-side active tasks (survives page refresh)
  const [serverTasks, setServerTasks] = useState([])

  useEffect(() => {
    let timer = null
    let mounted = true

    async function poll() {
      let hasActiveTasks = false
      try {
        const r = await fetch('/api/insights/status')
        const data = await r.json()
        if (mounted) {
          const newEvents = []
          if (data.running) {
            const pct = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0
            newEvents.push({
              id: 'insights',
              type: 'progress',
              label: 'Insight Generation',
              detail: `${data.completed}/${data.total} — ${data.current}`,
              pct,
            })
          }
          setEvents(newEvents)
          setServerTasks(data.active_tasks || [])
          if (data.history) setServerHistory(data.history)
          if (data.cancelling) setCancelling(true)
          else if (!data.running) setCancelling(false)
          hasActiveTasks = data.running || (data.active_tasks && data.active_tasks.length > 0)
        }
      } catch { /* ignore */ }
      if (mounted) {
        timer = setTimeout(poll, hasActiveTasks ? 3000 : 30000)
      }
    }

    poll()

    // Allow other components to trigger immediate re-poll
    function onPollNow() {
      clearTimeout(timer)
      poll()
    }
    window.addEventListener('notification-poll-now', onPollNow)
    return () => { mounted = false; clearTimeout(timer); window.removeEventListener('notification-poll-now', onPollNow) }
  }, [])

  // Close on outside click
  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  function handleHistoryClick(item) {
    if (!item.link) return
    setOpen(false)
    setHistoryOpen(false)
    if (item.link.startsWith('chat:')) {
      // Format: chat:<sid>:<mode>:<agent>:<msgIdx> (parts after sid are optional, any order)
      // mode: 'coach' or 'dev', msgIdx: all digits, agent: everything else (e.g. 'main-coach')
      const parts = item.link.slice(5).split(':')
      const sid = parts[0]
      let mode = null, agent = null, msgIdx = null
      for (let i = 1; i < parts.length; i++) {
        if (parts[i] === 'coach' || parts[i] === 'dev') mode = parts[i]
        else if (/^\d+$/.test(parts[i])) msgIdx = parseInt(parts[i], 10)
        else if (parts[i]) agent = parts[i]
      }
      if (sid) {
        if (mode) switchToSession(sid, agent, mode)
        else switchSession(sid, agent)
      }
      setChatOpen(true)
      if (msgIdx != null) {
        setPendingScrollIndex(msgIdx)
      }
    } else if (item.link.startsWith('workout:')) {
      // Open workout detail modal on whatever page the user is on
      const num = parseInt(item.link.slice(8), 10)
      if (!isNaN(num)) {
        window.dispatchEvent(new CustomEvent('open-workout-detail', { detail: { workoutNum: num } }))
      }
    } else if (item.link.includes('openTargets')) {
      // Nutrition targets modal — dispatch event if already on page, else navigate
      const onNutrition = window.location.pathname === '/nutrition'
      if (onNutrition) {
        window.dispatchEvent(new CustomEvent('open-nutrition-targets'))
      } else {
        navigate('/nutrition?openTargets=1')
      }
    } else {
      // Support hash fragments for scroll-to (e.g. /insights#workout-123)
      const hashIdx = item.link.indexOf('#')
      const hash = hashIdx >= 0 ? item.link.slice(hashIdx + 1) : null
      const navPath = hashIdx >= 0 ? item.link.slice(0, hashIdx) : item.link
      navigate(navPath)
      if (hash) {
        setTimeout(() => {
          const el = document.getElementById(hash)
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }, 500)
      }
    }
  }

  // Clear only dismisses items from the dropdown view (doesn't delete from DB)
  function clearDropdown() {
    setDismissed(new Set(serverHistory.map((_, i) => i)))
  }

  // Delete all from DB (used in history modal)
  async function deleteAllHistory() {
    try {
      await fetch('/api/insights/notifications', { method: 'DELETE' })
      setServerHistory([])
      setDismissed(new Set())
    } catch { /* ignore */ }
  }

  // Delete single notification from DB
  async function deleteSingleNotification(item, e) {
    e.stopPropagation()
    if (item.id) {
      try {
        await fetch(`/api/insights/notifications/${item.id}`, { method: 'DELETE' })
        setServerHistory(h => h.filter(x => x.id !== item.id))
      } catch { /* ignore */ }
    }
  }

  const localTaskList = useMemo(() => Object.entries(localTasks), [localTasks])
  const extraServerTasks = useMemo(() => {
    const localIds = new Set(Object.keys(localTasks))
    const localLabels = new Set(Object.values(localTasks).map(t => t.label))
    return serverTasks.filter(t => !localIds.has(t.id) && !localLabels.has(t.label))
  }, [localTasks, serverTasks])
  const visibleHistory = useMemo(() => serverHistory.filter((_, i) => !dismissed.has(i)), [serverHistory, dismissed])
  const dropdownHistory = useMemo(() => visibleHistory.slice(0, 10), [visibleHistory])
  const hasActive = useMemo(() => events.length > 0 || localTaskList.length > 0 || extraServerTasks.length > 0, [events.length, localTaskList.length, extraServerTasks.length])
  const hasAny = useMemo(() => hasActive || visibleHistory.length > 0, [hasActive, visibleHistory.length])

  return (
    <div className="notification-bell" ref={ref}>
      <button
        className={`notification-bell-btn${hasActive ? ' active' : ''}`}
        onClick={() => setOpen(o => !o)}
        title={t('notifications')}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {hasActive && <span className="notification-dot" />}
      </button>

      {open && (
        <div className="notification-dropdown">
          <div className="notification-header">
            <span>{t('notifications')}</span>
            {visibleHistory.length > 0 && (
              <button className="notification-clear-btn" onClick={clearDropdown}>{t('clear')}</button>
            )}
          </div>

          {/* Local LLM tasks */}
          {localTaskList.map(([id, task]) => (
            <div key={id}
              className={`notification-item notification-item-active${task.link ? ' clickable' : ''}`}
              onClick={() => task.link && handleHistoryClick({ link: task.link })}
            >
              <div className="notification-item-label">
                <span className="notification-status-dot running" />
                {task.label}
              </div>
              <div className="notification-item-detail">{t('processing')}</div>
            </div>
          ))}

          {/* Server-side active tasks (survive page refresh) */}
          {extraServerTasks.map(task => (
            <div key={task.id}
              className={`notification-item notification-item-active${task.link ? ' clickable' : ''}`}
              onClick={() => task.link && handleHistoryClick({ link: task.link })}
            >
              <div className="notification-item-label">
                <span className="notification-status-dot running" />
                {task.label}
              </div>
              <div className="notification-item-detail">{t('processing')}</div>
            </div>
          ))}

          {/* Batch insight progress */}
          {events.map(ev => (
            <div key={ev.id}
              className="notification-item notification-item-active clickable"
              onClick={() => handleHistoryClick({ link: '/insights' })}
            >
              <div className="notification-item-label">
                <span className="notification-status-dot running" />
                {ev.label}
                <button className={`notification-cancel-btn${cancelling ? ' cancelling' : ''}`} title={t('cancel')} disabled={cancelling} onClick={(e) => {
                  e.stopPropagation()
                  setCancelling(true)
                  fetch('/api/insights/batch/stop', { method: 'POST' })
                    .then(() => window.dispatchEvent(new CustomEvent('notification-poll-now')))
                    .catch(() => {})
                }}>
                  {cancelling
                    ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                    : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg>
                  }
                </button>
              </div>
              <div className="notification-item-detail">{ev.detail}</div>
              {ev.type === 'progress' && (
                <div className="notification-progress">
                  <div className="notification-progress-fill" style={{ width: `${ev.pct}%` }} />
                </div>
              )}
            </div>
          ))}

          {/* History items: grouped, show up to 10 groups, scrollable */}
          {dropdownHistory.length > 0 && (
            <div className="notification-history-scroll">
              {groupConsecutive(dropdownHistory).slice(0, 10).map((group, gi) => {
                const nType = getNotifType(group.label)
                const nStyle = NOTIF_ICONS[nType]
                if (group.items.length === 1) {
                  const ev = group.items[0]
                  const realIdx = serverHistory.indexOf(ev)
                  return (
                    <div key={`h-${ev.id || gi}`}
                      className={`notification-item notification-item-done${ev.link ? ' clickable' : ''}`}
                      onClick={() => ev.link && handleHistoryClick(ev)}
                    >
                      <div className="notification-item-label">
                        <span className="notification-type-icon" style={{ color: nStyle.color }}>{nStyle.icon}</span>
                        {ev.label}
                        <StatusBadge status={ev.status} t={t} />
                        <button className="notification-dismiss-btn"
                          onClick={(e) => { e.stopPropagation(); setDismissed(prev => new Set([...prev, realIdx])) }}
                          title={t('clear')}>&times;</button>
                      </div>
                      <div className="notification-item-detail">
                        {ev.detail}{ev.finished_at ? ` — ${ev.finished_at}` : ''}
                      </div>
                    </div>
                  )
                }
                // Grouped in dropdown: show collapsed with count
                const latest = group.items[0]
                return (
                  <div key={`hg-${gi}`}
                    className={`notification-item notification-item-done${latest.link ? ' clickable' : ''}`}
                    onClick={() => latest.link && handleHistoryClick(latest)}
                  >
                    <div className="notification-item-label">
                      <span className="notification-type-icon" style={{ color: nStyle.color }}>{nStyle.icon}</span>
                      {group.label}
                      <span className="notification-count-badge">&times;{group.items.length}</span>
                      <StatusBadge status={latest.status} t={t} />
                      <button className="notification-dismiss-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          const indices = new Set(group.items.map(item => serverHistory.indexOf(item)))
                          setDismissed(prev => new Set([...prev, ...indices]))
                        }}
                        title={t('clear')}>&times;</button>
                    </div>
                    <div className="notification-item-detail">
                      {latest.detail}{latest.finished_at ? ` — ${latest.finished_at}` : ''}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Always show History button at the bottom */}
          <div
            className="notification-show-older"
            onClick={() => { setHistoryOpen(true); setOpen(false) }}
          >
            {t('notif_history')} {serverHistory.length > 0 ? `(${serverHistory.length})` : ''}
          </div>

          {!hasAny && (
            <div className="notification-empty">{t('no_notifications')}</div>
          )}
        </div>
      )}

      {/* Full history modal */}
      {historyOpen && (
        <Modal title={t('notif_history')} onClose={() => setHistoryOpen(false)} wide>
          <div style={{ maxHeight: '60vh', overflowY: 'auto' }}>
            {serverHistory.length === 0 && (
              <p className="text-dim">{t('no_notifications')}</p>
            )}
            {groupConsecutive(serverHistory.slice(0, 100)).map((group, gi) => {
              const nType = getNotifType(group.label)
              const nStyle = NOTIF_ICONS[nType]
              const first = group.items[0]
              const last = group.items[group.items.length - 1]
              if (group.items.length === 1) {
                const ev = first
                return (
                  <div key={ev.id || gi}
                    className={`notification-item notification-item-done${ev.link ? ' clickable' : ''}`}
                    style={{ position: 'relative' }}
                    onClick={() => ev.link && handleHistoryClick(ev)}
                  >
                    <div className="notification-item-label">
                      <span className="notification-type-icon" style={{ color: nStyle.color }}>{nStyle.icon}</span>
                      {ev.label}
                      <span className="notification-actions">
                        <StatusBadge status={ev.status} t={t} />
                        <button className="notification-delete-btn" onClick={(e) => deleteSingleNotification(ev, e)} title={t('del')}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>
                        </button>
                      </span>
                    </div>
                    <div className="notification-item-detail">
                      {ev.detail}{ev.finished_at ? ` — ${ev.finished_at}` : ''}
                    </div>
                  </div>
                )
              }
              // Grouped: show count + time range
              return (
                <div key={`g-${gi}`} className="notification-item notification-item-done notification-group">
                  <div className="notification-item-label">
                    <span className="notification-type-icon" style={{ color: nStyle.color }}>{nStyle.icon}</span>
                    {group.label}
                    <span className="notification-count-badge">&times;{group.items.length}</span>
                    <span className="notification-actions">
                      <StatusBadge status={first.status} t={t} />
                    </span>
                  </div>
                  <div className="notification-item-detail">
                    {last.finished_at} — {first.finished_at}
                  </div>
                  <div className="notification-group-items">
                    {group.items.map(ev => (
                      <div key={ev.id} className={`notification-group-sub${ev.link ? ' clickable' : ''}`}
                        onClick={() => ev.link && handleHistoryClick(ev)}>
                        <span className="notification-item-detail">{ev.detail}{ev.finished_at ? ` — ${ev.finished_at}` : ''}</span>
                        <button className="notification-delete-btn" onClick={(e) => deleteSingleNotification(ev, e)} title={t('del')}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
          {serverHistory.length > 0 && (
            <div className="form-actions" style={{ marginTop: 12 }}>
              <button className="btn btn-red" onClick={() => { deleteAllHistory(); setHistoryOpen(false) }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{verticalAlign: 'middle', marginInlineEnd: 4}}><path d="M3 6h18"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>
                {t('notif_clear_all')}
              </button>
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}

// Helper to dispatch LLM task events from any component
// link: optional — where to navigate on click. Use 'chat:<sessionId>' for chat, or '/insights' etc.
export function notifyLlmStart(id, label, link) {
  window.dispatchEvent(new CustomEvent('llm-task-start', { detail: { id, label, link } }))
}
export function notifyLlmEnd(id, error) {
  window.dispatchEvent(new CustomEvent('llm-task-end', { detail: { id, error } }))
}
