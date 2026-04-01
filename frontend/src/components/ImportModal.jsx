import { useState, useCallback, useRef, useEffect } from 'react'
import { api } from '../api'
import { useApp } from '../context/AppContext'
import { useI18n } from '../i18n/I18nContext'
import Modal from './common/Modal'
import PostImportModal from './PostImportModal'


export default function ImportModal({ onClose }) {
  const { refreshWorkouts } = useApp()
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])
  const { t } = useI18n()
  const [path, setPath] = useState('')
  const [status, setStatus] = useState(null)
  const [running, setRunning] = useState(false)
  const [browsing, setBrowsing] = useState(false)
  const [newWorkouts, setNewWorkouts] = useState(null)
  const [datesWithNutrition, setDatesWithNutrition] = useState([])
  const [mergeCandidates, setMergeCandidates] = useState([])
  const [brickSessions, setBrickSessions] = useState([])
  const [dragOver, setDragOver] = useState(false)
  const [droppedFile, setDroppedFile] = useState(null) // File object for upload
  const [forceRebuild, setForceRebuild] = useState(false)

  async function browse() {
    setBrowsing(true)
    try {
      const r = await api('/api/pick-folder')
      if (r.path) {
        setDroppedFile(null)
        setPath(r.path)
        setStatus(null)
      }
    } catch (e) {
      // cancelled or error — ignore
    } finally {
      setBrowsing(false)
    }
  }

  function handleImportResult(r) {
    if (r.success) {
      refreshWorkouts()
      window.dispatchEvent(new Event('coach-data-update'))
      window.dispatchEvent(new CustomEvent('notification-poll-now'))
      // Always notify Layout so pending import icon appears immediately
      window.dispatchEvent(new Event('pending-import-changed'))
      if (!mountedRef.current) return
      setStatus({ type: 'success', text: r.output || t('import_success') })
      setMergeCandidates(r.merge_candidates || [])
      setBrickSessions(r.brick_sessions || [])
      if (r.new_workouts?.length || r.merge_candidates?.length || r.brick_sessions?.length) {
        setDatesWithNutrition(r.dates_with_nutrition || [])
        setNewWorkouts(r.new_workouts || [])
      }
    } else {
      if (!mountedRef.current) return
      setStatus({ type: 'error', text: (r.errors || '') + '\n' + (r.output || '') })
      setRunning(false)
    }
  }

  const runImport = async () => {
    if (droppedFile) {
      await runUploadImport()
      return
    }
    if (!path.trim()) return
    setRunning(true)
    setStatus({ type: 'loading', text: t('import_processing_msg') })
    // Poll after short delay so bell picks up server-side active task
    setTimeout(() => window.dispatchEvent(new CustomEvent('notification-poll-now')), 500)
    try {
      const r = await api('/api/import', { method: 'POST', body: JSON.stringify({ folder_path: path.trim(), force: forceRebuild }) })
      handleImportResult(r)
    } catch (e) {
      setStatus({ type: 'error', text: 'Error: ' + e.message })
      setRunning(false)
    }
  }

  const runUploadImport = async () => {
    setRunning(true)
    setStatus({ type: 'loading', text: t('import_processing_msg') })
    setTimeout(() => window.dispatchEvent(new CustomEvent('notification-poll-now')), 500)
    try {
      const formData = new FormData()
      formData.append('file', droppedFile)
      if (forceRebuild) formData.append('force', 'true')
      const r = await fetch('/api/import/upload', { method: 'POST', body: formData })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || 'Upload failed')
      handleImportResult(data)
    } catch (e) {
      setStatus({ type: 'error', text: 'Error: ' + e.message })
      setRunning(false)
    }
  }

  // Drag-and-drop handlers
  const onDragOver = useCallback(e => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }, [])
  const onDragLeave = useCallback(e => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }, [])
  const onDrop = useCallback(e => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    const files = e.dataTransfer?.files
    if (files?.length) {
      const file = files[0]
      if (file.name.toLowerCase().endsWith('.zip')) {
        setDroppedFile(file)
        setPath('')
        setStatus(null)
      } else {
        setStatus({ type: 'error', text: t('import_drop_zip_only') })
      }
    }
  }, [t])

  function clearDropped() {
    setDroppedFile(null)
    setStatus(null)
  }

  // Show post-import modal for adding context to new workouts
  if (newWorkouts) {
    return (
      <PostImportModal
        workouts={newWorkouts}
        datesWithNutrition={datesWithNutrition}
        mergeCandidates={mergeCandidates}
        brickSessions={brickSessions}
        onClose={() => {
          api('/api/import/pending', { method: 'DELETE' }).catch(() => {})
          refreshWorkouts()
          window.dispatchEvent(new Event('coach-data-update'))
          onClose()
        }}
        onDismiss={() => {
          // Data already saved to backend — refresh icon without re-opening modal
          window.dispatchEvent(new Event('pending-import-updated'))
          refreshWorkouts()
          window.dispatchEvent(new Event('coach-data-update'))
          onClose()
        }}
        onStarted={() => {
          window.dispatchEvent(new Event('insights-started'))
        }}
      />
    )
  }

  const canImport = droppedFile || path.trim()

  return (
    <Modal title={t('import_title')} onClose={onClose} small>
      <p>{t('import_desc')}</p>

      {/* Drop zone */}
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        style={{
          border: `2px dashed ${dragOver ? 'var(--accent)' : 'var(--border)'}`,
          borderRadius: 8,
          padding: droppedFile ? '12px 16px' : '24px 16px',
          textAlign: 'center',
          marginBottom: 12,
          background: dragOver ? 'rgba(130, 170, 255, 0.06)' : 'transparent',
          transition: 'border-color 0.15s, background 0.15s',
          cursor: 'default',
        }}
      >
        {droppedFile ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--green)" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
            <span style={{ fontWeight: 600 }}>{droppedFile.name}</span>
            <span className="text-dim text-sm">({(droppedFile.size / 1024 / 1024).toFixed(0)} MB)</span>
            <button className="btn btn-sm" onClick={clearDropped} style={{ marginInlineStart: 8, padding: '2px 8px' }}>&times;</button>
          </div>
        ) : (
          <>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.5" style={{ marginBottom: 6 }}>
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <div className="text-sm text-dim">{t('import_drop')}</div>
          </>
        )}
      </div>

      {/* Or use folder path */}
      {!droppedFile && (
        <>
          <div className="text-sm text-dim" style={{ textAlign: 'center', marginBottom: 8 }}>{t('import_or')}</div>
          <div className="form-group">
            <label>{t('import_folder_path')}</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                type="text" className="input-full" value={path}
                onChange={e => setPath(e.target.value)}
                placeholder="/Users/you/Downloads/apple_health_export"
                style={{ flex: 1 }}
              />
              <button
                className="btn btn-outline"
                onClick={browse}
                disabled={browsing || running}
                style={{ whiteSpace: 'nowrap' }}
              >
                {browsing ? t('import_selecting') : t('import_browse')}
              </button>
            </div>
          </div>
        </>
      )}

      <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-dim)', marginBottom: 8 }}>
        <input type="checkbox" checked={forceRebuild} onChange={e => setForceRebuild(e.target.checked)} />
        {t('import_rebuild_all')}
      </label>

      {status && (
        <div className={`import-status ${status.type}`}>{status.text}</div>
      )}
      <div className="form-actions">
        {status?.type === 'success' ? (
          <button className="btn btn-green" onClick={onClose}>{t('close')}</button>
        ) : (
          <button className="btn btn-accent" onClick={runImport} disabled={running || !canImport}>
            {running ? t('processing') : t('import_process')}
          </button>
        )}
      </div>
    </Modal>
  )
}
