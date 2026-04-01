import { useState, useEffect, useMemo } from 'react'
import { api } from '../api'
import { useApp } from '../context/AppContext'
import { useI18n } from '../i18n/I18nContext'
import { fmtDateShort, fmtTime, fmtDur, fmtDist, safef } from '../utils/formatters'
import useTableSort from '../utils/useTableSort'
import Badge from '../components/common/Badge'
import LoadingSpinner from '../components/common/LoadingSpinner'
import WorkoutDetailModal from '../components/WorkoutDetailModal'
import MergeActionBar from '../components/MergeActionBar'

export default function AllWorkoutsPage() {
  const { t } = useI18n()
  const { workouts, setWorkouts, dateFrom, dateTo, refreshWorkouts } = useApp()
  const [filter, setFilter] = useState([])
  const [loading, setLoading] = useState(!workouts.length)
  const [error, setError] = useState(null)
  const [detailNum, setDetailNum] = useState(null)
  const [brickNums, setBrickNums] = useState(new Set())
  const [selected, setSelected] = useState(new Set())
  const [showHidden, setShowHidden] = useState(false)
  const [hiddenNums, setHiddenNums] = useState(new Set())

  useEffect(() => {
    loadWorkouts()
  }, [showHidden])

  function loadWorkouts() {
    setLoading(true)
    const url = showHidden ? '/api/summary?show_hidden=true' : '/api/summary'
    api(url)
      .then((data) => { setWorkouts(data); setLoading(false) })
      .catch((e) => { setError(e.message); setLoading(false) })
  }

  useEffect(() => {
    if (showHidden) {
      api('/api/workouts/hidden')
        .then(nums => setHiddenNums(new Set(nums)))
        .catch(() => {})
    } else {
      setHiddenNums(new Set())
    }
  }, [showHidden])

  useEffect(() => {
    api(`/api/bricks?from_date=${dateFrom}&to_date=${dateTo}`)
      .then(bricks => {
        const nums = new Set()
        bricks.forEach(b => b.workouts.forEach(w => nums.add(String(w.workout_num))))
        setBrickNums(nums)
      })
      .catch(err => console.error('Failed to load:', err))
  }, [dateFrom, dateTo])

  const disciplines = ['run', 'bike', 'swim', 'strength', 'other']

  const toggleFilter = (d) => {
    setFilter(prev => prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d])
  }

  const filtered = useMemo(() => {
    let list = filter.length ? workouts.filter((w) => filter.includes(w.discipline)) : workouts
    if (showHidden && hiddenNums.size > 0) {
      list = list.map(w => hiddenNums.has(Number(w.workout_num)) ? { ...w, _hidden: true } : w)
    }
    return list
  }, [workouts, filter, showHidden, hiddenNums])

  const allSortCols = useMemo(() => ({
    num: w => Number(w.workout_num),
    date: w => w.startDate,
    type: w => w.discipline || '',
    duration: w => safef(w.duration_min),
    distance: w => safef(w.distance_km),
    avg_hr: w => safef(w.HeartRate_average),
    max_hr: w => safef(w.HeartRate_maximum),
    calories: w => safef(w.ActiveEnergyBurned_sum),
  }), [])
  const { sorted, handleSort, sortArrow } = useTableSort(filtered, allSortCols, 'date', 'desc')

  function toggleSelect(wnum, e) {
    e.stopPropagation()
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(wnum)) next.delete(wnum)
      else next.add(wnum)
      return next
    })
  }

  const selectedWorkouts = useMemo(() => {
    return filtered.filter(w => selected.has(w.workout_num))
  }, [filtered, selected])

  function handleActionDone() {
    setSelected(new Set())
    loadWorkouts()
    refreshWorkouts()
    api(`/api/bricks?from_date=${dateFrom}&to_date=${dateTo}`)
      .then(bricks => {
        const nums = new Set()
        bricks.forEach(b => b.workouts.forEach(w => nums.add(String(w.workout_num))))
        setBrickNums(nums)
      })
      .catch(() => {})
    if (showHidden) {
      api('/api/workouts/hidden')
        .then(nums => setHiddenNums(new Set(nums)))
        .catch(() => {})
    }
  }

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">Error: {error}</div>

  return (
    <>
      <h1 className="page-title">{t('page_workouts')} ({filtered.length})</h1>
      <div className="form-inline mb-12">
        <label>{t('workouts_filter')}: </label>
        <div className="filter-toggles">
          {disciplines.map(d => (
            <button
              key={d}
              className={`filter-toggle-btn${filter.includes(d) ? ' active' : ''}`}
              onClick={() => toggleFilter(d)}
            >
              {t(`workouts_filter_${d}`)}
            </button>
          ))}
          {filter.length > 0 && (
            <button className="filter-toggle-btn clear" onClick={() => setFilter([])}>
              {t('workouts_filter_all')}
            </button>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginInlineStart: 'auto', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-dim)', cursor: 'pointer' }}>
            <input type="checkbox" checked={showHidden} onChange={e => setShowHidden(e.target.checked)} />
            {t('show_hidden')}
          </label>
          {selected.size > 0 && (
            <>
              <MergeActionBar workouts={selectedWorkouts} onDone={handleActionDone} showHidden={showHidden} />
              <button className="btn btn-sm" onClick={() => setSelected(new Set())}>
                {t('clear_selection')} ({selected.size})
              </button>
            </>
          )}
        </div>
      </div>

      {/* Merge/brick context note row (rendered by MergeActionBar when active) */}

      <div className="table-scroll" style={{ maxHeight: 'calc(100vh - 220px)' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 30 }}></th>
              <th className="sortable-th" onClick={() => handleSort('num')}>#{sortArrow('num')}</th>
              <th className="sortable-th" onClick={() => handleSort('date')}>{t('date')}{sortArrow('date')}</th>
              <th>{t('workouts_start')}</th>
              <th className="sortable-th" onClick={() => handleSort('type')}>{t('workouts_type')}{sortArrow('type')}</th>
              <th className="sortable-th" onClick={() => handleSort('duration')}>{t('workouts_duration')}{sortArrow('duration')}</th>
              <th className="sortable-th" onClick={() => handleSort('distance')}>{t('workouts_distance')}{sortArrow('distance')}</th>
              <th className="sortable-th" onClick={() => handleSort('avg_hr')}>{t('workouts_avg_hr')}{sortArrow('avg_hr')}</th>
              <th className="sortable-th" onClick={() => handleSort('max_hr')}>{t('workouts_max_hr')}{sortArrow('max_hr')}</th>
              <th className="sortable-th" onClick={() => handleSort('calories')}>{t('th_calories')}{sortArrow('calories')}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((w) => {
              const isSelected = selected.has(w.workout_num)
              const isHidden = !!w._hidden
              return (
                <tr
                  key={w.workout_num}
                  className={`clickable${isSelected ? ' row-selected' : ''}${isHidden ? ' hidden-row' : ''}`}
                  onClick={() => setDetailNum(w.workout_num)}
                >
                  <td onClick={e => toggleSelect(w.workout_num, e)}>
                    <input type="checkbox" checked={isSelected} readOnly style={{ cursor: 'pointer' }} />
                  </td>
                  <td>{w.workout_num}</td>
                  <td>{fmtDateShort(w.startDate)}</td>
                  <td>{fmtTime(w.startDate, w.meta_TimeZone)}</td>
                  <td>
                    <Badge type={w.discipline} text={w.type} />
                    {brickNums.has(String(w.workout_num)) && <span className="brick-tag" title={t('page_bricks')}>🧱</span>}
                    {isHidden && <span className="hidden-tag">{t('hidden_tag')}</span>}
                  </td>
                  <td>{fmtDur(safef(w.duration_min))}</td>
                  <td>{safef(w.distance_km) > 0 ? fmtDist(safef(w.distance_km)) + ' km' : '-'}</td>
                  <td>{Math.round(safef(w.HeartRate_average)) || '-'}</td>
                  <td>{Math.round(safef(w.HeartRate_maximum)) || '-'}</td>
                  <td>{Math.floor(safef(w.ActiveEnergyBurned_sum)) || '-'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

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
