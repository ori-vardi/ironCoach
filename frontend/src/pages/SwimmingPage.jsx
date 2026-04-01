import { useState, useEffect, useMemo } from 'react'
import Plot from 'react-plotly.js'
import { api } from '../api'
import { COLORS, PLOTLY_LAYOUT, PLOTLY_CONFIG } from '../constants'
import { fmtDur, fmtDateShort, fmtTime, safef } from '../utils/formatters'
import { useApp } from '../context/AppContext'
import useTableSort from '../utils/useTableSort'
import LoadingSpinner from '../components/common/LoadingSpinner'
import WorkoutDetailModal from '../components/WorkoutDetailModal'
import MergeActionBar from '../components/MergeActionBar'
import { useI18n } from '../i18n/I18nContext'

export default function SwimmingPage() {
  const { dateFrom, dateTo, refreshWorkouts } = useApp()
  const [brickNums, setBrickNums] = useState(new Set())
  const { t } = useI18n()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [allSwims, setAllSwims] = useState([])
  const [detailNum, setDetailNum] = useState(null)
  const [selected, setSelected] = useState(new Set())

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const swims = await api('/api/workouts/by-type/swim')
        if (cancelled) return
        setAllSwims(swims.sort((a, b) => a.startDate.localeCompare(b.startDate)))
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    api(`/api/bricks?from_date=${dateFrom}&to_date=${dateTo}`)
      .then(bricks => {
        const nums = new Set()
        bricks.forEach(b => b.workouts.forEach(w => nums.add(String(w.workout_num))))
        setBrickNums(nums)
      })
      .catch(err => console.error('Failed to load:', err))
  }, [dateFrom, dateTo])

  const chrono = useMemo(() => allSwims.filter(w => {
    const d = (w.startDate || '').slice(0, 10)
    return d >= dateFrom && d <= dateTo
  }), [allSwims, dateFrom, dateTo])

  const swimSortCols = useMemo(() => ({
    num: w => Number(w.workout_num),
    date: w => w.startDate,
    duration: w => safef(w.duration_min),
    distance: w => safef(w.distance_km) * 1000,
    pace: w => { const dm = safef(w.distance_km) * 1000; return dm > 0 ? safef(w.duration_min) / (dm / 100) : 999 },
    strokes: w => safef(w.SwimmingStrokeCount_sum),
    avg_hr: w => safef(w.HeartRate_average),
    calories: w => safef(w.ActiveEnergyBurned_sum),
  }), [])
  const { sorted: tableSorted, handleSort, sortArrow } = useTableSort(chrono, swimSortCols, 'date', 'desc')

  const selectedWorkouts = useMemo(() =>
    chrono.filter(w => selected.has(w.workout_num)).map(w => ({ ...w, discipline: 'swim' }))
  , [chrono, selected])

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">{t('error_loading_data')}: {error}</div>
  if (!chrono.length) return <><h1 className="page-title">{t('page_swimming')}</h1><p className="text-dim">{t('swim_no_workouts')}</p></>

  const dates = chrono.map(w => w.startDate.slice(0, 10))

  const mk = (y, name, color) => ({
    x: dates, y, name, type: 'scatter', mode: 'lines+markers',
    marker: { size: 4, color }, line: { color },
  })

  // distance_km * 1000 = meters
  const distMeters = chrono.map(w => Math.round(safef(w.distance_km) * 1000))
  // Pace per 100m: duration_min / (distance_m / 100)
  const paces = chrono.map(w => {
    const dm = safef(w.distance_km) * 1000
    const t = safef(w.duration_min)
    return dm > 0 ? t / (dm / 100) : 0
  })

  function fmtPace100(pace100) {
    if (pace100 <= 0) return '-'
    return `${Math.floor(pace100)}:${String(Math.round((pace100 % 1) * 60)).padStart(2, '0')}`
  }

  function handleRowClick(num) {
    setDetailNum(num)
  }

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

  return (
    <>
      <h1 className="page-title">{t('page_swimming')} ({chrono.length} {t('workouts_count')})</h1>

      <div className="card mb-20">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <h4 style={{ margin: 0 }}>{t('swim_all_swims')}</h4>
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
                <th className="sortable-th" onClick={() => handleSort('duration')}>{t('th_duration')}{sortArrow('duration')}</th>
                <th className="sortable-th" onClick={() => handleSort('distance')}>{t('th_distance')}{sortArrow('distance')}</th>
                <th className="sortable-th" onClick={() => handleSort('pace')}>{t('th_pace_100m')}{sortArrow('pace')}</th>
                <th className="sortable-th" onClick={() => handleSort('strokes')}>{t('th_strokes')}{sortArrow('strokes')}</th>
                <th className="sortable-th" onClick={() => handleSort('avg_hr')}>{t('th_avg_hr')}{sortArrow('avg_hr')}</th>
                <th className="sortable-th" onClick={() => handleSort('calories')}>{t('th_calories')}{sortArrow('calories')}</th>
              </tr>
            </thead>
            <tbody>
              {tableSorted.map(w => {
                const dm = safef(w.distance_km) * 1000
                const pace100 = dm > 0 ? safef(w.duration_min) / (dm / 100) : 0
                return (
                  <tr key={w.workout_num} className={`clickable${selected.has(w.workout_num) ? ' row-selected' : ''}`} onClick={() => handleRowClick(w.workout_num)}>
                    <td onClick={e => toggleSelect(w.workout_num, e)}>
                      <input type="checkbox" checked={selected.has(w.workout_num)} readOnly style={{ cursor: 'pointer' }} />
                    </td>
                    <td>{w.workout_num}{brickNums.has(String(w.workout_num)) && <span className="brick-tag" title={t('page_bricks')}>🧱</span>}</td>
                    <td>{fmtDateShort(w.startDate)}</td>
                    <td>{fmtTime(w.startDate, w.meta_TimeZone)}</td>
                    <td>{fmtDur(safef(w.duration_min))}</td>
                    <td>{Math.round(dm)} m</td>
                    <td>{fmtPace100(pace100)}</td>
                    <td>{Math.round(safef(w.SwimmingStrokeCount_sum)) || '-'}</td>
                    <td>{Math.round(safef(w.HeartRate_average)) || '-'}</td>
                    <td>{Math.floor(safef(w.ActiveEnergyBurned_sum)) || '-'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="chart-row">
        <div className="chart-container">
          <h4>{t('swim_distance_chart')}</h4>
          <Plot
            data={[mk(distMeters, 'Distance (m)', COLORS.swim)]}
            layout={PLOTLY_LAYOUT}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: '100%' }}
          />
        </div>
        <div className="chart-container">
          <h4>{t('swim_hr_chart')}</h4>
          <Plot
            data={[mk(chrono.map(w => safef(w.HeartRate_average)), 'Avg HR', COLORS.swim)]}
            layout={PLOTLY_LAYOUT}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: '100%' }}
          />
        </div>
      </div>

      <div className="chart-row">
        <div className="chart-container">
          <h4>{t('swim_stroke_chart')}</h4>
          <Plot
            data={[mk(chrono.map(w => safef(w.SwimmingStrokeCount_sum)), 'Strokes', COLORS.swim)]}
            layout={PLOTLY_LAYOUT}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: '100%' }}
          />
        </div>
        <div className="chart-container">
          <h4>{t('swim_pace_chart')}</h4>
          <Plot
            data={[mk(paces, 'Pace/100m', COLORS.swim)]}
            layout={PLOTLY_LAYOUT}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: '100%' }}
          />
        </div>
      </div>

      {detailNum != null && (
        <WorkoutDetailModal workoutNum={detailNum} open onClose={() => setDetailNum(null)} />
      )}

    </>
  )
}
