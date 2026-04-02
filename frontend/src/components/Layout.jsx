import { NavLink, Outlet, Link } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useAuth } from '../context/AuthContext'
import { useChat } from '../context/ChatContext'
import { useApp } from '../context/AppContext'
import { useI18n } from '../i18n/I18nContext'
import ChatPanel from './chat/ChatPanel'
import ImportModal from './ImportModal'
import PostImportModal from './PostImportModal'
import NotificationBell from './NotificationBell'
import TokenUsage from './TokenUsage'
import UserMenu from './UserMenu'
import WorkoutDetailModal from './WorkoutDetailModal'
import ErrorBoundary from './common/ErrorBoundary'
import { api } from '../api'
import { getEventTypeLabel } from '../utils/classifiers'

const DEFAULT_NAV = [
  { to: '/', icon: '\u25C9', labelKey: 'nav_overview' },
  { to: '/nutrition', icon: '\uD83C\uDF74', labelKey: 'nav_nutrition' },
  { to: '/recovery', icon: '\uD83D\uDCA4', labelKey: 'nav_recovery' },
  { to: '/plan', icon: '\uD83D\uDCC5', labelKey: 'nav_plan' },
  { to: '/body', icon: '\u2696', labelKey: 'nav_body' },
  { to: '/insights', icon: '\uD83D\uDCA1', labelKey: 'nav_insights' },
  { to: '/running', icon: '\uD83C\uDFC3', labelKey: 'nav_running' },
  { to: '/cycling', icon: '\uD83D\uDEB2', labelKey: 'nav_cycling' },
  { to: '/swimming', icon: '\uD83C\uDFCA', labelKey: 'nav_swimming' },
  { to: '/workouts', icon: '\uD83D\uDCC8', labelKey: 'nav_workouts' },
  { to: '/bricks', icon: '\uD83E\uDDF1', labelKey: 'nav_bricks' },
  { to: '/events', icon: '\uD83C\uDFC5', labelKey: 'nav_events' },
  { to: '/sessions', icon: '\uD83E\uDD16', labelKey: 'nav_sessions' },
]

const NAV_ORDER_KEY = 'ironcoach_nav_order'

function loadNavOrder() {
  try {
    const saved = localStorage.getItem(NAV_ORDER_KEY)
    if (!saved) return null
    const order = JSON.parse(saved)
    // Validate: must be array of paths that all exist in DEFAULT_NAV
    const validPaths = new Set(DEFAULT_NAV.map(n => n.to))
    if (!Array.isArray(order) || !order.every(p => validPaths.has(p))) return null
    // Add any new nav items not in saved order
    const ordered = order.filter(p => validPaths.has(p))
    DEFAULT_NAV.forEach(n => { if (!ordered.includes(n.to)) ordered.push(n.to) })
    return ordered
  } catch { return null }
}

function getOrderedNav(order) {
  if (!order) return DEFAULT_NAV
  const byPath = Object.fromEntries(DEFAULT_NAV.map(n => [n.to, n]))
  return order.map(p => byPath[p]).filter(Boolean)
}

export default function Layout() {
  const { user } = useAuth()
  const { chatOpen, setChatOpen, chatMode, setChatMode } = useChat()
  const { workouts, allWorkouts, dateFrom, dateTo, setDateRange, aiEnabled, refreshWorkouts } = useApp()
  const { t, lang, setLang } = useI18n()
  const [importOpen, setImportOpen] = useState(false)
  const [pendingImport, setPendingImport] = useState(null)
  const [primaryEvent, setPrimaryEvent] = useState(null)
  const [globalWorkoutNum, setGlobalWorkoutNum] = useState(null)
  const [aiBannerDismissed, setAiBannerDismissed] = useState(() => localStorage.getItem('ai_banner_dismissed') === '1')

  // Reset banner dismiss when AI gets re-enabled
  useEffect(() => {
    if (aiEnabled) { setAiBannerDismissed(false); localStorage.removeItem('ai_banner_dismissed') }
  }, [aiEnabled])

  useEffect(() => {
    api('/api/race').then(r => { if (r?.event_type) setPrimaryEvent(r) }).catch(() => {})
    const onUpdate = () => api('/api/race').then(r => { if (r?.event_type) setPrimaryEvent(r) }).catch(() => {})
    window.addEventListener('coach-data-update', onUpdate)
    return () => window.removeEventListener('coach-data-update', onUpdate)
  }, [])

  // Listen for open-workout-detail events (from notification bell clicks)
  useEffect(() => {
    const handler = (e) => setGlobalWorkoutNum(e.detail?.workoutNum ?? null)
    window.addEventListener('open-workout-detail', handler)
    return () => window.removeEventListener('open-workout-detail', handler)
  }, [])

  // Load pending import from backend on mount + listen for changes
  useEffect(() => {
    const load = () => api('/api/import/pending').then(d => setPendingImport(d || null)).catch(() => {})
    const loadAndOpen = () => api('/api/import/pending').then(d => {
      if (!d) { setPendingImport(null); return }
      // Skip modal when AI is off and only workouts (no merges/bricks to act on)
      const hasActionable = d.mergeCandidates?.length > 0 || d.brickSessions?.length > 0
      if (!aiEnabled && !hasActionable) {
        api('/api/import/pending', { method: 'DELETE' }).catch(() => {})
        setPendingImport(null)
        refreshWorkouts()
        return
      }
      setPendingImport({ ...d, _open: true }); refreshWorkouts()
    }).catch(() => {})
    load()
    // pending-import-changed: auto-open modal (new import arrived)
    // pending-import-updated: refresh without opening (delete removed workouts)
    window.addEventListener('pending-import-changed', loadAndOpen)
    window.addEventListener('pending-import-updated', load)
    return () => {
      window.removeEventListener('pending-import-changed', loadAndOpen)
      window.removeEventListener('pending-import-updated', load)
    }
  }, [])

  // Nav ordering
  const [navOrder, setNavOrder] = useState(() => loadNavOrder())
  const navItems = useMemo(() => getOrderedNav(navOrder), [navOrder])

  const toggleChatMode = useCallback((targetMode) => {
    if (!aiEnabled) return
    if (chatMode !== targetMode) setChatMode(targetMode)
    setChatOpen(o => chatMode === targetMode ? !o : true)
  }, [chatMode, setChatMode, setChatOpen, aiEnabled])

  // ESC closes chat
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && chatOpen) {
        // Don't close if user is typing in an expanded textarea or editing something
        const tag = document.activeElement?.tagName
        if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
        setChatOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [chatOpen, setChatOpen])

  // Drag state
  const dragIdx = useRef(null)
  const [dragOverIdx, setDragOverIdx] = useState(null)

  const handleDragStart = useCallback((e, idx) => {
    dragIdx.current = idx
    e.dataTransfer.effectAllowed = 'move'
    e.currentTarget.classList.add('nav-dragging')
  }, [])

  const handleDragEnd = useCallback((e) => {
    e.currentTarget.classList.remove('nav-dragging')
    setDragOverIdx(null)
    dragIdx.current = null
  }, [])

  const handleDragOver = useCallback((e, idx) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOverIdx(idx)
  }, [])

  const handleDrop = useCallback((e, dropIdx) => {
    e.preventDefault()
    setDragOverIdx(null)
    const fromIdx = dragIdx.current
    if (fromIdx === null || fromIdx === dropIdx) return
    const paths = navItems.map(n => n.to)
    paths.splice(fromIdx, 1)
    const movedPath = navItems[fromIdx].to
    paths.splice(dropIdx, 0, movedPath)
    setNavOrder(paths)
    localStorage.setItem(NAV_ORDER_KEY, JSON.stringify(paths))
    dragIdx.current = null
  }, [navItems])

  return (
    <>
      <nav id="sidebar">
        <div className="sidebar-header">
          <h2>{t('sidebar_title')} {primaryEvent?.event_type && <span className="sidebar-703">{getEventTypeLabel(primaryEvent.event_type)}</span>}</h2>
        </div>
        <ul className="nav-list">
          {navItems.map((item, idx) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}${dragOverIdx === idx ? ' nav-drop-target' : ''}`}
              draggable
              onDragStart={e => handleDragStart(e, idx)}
              onDragEnd={handleDragEnd}
              onDragOver={e => handleDragOver(e, idx)}
              onDrop={e => handleDrop(e, idx)}
            >
              <span className="nav-icon">{item.icon}</span> {t(item.labelKey)}
            </NavLink>
          ))}
          {user?.role === 'admin' && (
            <NavLink to="/admin" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <span className="nav-icon">{'\uD83D\uDD12'}</span> {t('page_admin')}
            </NavLink>
          )}
        </ul>
        <div className="sidebar-credit">by Ori Vardi</div>
      </nav>

      <main id="main-content">
        <div className="main-topbar">
          <div className="topbar-left">
            <div className="topbar-date-range">
              <input
                type="date"
                className="topbar-date-input"
                value={dateFrom}
                onChange={e => setDateRange(e.target.value, dateTo)}
              />
              <span className="text-dim">-</span>
              <input
                type="date"
                className="topbar-date-input"
                value={dateTo}
                onChange={e => setDateRange(dateFrom, e.target.value)}
              />
              <span className="topbar-workout-count">
                {workouts.length}{workouts.length !== allWorkouts.length ? `/${allWorkouts.length}` : ''}
              </span>
            </div>
            <button className="topbar-icon-btn" onClick={() => setImportOpen(true)} title={t('import_data')}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </button>
            {pendingImport && (
              <button className="topbar-icon-btn pending-import-btn" onClick={() => setPendingImport({ ...pendingImport, _open: true })} title={t('post_import_reopen')}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--yellow)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
              </button>
            )}
          </div>
          <div className="topbar-right">
            <div className="lang-switcher lang-switcher-topbar">
              <button className={`lang-btn ${lang === 'en' ? 'active' : ''}`} onClick={() => setLang('en')}>EN</button>
              <button className={`lang-btn ${lang === 'he' ? 'active' : ''}`} onClick={() => setLang('he')}>HE</button>
            </div>
            <TokenUsage />
            <NotificationBell />
          <button
            className={`topbar-icon-btn${chatOpen && chatMode === 'coach' ? ' active' : ''}${!aiEnabled ? ' disabled' : ''}`}
            onClick={() => toggleChatMode('coach')}
            title={aiEnabled ? t('coach_chat') : t('ai_disabled_btn')}
            disabled={!aiEnabled}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </button>
          {user?.role === 'admin' && (
            <button
              className={`topbar-icon-btn${chatOpen && chatMode === 'dev' ? ' active' : ''}${!aiEnabled ? ' disabled' : ''}`}
              onClick={() => toggleChatMode('dev')}
              title={aiEnabled ? 'Developer Chat' : t('ai_disabled_btn')}
              disabled={!aiEnabled}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="3" width="20" height="18" rx="2" />
                <polyline points="7 8 10 11 7 14" />
                <line x1="13" y1="14" x2="17" y2="14" />
              </svg>
            </button>
          )}
          <UserMenu />
          </div>
        </div>
        {!aiEnabled && !aiBannerDismissed && (
          <div className="ai-disabled-banner">
            <span>{t('ai_disabled_banner')} {user?.role === 'admin'
              ? <Link to="/admin" style={{ color: 'var(--accent)' }}>{t('ai_disabled_banner_link')}</Link>
              : <span className="text-dim">Contact an admin to enable.</span>}
            </span>
            <button className="ai-banner-close" onClick={() => { setAiBannerDismissed(true); localStorage.setItem('ai_banner_dismissed', '1') }}>&times;</button>
          </div>
        )}
        <div className="main-scroll">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>

      <ChatPanel />
      {importOpen && <ImportModal onClose={() => setImportOpen(false)} />}
      {pendingImport?._open && (
        <PostImportModal
          workouts={pendingImport.workouts || []}
          datesWithNutrition={pendingImport.datesWithNutrition || []}
          mergeCandidates={pendingImport.mergeCandidates || []}
          brickSessions={pendingImport.brickSessions || []}
          onClose={() => {
            api('/api/import/pending', { method: 'DELETE' }).catch(() => {})
            setPendingImport(null)
            refreshWorkouts()
            window.dispatchEvent(new Event('coach-data-update'))
          }}
          onDismiss={() => {
            setPendingImport(prev => ({ ...prev, _open: false }))
          }}
          onStarted={() => {
            window.dispatchEvent(new Event('insights-started'))
          }}
        />
      )}
      {globalWorkoutNum != null && (
        <WorkoutDetailModal
          workoutNum={globalWorkoutNum}
          open={true}
          onClose={() => setGlobalWorkoutNum(null)}
        />
      )}
    </>
  )
}
