import { useState, useEffect, useRef, useCallback, useMemo, Component } from 'react'
import { api } from '../api'
import { md, fmtSize, formatCost, formatTokens } from '../utils/formatters'
import useTableSort from '../utils/useTableSort'
import LoadingSpinner from '../components/common/LoadingSpinner'
import Modal from '../components/common/Modal'
import ConfirmDialog from '../components/common/ConfirmDialog'
import { useI18n } from '../i18n/I18nContext'
import { useAuth } from '../context/AuthContext'
import InfoTip from '../components/common/InfoTip'
import { AGENT_LABELS } from '../utils/constants'

// Error boundary to catch rendering errors and show them instead of crashing
class AdminErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null } }
  static getDerivedStateFromError(error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 20, background: 'rgba(255,83,112,0.1)', borderRadius: 8, margin: 16 }}>
          <h3 style={{ color: '#ff5370' }}>Render Error</h3>
          <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', color: '#ff5370' }}>{this.state.error.message}</pre>
          <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', color: 'var(--text-dim)', marginTop: 8 }}>{this.state.error.stack}</pre>
          <button style={{ marginTop: 12 }} onClick={() => this.setState({ error: null })}>Retry</button>
        </div>
      )
    }
    return this.props.children
  }
}

// Only coaching agents (not dev agents or generic sub-agents)
const COACHING_AGENTS = new Set(['main-coach', 'run-coach', 'swim-coach', 'bike-coach', 'nutrition-coach', 'data-pipeline'])

function fmtDate(iso) {
  if (!iso) return '--'
  return iso.slice(0, 16).replace('T', ' ')
}

function toggleUserInSet(setSelectedUsers, uid) {
  setSelectedUsers(prev => {
    const next = new Set(prev)
    next.has(uid) ? next.delete(uid) : next.add(uid)
    return next
  })
}

const FILE_SIZE_LARGE = 400_000
const FILE_SIZE_MEDIUM = 200_000

function fileSizeColor(bytes) {
  if (bytes > FILE_SIZE_LARGE) return '#ff5370'
  if (bytes > FILE_SIZE_MEDIUM) return '#ffc777'
  return 'var(--green)'
}

function PathCell({ path, full }) {
  const ref = useRef(null)
  if (!path) return <span className="text-dim">--</span>
  const filename = path.split('/').pop() || path
  const display = full ? path : (filename.length > 24 ? filename.slice(0, 8) + '\u2026' + filename.slice(-12) : filename)
  return (
    <span
      ref={ref}
      title={path}
      style={{ fontFamily: 'monospace', fontSize: 10, color: 'var(--text-dim)', cursor: 'pointer', whiteSpace: 'nowrap' }}
      onClick={() => {
        navigator.clipboard.writeText(path)
        const el = ref.current
        if (el) {
          const orig = el.textContent
          el.textContent = 'copied!'
          el.style.color = 'var(--green)'
          setTimeout(() => { el.textContent = orig; el.style.color = 'var(--text-dim)' }, 1200)
        }
      }}
    >
      {display}
    </span>
  )
}

export default function AdminPage() {
  const { t } = useI18n()
  const [tab, setTab] = useState('settings')

  return (
    <>
      <h1 className="page-title">{t('admin_title')}</h1>
      <div className="detail-tabs" style={{ marginBottom: 16 }}>
        <button className={`detail-tab${tab === 'settings' ? ' active' : ''}`} onClick={() => setTab('settings')}>{t('admin_tab_settings')}</button>
        <button className={`detail-tab${tab === 'users' ? ' active' : ''}`} onClick={() => setTab('users')}>{t('admin_tab_users')}</button>
        <button className={`detail-tab${tab === 'agents' ? ' active' : ''}`} onClick={() => setTab('agents')}>{t('admin_tab_agents')}</button>
        <button className={`detail-tab${tab === 'sessions' ? ' active' : ''}`} onClick={() => setTab('sessions')}>{t('admin_tab_sessions')}</button>
        <button className={`detail-tab${tab === 'cli-sessions' ? ' active' : ''}`} onClick={() => setTab('cli-sessions')}>{t('admin_tab_cli_sessions')}</button>
        <button className={`detail-tab${tab === 'logs' ? ' active' : ''}`} onClick={() => setTab('logs')}>{t('admin_tab_logs')}</button>
      </div>
      <AdminErrorBoundary key={tab}>
        {tab === 'settings' && <SettingsTab />}
        {tab === 'users' && <UsersTab />}
        {tab === 'agents' && <AgentDefinitionsTab />}
        {tab === 'sessions' && <SessionsTab />}
        {tab === 'cli-sessions' && <CliSessionsTab />}
        {tab === 'logs' && <LogsTab />}
      </AdminErrorBoundary>
    </>
  )
}

/* ─── Users ─── */
function UsersTab() {
  const { t } = useI18n()
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState([])
  const [usageMap, setUsageMap] = useState({})  // user_id -> usage
  const [totalUsage, setTotalUsage] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState({ role: '', password: '', display_name: '' })
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', display_name: '', role: 'user', height_cm: '', birth_date: '', sex: 'male' })
  const [error, setError] = useState('')
  const [deleteTarget, setDeleteTarget] = useState(null) // { id, name, isSelf }

  const loadData = useCallback(async () => {
    try {
      const [userList, usageData] = await Promise.all([
        api('/api/admin/users'),
        api('/api/admin/usage').catch(() => null)
      ])
      setUsers(userList)
      if (usageData) {
        const map = {}
        for (const u of usageData.per_user) map[u.user_id] = u
        setUsageMap(map)
        setTotalUsage(usageData.total)
      }
    } catch (err) { console.error('Failed to load users:', err) }
    setLoading(false)
  }, [])

  useEffect(() => { loadData() }, [loadData])

  async function createUser(e) {
    e.preventDefault()
    try {
      const payload = { ...form }
      if (payload.height_cm) payload.height_cm = Number(payload.height_cm)
      else delete payload.height_cm
      if (!payload.birth_date) delete payload.birth_date
      await api('/api/admin/users', { method: 'POST', body: JSON.stringify(payload) })
      setShowForm(false)
      setForm({ username: '', password: '', display_name: '', role: 'user', height_cm: '', birth_date: '', sex: 'male' })
      loadData()
    } catch (err) { setError(err.message) }
  }

  function startEdit(u) {
    setEditingId(u.id)
    setEditForm({ role: u.role, password: '', display_name: u.display_name || '' })
  }

  async function saveEdit(id) {
    const body = {}
    if (editForm.role) body.role = editForm.role
    if (editForm.display_name !== undefined) body.display_name = editForm.display_name
    if (editForm.password) body.password = editForm.password
    try {
      await api(`/api/admin/users/${id}`, { method: 'PUT', body: JSON.stringify(body) })
      setEditingId(null)
      loadData()
    } catch (err) { setError(err.message) }
  }

  async function confirmDeleteUser() {
    if (!deleteTarget) return
    try {
      const res = await api(`/api/admin/users/${deleteTarget.id}`, { method: 'DELETE' })
      setDeleteTarget(null)
      if (res.reset) {
        // Factory reset — admin deleted themselves, redirect to setup
        sessionStorage.clear()
        window.location.href = '/'
        return
      }
      loadData()
    } catch (err) { setError(err.message); setDeleteTarget(null) }
  }

  if (loading) return <LoadingSpinner />

  return (
    <div className="card">
      <div className="flex-between mb-12">
        <h4>{t('admin_users_title')} ({users.length})</h4>
        {!showForm && <button className="btn btn-accent btn-sm" onClick={() => setShowForm(true)}>{t('admin_add_user')}</button>}
      </div>
      {showForm && (
        <form onSubmit={createUser} autoComplete="off" className="mb-12" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
          <div className="form-group" style={{ flex: 1, minWidth: 120 }}>
            <label>{t('admin_username')}</label>
            <input className="input-full" autoComplete="off" value={form.username} onChange={e => setForm(f => ({ ...f, username: e.target.value }))} required />
          </div>
          <div className="form-group" style={{ flex: 1, minWidth: 120 }}>
            <label>{t('admin_password')}</label>
            <input className="input-full" type="password" autoComplete="new-password" value={form.password} onChange={e => setForm(f => ({ ...f, password: e.target.value }))} required />
          </div>
          <div className="form-group" style={{ flex: 1, minWidth: 120 }}>
            <label>{t('admin_display_name')}</label>
            <input className="input-full" autoComplete="off" dir="auto" value={form.display_name} onChange={e => setForm(f => ({ ...f, display_name: e.target.value }))} />
          </div>
          <div className="form-group" style={{ minWidth: 100 }}>
            <label>{t('admin_role')}</label>
            <select className="input-full" value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div className="form-group" style={{ minWidth: 80 }}>
            <label>Height (cm)</label>
            <input className="input-full" type="number" placeholder="e.g. 180" value={form.height_cm} onChange={e => setForm(f => ({ ...f, height_cm: e.target.value }))} />
          </div>
          <div className="form-group" style={{ minWidth: 120 }}>
            <label>Birth Date</label>
            <input className="input-full" type="date" value={form.birth_date} onChange={e => setForm(f => ({ ...f, birth_date: e.target.value }))} />
          </div>
          <div className="form-group" style={{ minWidth: 80 }}>
            <label>Sex</label>
            <select className="input-full" value={form.sex} onChange={e => setForm(f => ({ ...f, sex: e.target.value }))}>
              <option value="male">Male</option>
              <option value="female">Female</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 6, alignSelf: 'end' }}>
            <button className="btn btn-sm" type="button" onClick={() => { setShowForm(false); setForm({ username: '', password: '', display_name: '', role: 'user', height_cm: '', birth_date: '', sex: 'male' }) }}>{t('cancel')}</button>
            <button className="btn btn-accent btn-sm" type="submit">{t('create')}</button>
          </div>
        </form>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>ID</th><th>{t('admin_username')}</th><th>{t('admin_display_name')}</th><th>{t('admin_role')}</th><th>{t('admin_created')}</th>
            <th style={{ textAlign: 'end' }}>{t('admin_cost')}</th>
            <th style={{ textAlign: 'end' }}>{t('admin_calls')}</th>
            <th style={{ textAlign: 'end' }}>{t('admin_tokens')}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => {
            const ug = usageMap[u.id]
            const editing = editingId === u.id
            return (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>{u.username}</td>
                <td dir="auto">{editing
                  ? <input className="input-full" autoComplete="off" dir="auto" value={editForm.display_name} onChange={e => setEditForm(f => ({ ...f, display_name: e.target.value }))} style={{ padding: '4px 8px', fontSize: 13 }} />
                  : u.display_name}</td>
                <td>{editing
                  ? <select className="input-full" value={editForm.role} onChange={e => setEditForm(f => ({ ...f, role: e.target.value }))} style={{ padding: '4px 8px', fontSize: 13, width: 'auto' }}>
                      <option value="user">User</option>
                      <option value="admin">Admin</option>
                    </select>
                  : <span className={`role-badge ${u.role}`}>{u.role}</span>}</td>
                <td>{u.created_at?.slice(0, 10) || '-'}</td>
                <td style={{ textAlign: 'end', color: 'var(--accent)', fontWeight: 500 }}>{formatCost(ug?.total_cost)}</td>
                <td style={{ textAlign: 'end' }}>{ug?.total_calls || 0}</td>
                <td style={{ textAlign: 'end' }}>{formatTokens((ug?.total_input || 0) + (ug?.total_output || 0))}</td>
                <td style={{ display: 'flex', gap: 4 }}>
                  {editing ? (<>
                    <input className="input-full" type="password" autoComplete="new-password" placeholder={t('admin_new_password')} value={editForm.password} onChange={e => setEditForm(f => ({ ...f, password: e.target.value }))} style={{ padding: '4px 8px', fontSize: 13, width: 100 }} />
                    <button className="btn btn-sm btn-accent" onClick={() => saveEdit(u.id)}>{t('save')}</button>
                    <button className="btn btn-sm" onClick={() => setEditingId(null)}>{t('cancel')}</button>
                  </>) : (<>
                    <button className="btn btn-sm" onClick={() => startEdit(u)}>{t('edit')}</button>
                    <button className="btn btn-sm btn-red" onClick={() => setDeleteTarget({ id: u.id, name: u.username, isSelf: u.id === currentUser?.id })}>{t('del')}</button>
                  </>)}
                </td>
              </tr>
            )
          })}
        </tbody>
        {!!totalUsage && (
          <tfoot>
            <tr style={{ fontWeight: 600, borderTop: '2px solid var(--border)' }}>
              <td colSpan={5} style={{ textAlign: 'end' }}>{t('admin_total')}</td>
              <td style={{ textAlign: 'end', color: 'var(--accent)' }}>{formatCost(totalUsage.total_cost)}</td>
              <td style={{ textAlign: 'end' }}>{totalUsage.total_calls}</td>
              <td style={{ textAlign: 'end' }}>{formatTokens(totalUsage.total_input + totalUsage.total_output)}</td>
              <td></td>
            </tr>
          </tfoot>
        )}
      </table>
      {!!error && <p className="text-red mt-8" style={{ cursor: 'pointer' }} onClick={() => setError('')}>{error}</p>}
      <ConfirmDialog
        open={!!deleteTarget}
        title={deleteTarget?.isSelf ? 'Factory Reset' : `Delete "${deleteTarget?.name}"?`}
        message={deleteTarget?.isSelf
          ? 'This will delete your admin account and ALL associated data (chat, insights, nutrition, training plan, token usage, training data files). The app will return to the first-run setup screen. This cannot be undone.'
          : `This will permanently delete user "${deleteTarget?.name}" and ALL their data (chat history, insights, nutrition, training plan, token usage, training data files). This cannot be undone.`}
        onConfirm={confirmDeleteUser}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

/* ─── Editable Number Select — dropdown with presets + free-text input ─── */
function NumSelect({ value, options, unit = '', settingKey, onSave, saving, min = 1, max = 9999, t }) {
  const [editMode, setEditMode] = useState(false)
  const [draft, setDraft] = useState(String(value))
  const isCustom = !options.some(o => o.value === value)

  function commitDraft() {
    const n = parseInt(draft)
    if (!isNaN(n) && n >= min && n <= max) {
      onSave(settingKey, String(n))
      setEditMode(false)
    }
  }

  if (editMode) {
    return (
      <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
        <input
          type="number"
          className="input-sm"
          style={{ width: 70, textAlign: 'center' }}
          value={draft}
          min={min}
          max={max}
          autoFocus
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') commitDraft(); if (e.key === 'Escape') setEditMode(false) }}
          onBlur={commitDraft}
        />
        <span className="text-dim" style={{ fontSize: 12 }}>{unit}</span>
      </span>
    )
  }

  return (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      <select
        className="input-sm"
        style={{ width: 'auto', minWidth: 70 }}
        value={isCustom ? '__custom' : value}
        onChange={e => {
          if (e.target.value === '__custom') { setDraft(String(value)); setEditMode(true) }
          else onSave(settingKey, e.target.value)
        }}
        disabled={saving === settingKey}
      >
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        {isCustom && <option value={value}>{value}</option>}
        <option value="__custom">{t ? t('admin_custom') : 'Custom...'}</option>
      </select>
      <span className="text-dim" style={{ fontSize: 12 }}>{unit}</span>
      {saving === settingKey && <span className="text-dim text-xs">{t ? t('admin_saving') : 'saving...'}</span>}
    </span>
  )
}

/* ─── Settings Tab ─── */
function SettingsTab() {
  const { t } = useI18n()
  const [settings, setSettings] = useState({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null) // key being saved
  const [cleaning, setCleaning] = useState(false)
  const [cleanupResult, setCleanupResult] = useState(null)
  const [showAiConfirm, setShowAiConfirm] = useState(false)

  useEffect(() => {
    api('/api/admin/settings')
      .then(s => setSettings(s))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function saveSetting(key, value) {
    setSaving(key)
    try {
      await api('/api/admin/settings', { method: 'PATCH', body: JSON.stringify({ [key]: String(value) }) })
      setSettings(prev => ({ ...prev, [key]: String(value) }))
      if (key === 'ai_enabled') window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch { /* ignore */ }
    finally { setSaving(null) }
  }

  async function deleteManualMerges() {
    setSaving('manual_merges')
    try {
      await api('/api/admin/settings', { method: 'PATCH', body: JSON.stringify({ manual_merges: '[]' }) })
      setSettings(prev => ({ ...prev, manual_merges: '[]' }))
    } catch { /* ignore */ }
    finally { setSaving(null) }
  }

  async function runCleanup() {
    const retention = parseInt(settings.session_retention_days) || 210
    if (!confirm(`Delete all chat + CLI sessions older than ${retention} days?`)) return
    setCleaning(true)
    setCleanupResult(null)
    try {
      const res = await api('/api/admin/cleanup-sessions', { method: 'POST' })
      setCleanupResult(res)
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch (err) { console.error('Cleanup failed:', err) }
    setCleaning(false)
  }

  if (loading) return <LoadingSpinner />

  const aiEnabled = settings.ai_enabled === '1'
  const agentModel = settings.agent_model || ''
  const chatSummaryMode = settings.chat_summary_mode || 'ai'
  const aiRateLimit = parseInt(settings.ai_rate_limit) || 0
  const sessionRotationKb = parseInt(settings.session_rotation_kb) || 800
  const autoMergeEnabled = settings.auto_merge_enabled !== '0'
  const autoMergeGap = parseInt(settings.auto_merge_gap) || 10
  const nutritionRegenEnabled = settings.nutrition_regen_enabled !== '0'
  const nutritionPreInsight = settings.nutrition_pre_insight !== '0'
  const nutritionPreHours = parseInt(settings.nutrition_pre_hours) || 4
  const nutritionPostHours = parseInt(settings.nutrition_post_hours) || 2
  const manualMerges = (() => { try { return JSON.parse(settings.manual_merges || '[]') } catch { return [] } })()

  return (
    <>
    {/* AI Features Toggle */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>{t('ai_features_title')}</h4>
      <p className="text-dim" style={{ fontSize: 12, marginBottom: 8 }}>
        {t('ai_features_desc')}
      </p>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input type="checkbox" checked={aiEnabled} disabled={saving === 'ai_enabled'}
          onChange={() => {
            if (!aiEnabled) {
              setShowAiConfirm(true)
            } else {
              saveSetting('ai_enabled', '0')
            }
          }} />
        <span style={{ color: aiEnabled ? 'var(--green)' : 'var(--text-dim)', fontWeight: 500 }}>
          {t('ai_features_on')}
        </span>
        {saving === 'ai_enabled' && <span className="text-dim text-xs">{t('admin_saving')}</span>}
      </div>
    </div>
    <ConfirmDialog
      open={showAiConfirm}
      title={t('ai_features_confirm_title')}
      message={t('ai_features_confirm_msg')}
      confirmLabel={t('ai_features_confirm_btn')}
      danger={false}
      onConfirm={() => { setShowAiConfirm(false); saveSetting('ai_enabled', '1') }}
      onCancel={() => setShowAiConfirm(false)}
    />

    {/* AI Model */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>{t('agent_model_title')}</h4>
      <p className="text-dim" style={{ fontSize: 12, marginBottom: 8 }}>
        {t('agent_model_desc')}
      </p>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <select className="input-sm" style={{ width: 260 }} value={agentModel}
          onChange={e => saveSetting('agent_model', e.target.value)} disabled={saving === 'agent_model'}>
          <option value="">{t('agent_model_default')}</option>
          <option value="claude-opus-4-6">Opus 4.6 (most capable, expensive)</option>
          <option value="claude-sonnet-4-6">Sonnet 4.6 (balanced)</option>
          <option value="claude-haiku-4-5-20251001">Haiku 4.5 (fastest, cheapest)</option>
        </select>
        {saving === 'agent_model' && <span className="text-dim text-xs">{t('admin_saving')}</span>}
      </div>
    </div>

    {/* AI Rate Limit */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        AI Rate Limit
        <InfoTip text="Maximum AI API calls per user per hour. Set to 0 to disable rate limiting (default). Example: 30 = max 30 insight/nutrition AI calls per hour per user." />
      </h4>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <NumSelect value={aiRateLimit} settingKey="ai_rate_limit" onSave={saveSetting} saving={saving} min={0} max={200} unit="calls/hr" t={t}
          options={[{value: 0, label: 'Off'}, {value: 10, label: '10'}, {value: 20, label: '20'}, {value: 30, label: '30'}, {value: 50, label: '50'}, {value: 100, label: '100'}]} />
      </div>
    </div>

    {/* Chat Summary Mode */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        {t('admin_rotation_context')}
        <InfoTip text={t('admin_rotation_context_tip')} />
      </h4>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <select className="input-sm" style={{ width: 480 }} value={chatSummaryMode}
          onChange={e => saveSetting('chat_summary_mode', e.target.value)} disabled={saving === 'chat_summary_mode'}>
          <option value="ai">{t('admin_chat_summary_ai')}</option>
          <option value="raw">{t('admin_chat_summary_raw')}</option>
        </select>
        {saving === 'chat_summary_mode' && <span className="text-dim text-xs">{t('admin_saving')}</span>}
      </div>
    </div>

    {/* Session Rotation Size */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        {t('admin_rotation_size') || 'Session Rotation Size'}
        <InfoTip text={t('admin_rotation_size_tip') || 'Maximum CLI session file size before rotation. Larger = richer context but higher cost per call. Smaller = cheaper calls but more frequent context resets. Default: 800KB.'} />
      </h4>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <NumSelect value={sessionRotationKb} settingKey="session_rotation_kb" onSave={saveSetting} saving={saving} min={200} max={2000} unit="KB" t={t}
          options={[{value: 400, label: '400'}, {value: 600, label: '600'}, {value: 800, label: '800'}, {value: 1000, label: '1000'}, {value: 1200, label: '1200'}, {value: 1600, label: '1600'}]} />
      </div>
    </div>

    {/* Auto-Merge */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        {t('admin_auto_merge')}
        <InfoTip text={t('admin_auto_merge_tip')} />
      </h4>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={autoMergeEnabled}
            onChange={e => saveSetting('auto_merge_enabled', e.target.checked ? '1' : '0')} />
          {t('admin_enable_auto_merge')}
        </label>
        {autoMergeEnabled && (
          <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {t('admin_gap_threshold')}
            <NumSelect value={autoMergeGap} settingKey="auto_merge_gap" onSave={saveSetting} saving={saving} min={1} max={60} unit="min" t={t}
              options={[{value: 5, label: '5'}, {value: 10, label: '10'}, {value: 15, label: '15'}, {value: 20, label: '20'}, {value: 30, label: '30'}]} />
          </label>
        )}
      </div>
      {manualMerges.length > 0 && (
        <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="text-dim" style={{ fontSize: 12 }}>{manualMerges.length} {t('admin_manual_merges')}</span>
          <button className="btn btn-sm btn-red" onClick={deleteManualMerges}
            disabled={saving === 'manual_merges'}>
            {saving === 'manual_merges' ? '...' : t('admin_clear_all')}
          </button>
        </div>
      )}
    </div>

    {/* Nutrition Insight Settings */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        {t('admin_nutrition_insights')}
        <InfoTip text={t('admin_nutrition_insights_tip')} />
      </h4>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={nutritionRegenEnabled}
            onChange={e => saveSetting('nutrition_regen_enabled', e.target.checked ? '1' : '0')} />
          {t('admin_auto_regen')}
          <InfoTip text={t('admin_auto_regen_tip')} />
        </label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={nutritionPreInsight}
            onChange={e => saveSetting('nutrition_pre_insight', e.target.checked ? '1' : '0')} />
          {t('admin_include_nutrition')}
          <InfoTip text={t('admin_include_nutrition_tip')} />
        </label>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13 }}>{t('admin_fueling_window')}</span>
          <NumSelect value={nutritionPreHours} settingKey="nutrition_pre_hours" onSave={saveSetting} saving={saving} min={1} max={12} unit={t('admin_h_before')} t={t}
            options={[{value: 2, label: '2'}, {value: 3, label: '3'}, {value: 4, label: '4'}, {value: 5, label: '5'}, {value: 6, label: '6'}]} />
          <NumSelect value={nutritionPostHours} settingKey="nutrition_post_hours" onSave={saveSetting} saving={saving} min={1} max={12} unit={t('admin_h_after')} t={t}
            options={[{value: 1, label: '1'}, {value: 2, label: '2'}, {value: 3, label: '3'}, {value: 4, label: '4'}]} />
        </div>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer', marginTop: 6 }}>
          <input type="checkbox" checked={settings.nutrition_auto_suggest === '1'}
            onChange={e => saveSetting('nutrition_auto_suggest', e.target.checked ? '1' : '0')} />
          {t('admin_auto_suggest_targets')}
          <InfoTip text={t('admin_auto_suggest_targets_tip')} />
        </label>
      </div>
    </div>

    {/* Session Cleanup */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        {t('admin_session_cleanup')}
        <InfoTip text={t('admin_session_cleanup_tip')} />
      </h4>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {t('admin_retention')}
          <NumSelect value={parseInt(settings.session_retention_days) || 210} settingKey="session_retention_days" onSave={saveSetting} saving={saving} min={7} max={3650} unit={t('admin_days')} t={t}
            options={[{value: 30, label: '30'}, {value: 90, label: '90'}, {value: 180, label: '180'}, {value: 210, label: '210'}, {value: 365, label: '365'}, {value: 730, label: '730'}]} />
        </label>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn btn-sm btn-red" onClick={runCleanup} disabled={cleaning}>
          {cleaning ? t('admin_cleaning') : t('admin_run_cleanup')}
        </button>
        <span className="text-dim" style={{ fontSize: 11 }}>
          {t('admin_cleanup_desc').replace('{days}', parseInt(settings.session_retention_days) || 210)}
        </span>
      </div>
      {cleanupResult && (
        <div style={{ marginTop: 10, padding: '8px 12px', background: 'var(--bg-1)', borderRadius: 'var(--radius)', fontSize: 12 }}>
          {t('admin_cleanup_result').replace('{date}', cleanupResult.cutoff_date)}
          <strong> {cleanupResult.deleted_chat_sessions}</strong> {t('admin_cleanup_chat')},
          <strong> {cleanupResult.deleted_cli_sessions}</strong> {t('admin_cleanup_cli')},
          <strong> {cleanupResult.deleted_bak_files}</strong> {t('admin_cleanup_bak')}
        </div>
      )}
    </div>

    {/* Notification History Size */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        {t('admin_notif_history')}
        <InfoTip text={t('admin_notif_history_tip')} />
      </h4>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {t('admin_notif_max_keep')}
          <NumSelect value={parseInt(settings.notification_max_keep) || 50} settingKey="notification_max_keep" onSave={saveSetting} saving={saving} min={10} max={500} t={t}
            options={[{value: 25, label: '25'}, {value: 50, label: '50'}, {value: 100, label: '100'}, {value: 200, label: '200'}]} />
        </label>
      </div>
    </div>

    {/* Upload Cleanup */}
    <div className="card" style={{ marginBottom: 16 }}>
      <h4 style={{ marginBottom: 8 }}>
        {t('admin_upload_cleanup')}
        <InfoTip text={t('admin_upload_cleanup_tip')} />
      </h4>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {t('admin_upload_max')}
          <NumSelect value={parseInt(settings.upload_max_mb) || 200} settingKey="upload_max_mb" onSave={saveSetting} saving={saving} min={50} max={1000} unit="MB" t={t}
            options={[{value: 100, label: '100'}, {value: 200, label: '200'}, {value: 300, label: '300'}, {value: 500, label: '500'}]} />
        </label>
      </div>
    </div>
    </>
  )
}

/* ─── Unified Sessions Tab (Chat + Coaching Agent) ─── */
function SessionsTab() {
  const { t } = useI18n()
  const [subTab, setSubTab] = useState('chat')
  const [users, setUsers] = useState({})

  useEffect(() => {
    api('/api/admin/users')
      .then(userList => {
        const map = {}
        userList.forEach(u => { map[u.id] = u.username })
        setUsers(map)
      })
      .catch(err => console.error('Failed to load users:', err))
  }, [])

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button className={`btn btn-sm${subTab === 'chat' ? ' btn-accent' : ' btn-outline'}`} onClick={() => setSubTab('chat')}>
          {t('admin_chat_sessions')}
        </button>
        <button className={`btn btn-sm${subTab === 'coaching' ? ' btn-accent' : ' btn-outline'}`} onClick={() => setSubTab('coaching')}>
          {t('admin_agent_sessions')}
        </button>
      </div>
      {subTab === 'chat' && <ChatSessionsSub users={users} />}
      {subTab === 'coaching' && <CoachingSessionsSub users={users} />}
    </>
  )
}

function ChatSessionsSub({ users }) {
  const { t } = useI18n()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedUsers, setSelectedUsers] = useState(new Set())
  const [expandedId, setExpandedId] = useState(null)
  const [viewModal, setViewModal] = useState(null)
  const [modalData, setModalData] = useState(null)
  const [modalLoading, setModalLoading] = useState(false)
  const [error, setError] = useState('')

  const loadData = useCallback(() => {
    api('/api/admin/chat-sessions')
      .then(sess => setSessions(sess))
      .catch(err => console.error('Failed to load:', err))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadData() }, [loadData])
  useEffect(() => {
    window.addEventListener('coach-data-update', loadData)
    return () => window.removeEventListener('coach-data-update', loadData)
  }, [loadData])

  async function deleteSession(sessionId) {
    if (!confirm('Delete this chat session and all its CLI files?')) return
    try {
      await api(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' })
      setSessions(s => s.filter(x => x.session_id !== sessionId))
      if (expandedId === sessionId) setExpandedId(null)
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch (err) { setError(err.message) }
  }

  async function openModal(type, session, bakPath) {
    const key = { type, sessionId: session.session_id, bakPath }
    setViewModal(key)
    setModalData(null)
    setModalLoading(true)
    try {
      if (type === 'chat') {
        setModalData(await api(`/api/admin/chat-history/${session.session_id}`))
      } else if (type === 'cli') {
        const data = await api(`/api/sessions/${session.claude_session_uuid}/transcript`)
        setModalData(Array.isArray(data) ? data : data.messages || [])
      } else {
        const data = await api(`/api/admin/session-file-transcript?path=${encodeURIComponent(bakPath)}`)
        setModalData(Array.isArray(data) ? data : data.messages || [])
      }
    } catch { setModalData([]) }
    setModalLoading(false)
  }

  async function deleteCliFile(session, bakPath) {
    const path = bakPath || session.claude_file_path
    if (!confirm(`Delete this CLI session file?`)) return
    try {
      await api(`/api/admin/session-file?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
      setViewModal(null)
      loadData()
    } catch (err) { setError(err.message) }
  }

  if (loading) return <LoadingSpinner />

  const filtered = selectedUsers.size > 0 ? sessions.filter(s => selectedUsers.has(String(s.user_id))) : sessions

  function renderTranscript(msgs) {
    if (!msgs || msgs.length === 0) return <p className="text-dim" style={{ padding: 8 }}>{t('admin_no_messages')}</p>
    return msgs.filter(m => {
      if (m.role === 'tool') return false
      if (m.role === 'assistant' && !m.content?.trim()) return false
      return true
    }).map((msg, i) => {
      const cssRole = msg.role === 'user' ? 'user' : msg.role
      return (
        <div key={i} className={`transcript-msg ${cssRole}`}>
          <div className="transcript-role">{msg.role === 'human' ? 'user' : msg.role}</div>
          <div dir="auto" dangerouslySetInnerHTML={md(msg.content)} />
        </div>
      )
    })
  }

  // Modal title
  const modalTitle = viewModal?.type === 'chat' ? t('admin_chat_messages') : viewModal?.type === 'cli' ? t('admin_cli_current') : t('admin_cli_rotated')

  return (
    <>
      <div className="card">
        <div className="flex-between mb-12">
          <h4>{t('admin_in_app_chat')} ({filtered.length})</h4>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {Object.entries(users).map(([id, name]) => (
              <button key={id} className={`btn btn-xs${selectedUsers.has(id) ? ' btn-accent' : ' btn-outline'}`}
                onClick={() => toggleUserInSet(setSelectedUsers, id)}>{name}</button>
            ))}
            {selectedUsers.size > 0 && (
              <button className="btn btn-xs btn-outline" onClick={() => setSelectedUsers(new Set())}>{t('clear')}</button>
            )}
          </div>
        </div>
        <div style={{ maxHeight: 'calc(100vh - 280px)', overflowY: 'auto' }}>
          {filtered.map(s => {
            const isExpanded = expandedId === s.session_id
            const hasCli = !!s.claude_file_path
            const bakFiles = s.bak_files || []
            const totalCliSize = s.claude_file_size + bakFiles.reduce((a, b) => a + b.size, 0)
            const totalFiles = (hasCli ? 1 : 0) + bakFiles.length

            return (
              <div key={s.session_id} className="card" style={{ marginBottom: 8, padding: 0, border: isExpanded ? '1px solid var(--accent)' : '1px solid var(--border)' }}>
                {/* Session header row */}
                <div style={{ display: 'flex', alignItems: 'center', padding: '8px 12px', gap: 12, cursor: 'pointer' }}
                  onClick={() => setExpandedId(isExpanded ? null : s.session_id)}>
                  <span style={{ fontSize: 11, color: 'var(--text-dim)', width: 16 }}>{isExpanded ? '\u25BC' : '\u25B6'}</span>
                  <span style={{ fontWeight: 500, minWidth: 50 }}>{users[s.user_id] || `#${s.user_id}`}</span>
                  <span className="text-dim" dir="auto" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.preview || '-'}</span>
                  <span style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{s.msg_count} {t('admin_msgs').toLowerCase()}</span>
                  <span className="text-dim" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{fmtDate(s.last_msg)}</span>
                  {totalFiles > 0 && (
                    <span style={{ fontSize: 10, color: 'var(--accent)', whiteSpace: 'nowrap' }}>
                      {totalFiles} CLI file{totalFiles > 1 ? 's' : ''} ({fmtSize(totalCliSize)})
                    </span>
                  )}
                  <button className="btn btn-sm btn-red" onClick={(e) => { e.stopPropagation(); deleteSession(s.session_id) }} style={{ flexShrink: 0 }}>
                    {t('admin_del_all')}
                  </button>
                </div>

                {/* Expanded: file list */}
                {isExpanded && (
                  <div style={{ borderTop: '1px solid var(--border)', padding: '8px 12px 8px 40px', background: 'var(--bg-1)' }}>
                    {/* Chat messages row */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', cursor: 'pointer' }}
                      onClick={() => openModal('chat', s)}>
                      <span style={{ fontSize: 12 }}>💬</span>
                      <span style={{ fontSize: 12 }}>{t('admin_chat_messages')}</span>
                      <span className="text-dim" style={{ fontSize: 11 }}>{s.msg_count} {t('admin_msgs').toLowerCase()}</span>
                    </div>
                    {/* Current CLI session */}
                    {hasCli && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
                        <span style={{ fontSize: 12, cursor: 'pointer', flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}
                          onClick={() => openModal('cli', s)}>
                          <span>📄</span>
                          <span>{t('admin_current_cli')}</span>
                          <span style={{ fontSize: 11, color: fileSizeColor(s.claude_file_size) }}>
                            {fmtSize(s.claude_file_size)}
                          </span>
                          <PathCell path={s.claude_file_path} />
                        </span>
                        <button className="btn btn-xs btn-red" onClick={() => deleteCliFile(s, null)}>{t('del')}</button>
                      </div>
                    )}
                    {/* Rotated .bak files */}
                    {bakFiles.map((bak, i) => (
                      <div key={bak.path} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
                        <span style={{ fontSize: 12, cursor: 'pointer', flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}
                          onClick={() => openModal('bak', s, bak.path)}>
                          <span>🔄</span>
                          <span style={{ color: 'var(--yellow)' }}>{t('admin_rotated')} #{i + 1}</span>
                          <span style={{ fontSize: 11, color: 'var(--yellow)' }}>{fmtSize(bak.size)}</span>
                          <PathCell path={bak.path} />
                        </span>
                        <button className="btn btn-xs btn-red" onClick={() => deleteCliFile(s, bak.path)}>{t('del')}</button>
                      </div>
                    ))}
                    {!hasCli && bakFiles.length === 0 && (
                      <span className="text-dim" style={{ fontSize: 11 }}>{t('admin_no_cli_files')}</span>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          {filtered.length === 0 && <p className="text-dim" style={{ padding: 8 }}>{t('no_data')}</p>}
        </div>
      </div>
      {!!error && <p className="text-red mt-8" style={{ cursor: 'pointer' }} onClick={() => setError('')}>{error}</p>}

      {/* Transcript modal */}
      <Modal open={viewModal != null} onClose={() => setViewModal(null)} title={modalTitle} wide>
        {viewModal && (
          <div>
            {/* Delete button in modal header */}
            {viewModal.type !== 'chat' && (
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                <button className="btn btn-sm btn-red" onClick={() => {
                  const s = sessions.find(x => x.session_id === viewModal.sessionId)
                  if (s) deleteCliFile(s, viewModal.bakPath)
                }}>
                  {t('admin_delete_file')}
                </button>
              </div>
            )}
            <div className="transcript" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
              {modalLoading ? <LoadingSpinner /> : renderTranscript(modalData)}
            </div>
          </div>
        )}
      </Modal>
    </>
  )
}

function CoachingSessionsSub({ users }) {
  const { t } = useI18n()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedUsers, setSelectedUsers] = useState(new Set())
  const [filterAgent, setFilterAgent] = useState('')
  const [transcript, setTranscript] = useState(null)
  const [transcriptLoading, setTranscriptLoading] = useState(false)
  const [viewUuid, setViewUuid] = useState(null)

  const loadData = useCallback(() => {
    api('/api/sessions')
      .then(sess => setSessions(sess.filter(s => COACHING_AGENTS.has(s.agent_name))))
      .catch(err => console.error('Failed to load:', err))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadData() }, [loadData])

  useEffect(() => {
    window.addEventListener('coach-data-update', loadData)
    return () => window.removeEventListener('coach-data-update', loadData)
  }, [loadData])


  async function openTranscript(uuid) {
    setViewUuid(uuid)
    setTranscriptLoading(true)
    try {
      const data = await api(`/api/sessions/${uuid}/transcript`)
      setTranscript(Array.isArray(data) ? data : data.messages || [])
    } catch { setTranscript([]) }
    setTranscriptLoading(false)
  }

  // Hooks must be called unconditionally (before any early return)
  const sortCols = useMemo(() => ({
    user: s => (users[s.user_id] || '').toLowerCase(),
    agent: s => s.agent_name || '',
    context: s => s.context_key || s.agent_name || '',
    msgs: s => s.message_count || 0,
    size: s => s.file_size || 0,
    date: s => s.last_used_at || s.created_at || '',
  }), [users])

  let filtered = sessions
  if (selectedUsers.size > 0) filtered = filtered.filter(s => selectedUsers.has(String(s.user_id)))
  if (filterAgent) filtered = filtered.filter(s => s.agent_name === filterAgent)

  const { sorted, handleSort, sortArrow } = useTableSort(filtered, sortCols, 'date', 'desc')

  if (loading) return <LoadingSpinner />

  const agentNames = [...new Set(sessions.map(s => s.agent_name))].sort()

  return (
    <>
      <div className="card">
        <div className="flex-between mb-12">
          <h4>{t('admin_agent_sessions')} ({filtered.length})</h4>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select className="input-sm" value={filterAgent} onChange={e => setFilterAgent(e.target.value)} style={{ width: 160 }}>
              <option value="">{t('admin_all_agents')}</option>
              {agentNames.map(a => <option key={a} value={a}>{AGENT_LABELS[a] || a}</option>)}
            </select>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {Object.entries(users).map(([id, name]) => (
                <button key={id} className={`btn btn-xs${selectedUsers.has(id) ? ' btn-accent' : ' btn-outline'}`}
                  onClick={() => toggleUserInSet(setSelectedUsers, id)}>{name}</button>
              ))}
              {selectedUsers.size > 0 && (
                <button className="btn btn-xs btn-outline" onClick={() => setSelectedUsers(new Set())}>{t('clear')}</button>
              )}
            </div>
          </div>
        </div>
        {filtered.length === 0 ? (
          <p className="text-dim">{t('no_data')}</p>
        ) : (
          <div className="table-scroll" style={{ maxHeight: 600 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th className="sortable-th" onClick={() => handleSort('user')}>{t('admin_user')}{sortArrow('user')}</th>
                  <th className="sortable-th" onClick={() => handleSort('agent')}>{t('admin_agent')}{sortArrow('agent')}</th>
                  <th className="sortable-th" onClick={() => handleSort('context')}>{t('admin_context')}{sortArrow('context')}</th>
                  <th className="sortable-th" onClick={() => handleSort('msgs')}>{t('admin_msgs')}{sortArrow('msgs')}</th>
                  <th className="sortable-th" onClick={() => handleSort('size')}>{t('admin_size')}{sortArrow('size')}</th>
                  <th className="sortable-th" onClick={() => handleSort('date')}>{t('admin_last_active')}{sortArrow('date')}</th>
                  <th>{t('admin_file')}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(s => (
                  <tr key={s.session_uuid} className="clickable" style={{ cursor: 'pointer' }} onClick={() => openTranscript(s.session_uuid)}>
                    <td style={{ whiteSpace: 'nowrap' }}>{users[s.user_id] || `#${s.user_id || '?'}`}</td>
                    <td style={{ whiteSpace: 'nowrap' }}><span style={{ fontSize: 12 }}>{AGENT_LABELS[s.agent_name] || s.agent_name}</span></td>
                    <td style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{s.context_key || s.agent_name}</td>
                    <td>{s.message_count || '--'}</td>
                    <td className="text-dim" style={{ whiteSpace: 'nowrap' }}>{s.file_size ? fmtSize(s.file_size) : '--'}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(s.last_used_at || s.created_at)}</td>
                    <td onClick={e => e.stopPropagation()}><PathCell path={s.file_path} /></td>
                    <td style={{ fontSize: 11, color: 'var(--text-dim)' }}>▶</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <TranscriptModal viewUuid={viewUuid} transcript={transcript} transcriptLoading={transcriptLoading} onClose={() => { setViewUuid(null); setTranscript(null) }} />
    </>
  )
}


/* ─── Agent Definitions ─── */
function AgentDefinitionsTab() {
  const { t } = useI18n()
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [viewDef, setViewDef] = useState(null)
  const [viewMode, setViewMode] = useState('rendered') // 'rendered' | 'raw' | 'edit'
  const [editText, setEditText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api('/api/agents').then(d => setAgents(d.agents || [])).catch(err => console.error('Failed to load agents:', err)).finally(() => setLoading(false))
  }, [])

  function openAgent(agent) {
    setViewDef(agent)
    setViewMode('rendered')
    setEditText(agent.definition || '')
  }

  async function saveAgent() {
    if (!viewDef) return
    setSaving(true)
    try {
      await api(`/api/agents/${viewDef.name}`, { method: 'PUT', body: JSON.stringify({ definition: editText }) })
      setViewDef(prev => ({ ...prev, definition: editText }))
      setAgents(prev => prev.map(a => a.name === viewDef.name ? { ...a, definition: editText } : a))
      setViewMode('rendered')
    } catch (err) { setError(err.message) }
    setSaving(false)
  }

  if (loading) return <LoadingSpinner />

  const AGENT_GROUPS = {
    [t('admin_group_coaching')]: ['main-coach', 'run-coach', 'swim-coach', 'bike-coach', 'nutrition-coach'],
    [t('admin_group_development')]: ['frontend-dev', 'backend-dev', 'data-pipeline'],
    [t('admin_group_review')]: ['security-reviewer', 'frontend-reviewer', 'backend-reviewer', 'data-reviewer'],
  }

  // Group agents, put ungrouped ones under "Other"
  const grouped = {}
  const agentsByName = Object.fromEntries(agents.map(a => [a.name, a]))
  for (const [group, names] of Object.entries(AGENT_GROUPS)) {
    const found = names.filter(n => agentsByName[n]).map(n => agentsByName[n])
    if (found.length) grouped[group] = found
  }
  const groupedNames = new Set(Object.values(AGENT_GROUPS).flat())
  const other = agents.filter(a => !groupedNames.has(a.name))
  if (other.length) grouped[t('admin_group_other')] = other

  function renderAgent(agent) {
    return (
      <div key={agent.name} className="card mb-12" style={{ padding: '12px 16px', cursor: 'pointer' }}
        onClick={() => openAgent(agent)}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <strong style={{ fontSize: 14 }}>{agent.name}</strong>
            {agent.delegates_to?.length > 0 && (
              <span className="text-dim text-xs" style={{ marginLeft: 8 }}>
                {t('admin_delegates_to')} {agent.delegates_to.join(', ')}
              </span>
            )}
            {agent.delegated_by?.length > 0 && (
              <span className="agent-sub-badge" style={{ marginLeft: 8 }}>sub-agent</span>
            )}
            <div style={{ marginTop: 4 }} onClick={e => e.stopPropagation()}>
              <PathCell path={agent.file_path} />
            </div>
          </div>
          <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>▶</span>
        </div>
      </div>
    )
  }

  return (
    <>
      {!!error && <p className="text-red mt-8" style={{ cursor: 'pointer' }} onClick={() => setError('')}>{error}</p>}
      <p className="text-dim mb-12">{agents.length} {t('admin_agents_defined')}</p>
      {Object.entries(grouped).map(([group, groupAgents]) => (
        <div key={group} style={{ marginBottom: 20 }}>
          <h4 style={{ fontSize: 13, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>{group}</h4>
          {groupAgents.map(renderAgent)}
        </div>
      ))}

      <Modal open={viewDef != null} onClose={() => setViewDef(null)} title={viewDef ? `Agent: ${viewDef.name}` : ''} wide>
        {viewDef && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <PathCell path={viewDef.file_path} />
              <div style={{ display: 'flex', gap: 6 }}>
                {viewMode === 'edit' ? (
                  <>
                    <button className="btn btn-sm btn-outline" onClick={() => { setEditText(viewDef.definition || ''); setViewMode('rendered') }}>{t('cancel')}</button>
                    <button className="btn btn-sm btn-accent" onClick={saveAgent} disabled={saving}>{saving ? t('admin_saving') : t('save')}</button>
                  </>
                ) : (
                  <>
                    <button className="btn btn-sm" onClick={() => setViewMode(viewMode === 'raw' ? 'rendered' : 'raw')}>
                      {viewMode === 'raw' ? t('admin_rendered') : t('admin_raw')}
                    </button>
                    <button className="btn btn-sm btn-accent" onClick={() => { setEditText(viewDef.definition || ''); setViewMode('edit') }}>{t('edit')}</button>
                  </>
                )}
              </div>
            </div>
            {viewMode === 'edit' ? (
              <textarea
                value={editText}
                onChange={e => setEditText(e.target.value)}
                style={{
                  width: '100%', minHeight: '60vh', fontFamily: 'monospace', fontSize: 12,
                  background: 'var(--bg-1)', color: 'var(--text)', border: '1px solid var(--border)',
                  borderRadius: 'var(--radius)', padding: 12, resize: 'vertical',
                }}
              />
            ) : viewMode === 'raw' ? (
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, background: 'var(--bg-1)', padding: 12, borderRadius: 'var(--radius)', maxHeight: '70vh', overflow: 'auto' }}>
                {viewDef.definition || ''}
              </pre>
            ) : (
              <div className="markdown-body" dir="auto" dangerouslySetInnerHTML={md(viewDef.definition)} />
            )}
          </div>
        )}
      </Modal>
    </>
  )
}

/* ─── CLI Sessions ─── */
function CliSessionsTab() {
  const { t } = useI18n()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [transcript, setTranscript] = useState(null)
  const [transcriptLoading, setTranscriptLoading] = useState(false)
  const [viewUuid, setViewUuid] = useState(null)
  const [error, setError] = useState('')

  const sortCols = useMemo(() => ({
    name: s => (s.slug || s.context_key || s.session_uuid || '').toLowerCase(),
    msgs: s => s.message_count || 0,
    size: s => s.file_size || 0,
    date: s => s.last_used_at || s.created_at || '',
  }), [])

  useEffect(() => {
    api('/api/sessions')
      .then(data => setSessions(data.filter(s => !COACHING_AGENTS.has(s.agent_name))))
      .catch(err => console.error('Failed to load:', err)).finally(() => setLoading(false))
  }, [])

  const { sorted, handleSort, sortArrow } = useTableSort(sessions, sortCols, 'date', 'desc')

  async function openTranscript(uuid) {
    setViewUuid(uuid)
    setTranscriptLoading(true)
    try {
      const data = await api(`/api/sessions/${uuid}/transcript`)
      setTranscript(Array.isArray(data) ? data : data.messages || [])
    } catch { setTranscript([]) }
    setTranscriptLoading(false)
  }

  async function deleteSession(uuid) {
    if (!confirm('Delete this CLI session?')) return
    try {
      await api(`/api/sessions/${uuid}`, { method: 'DELETE' })
      setSessions(s => s.filter(x => x.session_uuid !== uuid))
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch (err) { setError(err.message) }
  }

  async function deleteAll() {
    if (!confirm(`Delete all ${sessions.length} CLI sessions?`)) return
    await Promise.allSettled(
      sessions.map(s => api(`/api/sessions/${s.session_uuid}`, { method: 'DELETE' }).catch(err => console.error('Failed to delete session:', err)))
    )
    setSessions([])
    window.dispatchEvent(new CustomEvent('coach-data-update'))
  }

  if (loading) return <LoadingSpinner />

  return (
    <>
      <div className="flex-between mb-12">
        <p className="text-dim">{sessions.length} {t('admin_cli_sessions_desc')}</p>
        {sessions.length > 0 && (
          <button className="btn btn-sm btn-red" onClick={deleteAll}>{t('delete_all')}</button>
        )}
      </div>
      {!!error && <p className="text-red mt-8" style={{ cursor: 'pointer' }} onClick={() => setError('')}>{error}</p>}

      {sessions.length === 0 ? (
        <div className="card" style={{ padding: 16, textAlign: 'center' }}>
          <p className="text-dim">{t('admin_no_cli_sessions')}</p>
        </div>
      ) : (
        <div className="card">
          <div className="table-scroll" style={{ maxHeight: 600 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th className="sortable-th" onClick={() => handleSort('name')}>{t('admin_session_name')}{sortArrow('name')}</th>
                  <th className="sortable-th" onClick={() => handleSort('msgs')}>{t('admin_msgs')}{sortArrow('msgs')}</th>
                  <th className="sortable-th" onClick={() => handleSort('size')}>{t('admin_size')}{sortArrow('size')}</th>
                  <th className="sortable-th" onClick={() => handleSort('date')}>{t('admin_last_active')}{sortArrow('date')}</th>
                  <th>{t('admin_file')}</th>
                  <th></th><th></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(s => (
                  <tr key={s.session_uuid} className="clickable" style={{ cursor: 'pointer' }} onClick={() => openTranscript(s.session_uuid)}>
                    <td style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{s.slug || s.context_key || s.session_uuid?.slice(0, 12)}</td>
                    <td>{s.message_count || '--'}</td>
                    <td className="text-dim" style={{ whiteSpace: 'nowrap' }}>{s.file_size ? fmtSize(s.file_size) : '--'}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(s.last_used_at || s.created_at)}</td>
                    <td onClick={e => e.stopPropagation()}><PathCell path={s.file_path} /></td>
                    <td onClick={e => e.stopPropagation()}>
                      <button className="btn btn-sm btn-red" onClick={() => deleteSession(s.session_uuid)}>{t('del')}</button>
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--text-dim)' }}>{'\u25B6'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <TranscriptModal viewUuid={viewUuid} transcript={transcript} transcriptLoading={transcriptLoading} onClose={() => { setViewUuid(null); setTranscript(null) }} />
    </>
  )
}

/* ─── Logs ─── */
function LogsTab() {
  const { t } = useI18n()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [viewFile, setViewFile] = useState(null)
  const [fileData, setFileData] = useState(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [autoTail, setAutoTail] = useState(false)
  const [error, setError] = useState('')
  const viewerRef = useRef(null)
  const tailRef = useRef(null)

  const loadList = useCallback(() => {
    api('/api/admin/logfiles').then(d => setData(d)).catch(err => console.error('Failed to load logs:', err)).finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadList() }, [loadList])

  async function openFile(name) {
    setViewFile(name)
    setFileLoading(true)
    try {
      const d = await api(`/api/admin/logfiles/${encodeURIComponent(name)}`)
      setFileData(d)
      // Auto-enable tail for the current log
      setAutoTail(d.is_current)
    } catch (err) { console.error('Failed to load log file:', err) }
    setFileLoading(false)
  }

  // Scroll to bottom when log data loads
  useEffect(() => {
    if (!fileData || fileLoading) return
    const timer = setTimeout(() => {
      if (viewerRef.current) viewerRef.current.scrollTop = viewerRef.current.scrollHeight
    }, 50)
    return () => clearTimeout(timer)
  }, [fileData, fileLoading])

  // Auto-tail: refresh every 5s for the current log
  useEffect(() => {
    if (!autoTail || !viewFile) return
    tailRef.current = setInterval(async () => {
      try {
        const d = await api(`/api/admin/logfiles/${encodeURIComponent(viewFile)}`)
        setFileData(d)
      } catch (err) { console.error('Failed to tail log:', err) }
    }, 5000)
    return () => { if (tailRef.current) clearInterval(tailRef.current) }
  }, [autoTail, viewFile])

  async function deleteFile(name) {
    if (!confirm(`Delete ${name}?`)) return
    try {
      await api(`/api/admin/logfiles/${encodeURIComponent(name)}`, { method: 'DELETE' })
      loadList()
      if (viewFile === name) { setViewFile(null); setFileData(null) }
    } catch (e) { setError(e.message) }
  }

  if (loading) return <LoadingSpinner />
  if (!data) return <div className="card">{t('admin_failed_load')}</div>

  function closeFile() { setViewFile(null); setFileData(null); setAutoTail(false) }
  function toggleFile(name) { viewFile === name ? closeFile() : openFile(name) }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 200px)' }}>
      {!!error && <p className="text-red mt-8" style={{ cursor: 'pointer' }} onClick={() => setError('')}>{error}</p>}
      <div className="card" style={{ marginBottom: 8, flexShrink: 0 }}>
        <div className="text-dim text-xs mb-12" style={{ fontFamily: 'monospace' }}>{t('admin_log_dir')} <PathCell path={data.dir} full /></div>
        <table className="data-table">
          <thead><tr><th></th><th>{t('admin_file')}</th><th>{t('admin_size')}</th><th>{t('admin_created')}</th><th>Path</th><th></th></tr></thead>
          <tbody>
            {data.files.map(f => (
              <tr key={f.name} className="clickable" style={{ cursor: 'pointer', background: viewFile === f.name ? 'var(--bg-3)' : undefined }} onClick={() => toggleFile(f.name)}>
                <td style={{ width: 20, fontSize: 11, color: 'var(--text-dim)' }}>{viewFile === f.name ? '\u25BC' : '\u25B6'}</td>
                <td style={{ fontFamily: 'monospace', fontSize: '0.85rem', whiteSpace: 'nowrap' }}>
                  {f.name}
                  {!!f.is_current && <span style={{ marginInlineStart: 6, fontSize: 10, color: 'var(--green)', fontWeight: 600 }}>CURRENT</span>}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>{fmtSize(f.size)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(f.modified)}</td>
                <td onClick={e => e.stopPropagation()}><PathCell path={f.path || `${data.dir}/${f.name}`} full /></td>
                <td style={{ whiteSpace: 'nowrap' }} onClick={e => e.stopPropagation()}>
                  {!f.is_current && (
                    <button className="btn btn-sm btn-red" onClick={() => deleteFile(f.name)}>{t('del')}</button>
                  )}
                </td>
              </tr>
            ))}
            {data.files.length === 0 && <tr><td colSpan={6} className="text-dim">{t('admin_no_logs')}</td></tr>}
          </tbody>
        </table>
      </div>
      {viewFile && (
        <div className="card" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div className="flex-between" style={{ marginBottom: 8, flexShrink: 0 }}>
            <h4 style={{ fontSize: '0.85rem', fontFamily: 'monospace', margin: 0 }}>
              {viewFile} <span className="text-dim">({fileData?.total_lines?.toLocaleString() || '...'} {t('admin_lines')})</span>
              {fileData?.is_current && <span style={{ marginInlineStart: 8, fontSize: 10, color: 'var(--green)' }}>LIVE</span>}
            </h4>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {fileData?.is_current && (
                <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                  <input type="checkbox" checked={autoTail} onChange={(e) => setAutoTail(e.target.checked)} />
                  {t('admin_auto_tail')}
                </label>
              )}
              <button className="btn btn-sm btn-outline" onClick={() => openFile(viewFile)}>{t('refresh')}</button>
              <button className="btn btn-sm" onClick={closeFile}>{t('close')}</button>
            </div>
          </div>
          {fileLoading && !fileData ? <LoadingSpinner /> : fileData ? (
            <div className="logfile-viewer" ref={viewerRef} style={{ flex: 1, minHeight: 0 }}>
              {fileData.lines.map((line, i) => (
                <div key={i} className={`logfile-line${line.includes('[ERROR]') ? ' logfile-error' : line.includes('[WARNING]') ? ' logfile-warn' : ''}`}>{line}</div>
              ))}
            </div>
          ) : <div className="text-dim">{t('admin_failed_load')}</div>}
        </div>
      )}
    </div>
  )
}

/* ─── Shared Transcript Modal ─── */
function TranscriptModal({ viewUuid, transcript, transcriptLoading, onClose }) {
  const { t } = useI18n()
  return (
    <Modal open={viewUuid != null} onClose={onClose} title={t('admin_session_transcript')} wide>
      {transcriptLoading ? <LoadingSpinner /> : (
        <div className="transcript" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
          {(!transcript || transcript.length === 0) && <p className="text-dim">{t('admin_no_messages')}</p>}
          {transcript?.filter(m => {
            if (m.role === 'tool') return false
            if (m.role === 'assistant') {
              if (typeof m.content === 'string' && !m.content.trim()) return false
              if (Array.isArray(m.content) && m.content.length === 0) return false
            }
            return true
          }).map((msg, i) => {
            const role = msg.role || 'system'
            const cssRole = role === 'human' ? 'user' : role
            let text = typeof msg.content === 'string' ? msg.content
              : Array.isArray(msg.content) ? msg.content.filter(b => b.type === 'text').map(b => b.text).join('\n')
              : JSON.stringify(msg.content, null, 2)
            if (!text.trim()) return null
            return (
              <div key={i} className={`transcript-msg ${cssRole}`}>
                <div className="transcript-role">{role === 'human' ? 'user' : role}</div>
                <div dir="auto" dangerouslySetInnerHTML={md(text)} />
              </div>
            )
          })}
        </div>
      )}
    </Modal>
  )
}
