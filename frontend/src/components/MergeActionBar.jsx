import { useState } from 'react'
import { api } from '../api'
import { autoGrow } from '../utils/formatters'
import { useI18n } from '../i18n/I18nContext'
import { notifyLlmStart, notifyLlmEnd } from './NotificationBell'

/**
 * Inline action buttons for selected workouts.
 * Returns two elements:
 * - ActionButtons: inline buttons (Hide/Unhide, Merge/Brick) for the top bar
 * - MergeContextRow: expandable context note row (only when merge mode active)
 */
export default function MergeActionBar({ workouts, onDone, showHidden }) {
  const { t } = useI18n()
  const [mergeMode, setMergeMode] = useState(null) // 'merge' | 'brick' | null
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(null)

  if (!workouts || workouts.length < 1) return null

  const disciplines = new Set(workouts.map(w => w.discipline))
  const isSameDiscipline = disciplines.size === 1
  const nums = workouts.map(w => w.workout_num).sort((a, b) => a - b)
  const canMerge = workouts.length >= 2
  const hasHidden = workouts.some(w => w._hidden)

  async function doMergeBrick() {
    setLoading(true)
    setError(null)
    try {
      if (mergeMode === 'merge') {
        const pairs = []
        for (let i = 0; i < nums.length - 1; i++) {
          pairs.push([nums[i], nums[i + 1]])
        }
        await api('/api/merges', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pairs })
        })
      }
      setSuccess(mergeMode === 'merge' ? t('merge_success') : t('brick_success'))
      const taskId = `${mergeMode}-insight-${nums.join('-')}`
      notifyLlmStart(taskId, `Insight Generation: #${nums.join(', #')}`, '/insights')
      api('/api/insights/generate-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workout_nums: nums,
          user_context: note ? Object.fromEntries(nums.map(n => [String(n), note])) : {}
        })
      }).then(() => notifyLlmEnd(taskId)).catch(e => notifyLlmEnd(taskId, e.message))
      window.dispatchEvent(new CustomEvent('notification-poll-now'))
      setTimeout(() => { setMergeMode(null); onDone() }, 1500)
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  const [confirmDelete, setConfirmDelete] = useState(false)

  async function handleDelete() {
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    setLoading(true)
    setError(null)
    try {
      await api('/api/workouts/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workout_nums: nums })
      })
      setSuccess(t('delete_success'))
      setConfirmDelete(false)
      window.dispatchEvent(new Event('pending-import-updated'))
      setTimeout(() => onDone(), 800)
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  async function handleHide() {
    setLoading(true)
    setError(null)
    try {
      await api('/api/workouts/hide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workout_nums: nums })
      })
      setSuccess(t('hide_success'))
      setTimeout(() => onDone(), 800)
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  async function handleUnhide() {
    setLoading(true)
    setError(null)
    try {
      await api('/api/workouts/unhide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workout_nums: nums })
      })
      setSuccess(t('unhide_btn'))
      setTimeout(() => onDone(), 800)
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  function startMerge() {
    setMergeMode(isSameDiscipline ? 'merge' : 'brick')
  }

  return (
    <>
      {/* Inline action buttons */}
      {canMerge && !mergeMode && (
        <button className="btn btn-sm btn-outline" onClick={startMerge} disabled={loading}>
          {isSameDiscipline ? t('merge_btn') : t('brick_btn')}
        </button>
      )}
      {hasHidden && showHidden ? (
        <button className="btn btn-sm btn-outline" onClick={handleUnhide} disabled={loading}>
          {t('unhide_btn')}
        </button>
      ) : (
        <button className="btn btn-sm btn-danger-outline" onClick={handleHide} disabled={loading}>
          {t('hide_btn')}
        </button>
      )}
      <button className="btn btn-sm btn-red" onClick={handleDelete} disabled={loading}>
        {confirmDelete ? t('delete_confirm') : t('delete_btn')}
      </button>

      {/* Merge context note — renders as block, breaks out of flex via full-width */}
      {mergeMode && (
        <div className="merge-context-row" onClick={e => e.stopPropagation()}>
          <span className="text-sm text-dim" style={{ whiteSpace: 'nowrap' }}>{nums.map(n => `#${n}`).join(', ')}</span>
          <textarea
            className="input-full"
            dir="auto"
            placeholder={t('merge_note_placeholder')}
            value={note}
            onChange={e => setNote(e.target.value)}
            onInput={autoGrow}
            rows={1}
            style={{ overflow: 'hidden', flex: 1, minWidth: 200 }}
          />
          <button className="btn btn-sm btn-accent" onClick={doMergeBrick} disabled={loading}>
            {loading ? '...' : (mergeMode === 'merge' ? t('merge_btn') : t('brick_btn'))}
          </button>
          <button className="btn btn-sm" onClick={() => { setMergeMode(null); setNote('') }}>✕</button>
        </div>
      )}

      {error && <div className="error-msg" style={{ fontSize: 12 }}>{error}</div>}
      {success && <div style={{ color: 'var(--green)', fontSize: 12, fontWeight: 500 }}>{success}</div>}
    </>
  )
}
