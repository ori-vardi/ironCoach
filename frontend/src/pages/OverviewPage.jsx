import { useState, useEffect, useMemo } from 'react'
import Plot from 'react-plotly.js'
import { api } from '../api'
import { COLORS, PLOTLY_LAYOUT, PLOTLY_CONFIG } from '../constants'
import { fmtDur, fmtDist, fmtDateShort, fmtTime, safef, fmtSleepHours, getRecoveryInfoTexts, computeRaceTsbData } from '../utils/formatters'
import { recoveryColor, trainingPhase, statusColor, fatigueColor } from '../utils/classifiers'
import { useApp } from '../context/AppContext'
import KpiCard from '../components/common/KpiCard'
import Badge from '../components/common/Badge'
import InfoTip from '../components/common/InfoTip'
import LoadingSpinner from '../components/common/LoadingSpinner'
import WorkoutDetailModal from '../components/WorkoutDetailModal'
import MergeActionBar from '../components/MergeActionBar'
import ReadinessGauge from '../components/common/ReadinessGauge'
import useTableSort from '../utils/useTableSort'
import { useI18n } from '../i18n/I18nContext'

// Convert ISO week (2026-W08) to a Monday date string for Plotly
function isoWeekToDate(weekStr) {
  const m = weekStr.match(/^(\d{4})-W(\d{2})$/)
  if (!m) return weekStr
  const year = parseInt(m[1])
  const week = parseInt(m[2])
  if (week < 1 || week > 53) return weekStr
  // Jan 4 is always in week 1 per ISO 8601
  const jan4 = new Date(year, 0, 4)
  const dayOfWeek = jan4.getDay() || 7 // Mon=1 .. Sun=7
  const monday = new Date(jan4)
  monday.setDate(jan4.getDate() - dayOfWeek + 1 + (week - 1) * 7)
  return monday.toISOString().slice(0, 10)
}

function getDiscLabels(t) {
  return { swim: t('disc_swim'), bike: t('disc_bike'), run: t('disc_run'), strength: t('disc_strength') }
}
const DISC_ICONS = { swim: '\uD83C\uDFCA', bike: '\uD83D\uDEB2', run: '\uD83C\uDFC3', strength: '\uD83C\uDFCB' }

function fmtDaysSince(days, t) {
  if (days === null || days === undefined) return t('never')
  if (days === 0) return t('today')
  if (days === 1) return t('yesterday')
  return `${days} ${t('days_ago')}`
}

export default function OverviewPage() {
  const { workouts, allWorkouts, setWorkouts, setRace, dateFrom, dateTo, refreshWorkouts } = useApp()
  const { t } = useI18n()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [detailNum, setDetailNum] = useState(null)
  const [rawData, setRawData] = useState(null)
  const [selected, setSelected] = useState(new Set())
  const [raceEvents, setRaceEvents] = useState([])
  const [readiness, setReadiness] = useState(null)
  const [riskAlerts, setRiskAlerts] = useState([])
  const [dismissedAlerts, setDismissedAlerts] = useState(new Set())
  const [latestInsight, setLatestInsight] = useState(null)
  const [todayCalories, setTodayCalories] = useState(null)
  const [calorieTarget, setCalorieTarget] = useState(null)
  const [brickNums, setBrickNums] = useState(new Set())
  const [refreshKey, setRefreshKey] = useState(0)

  const overviewSortCols = useMemo(() => ({
    num: w => Number(w.workout_num),
    date: w => w.startDate,
    type: w => w.discipline || '',
    duration: w => safef(w.duration_min),
    distance: w => safef(w.distance_km),
    avg_hr: w => safef(w.HeartRate_average),
  }), [])

  const { recentAll, selectedWorkouts } = useMemo(() => {
    const recentAll = [...workouts].sort((a, b) => b.startDate.localeCompare(a.startDate)).slice(0, 10)
    const selectedWorkouts = recentAll.filter(w => selected.has(w.workout_num))
    return { recentAll, selectedWorkouts }
  }, [workouts, selected])

  const { sorted: recent, handleSort, sortArrow } = useTableSort(recentAll, overviewSortCols, 'date', 'desc')

  function toggleSelect(wnum, e) {
    e.stopPropagation()
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(wnum)) next.delete(wnum)
      else next.add(wnum)
      return next
    })
  }

  function handleMergeDone() {
    setSelected(new Set())
    refreshWorkouts()
  }

  function tParams(key, params) {
    let str = t(key)
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        str = str.replace(`{${k}}`, v)
      }
    }
    return str
  }

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const today = new Date().toISOString().slice(0, 10)
        const [weekly, race, recovery, events, insights, nutrition, targets, bricks] = await Promise.all([
          api('/api/stats/weekly'),
          api('/api/race'),
          api('/api/recovery').catch(() => null),
          api('/api/events').catch(() => []),
          api('/api/insights/all?limit=1').catch(() => []),
          api(`/api/nutrition?date=${today}`).catch(() => null),
          api('/api/nutrition/targets').catch(() => null),
          api(`/api/bricks?from_date=${dateFrom}&to_date=${dateTo}`).catch(() => []),
        ])
        if (cancelled) return
        setRace(race)
        setRawData({ weekly, race, recovery })
        if (recovery?.readiness) setReadiness(recovery.readiness)
        if (recovery?.risk_alerts) setRiskAlerts(recovery.risk_alerts)
        if (Array.isArray(events)) {
          setRaceEvents(events.filter(e => e.event_date).sort((a, b) => new Date(a.event_date) - new Date(b.event_date)))
        }
        // Latest insight (already sorted by workout_date DESC from server, limit=1)
        if (Array.isArray(insights) && insights.length > 0) {
          setLatestInsight(insights[0])
        }
        // Today's calories — /api/nutrition?date= returns an array of meals directly
        if (Array.isArray(nutrition) && nutrition.length > 0) {
          const total = nutrition.reduce((s, m) => s + (m.calories || 0), 0)
          setTodayCalories(total)
        } else {
          setTodayCalories(0)
        }
        if (targets && targets.calories) {
          setCalorieTarget(targets.calories)
        }
        if (Array.isArray(bricks)) {
          const nums = new Set()
          for (const b of bricks) {
            for (const bw of b.workouts || []) nums.add(String(bw.workout_num))
          }
          setBrickNums(nums)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [setRace, refreshKey, dateFrom, dateTo])

  // Re-fetch overview data when import or merge completes
  useEffect(() => {
    const onUpdate = () => setRefreshKey(k => k + 1)
    window.addEventListener('coach-data-update', onUpdate)
    return () => window.removeEventListener('coach-data-update', onUpdate)
  }, [])

  // Filter weekly data by date range
  const filteredWeekly = useMemo(() => {
    if (!rawData?.weekly) return []
    return rawData.weekly.filter(w => {
      const d = isoWeekToDate(w.week)
      return d >= dateFrom && d <= dateTo
    })
  }, [rawData?.weekly, dateFrom, dateTo])

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">{t('error_loading_data')}: {error}</div>

  const { race, recovery } = rawData

  const daysUntil = race?.days_until ?? '?'
  const totalW = workouts.length
  const swimKm = workouts.filter(w => w.discipline === 'swim').reduce((s, w) => s + safef(w.distance_km), 0)
  const bikeKm = workouts.filter(w => w.discipline === 'bike').reduce((s, w) => s + safef(w.distance_km), 0)
  const runKm = workouts.filter(w => w.discipline === 'run').reduce((s, w) => s + safef(w.distance_km), 0)
  const totalHrs = workouts.reduce((s, w) => s + safef(w.duration_min), 0) / 60
  const hrs = workouts.filter(w => safef(w.HeartRate_average) > 0)
  const avgHR = hrs.length ? Math.round(hrs.reduce((s, w) => s + safef(w.HeartRate_average), 0) / hrs.length) : '-'
  const dates = workouts.map(w => w.startDate?.slice(0, 10)).filter(Boolean).sort()
  const firstDate = dates.length ? dates[0] : '?'
  const lastDate = dates.length ? dates[dates.length - 1] : '?'

  const rc = recovery?.current
  const timeline = recovery?.timeline || []
  const disciplines = recovery?.disciplines || {}
  const recovery_data = recovery?.recovery_data || []
  const vo2max = recovery?.vo2max || []
  const weekly_load = recovery?.weekly_load || null
  const hasHrtss = timeline.some(t => (t.day_hrtss ?? 0) > 0)
  const pw = recovery?.per_workout || {}

  // Latest recovery data for ring cards
  const lastRec = recovery_data.length > 0 ? recovery_data[recovery_data.length - 1] : null

  // Phase-aware color coding
  // Nearest upcoming event for TSB readiness; primary only for phase coloring
  const nearestEvent = raceEvents.find(e => (e.days_until ?? 999) > 0) || raceEvents[0]
  const primaryEvent = raceEvents.find(e => e.is_primary) || nearestEvent
  const daysToRaceCalc = primaryEvent?.days_until ?? (primaryEvent ? Math.ceil((new Date(primaryEvent.event_date) - new Date()) / 86400000) : 999)
  const phase = trainingPhase(daysToRaceCalc)

  const INFO = getRecoveryInfoTexts(t)
  const DISC_LABELS = getDiscLabels(t)

  // Calories KPI color
  const calDiff = (todayCalories !== null && calorieTarget) ? Math.abs(todayCalories - calorieTarget) : null
  let calColor = 'var(--text)'
  if (calDiff !== null) {
    if (calDiff <= 500) calColor = '#c3e88d'
    else if (calDiff <= 800) calColor = '#ffc777'
    else calColor = '#ff5370'
  }

  // Weekly chart data -- convert ISO week to actual dates
  const wkDates = filteredWeekly.map(w => isoWeekToDate(w.week))
  const weeklyTraces = [
    { x: wkDates, y: filteredWeekly.map(w => (w.swim_min || 0) / 60), name: 'Swim', type: 'bar', marker: { color: COLORS.swim } },
    { x: wkDates, y: filteredWeekly.map(w => (w.bike_min || 0) / 60), name: 'Bike', type: 'bar', marker: { color: COLORS.bike } },
    { x: wkDates, y: filteredWeekly.map(w => (w.run_min || 0) / 60), name: 'Run', type: 'bar', marker: { color: COLORS.run } },
    { x: wkDates, y: filteredWeekly.map(w => (w.strength_min || 0) / 60), name: 'Strength', type: 'bar', marker: { color: COLORS.strength } },
  ]

  // Donut data
  const counts = {}
  workouts.forEach(w => { counts[w.discipline] = (counts[w.discipline] || 0) + 1 })
  const donutLabels = Object.keys(counts)
  const donutValues = Object.values(counts)

  return (
    <>
      <h1 className="page-title">{t('page_overview')}</h1>

      {/* Recovery & Readiness Hero */}
      {rc && (() => {
        const rhrColor = lastRec?.resting_hr ? statusColor(lastRec.resting_hr, 50, 60, false) : null
        const hrvColor = lastRec?.hrv_ms ? statusColor(lastRec.hrv_ms, 50, 30, true) : null
        const sleepH = lastRec?.sleep_total ? lastRec.sleep_total / 60 : 0
        const sleepColor = sleepH ? statusColor(sleepH, 7, 5, true) : null
        const latestTrimp = timeline.findLast(e => e.day_trimp > 0)
        const latestHrtss = hasHrtss ? timeline.findLast(e => e.day_hrtss > 0) : null
        const tsbVal = Math.round(rc.fitness - rc.fatigue)
        const allCards = [
          <KpiCard key="recovery" value={`${Math.round(rc.recovery)}%`} label={t('recovery_label')} sublabel={rc.label} info={INFO.recovery} style={{ color: rc.color }} fillPct={Math.max(0, Math.min(100, rc.recovery))} fillColor={rc.color} />,
          <KpiCard key="fitness" animate={Math.round(rc.fitness)} label={t('fitness_ctl')} info={INFO.fitness} style={{ color: statusColor(rc.fitness, 80, 40, true) }} fillPct={Math.min(rc.fitness, 100)} fillColor={statusColor(rc.fitness, 80, 40, true)} />,
          latestTrimp
            ? <KpiCard key="trimp" value={Math.round(latestTrimp.day_trimp)} label={t('trimp_label')} sublabel={latestTrimp.date} info={INFO.trimp} style={{ color: latestTrimp.day_trimp > 150 ? '#ff5370' : latestTrimp.day_trimp > 80 ? '#ff966c' : latestTrimp.day_trimp > 0 ? '#c3e88d' : 'var(--text-dim)' }} />
            : null,
          latestHrtss
            ? <KpiCard key="hrtss" value={Math.round(latestHrtss.day_hrtss)} label={t('hrtss_label')} sublabel={latestHrtss.date} info={t('info_hrtss')} style={{ color: latestHrtss.day_hrtss > 100 ? '#ff5370' : latestHrtss.day_hrtss > 60 ? '#ffc777' : '#c3e88d' }} />
            : null,
          rhrColor
            ? <KpiCard key="rhr" value={`${Math.round(lastRec.resting_hr)} bpm`} label={t('resting_hr')} sublabel={lastRec.date} info={INFO.rhr} style={{ color: rhrColor }} fillPct={Math.min(((lastRec.resting_hr - 35) / 45) * 100, 100)} fillColor={rhrColor} />
            : null,
          hrvColor
            ? <KpiCard key="hrv" value={`${Math.round(lastRec.hrv_ms)} ms`} label={t('hrv')} sublabel={lastRec.date} info={INFO.hrv} style={{ color: hrvColor }} fillPct={Math.min((lastRec.hrv_ms / 100) * 100, 100)} fillColor={hrvColor} />
            : null,
          <KpiCard key="fatigue" animate={Math.round(rc.fatigue)} label={t('fatigue_atl')} info={INFO.fatigue} style={{ color: fatigueColor(rc.fatigue, phase) }} fillPct={Math.min(rc.fatigue, 100)} fillColor={fatigueColor(rc.fatigue, phase)} />,
          <KpiCard key="tsb" value={tsbVal} label={t('form_tsb')} sublabel={tsbVal > 0 ? t('rested') : t('building')} info={INFO.tsb} style={{ color: tsbVal > 0 ? '#c3e88d' : '#ff966c' }}
            infoChildren={(() => {
              const { tsbPct } = computeRaceTsbData(tsbVal, 999)
              return (
                <div style={{ marginTop: 10 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>Your position:</div>
                  <div className="race-tsb-bar-wrapper">
                    <div className="race-tsb-marker" style={{ left: `${tsbPct}%` }} title={`TSB: ${tsbVal}`} />
                    <div className="race-tsb-bar">
                      <div className="race-tsb-zone zone-building" style={{ flex: 25 }}>{t('building')}</div>
                      <div className="race-tsb-zone zone-maintaining" style={{ flex: 25 }}>{t('maintaining')}</div>
                      <div className="race-tsb-zone zone-tapering" style={{ flex: 25 }}>{t('tapering')}</div>
                      <div className="race-tsb-zone zone-peaked" style={{ flex: 25 }}>{t('peaked')}</div>
                    </div>
                  </div>
                </div>
              )
            })()}
          />,
          sleepColor
            ? <KpiCard key="sleep" value={fmtSleepHours(lastRec.sleep_total)} label={t('sleep')} sublabel={lastRec.date} info={INFO.sleep} style={{ color: sleepColor }} fillPct={Math.min((sleepH / 9) * 100, 100)} fillColor={sleepColor} />
            : null,
          vo2max.length > 0
            ? <KpiCard key="vo2" value={`${vo2max[vo2max.length - 1].value}`} label={t('vo2max')} sublabel={vo2max[vo2max.length - 1].date} info={t('info_vo2max')} style={{ color: '#82aaff' }} fillPct={Math.min((vo2max[vo2max.length - 1].value / 60) * 100, 100)} fillColor="#82aaff" />
            : null,
          weekly_load && weekly_load.prev_week_trimp > 0
            ? <KpiCard key="weekly" value={`${weekly_load.change_pct > 0 ? '+' : ''}${weekly_load.change_pct}%`} label={t('weekly_load_change')} sublabel={`${Math.round(weekly_load.current_week_trimp)} vs ${Math.round(weekly_load.prev_week_trimp)}`} info={t('info_weekly_load')} style={{ color: Math.abs(weekly_load.change_pct) > 20 ? '#ff5370' : Math.abs(weekly_load.change_pct) > 10 ? '#ffc777' : '#c3e88d' }} />
            : null,
        ].filter(Boolean)

        return (
          <div className="overview-dashboard">
            {/* Row 1: cards-left | alert | cards-right */}
            <div className="overview-top-row">
              <div className="overview-top-left">
                <div className="overview-card-pair">
                  <KpiCard value={`${fmtDist(swimKm)} km`} label={t('overview_swim_distance')} className="kpi-swim" />
                  <KpiCard value={`${fmtDist(bikeKm)} km`} label={t('overview_bike_distance')} className="kpi-bike" />
                </div>
              </div>
              <div className="overview-top-alert">
                {rc.stale && (
                  <div className="recovery-alert-card recovery-alert-banner">
                    <div className="recovery-alert-icon">⚠</div>
                    <div className="recovery-alert-body"><span dir="auto">{rc.stale.message}</span></div>
                  </div>
                )}
                {riskAlerts.filter(a => !dismissedAlerts.has(a.type)).map(alert => (
                  <div key={alert.type} className={`recovery-alert-card recovery-alert-banner recovery-alert-${alert.severity}`}>
                    <div className="recovery-alert-icon">⚠</div>
                    <div className="recovery-alert-body"><span dir="auto">{alert.message || tParams(alert.key, alert.params)}</span></div>
                    <button className="recovery-alert-close" onClick={() => setDismissedAlerts(prev => new Set([...prev, alert.type]))}>
                      {'\u2715'}
                    </button>
                  </div>
                ))}
              </div>
              <div className="overview-top-right">
                <div className="overview-card-pair">
                  <KpiCard value={`${fmtDist(runKm)} km`} label={t('overview_run_distance')} className="kpi-run" />
                  <KpiCard animate={typeof daysUntil === 'number' ? daysUntil : undefined} value={daysUntil} label={t('overview_days_to_race')} style={{ color: 'var(--accent)' }} />
                </div>
              </div>
            </div>

            {/* Row 2: workouts | ring | insight */}
            <div className="overview-main-row">
              <div className="overview-side-left">
                <h4 className="recovery-side-title">{t('time_since_last')}</h4>
                {['swim', 'bike', 'run', 'strength'].map(disc => {
                  const d = disciplines[disc]
                  if (!d) return null
                  let dayColor = '#ff966c'
                  if (d.days_since === null) dayColor = 'var(--text-dim)'
                  else if (d.days_since <= 1) dayColor = '#c3e88d'
                  else if (d.days_since <= 3) dayColor = '#ffc777'
                  const fmtDateDisc = d.last_workout
                    ? new Date(d.last_workout + 'T00:00:00').toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' })
                    : ''
                  const lastW = d.last_workout ? workouts.find(w => w.discipline === disc && w.startDate?.slice(0, 10) === d.last_workout) : null
                  return (
                    <div key={disc} className="recovery-disc-row card" style={{ cursor: lastW ? 'pointer' : undefined }} onClick={() => lastW && setDetailNum(lastW.workout_num)}>
                      <div className="recovery-disc-icon">{DISC_ICONS[disc]}</div>
                      <div className="recovery-disc-info">
                        <div className="recovery-disc-name">{DISC_LABELS[disc]}</div>
                        {d.week_count > 0 && (
                          <div className="recovery-disc-week text-dim">{d.week_count}x &middot; {d.week_duration}min{d.week_distance > 0 ? ` \u00B7 ${d.week_distance}km` : ''}</div>
                        )}
                      </div>
                      <div className="recovery-disc-right">
                        <div className="recovery-disc-since" style={{ color: dayColor }}>{fmtDaysSince(d.days_since, t)}</div>
                        {fmtDateDisc && <div className="recovery-disc-date text-dim">{fmtDateDisc}</div>}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Center: ring with orbiting cards */}
              <div className="overview-ring">
                <div className="readiness-center">
                  {readiness ? (
                    <ReadinessGauge score={readiness.score} components={{}} compact infoTip={t('readiness_score_tip')} />
                  ) : (
                    <div style={{ textAlign: 'center', padding: 20, color: 'var(--text-dim)' }}>
                      <div style={{ fontSize: 36, fontWeight: 800 }}>{Math.round(rc.recovery)}{'%'}</div>
                      <div style={{ fontSize: 12, textTransform: 'uppercase' }}>{rc.label}</div>
                    </div>
                  )}
                </div>
                {allCards.map((card, i) => {
                  const n = allCards.length
                  // Distribute cards in a full circle
                  const ang = (i / n) * 360 - 90  // start from top (-90°)
                  return (
                    <div key={`c${i}`} className="overview-ring-slot" style={{ '--angle': `${ang}deg` }}>
                      {card}
                    </div>
                  )
                })}
              </div>

              {/* Right side: insight + race readiness */}
              <div className="overview-side-right">
                {latestInsight && (
                  <div className="card" style={{ cursor: 'pointer' }} onClick={() => setDetailNum(latestInsight.workout_num)}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <h4 style={{ margin: 0, fontSize: 13 }}>{t('latest_insight')}</h4>
                      <Badge type={latestInsight.discipline} text={latestInsight.discipline} />
                    </div>
                    <span className="text-dim text-sm">{fmtDateShort(latestInsight.start_time)}</span>
                    <p className="text-dim" style={{ fontSize: 12, marginTop: 6, lineClamp: 4, WebkitLineClamp: 4, display: '-webkit-box', WebkitBoxOrient: 'vertical', overflow: 'hidden' }} dir="auto">
                      {latestInsight.insight?.substring(0, 250)}
                    </p>
                  </div>
                )}
                {raceEvents.length > 0 && (
                  <div className="card" style={{ marginTop: 6 }}>
                    <h4 style={{ margin: '0 0 8px', fontSize: 13 }}>{t('events_title')}</h4>
                    {raceEvents.filter(e => (e.days_until ?? 999) > 0).slice(0, 5).map(ev => (
                      <div key={ev.id} style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4, direction: 'ltr', unicodeBidi: 'isolate', flexWrap: 'wrap' }}>
                        <strong dir="auto" style={{ fontSize: 13 }}>{ev.event_name || ev.event_type}</strong>
                        <span style={{ color: 'var(--accent)', fontSize: 13 }}>{ev.days_until}d</span>
                        {!!ev.is_primary && <span style={{ color: 'var(--yellow)', fontSize: 10 }}>PRIMARY</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )
      })()}

      {/* Race Day Readiness — shown even without workout data if events exist */}
      {!rc && nearestEvent && (
        <div className="card mb-20" style={{ display: 'flex', justifyContent: 'center' }}>
          <ReadinessGauge score={0} event={nearestEvent} tsb={0} compact infoTip={t('readiness_score_tip')} />
        </div>
      )}

      <div className="card mb-20">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <h4 style={{ margin: 0 }}>{t('overview_recent_workouts')}</h4>
          {selected.size > 0 && (
            <>
              <MergeActionBar workouts={selectedWorkouts} onDone={handleMergeDone} />
              <button className="btn btn-sm" onClick={() => setSelected(new Set())}>
                {t('clear_selection')} ({selected.size})
              </button>
            </>
          )}
        </div>
        <div className="table-scroll" style={{ maxHeight: 400 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 30 }}></th>
                <th className="sortable-th" onClick={() => handleSort('num')}>#{sortArrow('num')}</th>
                <th className="sortable-th" onClick={() => handleSort('date')}>{t('th_date')}{sortArrow('date')}</th>
                <th>{t('th_start')}</th>
                <th className="sortable-th" onClick={() => handleSort('type')}>{t('th_type')}{sortArrow('type')}</th>
                <th className="sortable-th" onClick={() => handleSort('duration')}>{t('th_duration')}{sortArrow('duration')}</th>
                <th className="sortable-th" onClick={() => handleSort('distance')}>{t('th_distance')}{sortArrow('distance')}</th>
                <th className="sortable-th" onClick={() => handleSort('avg_hr')}>{t('th_avg_hr')}{sortArrow('avg_hr')}</th>
                <th>{t('th_hrtss')}</th>
                <th>
                  {t('th_recovery')}
                  <InfoTip text={t('overview_recovery_tip')} />
                </th>
              </tr>
            </thead>
            <tbody>
              {recent.map(w => {
                const r = pw[String(w.workout_num)]
                let recCell = '-'
                if (r && (r.before > 0 || r.after > 0)) {
                  const bc = recoveryColor(r.before)
                  const ac = recoveryColor(r.after)
                  recCell = (
                    <>
                      <span style={{ color: bc }}>{r.before}%</span>
                      {' \u2192 '}
                      <span style={{ color: ac }}>{r.after}%</span>
                    </>
                  )
                }
                return (
                  <tr key={w.workout_num} className={`clickable${selected.has(w.workout_num) ? ' row-selected' : ''}`} onClick={() => setDetailNum(w.workout_num)}>
                    <td onClick={e => toggleSelect(w.workout_num, e)}>
                      <input type="checkbox" checked={selected.has(w.workout_num)} readOnly style={{ cursor: 'pointer' }} />
                    </td>
                    <td>{w.workout_num}{brickNums.has(String(w.workout_num)) && <span className="brick-tag" title={t('page_bricks')}>🧱</span>}</td>
                    <td>{fmtDateShort(w.startDate)}</td>
                    <td>{fmtTime(w.startDate, w.meta_TimeZone)}</td>
                    <td><Badge type={w.discipline} text={w.type} /></td>
                    <td>{fmtDur(safef(w.duration_min))}</td>
                    <td>{safef(w.distance_km) > 0 ? fmtDist(safef(w.distance_km)) + ' km' : '-'}</td>
                    <td>{safef(w.HeartRate_average) ? Math.round(safef(w.HeartRate_average)) : '-'}</td>
                    <td style={{ color: 'var(--text-dim)' }}>
                      {r?.hrtss ? Math.round(r.hrtss) : '-'}
                    </td>
                    <td>{recCell}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="chart-row">
        <div className="chart-container">
          <h4>
            {t('overview_weekly_volume')}
            <InfoTip text={t('overview_weekly_volume_tip')} />
          </h4>
          <Plot
            data={weeklyTraces}
            layout={{ ...PLOTLY_LAYOUT, barmode: 'stack' }}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: 250 }}
          />
        </div>
        <div className="chart-container">
          <h4>{t('overview_workout_distribution')}</h4>
          <Plot
            data={[{
              labels: donutLabels,
              values: donutValues,
              type: 'pie',
              hole: 0.5,
              marker: { colors: donutLabels.map(l => COLORS[l] || COLORS.other) },
              textfont: { color: '#c8d3f5' },
            }]}
            layout={{ ...PLOTLY_LAYOUT, showlegend: true }}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: 250 }}
          />
        </div>
      </div>

      {detailNum != null && (
        <WorkoutDetailModal workoutNum={detailNum} open onClose={() => setDetailNum(null)} />
      )}

    </>
  )
}
