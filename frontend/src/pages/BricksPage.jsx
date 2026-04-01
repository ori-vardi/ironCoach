import { useState, useEffect, useMemo } from 'react'
import { api } from '../api'
import { useApp } from '../context/AppContext'
import { useI18n } from '../i18n/I18nContext'
import { fmtDateShort, fmtDur, fmtDist, safef } from '../utils/formatters'
import useTableSort from '../utils/useTableSort'
import Badge from '../components/common/Badge'
import KpiCard from '../components/common/KpiCard'
import LoadingSpinner from '../components/common/LoadingSpinner'
import WorkoutDetailModal from '../components/WorkoutDetailModal'

export default function BricksPage() {
  const { t } = useI18n()
  const { dateFrom, dateTo } = useApp()
  const [bricks, setBricks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [detailNum, setDetailNum] = useState(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    setLoading(true)
    api(`/api/bricks?from_date=${dateFrom}&to_date=${dateTo}`)
      .then(data => { setBricks(data); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [dateFrom, dateTo])

  const brickTypes = useMemo(() => {
    const types = new Set(bricks.map(b => b.brick_type))
    return [...types].sort()
  }, [bricks])

  const filtered = useMemo(() => {
    return filter ? bricks.filter(b => b.brick_type === filter) : bricks
  }, [bricks, filter])

  const brickSortCols = useMemo(() => ({
    date: b => b.date,
    type: b => b.brick_type,
    duration: b => safef(b.total_duration_min),
    distance: b => safef(b.total_distance_km),
    avg_hr: b => {
      const dur = b.workouts.reduce((s, w) => s + safef(w.duration_min), 0) || 1
      return b.workouts.reduce((s, w) => s + safef(w.HeartRate_average) * safef(w.duration_min), 0) / dur
    },
    transition: b => safef(b.transition_times?.[0]),
    calories: b => safef(b.total_calories),
  }), [])
  const { sorted, handleSort, sortArrow } = useTableSort(filtered, brickSortCols, 'date', 'desc')

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">Error: {error}</div>

  const avgTransition = bricks.length
    ? bricks.reduce((s, b) => s + (b.transition_times || []).reduce((a, v) => a + v, 0) / (b.transition_times?.length || 1), 0) / bricks.length
    : 0

  const formatTransition = (times) => {
    if (!times || !times.length) return '-'
    return times.map(t => `${Math.round(t)}m`).join(', ')
  }

  const avgHr = (workouts) => {
    const totalDur = workouts.reduce((s, w) => s + safef(w.duration_min), 0)
    if (!totalDur) return '-'
    const weighted = workouts.reduce((s, w) => s + safef(w.HeartRate_average) * safef(w.duration_min), 0)
    const avg = Math.round(weighted / totalDur)
    return avg || '-'
  }

  return (
    <>
      <h1 className="page-title">{t('page_bricks')}</h1>

      <div className="card-grid">
        <KpiCard
          value={bricks.length}
          label={t('bricks_total')}
        />
        <KpiCard
          value={avgTransition ? `${Math.round(avgTransition)}m` : '--'}
          label={t('bricks_avg_transition')}
        />
      </div>

      <div className="form-inline mb-12">
        <label>{t('workouts_filter')}: </label>
        <select value={filter} onChange={e => setFilter(e.target.value)}>
          <option value="">{t('workouts_filter_all')}</option>
          {brickTypes.map(bt => (
            <option key={bt} value={bt}>{bt}</option>
          ))}
        </select>
      </div>

      {sorted.length === 0 ? (
        <div className="empty-state">
          <p>{t('bricks_none')}</p>
        </div>
      ) : (
        <div className="table-scroll" style={{ maxHeight: 'calc(100vh - 280px)' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleSort('date')}>
                  {t('date')}{sortArrow('date')}
                </th>
                <th className="sortable-th" onClick={() => handleSort('type')}>
                  {t('bricks_type')}{sortArrow('type')}
                </th>
                <th className="sortable-th" onClick={() => handleSort('duration')} style={{ textAlign: 'right' }}>
                  {t('workouts_duration')}{sortArrow('duration')}
                </th>
                <th className="sortable-th" onClick={() => handleSort('distance')} style={{ textAlign: 'right' }}>
                  {t('workouts_distance')}{sortArrow('distance')}
                </th>
                <th className="sortable-th" onClick={() => handleSort('avg_hr')} style={{ textAlign: 'right' }}>
                  {t('workouts_avg_hr')}{sortArrow('avg_hr')}
                </th>
                <th className="sortable-th" onClick={() => handleSort('transition')} style={{ textAlign: 'right' }}>
                  {t('bricks_transition')}{sortArrow('transition')}
                </th>
                <th className="sortable-th" onClick={() => handleSort('calories')} style={{ textAlign: 'right' }}>
                  {t('th_calories')}{sortArrow('calories')}
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(brick => (
                <BrickRow
                  key={brick.brick_id}
                  brick={brick}
                  expanded={expandedId === brick.brick_id}
                  onToggle={() => setExpandedId(expandedId === brick.brick_id ? null : brick.brick_id)}
                  onWorkoutClick={setDetailNum}
                  avgHr={avgHr}
                  formatTransition={formatTransition}
                  t={t}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detailNum != null && (
        <WorkoutDetailModal
          workoutNum={detailNum}
          open={true}
          onClose={() => setDetailNum(null)}
        />
      )}
    </>
  )
}

function BrickRow({ brick, expanded, onToggle, onWorkoutClick, avgHr, formatTransition, t }) {
  return (
    <>
      <tr className="clickable" onClick={onToggle}>
        <td>{fmtDateShort(brick.date)}</td>
        <td>
          {brick.disciplines.map((d, i) => (
            <span key={i}>
              {i > 0 && <span className="text-dim" style={{ margin: '0 4px' }}>{'\u2192'}</span>}
              <Badge type={d} text={d} />
            </span>
          ))}
        </td>
        <td style={{ textAlign: 'right' }}>{fmtDur(safef(brick.total_duration_min))}</td>
        <td style={{ textAlign: 'right' }}>
          {safef(brick.total_distance_km) > 0 ? fmtDist(safef(brick.total_distance_km)) + ' km' : '-'}
        </td>
        <td style={{ textAlign: 'right' }}>{avgHr(brick.workouts)}</td>
        <td style={{ textAlign: 'right' }}>{formatTransition(brick.transition_times)}</td>
        <td style={{ textAlign: 'right' }}>{Math.floor(safef(brick.total_calories)) || '-'}</td>
      </tr>
      {expanded && brick.workouts.map((w, i) => (
        <tr
          key={w.workout_num || i}
          className="clickable"
          style={{ background: 'var(--bg-1)' }}
          onClick={e => { e.stopPropagation(); onWorkoutClick(w.workout_num) }}
        >
          <td style={{ paddingInlineStart: 28 }} className="text-dim">
            {i === 0 ? '' : `T${i}: ${brick.transition_times?.[i - 1] ? Math.round(brick.transition_times[i - 1]) + 'm' : '-'}`}
          </td>
          <td><Badge type={w.discipline} text={w.type} /></td>
          <td style={{ textAlign: 'right' }}>{fmtDur(safef(w.duration_min))}</td>
          <td style={{ textAlign: 'right' }}>
            {safef(w.distance_km) > 0 ? fmtDist(safef(w.distance_km)) + ' km' : '-'}
          </td>
          <td style={{ textAlign: 'right' }}>{Math.round(safef(w.HeartRate_average)) || '-'}</td>
          <td></td>
          <td style={{ textAlign: 'right' }}>{Math.floor(safef(w.ActiveEnergyBurned_sum)) || '-'}</td>
        </tr>
      ))}
    </>
  )
}
