import { useState, useRef, useEffect } from 'react'
import { api } from '../api'
import { useI18n } from '../i18n/I18nContext'
import Modal from './common/Modal'
import Badge from './common/Badge'
import WorkoutDetailModal from './WorkoutDetailModal'
import { classifyType } from '../utils/classifiers'
import { fmtDateShort, fmtDur, fmtDist, fmtTime, autoGrow, detectDir } from '../utils/formatters'
import { useApp } from '../context/AppContext'

const LARGE_BATCH_THRESHOLD = 10
const MAX_FILE_SIZE_MB = 10
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

export default function PostImportModal({ workouts, datesWithNutrition = [], mergeCandidates = [], brickSessions = [], onClose, onDismiss, onStarted }) {
  const { t } = useI18n()
  const { aiEnabled } = useApp()
  const [notes, setNotes] = useState({}) // { "wnum": "text", ... }
  const [attachments, setAttachments] = useState({}) // { "wnum": [{file_path, filename}], ... }
  const fileInputRefs = useRef({}) // { "wnum": ref }
  const [selected, setSelected] = useState(() => {
    // If >10 workouts, none selected by default; otherwise all selected
    if (workouts.length > LARGE_BATCH_THRESHOLD) return {}
    const init = {}
    workouts.forEach(w => { init[w.workout_num] = true })
    return init
  })
  const [mergeApproved, setMergeApproved] = useState({}) // { "190-191": true/false }
  const [localMergeCandidates, setLocalMergeCandidates] = useState(mergeCandidates)
  const [mergedWorkouts, setMergedWorkouts] = useState([]) // workouts added from completed merges
  const [consumedNums, setConsumedNums] = useState(new Set()) // workout_b nums absorbed by merge
  const [merging, setMerging] = useState(false)
  const [starting, setStarting] = useState(false)
  const [confirmGenerate, setConfirmGenerate] = useState(false)
  const [expandedNote, setExpandedNote] = useState(null) // workout_num or null
  const expandRef = useRef(null)
  useEffect(() => {
    if (expandedNote != null && expandRef.current) {
      expandRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [expandedNote])
  const [previewNum, setPreviewNum] = useState(null) // workout num to preview in detail modal
  const [confirmSkip, setConfirmSkip] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [includeRawData, setIncludeRawData] = useState({}) // { "wnum": true/false }

  // Combine original workouts with workouts created from merges (deduped by workout_num)
  // consumedNums = workout_b numbers absorbed into workout_a — removed entirely
  const mergedNums = new Set(mergedWorkouts.map(w => w.workout_num))
  const allWorkouts = [
    ...workouts.filter(w => !mergedNums.has(w.workout_num) && !consumedNums.has(w.workout_num)),
    ...mergedWorkouts,
  ]

  const selectedCount = Object.values(selected).filter(Boolean).length
  const allSelected = allWorkouts.length > 0 && selectedCount === allWorkouts.length
  const noneSelected = selectedCount === 0
  const isLargeBatch = allWorkouts.length > LARGE_BATCH_THRESHOLD
  const hasMergesChecked = localMergeCandidates.some(mc => mergeApproved[`${mc.workout_a}-${mc.workout_b}`])

  function toggleAll() {
    if (allSelected) {
      setSelected({})
    } else {
      const next = {}
      allWorkouts.forEach(w => { next[w.workout_num] = true })
      setSelected(next)
    }
  }

  function toggleOne(wnum) {
    setSelected(prev => ({ ...prev, [wnum]: !prev[wnum] }))
  }

  function toggleMerge(key) {
    setMergeApproved(prev => ({ ...prev, [key]: !prev[key] }))
  }

  async function handleMerge() {
    const approved = localMergeCandidates.filter(mc => mergeApproved[`${mc.workout_a}-${mc.workout_b}`])
    if (!approved.length) return
    setMerging(true)
    try {
      await api('/api/merges', {
        method: 'POST',
        body: JSON.stringify({ pairs: approved.map(mc => [mc.workout_a, mc.workout_b]) }),
      })

      // Create combined workout entries (workout_a with combined stats)
      // and track consumed workout_b nums (removed from list entirely)
      const newConsumed = new Set()
      const newEntries = approved.map(mc => {
        newConsumed.add(mc.workout_b)
        return {
          workout_num: mc.workout_a,
          date: mc.date,
          type: mc.type,
          duration_min: mc.a_duration_min + mc.b_duration_min,
          distance_km: mc.a_distance_km + mc.b_distance_km,
          start_time: mc.a_start_time,
          end_time: mc.b_end_time,
        }
      })

      setConsumedNums(prev => new Set([...prev, ...newConsumed]))
      setMergedWorkouts(prev => [...prev, ...newEntries])

      // Auto-select merged workouts, deselect consumed ones
      setSelected(prev => {
        const next = { ...prev }
        newEntries.forEach(w => { next[w.workout_num] = true })
        for (const num of newConsumed) delete next[num]
        return next
      })

      // Remove merged pairs from candidates, reset their checkboxes
      const mergedKeys = new Set(approved.map(mc => `${mc.workout_a}-${mc.workout_b}`))
      setLocalMergeCandidates(prev => prev.filter(mc => !mergedKeys.has(`${mc.workout_a}-${mc.workout_b}`)))
      setMergeApproved(prev => {
        const next = { ...prev }
        for (const key of mergedKeys) delete next[key]
        return next
      })
      window.dispatchEvent(new Event('coach-data-update'))
    } finally {
      setMerging(false)
    }
  }

  async function uploadFile(wnum, file) {
    if (file.size > MAX_FILE_SIZE_BYTES) {
      setUploadError(`File "${file.name}" is too large (${(file.size / 1024 / 1024).toFixed(1)}MB). Max size: ${MAX_FILE_SIZE_MB}MB`)
      setTimeout(() => setUploadError(null), 5000)
      return
    }
    const form = new FormData()
    form.append('file', file)
    try {
      const r = await fetch('/api/chat/upload', { method: 'POST', body: form })
      const data = await r.json()
      setAttachments(prev => ({
        ...prev,
        [wnum]: [...(prev[wnum] || []), { file_path: data.file_path, filename: data.filename }],
      }))
    } catch (er) {
      setUploadError(`Upload failed: ${er.message}`)
      setTimeout(() => setUploadError(null), 5000)
    }
  }

  function removeAttachment(wnum, idx) {
    setAttachments(prev => ({
      ...prev,
      [wnum]: (prev[wnum] || []).filter((_, i) => i !== idx),
    }))
  }

  async function generateSelected() {
    setStarting(true)
    try {
      // Generate insights for selected workouts
      if (selectedCount > 0) {
        const userContext = {}
        for (const [k, v] of Object.entries(notes)) {
          if (v.trim() && selected[k]) userContext[k] = v.trim()
        }
        // Append image file paths to user context
        const userFiles = {}
        for (const [k, files] of Object.entries(attachments)) {
          if (files.length > 0 && selected[k]) userFiles[k] = files.map(f => f.file_path)
        }
        const selectedNums = allWorkouts
          .filter(w => selected[w.workout_num])
          .map(w => w.workout_num)

        // Language priority: 1) detected from user notes, 2) insightLang setting, 3) 'en'
        const allNotes = Object.values(userContext).join(' ')
        const notesDir = detectDir(allNotes)
        const detectedLang = notesDir === 'rtl' ? 'he' : (notesDir === 'ltr' ? 'en' : null)
        const insightLang = detectedLang || localStorage.getItem('insightLang') || 'en'

        // Build per-workout include_raw_data map
        const rawDataNums = selectedNums.filter(n => includeRawData[n])
        const batchBody = {
          workout_nums: selectedNums,
          user_context: Object.keys(userContext).length ? userContext : null,
          user_files: Object.keys(userFiles).length ? userFiles : null,
          lang: insightLang,
        }
        if (rawDataNums.length > 0) batchBody.include_raw_data_nums = rawDataNums
        await api('/api/insights/generate-batch', {
          method: 'POST',
          body: JSON.stringify(batchBody),
        })
        window.dispatchEvent(new CustomEvent('notification-poll-now'))
        if (onStarted) onStarted()
      }

      // Dismiss unselected workouts so they don't appear next import
      const unselectedNums = allWorkouts
        .filter(w => !selected[w.workout_num])
        .map(w => w.workout_num)
      dismissWorkouts(unselectedNums)

      onClose()
    } catch (e) {
      setStarting(false)
    }
  }

  // Estimated cost: ~$0.03 per workout (3 agent calls avg)
  const estimatedCost = (selectedCount * 0.03).toFixed(2)

  const nutritionSet = new Set(datesWithNutrition)
  const datesWithoutNutrition = [...new Set(allWorkouts.map(w => w.date))].filter(d => !nutritionSet.has(d))

  function dismissWorkouts(nums) {
    if (nums.length > 0) {
      api('/api/insights/dismiss', {
        method: 'POST',
        body: JSON.stringify({ workout_nums: nums }),
      }).catch(() => {})
    }
  }

  function doSkip() {
    dismissWorkouts(allWorkouts.map(w => w.workout_num))
    fetch('/api/insights/notifications', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: 'Insight Generation', detail: `Skipped (${allWorkouts.length} workouts)`, status: 'cancelled', link: '/insights' }),
    }).then(() => window.dispatchEvent(new CustomEvent('notification-poll-now'))).catch(() => {})
    onClose()
  }

  function handleSkip() {
    // No double-confirm when AI is off — nothing to lose
    if (!aiEnabled || confirmSkip) {
      doSkip()
    } else {
      setConfirmSkip(true)
    }
  }

  // Close modal (ESC/X) — if user already saw skip warning, just close fully
  function handleDismiss() {
    if (previewNum) return // Don't close when preview modal is open
    if (confirmSkip) { doSkip(); return }
    if (onDismiss) onDismiss()
    else onClose()
  }

  return (
    <>
    <Modal title={t('post_import_title')} onClose={handleDismiss}>
      {/* Brick sessions — shown before merge candidates */}
      {brickSessions.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <h5 style={{ marginBottom: 6 }}>{t('post_import_brick_title')}</h5>
          <p className="text-sm text-dim" style={{ marginBottom: 8 }}>{t('post_import_brick_desc')}</p>
          {brickSessions.map((bs, i) => (
            <div key={i} className="card" style={{ padding: '8px 12px', marginBottom: 6, borderInlineStart: '3px solid var(--purple)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span className="text-sm" style={{ fontWeight: 600 }}>{bs.brick_type}</span>
                <span className="text-sm text-dim">{fmtDateShort(bs.date)}</span>
                {bs.transition_times?.length > 0 && (
                  <span className="text-sm text-dim">T{bs.transition_times.map(t => `${Math.round(t)}min`).join(', ')}</span>
                )}
              </div>
              <div style={{ display: 'flex', gap: 12, marginTop: 4, flexWrap: 'wrap' }}>
                {bs.workouts.map(bw => (
                  <span key={bw.workout_num} className="text-sm" style={{ cursor: 'pointer', color: 'var(--accent)', textDecoration: 'underline', textUnderlineOffset: 2 }} onClick={() => setPreviewNum(bw.workout_num)}>
                    #{bw.workout_num} {bw.type} · {fmtTime(bw.start_time, bw.tz)}–{fmtTime(bw.end_time, bw.tz)}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Merge candidates section */}
      {localMergeCandidates.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <h5 style={{ marginBottom: 6 }}>{t('post_import_merge_title')}</h5>
          <p className="text-sm text-dim" style={{ marginBottom: 8 }}>{t('post_import_merge_desc')}</p>
          {localMergeCandidates.map(mc => {
            const key = `${mc.workout_a}-${mc.workout_b}`
            const disc = classifyType(mc.type)
            const fmtW = (num, dur, dist) => {
              const parts = [`#${num}`]
              if (dur > 0) parts.push(fmtDur(dur))
              if (dist > 0) parts.push(disc === 'swim' ? Math.round(dist * 1000) + 'm' : fmtDist(dist) + 'km')
              return parts.join(' · ')
            }
            return (
              <div key={key} className="card" style={{ padding: '8px 12px', marginBottom: 6, borderInlineStart: '3px solid var(--green)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input type="checkbox" checked={!!mergeApproved[key]} onChange={() => toggleMerge(key)} style={{ cursor: 'pointer' }} />
                  <Badge type={disc} text={mc.type} />
                  <span className="text-sm text-dim">{fmtDateShort(mc.date)} · {mc.gap_min} min gap</span>
                </div>
                <div style={{ display: 'flex', gap: 16, marginTop: 4, marginInlineStart: 28 }}>
                  <span className="text-sm" style={{ cursor: 'pointer', color: 'var(--accent)', textDecoration: 'underline', textUnderlineOffset: 2 }} onClick={() => setPreviewNum(mc.workout_a)}>
                    {fmtW(mc.workout_a, mc.a_duration_min, mc.a_distance_km)} {fmtTime(mc.a_start_time, mc.a_tz)}–{fmtTime(mc.a_end_time, mc.a_tz)}
                  </span>
                  <span className="text-sm text-dim">+</span>
                  <span className="text-sm" style={{ cursor: 'pointer', color: 'var(--accent)', textDecoration: 'underline', textUnderlineOffset: 2 }} onClick={() => setPreviewNum(mc.workout_b)}>
                    {fmtW(mc.workout_b, mc.b_duration_min, mc.b_distance_km)} {fmtTime(mc.b_start_time, mc.b_tz)}–{fmtTime(mc.b_end_time, mc.b_tz)}
                  </span>
                </div>
              </div>
            )
          })}
          <button className="btn btn-green btn-sm" onClick={handleMerge} disabled={merging || !hasMergesChecked} style={{ marginTop: 8 }}>
            {merging ? t('processing') : t('post_import_save_merges')}
          </button>
        </div>
      )}

      {/* Insights section — only show when there are workouts */}
      {allWorkouts.length > 0 && !aiEnabled && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, borderInlineStart: '3px solid var(--yellow)' }}>
          <span className="text-sm" style={{ color: 'var(--yellow)' }}>{t('ai_disabled_chat')}</span>
        </div>
      )}
      {allWorkouts.length > 0 && aiEnabled && <p className="text-sm text-dim mb-12">{t('post_import_desc')}</p>}

      {allWorkouts.length > 0 && aiEnabled && uploadError && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, borderInlineStart: '3px solid var(--red)' }}>
          <span className="text-sm" style={{ color: 'var(--red)' }}>{uploadError}</span>
        </div>
      )}

      {allWorkouts.length > 0 && aiEnabled && isLargeBatch && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, borderInlineStart: '3px solid var(--yellow)' }}>
          <span className="text-sm" style={{ color: 'var(--yellow)' }}>
            {t('post_import_large_batch', { count: allWorkouts.length })}
          </span>
        </div>
      )}

      {allWorkouts.length > 0 && aiEnabled && datesWithoutNutrition.length > 0 && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, borderInlineStart: '3px solid var(--blue)' }}>
          <span className="text-sm" style={{ color: 'var(--blue)' }}>
            {t('post_import_nutrition_missing', { dates: datesWithoutNutrition.join(', ') })}
          </span>
        </div>
      )}

      {allWorkouts.length > 0 && aiEnabled && <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={allSelected}
            ref={el => { if (el) el.indeterminate = !allSelected && !noneSelected }}
            onChange={toggleAll}
          />
          <span className="text-sm">{t('post_import_select_all')}</span>
        </label>
        {selectedCount > 0 && (
          <span className="text-sm text-dim">
            {selectedCount} selected · ~${estimatedCost} estimated
          </span>
        )}
      </div>}

      {allWorkouts.length > 0 && aiEnabled && <div style={{ maxHeight: 350, overflowY: 'auto', marginBottom: 16 }}>
        {allWorkouts.map(w => {
          const disc = classifyType(w.type)
          const dist = w.distance_km > 0
            ? (disc === 'swim' ? Math.round(w.distance_km * 1000) + ' m' : fmtDist(w.distance_km) + ' km')
            : ''
          const stats = [fmtDur(w.duration_min), dist].filter(Boolean).join(' · ')
          const isChecked = !!selected[w.workout_num]
          return (
            <div key={w.workout_num} className="card" style={{
              padding: '10px 14px', marginBottom: 8,
              opacity: isChecked ? 1 : 0.5,
              transition: 'opacity 0.15s',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggleOne(w.workout_num)}
                  style={{ cursor: 'pointer' }}
                />
                <Badge type={disc} text={w.type} />
                <span style={{ fontWeight: 600 }}>#{w.workout_num}</span>
                <span className="text-sm text-dim">{fmtDateShort(w.date)}</span>
                <span className="text-sm text-dim">{fmtTime(w.start_time, w.tz)}–{fmtTime(w.end_time, w.tz)}</span>
                <span className="text-sm text-dim">{stats}</span>
              </div>
              {isChecked && (
                <div style={{ marginInlineStart: 28, position: 'relative' }}>
                  {expandedNote === w.workout_num && <div className="expand-backdrop" onClick={() => setExpandedNote(null)} />}
                  <div ref={expandedNote === w.workout_num ? expandRef : null} className={expandedNote === w.workout_num ? 'note-expand-area expanded' : ''} style={{ display: 'flex', gap: 4, alignItems: 'flex-start' }}>
                    <textarea
                      className="input-full"
                      placeholder={t('post_import_note_placeholder')}
                      value={notes[w.workout_num] || ''}
                      onChange={e => setNotes(prev => ({ ...prev, [w.workout_num]: e.target.value }))}
                      onInput={autoGrow}
                      onKeyDown={e => { if (e.key === 'Escape' && expandedNote === w.workout_num) setExpandedNote(null) }}
                      dir="auto"
                      rows={expandedNote === w.workout_num ? 6 : 1}
                      style={{ overflow: 'hidden', resize: 'none', flex: 1 }}
                    />
                    <input type="file" accept="image/*" multiple style={{ display: 'none' }}
                      ref={el => { fileInputRefs.current[w.workout_num] = el }}
                      onChange={e => { for (const f of e.target.files) uploadFile(w.workout_num, f); e.target.value = '' }} />
                    <button
                      className="btn btn-sm btn-icon"
                      style={{ flexShrink: 0, padding: '4px 6px' }}
                      onClick={() => fileInputRefs.current[w.workout_num]?.click()}
                      title={t('attach_photo')}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                    </button>
                    <button
                      className="btn btn-sm btn-icon"
                      style={{ flexShrink: 0, padding: '4px 6px' }}
                      onClick={() => setExpandedNote(expandedNote === w.workout_num ? null : w.workout_num)}
                      title={expandedNote === w.workout_num ? 'Collapse' : 'Expand'}
                    >
                      {expandedNote === w.workout_num ? '\u2193' : '\u2191'}
                    </button>
                  </div>
                  {(attachments[w.workout_num] || []).length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                      {attachments[w.workout_num].map((af, i) => (
                        <span key={af.file_path || i} className="attached-file-tag" style={{ fontSize: 11 }}>
                          {af.filename}
                          <button onClick={() => removeAttachment(w.workout_num, i)}>&times;</button>
                        </span>
                      ))}
                    </div>
                  )}
                  <label style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 6, cursor: 'pointer' }}>
                    <input type="checkbox" checked={!!includeRawData[w.workout_num]} onChange={e => setIncludeRawData(prev => ({ ...prev, [w.workout_num]: e.target.checked }))} />
                    <span className="text-xs text-dim">{t('include_raw_data')}</span>
                  </label>
                </div>
              )}
            </div>
          )
        })}
      </div>}

      {confirmSkip && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, borderInlineStart: '3px solid var(--yellow)' }}>
          <span className="text-sm" style={{ color: 'var(--yellow)' }}>
            {t('post_import_skip_warning')}
          </span>
        </div>
      )}
      {confirmGenerate && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, borderInlineStart: '3px solid var(--yellow)' }}>
          <span className="text-sm" style={{ color: 'var(--yellow)' }}>
            {t('post_import_cost_warning', { count: selectedCount, cost: estimatedCost })}
          </span>
        </div>
      )}
      <div className="form-actions" style={{ display: 'flex', gap: 8 }}>
        {allWorkouts.length > 0 && aiEnabled && (
          <button className="btn btn-accent" onClick={() => {
            if (!confirmGenerate && selectedCount >= LARGE_BATCH_THRESHOLD) {
              setConfirmGenerate(true)
            } else {
              generateSelected()
            }
          }} disabled={starting || selectedCount === 0}>
            {starting ? t('processing') : confirmGenerate
              ? `${t('post_import_generate_confirm')} (${selectedCount})`
              : `${t('post_import_generate')} (${selectedCount})`}
          </button>
        )}
        <button className="btn" onClick={handleSkip} disabled={starting || merging}>
          {confirmSkip ? t('post_import_skip_confirm') : t('post_import_later')}
        </button>
      </div>
    </Modal>
    {previewNum && <WorkoutDetailModal workoutNum={previewNum} open onClose={() => setPreviewNum(null)} />}
    </>
  )
}
