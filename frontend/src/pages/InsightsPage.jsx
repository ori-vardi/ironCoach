import { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import { md, fmtDate, fmtDateShort, fmtTime, fmtDur, fmtDist, localDateStr, hasHebrew } from '../utils/formatters'
import { classifyType } from '../utils/classifiers'
import { useApp } from '../context/AppContext'
import { useChat } from '../context/ChatContext'
import { useI18n } from '../i18n/I18nContext'
import Badge from '../components/common/Badge'
import LoadingSpinner from '../components/common/LoadingSpinner'
import WorkoutDetailModal from '../components/WorkoutDetailModal'
import { notifyLlmStart, notifyLlmEnd } from '../components/NotificationBell'

const DEFAULT_INSIGHTS_SINCE = '2026-02-01'

export default function InsightsPage() {
  const { dateFrom, dateTo, aiEnabled } = useApp()
  const { setChatOpen, setPendingInput, newSession } = useChat()
  const { t } = useI18n()
  const [allInsights, setAllInsights] = useState([])
  const [missing, setMissing] = useState([])
  const [progress, setProgress] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [detailNum, setDetailNum] = useState(null)

  const [mode, setMode] = useState('single') // 'single' | 'batch' | 'period'
  const [singleNum, setSingleNum] = useState('')
  const [singleLoading, setSingleLoading] = useState(false)
  const [batchFrom, setBatchFrom] = useState(DEFAULT_INSIGHTS_SINCE)
  const [batchTo, setBatchTo] = useState(localDateStr(new Date()))
  const [batchLoading, setBatchLoading] = useState(false)
  const [periodFrom, setPeriodFrom] = useState(localDateStr(new Date(Date.now() - 7 * 86400000)))
  const [periodTo, setPeriodTo] = useState(localDateStr(new Date()))
  const [periodInsights, setPeriodInsights] = useState([])
  const [periodLoading, setPeriodLoading] = useState(false)
  const [insightLang, setInsightLang] = useState(localStorage.getItem('insightLang') || 'en')
  const [includeRawData, setIncludeRawData] = useState(false)
  const periodAbortRef = useRef(null)

  const [nutritionMissing, setNutritionMissing] = useState(false)
  const pollRef = useRef(null)
  const abortRef = useRef(null)
  const location = useLocation()

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [ins, status, miss, period] = await Promise.all([
        api('/api/insights/all'),
        api('/api/insights/status'),
        api(`/api/insights/missing?since_date=${DEFAULT_INSIGHTS_SINCE}`),
        api('/api/insights/period'),
      ])
      setAllInsights(ins)
      setMissing(miss)
      setPeriodInsights(period)
      if (miss.length && !singleNum) setSingleNum(String(miss[0].workout_num))
      if (status.running) {
        setProgress(status)
        startPolling()
      } else {
        setProgress(null)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetchAll()
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [fetchAll])

  // Scroll to specific workout insight card when navigated via hash
  useEffect(() => {
    if (!location.hash || loading) return
    const timer = setTimeout(() => {
      const el = document.getElementById(location.hash.slice(1))
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 300)
    return () => clearTimeout(timer)
  }, [location.hash, loading, allInsights])

  // Check if selected workout's date has nutrition data
  useEffect(() => {
    if (!singleNum || !missing.length) { setNutritionMissing(false); return }
    const workout = missing.find(m => String(m.workout_num) === String(singleNum))
    if (!workout) { setNutritionMissing(false); return }
    const date = (workout.date || '').slice(0, 10)
    if (!date) { setNutritionMissing(false); return }
    api(`/api/nutrition?date=${date}`)
      .then(meals => setNutritionMissing(!meals || meals.length === 0))
      .catch(() => setNutritionMissing(false))
  }, [singleNum, missing])

  function startPolling() {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const s = await api('/api/insights/status')
        setProgress(s)
        if (!s.running) {
          clearInterval(pollRef.current)
          pollRef.current = null
          fetchAll()
        }
      } catch (err) { console.error('Failed to poll:', err) }
    }, 3000)
  }

  async function generateSingle() {
    if (!singleNum) return
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setSingleLoading(true)
    notifyLlmStart('insight-single', `Insight #${singleNum}`, `/insights#workout-${singleNum}`)
    let err = null
    try {
      const body = { lang: insightLang }
      if (includeRawData) body.include_raw_data = true
      await api(`/api/insights/generate/${singleNum}`, { method: 'POST', body: JSON.stringify(body), signal: ctrl.signal })
      fetchAll()
    } catch (e) {
      if (e.name !== 'AbortError') { setError(e.message); err = e.message }
    } finally {
      abortRef.current = null
      setSingleLoading(false)
      notifyLlmEnd('insight-single', err)
    }
  }

  async function startBatch() {
    setBatchLoading(true)
    try {
      await api('/api/insights/generate-batch', {
        method: 'POST',
        body: JSON.stringify({ since_date: batchFrom, to_date: batchTo, lang: insightLang }),
      })
      startPolling()
      window.dispatchEvent(new CustomEvent('notification-poll-now'))
    } catch (e) {
      setError(e.message)
    } finally {
      setBatchLoading(false)
    }
  }

  function applyPreset(preset) {
    const now = new Date()
    const day = now.getDay() // 0=Sun
    switch (preset) {
      case 'this_week': {
        const mon = new Date(now); mon.setDate(now.getDate() - ((day + 6) % 7))
        setPeriodFrom(localDateStr(mon)); setPeriodTo(localDateStr(now))
        break
      }
      case 'last_week': {
        const mon = new Date(now); mon.setDate(now.getDate() - ((day + 6) % 7) - 7)
        const sun = new Date(mon); sun.setDate(mon.getDate() + 6)
        setPeriodFrom(localDateStr(mon)); setPeriodTo(localDateStr(sun))
        break
      }
      case 'this_month': {
        setPeriodFrom(localDateStr(new Date(now.getFullYear(), now.getMonth(), 1))); setPeriodTo(localDateStr(now))
        break
      }
      case 'last_month': {
        const first = new Date(now.getFullYear(), now.getMonth() - 1, 1)
        const last = new Date(now.getFullYear(), now.getMonth(), 0)
        setPeriodFrom(localDateStr(first)); setPeriodTo(localDateStr(last))
        break
      }
    }
  }

  async function generatePeriod() {
    const ctrl = new AbortController()
    periodAbortRef.current = ctrl
    setPeriodLoading(true)
    notifyLlmStart('period-insights', t('period_title'), '/insights')
    let err = null
    try {
      await api('/api/insights/period/generate', {
        method: 'POST',
        body: JSON.stringify({ from_date: periodFrom, to_date: periodTo, lang: insightLang }),
        signal: ctrl.signal,
      })
      const period = await api('/api/insights/period')
      setPeriodInsights(period)
    } catch (e) {
      if (e.name !== 'AbortError') { setError(e.message); err = e.message }
    } finally {
      periodAbortRef.current = null
      setPeriodLoading(false)
      notifyLlmEnd('period-insights', err)
    }
  }

  async function deletePeriodOne(id) {
    try {
      await api(`/api/insights/period/${id}`, { method: 'DELETE' })
      setPeriodInsights(prev => prev.filter(p => p.id !== id))
    } catch (e) { setError(e.message) }
  }

  function discussInsight(workoutNum, workoutType, workoutDate, insightText) {
    const snippet = (insightText || '').slice(0, 500)
    const disc = classifyType(workoutType)
    const agentMap = { run: 'run-coach', swim: 'swim-coach', bike: 'bike-coach' }
    const agent = agentMap[disc] || 'main-coach'
    const msg = `Let's discuss workout #${workoutNum} (${workoutType}, ${workoutDate}). Here's the current insight: ${snippet}... `
    newSession(agent)
    setPendingInput(msg)
    setChatOpen(true)
  }

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">Error: {error}</div>

  return (
    <>
      <h1 className="page-title">{t('page_insights')}</h1>

      {/* Generate Insights */}
      <div className="card mb-20">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <h4 style={{ margin: 0 }}>{t('insights_generate_title')}</h4>
          <select
            className="input-full"
            style={{ width: 'auto', minWidth: 100, padding: '4px 8px', fontSize: '0.85rem' }}
            value={insightLang}
            onChange={(e) => { setInsightLang(e.target.value); localStorage.setItem('insightLang', e.target.value) }}
          >
            <option value="en">English</option>
            <option value="he">עברית</option>
          </select>
        </div>

        {/* Mode tabs */}
        <div style={{ display: 'flex', gap: 0, marginBottom: 16, borderBottom: '1px solid var(--border)' }}>
          {[
            { key: 'single', label: t('insights_single_workout') },
            { key: 'batch', label: `${t('insights_batch_label')} (${missing.length})` },
            { key: 'period', label: t('period_title') },
          ].map(tab => (
            <button
              key={tab.key}
              className={`btn btn-sm${mode === tab.key ? ' btn-accent' : ''}`}
              style={{ borderRadius: '6px 6px 0 0', borderBottom: mode === tab.key ? '2px solid var(--accent)' : '2px solid transparent' }}
              onClick={() => setMode(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Single workout — one AI insight per workout */}
        {mode === 'single' && (() => {
          const filtered = missing.filter(m => {
            const d = (m.date || '').slice(0, 10)
            return d >= batchFrom && d <= batchTo
          })
          return (
            <div>
              <p className="text-sm text-dim mb-12">{t('insights_single_desc')}</p>
              <div className="form-row" style={{ maxWidth: 500, marginBottom: 12 }}>
                <div className="form-group">
                  <label>{t('insights_from')}</label>
                  <input type="date" className="input-full" value={batchFrom} onChange={(e) => setBatchFrom(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>{t('insights_to')}</label>
                  <input type="date" className="input-full" value={batchTo} onChange={(e) => setBatchTo(e.target.value)} />
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <select
                  className="input-full"
                  style={{ maxWidth: 360 }}
                  value={singleNum}
                  onChange={(e) => setSingleNum(e.target.value)}
                >
                  {filtered.length ? filtered.map((m) => (
                    <option key={m.workout_num} value={m.workout_num}>
                      #{m.workout_num} — {m.type} — {fmtDateShort(m.date)} — {fmtDur(m.duration_min)}
                      {m.distance_km > 0 ? ', ' + fmtDist(m.distance_km) + ' km' : ''}
                    </option>
                  )) : <option value="">{t('insights_no_pending')}</option>}
                </select>
                {singleLoading ? (
                  <button className="btn btn-red btn-sm" onClick={() => abortRef.current?.abort()}>{t('stop')}</button>
                ) : (
                  <button className="btn btn-accent btn-sm" onClick={generateSingle} disabled={!filtered.length || !aiEnabled}>
                    {aiEnabled ? t('generate') : t('ai_disabled_btn')}
                  </button>
                )}
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={includeRawData} onChange={e => setIncludeRawData(e.target.checked)} />
                <span className="text-sm text-dim">{t('include_raw_data')}</span>
              </label>
              {nutritionMissing && (
                <div style={{ marginTop: 8, padding: '6px 10px', borderInlineStart: '3px solid var(--blue)', background: 'var(--bg-2)', borderRadius: 4 }}>
                  <span className="text-sm" style={{ color: 'var(--blue)' }}>
                    {t('insights_no_nutrition')}
                  </span>
                </div>
              )}
              <p className="text-sm text-dim" style={{ marginTop: 8 }}>{filtered.length} {t('insights_pending')}</p>
            </div>
          )
        })()}

        {/* Batch — generate per-workout insight for all pending */}
        {mode === 'batch' && (() => {
          const filtered = missing.filter(m => {
            const d = (m.date || '').slice(0, 10)
            return d >= batchFrom && d <= batchTo
          })
          return (
            <div>
              <p className="text-sm text-dim mb-12">{t('insights_batch_desc')}</p>
              <div className="form-row" style={{ maxWidth: 500 }}>
                <div className="form-group">
                  <label>{t('insights_from')}</label>
                  <input type="date" className="input-full" value={batchFrom} onChange={(e) => setBatchFrom(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>{t('insights_to')}</label>
                  <input type="date" className="input-full" value={batchTo} onChange={(e) => setBatchTo(e.target.value)} />
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="btn btn-outline" onClick={startBatch} disabled={batchLoading || !aiEnabled}>
                  {!aiEnabled ? t('ai_disabled_btn') : batchLoading ? t('insights_starting') : `${t('insights_generate_all')} (${filtered.length} ${t('insights_pending')})`}
                </button>
                {progress?.running && (
                  <button className="btn btn-red btn-sm" onClick={async () => {
                    try { await api('/api/insights/batch/stop', { method: 'POST' }) } catch (err) { console.error('Failed to stop batch:', err) }
                  }}>{t('stop')}</button>
                )}
              </div>
            </div>
          )
        })()}

        {/* Period assessment */}
        {mode === 'period' && (
          <div>
            <p className="text-sm text-dim mb-12">{t('period_desc')}</p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              {['this_week', 'last_week', 'this_month', 'last_month'].map(p => (
                <button key={p} className="btn btn-sm" onClick={() => applyPreset(p)}>{t(`period_${p}`)}</button>
              ))}
            </div>
            <div className="form-row" style={{ maxWidth: 500 }}>
              <div className="form-group">
                <label>{t('insights_from')}</label>
                <input type="date" className="input-full" value={periodFrom} onChange={(e) => setPeriodFrom(e.target.value)} />
              </div>
              <div className="form-group">
                <label>{t('insights_to')}</label>
                <input type="date" className="input-full" value={periodTo} onChange={(e) => setPeriodTo(e.target.value)} />
              </div>
            </div>
            {periodLoading ? (
              <button className="btn btn-red btn-sm" onClick={() => periodAbortRef.current?.abort()}>{t('stop')}</button>
            ) : (
              <button className="btn btn-outline" onClick={generatePeriod} disabled={!aiEnabled}>{aiEnabled ? t('period_generate') : t('ai_disabled_btn')}</button>
            )}
          </div>
        )}
      </div>

      {/* Progress bar */}
      {progress?.running && (
        <div className="insight-progress">
          {t('insights_progress')}: {progress.completed}/{progress.total} {progress.current ? '— ' + progress.current : ''}
          <div className="bar">
            <div className="bar-fill" style={{ width: `${progress.total > 0 ? Math.round(progress.completed / progress.total * 100) : 0}%` }} />
          </div>
        </div>
      )}

      {/* Period Insight Results */}
      {periodInsights.map(item => (
        <div key={item.id} className="card mb-20">
          <div className="flex-between mb-12">
            <h4>{t('period_title')}: {fmtDateShort(item.from_date)} — {fmtDateShort(item.to_date)}</h4>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="text-sm text-dim">{fmtDate(item.generated_at)}</span>
              <button className="btn btn-sm btn-red" onClick={() => deletePeriodOne(item.id)}>{t('del')}</button>
            </div>
          </div>
          <div className="insight-general" dir={hasHebrew(item.content) ? 'rtl' : 'auto'} dangerouslySetInnerHTML={md(item.content)} />
        </div>
      ))}

      {/* Per-Workout Insights (filtered by global date range) */}
      {(() => {
        const filtered = allInsights.filter(ins => {
          const d = (ins.workout_date || '').slice(0, 10)
          return d >= dateFrom && d <= dateTo
        })
        return (
          <>
            <h3 className="mb-12" style={{ color: 'var(--text-dim)' }}>
              {t('insights_per_workout')} ({filtered.length}{filtered.length !== allInsights.length ? ` ${t('of')} ${allInsights.length}` : ''})
            </h3>
            {!filtered.length ? (
              <p className="text-dim">{t('insights_no_in_range')}</p>
            ) : (
              filtered.map((ins) => (
                <InsightCard
                  key={ins.workout_num}
                  id={`workout-${ins.workout_num}`}
                  ins={ins}
                  onDiscuss={discussInsight}
                  onViewDetail={setDetailNum}
                  aiEnabled={aiEnabled}
                />
              ))
            )}
          </>
        )
      })()}

      {detailNum != null && (
        <WorkoutDetailModal workoutNum={detailNum} open={true} onClose={() => setDetailNum(null)} />
      )}
    </>
  )
}

function InsightCard({ id, ins, onDiscuss, onViewDetail, aiEnabled }) {
  const { t } = useI18n()
  const [collapsed, setCollapsed] = useState(false)

  const dur = ins.duration_min ? fmtDur(ins.duration_min) : ''
  const dist = ins.distance_km > 0
    ? (ins.discipline === 'swim' ? Math.round(ins.distance_km * 1000) + ' m' : fmtDist(ins.distance_km) + ' km')
    : ''
  const hr = ins.hr_avg ? `HR ${Math.round(ins.hr_avg)}/${Math.round(ins.hr_max)}` : ''
  const cal = ins.calories ? `${Math.floor(ins.calories)} cal` : ''
  const startTime = ins.start_time ? fmtTime(ins.start_time, ins.tz) : ''
  const stats = [dur, dist, hr, cal].filter(Boolean).join(' \u00b7 ')

  return (
    <div className="insight-card" id={id}>
      <div className="insight-card-header" onClick={() => setCollapsed(!collapsed)}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
          <Badge type={classifyType(ins.workout_type)} text={ins.workout_type} />
          <h4 style={{ margin: 0 }}>
            <span className="clickable" onClick={(e) => { e.stopPropagation(); onViewDetail(ins.workout_num) }}>
              #{ins.workout_num}
            </span>
            {' — '}{fmtDate(ins.workout_date)} {startTime}
          </h4>
          <span className="text-sm text-dim">{stats}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            className="btn btn-sm"
            onClick={(e) => { e.stopPropagation(); onDiscuss(ins.workout_num, ins.workout_type, ins.workout_date, ins.insight) }}
            disabled={!aiEnabled}
          >
            {t('insights_discuss')}
          </button>
          <button
            className="btn btn-sm"
            onClick={(e) => { e.stopPropagation(); onViewDetail(ins.workout_num) }}
          >
            {t('insights_view')}
          </button>
          <span className="toggle">{collapsed ? t('insights_show') : t('insights_hide')}</span>
        </div>
      </div>
      {!collapsed && (
        <div className="insight-card-body">
          <div dir={hasHebrew(ins.insight) ? 'rtl' : 'auto'} dangerouslySetInnerHTML={md(ins.insight)} />
          {ins.plan_comparison && (
            <div className="insight-plan-cmp" dir={hasHebrew(ins.plan_comparison) ? 'rtl' : 'auto'} dangerouslySetInnerHTML={md(ins.plan_comparison)} />
          )}
        </div>
      )}
    </div>
  )
}
