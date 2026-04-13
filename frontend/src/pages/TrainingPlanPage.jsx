import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { localDateStr, fmtDur, fmtDist, fmtDate, fmtTime, safef, autoGrow } from '../utils/formatters'
import { useApp } from '../context/AppContext'
import { useI18n } from '../i18n/I18nContext'
import { classifyType, trainingPhase } from '../utils/classifiers'
import Badge from '../components/common/Badge'
import Modal from '../components/common/Modal'
import ConfirmDialog from '../components/common/ConfirmDialog'
import InfoTip from '../components/common/InfoTip'
import LoadingSpinner from '../components/common/LoadingSpinner'
import WorkoutDetailModal from '../components/WorkoutDetailModal'

const SHORT_TYPE = {
  Swimming: 'Swim', Running: 'Run', Cycling: 'Bike',
  Walking: 'Walk', TraditionalStrengthTraining: 'Strength',
  FunctionalStrengthTraining: 'Strength', JumpRope: 'Jump Rope',
}

const EMPTY_FORM = {
  date: '', discipline: 'run', title: '', description: '', notes: '',
  duration_planned_min: 0, distance_planned_km: 0, intensity: 'easy',
}

export default function TrainingPlanPage() {
  const navigate = useNavigate()
  const { workouts, setWorkouts } = useApp()
  const { t } = useI18n()
  const [plan, setPlan] = useState([])
  const [race, setRace] = useState(null)
  const [allEvents, setAllEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [viewMonth, setViewMonth] = useState(() => {
    const now = new Date()
    return { year: now.getFullYear(), month: now.getMonth() }
  })

  // Day detail modal
  const [dayModalOpen, setDayModalOpen] = useState(false)
  const [dayModalDate, setDayModalDate] = useState(null)

  // Workout detail modal
  const [detailNum, setDetailNum] = useState(null)

  // Plan form
  const [formOpen, setFormOpen] = useState(false)
  const [formData, setFormData] = useState(EMPTY_FORM)
  const [editingId, setEditingId] = useState(null)

  // Expand text overlay
  const [expandText, setExpandText] = useState(null)

  useEffect(() => {
    if (expandText == null) return
    const handler = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); setExpandText(null) }
    }
    window.addEventListener('keydown', handler, true)
    return () => window.removeEventListener('keydown', handler, true)
  }, [expandText])

  // Confirm dialog
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmTarget, setConfirmTarget] = useState(null)

  const loadData = useCallback(async () => {
    try {
      const [planData, raceData, workoutData, eventsData] = await Promise.all([
        api('/api/plan'),
        api('/api/race'),
        workouts.length ? Promise.resolve(workouts) : api('/api/summary'),
        api('/api/events').catch(() => []),
      ])
      setPlan(planData)
      setRace(raceData)
      setAllEvents(eventsData || [])
      if (!workouts.length) setWorkouts(workoutData)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [workouts, setWorkouts])

  useEffect(() => { loadData() }, [loadData])

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">Error: {error}</div>

  const today = new Date(); today.setHours(0, 0, 0, 0)
  const primaryEv = allEvents.find(e => e.is_primary)
  const raceDate = primaryEv?.event_date || race?.race_date || race?.event_date || ''
  const raceDt = new Date(raceDate + 'T00:00:00')

  // Phase bar — proportional: taper (14d) + peak (14d) + mid (40% of rest) + build (60% of rest)
  // Use first workout date as training start (or 6 months before race as fallback)
  const dates = workouts.map(w => w.startDate?.slice(0, 10)).filter(Boolean).sort()
  const trainStart = dates.length > 0
    ? new Date(dates[0] + 'T00:00:00')
    : new Date(raceDt.getTime() - 180 * 86400000)
  trainStart.setHours(0, 0, 0, 0)
  const taperStart = new Date(raceDt); taperStart.setDate(taperStart.getDate() - 14)
  const peakStart = new Date(raceDt); peakStart.setDate(peakStart.getDate() - 28)
  const totalTrainDays = Math.max(1, Math.ceil((raceDt - trainStart) / 86400000))
  const remainingDays = Math.max(0, totalTrainDays - 28)
  const midDays = Math.max(14, Math.round(remainingDays * 0.4))
  const buildDays = Math.max(0, remainingDays - midDays)
  const midStart = new Date(peakStart); midStart.setDate(midStart.getDate() - midDays)

  const monthName = new Date(viewMonth.year, viewMonth.month)
    .toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  // Lookups
  const actualByDate = {}
  workouts.forEach(w => {
    const d = (w.startDate || '').slice(0, 10)
    if (!d) return
    if (!actualByDate[d]) actualByDate[d] = []
    actualByDate[d].push({ ...w, discipline: w.discipline || classifyType(w.type) })
  })

  const planByDate = {}
  plan.forEach(p => {
    if (!planByDate[p.date]) planByDate[p.date] = []
    planByDate[p.date].push(p)
  })

  // Event dates lookup
  const eventsByDate = {}
  allEvents.forEach(ev => {
    if (ev.event_date) eventsByDate[ev.event_date] = ev
  })

  // Calendar grid
  const dayHeaders = ['day_sun', 'day_mon', 'day_tue', 'day_wed', 'day_thu', 'day_fri', 'day_sat'].map(k => t(k))
  const firstOfMonth = new Date(viewMonth.year, viewMonth.month, 1)
  const startDay = new Date(firstOfMonth)
  startDay.setDate(startDay.getDate() - startDay.getDay())
  const lastOfMonth = new Date(viewMonth.year, viewMonth.month + 1, 0)

  const calendarDays = []
  const cur = new Date(startDay)
  while (cur <= lastOfMonth || cur.getDay() !== 0) {
    calendarDays.push(new Date(cur))
    cur.setDate(cur.getDate() + 1)
    if (cur > lastOfMonth && cur.getDay() === 0) break
  }

  function changeMonth(delta) {
    setViewMonth(prev => {
      let m = prev.month + delta
      let y = prev.year
      if (m > 11) { m = 0; y++ }
      if (m < 0) { m = 11; y-- }
      return { year: y, month: m }
    })
  }

  function goToday() {
    const now = new Date()
    setViewMonth({ year: now.getFullYear(), month: now.getMonth() })
  }

  function openDayDetail(dateStr) {
    setDayModalDate(dateStr)
    setDayModalOpen(true)
  }

  function openAddForm(dateStr) {
    setEditingId(null)
    setFormData({ ...EMPTY_FORM, date: dateStr || localDateStr(new Date()) })
    setFormOpen(true)
    setDayModalOpen(false)
  }

  async function openEditForm(id) {
    const item = plan.find(p => p.id === id)
    if (!item) return
    setEditingId(id)
    setFormData({
      date: item.date,
      discipline: item.discipline,
      title: item.title || '',
      description: item.description || '',
      duration_planned_min: item.duration_planned_min || 0,
      distance_planned_km: item.distance_planned_km || 0,
      intensity: item.intensity || 'easy',
      completed: item.completed,
    })
    setFormOpen(true)
  }

  async function savePlanItem() {
    const data = {
      date: formData.date,
      discipline: formData.discipline,
      title: formData.title,
      description: formData.description,
      duration_planned_min: parseFloat(formData.duration_planned_min) || 0,
      distance_planned_km: parseFloat(formData.distance_planned_km) || 0,
      intensity: formData.intensity,
      notes: formData.notes || '',
      phase: 'build',
    }
    if (editingId) {
      await api(`/api/plan/${editingId}`, { method: 'PUT', body: JSON.stringify(data) })
    } else {
      await api('/api/plan', { method: 'POST', body: JSON.stringify(data) })
    }
    setFormOpen(false)
    setLoading(true)
    loadData()
  }

  async function toggleComplete(id, val) {
    await api(`/api/plan/${id}`, { method: 'PUT', body: JSON.stringify({ completed: val }) })
    setLoading(true)
    loadData()
  }

  function requestDeletePlan(id) {
    setConfirmTarget(id)
    setConfirmOpen(true)
  }

  async function confirmDeletePlan() {
    if (!confirmTarget) return
    await api(`/api/plan/${confirmTarget}`, { method: 'DELETE' })
    setConfirmOpen(false)
    setConfirmTarget(null)
    setFormOpen(false)
    setLoading(true)
    loadData()
  }

  // Day detail data
  const dayPlan = dayModalDate ? (planByDate[dayModalDate] || []) : []
  const dayActual = dayModalDate ? (actualByDate[dayModalDate] || []) : []
  const dayEventDetail = dayModalDate ? eventsByDate[dayModalDate] : null

  return (
    <>
      <div className="flex-between mb-20">
        <h1 className="page-title" style={{ margin: 0 }}>{t('page_plan')}</h1>
        <button className="btn btn-accent" onClick={() => openAddForm()}>+ {t('plan_add_workout')}</button>
      </div>

      {(() => {
        const totalDays = buildDays + midDays + 14 + 14
        const daysFromStart = Math.ceil((today - trainStart) / 86400000)
        const todayPct = totalDays > 0 ? Math.max(0, Math.min(100, (daysFromStart / totalDays) * 100)) : 0
        return (
          <div style={{ position: 'relative', marginBottom: 20 }}>
            <div className="phase-bar" style={{ marginBottom: 0 }}>
              <div className="phase-build" style={{ flex: buildDays }} title={t('info_phase_build')}>{buildDays > 20 ? t('plan_phase_build') : ''}</div>
              {midDays > 0 && <div className="phase-mid" style={{ flex: midDays }} title={t('info_phase_mid')}>{midDays > 14 ? t('plan_phase_mid') : ''}</div>}
              <div className="phase-peak" style={{ flex: 14 }} title={t('info_phase_peak')}>{t('plan_phase_peak')}</div>
              <div className="phase-taper" style={{ flex: 14 }} title={t('info_phase_taper')}>{t('plan_phase_taper')}</div>
            </div>
            {todayPct > 0 && todayPct < 100 && (
              <div style={{
                position: 'absolute', left: `${todayPct}%`, top: -8, transform: 'translateX(-50%)',
                display: 'flex', flexDirection: 'column', alignItems: 'center', pointerEvents: 'none', zIndex: 2,
              }}>
                <div style={{ fontSize: 14, color: '#ff966c', fontWeight: 700, lineHeight: 1 }}>▼</div>
                <div style={{
                  width: 2.5, height: 32, background: '#ff966c',
                }} />
                <div style={{ fontSize: 10, color: '#ff966c', fontWeight: 700, marginTop: 1 }}>{t('today')}</div>
              </div>
            )}
            <div className="phase-labels" style={{ display: 'flex', marginTop: 4, fontSize: 10, color: 'var(--text-dim)' }}>
              <span style={{ flex: buildDays, textAlign: 'center', whiteSpace: 'nowrap' }}>
                {t('plan_phase_build')} <InfoTip text={t('info_phase_build')} />
              </span>
              {midDays > 0 && (
                <span style={{ flex: midDays, textAlign: 'center', whiteSpace: 'nowrap' }}>
                  {t('plan_phase_mid')} ({midStart.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' })}) <InfoTip text={t('info_phase_mid')} />
                </span>
              )}
              <span style={{ flex: 14, textAlign: 'center', whiteSpace: 'nowrap' }}>
                {t('plan_phase_peak')} ({peakStart.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' })}) <InfoTip text={t('info_phase_peak')} />
              </span>
              <span style={{ flex: 14, textAlign: 'center', whiteSpace: 'nowrap' }}>
                {t('plan_phase_taper')} ({taperStart.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' })}) <InfoTip text={t('info_phase_taper')} />
              </span>
            </div>
          </div>
        )
      })()}

      <div className="flex-between mb-12">
        <div className="plan-month-nav">
          <button className="btn btn-sm" onClick={() => changeMonth(-1)}>&lt; {t('plan_prev')}</button>
          <h3>{monthName}</h3>
          <button className="btn btn-sm" onClick={() => changeMonth(1)}>{t('plan_next')} &gt;</button>
        </div>
        <button className="btn btn-sm btn-outline" onClick={goToday}>{t('today')}</button>
      </div>

      <div className="plan-calendar" id="plan-cal">
        {dayHeaders.map(d => (
          <div key={d} className="plan-day-header">{d}</div>
        ))}
        {calendarDays.map(day => {
          const ds = localDateStr(day)
          const isToday = ds === localDateStr(today)
          const isPast = day < today
          const isRace = ds === raceDate
          const dayEvent = eventsByDate[ds]
          const inMonth = day.getMonth() === viewMonth.month
          const planned = planByDate[ds] || []
          const actual = actualByDate[ds] || []

          // Match planned items to actual workouts by discipline
          const matchedDiscs = new Set()
          const plannedWithActual = planned.map(item => {
            const match = actual.find(w => (w.discipline || classifyType(w.type)) === item.discipline && !matchedDiscs.has(w.workout_num))
            if (match) matchedDiscs.add(match.workout_num)
            return { ...item, matchedWorkout: match || null }
          })
          // Unplanned actuals = workouts not matched to any plan item
          const unplanned = actual.filter(w => !matchedDiscs.has(w.workout_num))

          return (
            <div
              key={ds}
              className={`plan-day clickable${isToday ? ' today' : ''}${isPast ? ' past' : ''}${!inMonth ? ' text-dim' : ''}${dayEvent ? ' event-day' : ''}`}
              onClick={() => openDayDetail(ds)}
              title="Click for day detail"
            >
              <div className="plan-day-date">
                {day.getDate()}
                {dayEvent && <span className="plan-event-marker" title={dayEvent.event_name}>{dayEvent.is_primary ? ' \u{1F3C5}' : ' \u{1F3AF}'}</span>}
                {isToday ? ` (${t('today')})` : ''}
              </div>
              {dayEvent && (
                <div className="plan-day-event" style={{ cursor: 'pointer' }}
                  onClick={(e) => { e.stopPropagation(); navigate('/events') }}
                  title={`${dayEvent.event_name} — click to view`}
                >{dayEvent.event_name}</div>
              )}
              {plannedWithActual.map(item => {
                const done = item.completed || !!item.matchedWorkout
                const missed = isPast && !isToday && !done && item.discipline !== 'rest'
                const dur = item.matchedWorkout ? safef(item.matchedWorkout.duration_min) : null
                return (
                  <div
                    key={`p-${item.id}`}
                    className={`plan-day-item ${item.discipline}${done ? ' completed' : ''}${missed ? ' missed' : ''}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (item.matchedWorkout) setDetailNum(item.matchedWorkout.workout_num)
                      else openEditForm(item.id)
                    }}
                    title={item.description || item.title}
                  >
                    {missed ? '✗ ' : done ? '✓ ' : ''}{item.title || item.discipline}
                    {dur ? ` (${fmtDur(dur)})` : ''}
                  </div>
                )
              })}
              {unplanned.map(w => {
                const dur = safef(w.duration_min)
                const typeName = SHORT_TYPE[w.type] || w.type || w.discipline
                const label = `${typeName} ${dur ? fmtDur(dur) : ''}`
                return (
                  <div
                    key={`a-${w.workout_num}`}
                    className={`plan-day-actual ${w.discipline}`}
                    style={{ cursor: 'pointer' }}
                    onClick={(e) => { e.stopPropagation(); setDetailNum(w.workout_num) }}
                    title={`Unplanned: ${label} (click to view)`}
                  >
                    {label}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>

      {/* Day Detail Modal */}
      <Modal open={dayModalOpen} onClose={() => setDayModalOpen(false)} title={`${t('plan_day_detail')} — ${dayModalDate ? fmtDate(dayModalDate) : ''}`}>
        {dayEventDetail && (
          <div className="day-detail-section event-banner" style={{ background: 'rgba(130,170,255,0.1)', borderRadius: 'var(--radius)', padding: '12px 16px', marginBottom: 16 }}>
            <div className="flex-between">
              <div>
                <strong style={{ fontSize: '1.1em' }}>{dayEventDetail.is_primary ? '🏅 ' : '🎯 '}{dayEventDetail.event_name}</strong>
                <div className="text-sm text-dim" style={{ marginTop: 4 }}>
                  {dayEventDetail.event_type?.replace('_', ' ')}
                  {dayEventDetail.swim_km > 0 && ` · Swim ${dayEventDetail.swim_km}km`}
                  {dayEventDetail.bike_km > 0 && ` · Bike ${dayEventDetail.bike_km}km`}
                  {dayEventDetail.run_km > 0 && ` · Run ${dayEventDetail.run_km}km`}
                </div>
                {dayEventDetail.goal && <div className="text-sm" style={{ marginTop: 4 }} dir="auto">{dayEventDetail.goal}</div>}
                {dayEventDetail.targets && <div className="text-sm text-dim" style={{ marginTop: 2 }} dir="auto">Targets: {dayEventDetail.targets}</div>}
                {dayEventDetail.cutoffs && <div className="text-sm text-dim" style={{ marginTop: 2 }}>Cutoffs: {dayEventDetail.cutoffs}</div>}
              </div>
              <button className="btn btn-sm btn-outline" onClick={() => { setDayModalOpen(false); navigate('/events') }}>{t('view')}</button>
            </div>
          </div>
        )}
        <div className="day-detail-section">
          <h4>{t('plan_planned_workouts')}</h4>
          {dayPlan.length ? dayPlan.map(p => {
            const stats = []
            if (p.duration_planned_min) stats.push(fmtDur(p.duration_planned_min))
            if (p.distance_planned_km) stats.push(fmtDist(p.distance_planned_km) + ' km')
            return (
              <div key={p.id} className="day-detail-item">
                <div className="item-header">
                  <Badge type={p.discipline} /> <strong>{p.title || p.discipline}</strong>
                  <div><button className="btn btn-sm" onClick={() => { setDayModalOpen(false); openEditForm(p.id) }}>{t('edit')}</button></div>
                </div>
                <div className="item-stats">
                  {stats.join(' · ')}
                  {p.intensity && <> · <Badge type={p.intensity} /></>}
                </div>
                {p.description && (
                  <div className="text-sm mt-12 expandable-text">
                    <span dir="auto">{p.description}</span>
                    <button className="expand-text-btn" onClick={(e) => { e.stopPropagation(); setExpandText(p.description) }} title="Expand">&#x2922;</button>
                  </div>
                )}
              </div>
            )
          }) : <p className="text-dim text-sm">{t('plan_no_planned')}</p>}
        </div>

        <div className="day-detail-section">
          <h4>{t('plan_actual_workouts')}</h4>
          {dayActual.length ? dayActual.map(w => {
            const disc = w.discipline || classifyType(w.type)
            const dur = safef(w.duration_min)
            const dist = safef(w.distance_km)
            const hr = safef(w.HeartRate_average)
            const stats = []
            if (dur) stats.push(fmtDur(dur))
            if (dist > 0) stats.push(disc === 'swim' ? Math.round(dist * 1000) + ' m' : fmtDist(dist) + ' km')
            if (hr) stats.push('HR ' + Math.round(hr))
            return (
              <div key={w.workout_num} className="day-detail-item" style={{ cursor: 'pointer' }}
                onClick={() => { setDayModalOpen(false); setDetailNum(w.workout_num) }}>
                <div className="item-header">
                  <Badge type={disc} text={w.type} /> <strong>#{w.workout_num} {w.type}</strong>
                  <span className="text-sm text-dim">{fmtTime(w.startDate, w.meta_TimeZone)}</span>
                </div>
                <div className="item-stats">{stats.join(' \u00B7 ')}</div>
              </div>
            )
          }) : <p className="text-dim text-sm">{t('plan_no_actual')}</p>}
        </div>

        {dayPlan.length > 0 && dayActual.length > 0 && (
          <div className="day-detail-section">
            <h4>{t('plan_vs_actual')}</h4>
            {dayPlan.map(p => {
              const matched = dayActual.find(w => (w.discipline || classifyType(w.type)) === p.discipline)
              if (!matched) return null
              const actualDur = safef(matched.duration_min)
              const actualDist = safef(matched.distance_km)
              const plannedDur = safef(p.duration_planned_min)
              const plannedDist = safef(p.distance_planned_km)
              const durDiff = plannedDur ? ((actualDur - plannedDur) / plannedDur * 100).toFixed(0) : null
              const distDiff = plannedDist ? ((actualDist - plannedDist) / plannedDist * 100).toFixed(0) : null
              return (
                <div key={p.id} className="day-detail-item">
                  <div className="item-header"><Badge type={p.discipline} /> {p.title || p.discipline}</div>
                  <div className="item-stats">
                    {t('plan_duration')}: {plannedDur ? fmtDur(plannedDur) : '-'} {t('plan_planned')} / {actualDur ? fmtDur(actualDur) : '-'} {t('plan_actual')}
                    {durDiff != null && ` (${durDiff > 0 ? '+' : ''}${durDiff}%)`}<br />
                    {t('plan_distance')}: {plannedDist ? fmtDist(plannedDist) + ' km' : '-'} {t('plan_planned')} / {actualDist > 0 ? fmtDist(actualDist) + ' km' : '-'} {t('plan_actual')}
                    {distDiff != null && ` (${distDiff > 0 ? '+' : ''}${distDiff}%)`}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        <div className="form-actions mt-20">
          <button className="btn btn-accent btn-sm" onClick={() => openAddForm(dayModalDate)}>
            + {t('plan_add_for_day')}
          </button>
        </div>
      </Modal>

      {/* Add/Edit Form Modal */}
      <Modal open={formOpen} onClose={() => setFormOpen(false)} wide
        title={editingId
          ? <input type="text" className="input-full modal-title-input" dir="auto" value={formData.title}
              placeholder={t('plan_title_placeholder')}
              onChange={e => setFormData(f => ({ ...f, title: e.target.value }))} />
          : t('plan_add_planned')
        }
      >
        <div className="form-row mt-12">
          <div className="form-group">
            <label>{t('date')}</label>
            <input type="date" className="input-full" value={formData.date}
              onChange={e => setFormData(f => ({ ...f, date: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>{t('plan_discipline')}</label>
            <select className="input-full" value={formData.discipline}
              onChange={e => setFormData(f => ({ ...f, discipline: e.target.value }))}>
              <option value="swim">Swim</option>
              <option value="bike">Bike</option>
              <option value="run">Run</option>
              <option value="strength">{t('plan_strength')}</option>
              <option value="rest">{t('plan_rest')}</option>
            </select>
          </div>
        </div>
        {!editingId && (
          <div className="form-group">
            <label>{t('plan_title')}</label>
            <input type="text" className="input-full" dir="auto" value={formData.title}
              placeholder={t('plan_title_placeholder')}
              onChange={e => setFormData(f => ({ ...f, title: e.target.value }))} />
          </div>
        )}
        <div className="form-row-3">
          <div className="form-group">
            <label>{t('plan_duration_min')}</label>
            <input type="number" className="input-full" value={formData.duration_planned_min}
              onChange={e => setFormData(f => ({ ...f, duration_planned_min: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>{t('plan_distance_km')}</label>
            <input type="number" className="input-full" step="0.1" value={formData.distance_planned_km}
              onChange={e => setFormData(f => ({ ...f, distance_planned_km: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>{t('plan_intensity')}</label>
            <select className="input-full" value={formData.intensity}
              onChange={e => setFormData(f => ({ ...f, intensity: e.target.value }))}>
              <option value="easy">{t('plan_easy')}</option>
              <option value="moderate">{t('plan_moderate')}</option>
              <option value="hard">{t('plan_hard')}</option>
              <option value="race">{t('plan_race')}</option>
            </select>
          </div>
        </div>
        <div className="form-group" style={{ position: 'relative', flex: '1 1 auto', display: 'flex', flexDirection: 'column' }}>
          <label>{t('description')}</label>
          <textarea dir="auto" value={formData.description}
            onChange={e => setFormData(f => ({ ...f, description: e.target.value }))}
            style={{ overflow: 'auto', flex: '1 1 auto', minHeight: '200px', resize: 'vertical' }} />
          <button
            type="button"
            className="expand-text-btn textarea-expand"
            onClick={() => setExpandText(formData.description)}
            title="Expand"
          >&#x2922;</button>
        </div>
        <div className="form-actions">
          <button className="btn btn-accent" onClick={savePlanItem}>{editingId ? t('update') : t('add')}</button>
          {editingId && (
            <>
              <button className="btn btn-green" onClick={() => { toggleComplete(editingId, formData.completed ? 0 : 1); setFormOpen(false) }}>
                {formData.completed ? t('plan_mark_incomplete') : t('plan_mark_complete')}
              </button>
              <button className="btn btn-red" onClick={() => requestDeletePlan(editingId)}>{t('delete')}</button>
            </>
          )}
          <button className="btn" onClick={() => setFormOpen(false)}>{t('cancel')}</button>
        </div>
      </Modal>

      {expandText != null && (
        <div className="expand-overlay" onClick={() => setExpandText(null)}>
          <div className="expand-overlay-content" onClick={e => e.stopPropagation()}>
            <button className="btn btn-sm modal-close" onClick={() => setExpandText(null)} style={{ position: 'absolute', top: 12, insetInlineEnd: 12 }}>&times;</button>
            <div className="expand-overlay-text" dir="auto">{expandText}</div>
          </div>
        </div>
      )}

      {detailNum != null && (
        <WorkoutDetailModal
          workoutNum={detailNum}
          open={true}
          onClose={() => setDetailNum(null)}
        />
      )}
      <ConfirmDialog
        open={confirmOpen}
        title={t('delete_plan')}
        message={t('delete_plan_confirm')}
        onConfirm={confirmDeletePlan}
        onCancel={() => { setConfirmOpen(false); setConfirmTarget(null) }}
      />
    </>
  )
}
