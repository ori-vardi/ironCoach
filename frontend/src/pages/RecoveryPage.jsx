import { useState, useEffect, useCallback } from 'react'
import Plot from 'react-plotly.js'
import { api } from '../api'
import { PLOTLY_LAYOUT, PLOTLY_CONFIG } from '../constants'
import { fmtSleepHours, getRecoveryInfoTexts } from '../utils/formatters'
import InfoTip from '../components/common/InfoTip'
import RaceReadinessBar from '../components/common/RaceReadinessBar'
import LoadingSpinner from '../components/common/LoadingSpinner'
import { useI18n } from '../i18n/I18nContext'
import { useApp } from '../context/AppContext'

function renderInlineMarkup(text) {
  if (!text.includes('**') && !text.includes('[val:')) return text
  const parts = text.split(/(\*\*.+?\*\*|\[val:#[a-fA-F0-9]{3,6}:[^\]]+\])/)
  return parts.map((p, j) => {
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={j}>{p.slice(2, -2)}</strong>
    const valMatch = p.match(/^\[val:(#[a-fA-F0-9]{3,6}):(.+)\]$/)
    if (valMatch) return <strong key={j} style={{ color: valMatch[1] }}>{valMatch[2]}</strong>
    return p
  })
}

function renderEduLine(line, i) {
  const trimmed = line.trim()
  if (!trimmed) return <div key={i} className="education-spacer" />
  if (trimmed.startsWith('**') && trimmed.endsWith('**')) {
    return <h5 key={i} className="education-heading">{trimmed.slice(2, -2)}</h5>
  }
  const dotMatch = trimmed.match(/^\[dot:(#[a-fA-F0-9]+)\]\s*(.*)/)
  if (dotMatch) {
    return (
      <div key={i} className="education-dot-line">
        <span className="info-tip-dot" style={{ background: dotMatch[1] }} />
        <span>{renderInlineMarkup(dotMatch[2])}</span>
      </div>
    )
  }
  return <p key={i} className="education-line">{renderInlineMarkup(trimmed)}</p>
}

function assessValue(val, ranges) {
  for (let i = ranges.length - 1; i >= 0; i--) {
    if (val >= ranges[i][0]) return { color: ranges[i][1], label: ranges[i][2] }
  }
  return { color: ranges[0][1], label: ranges[0][2] }
}

function coloredVal(value, unit, assessment) {
  return `[val:${assessment.color}:${value}${unit}] (${assessment.label})`
}

function EducationPanel({ t, current, lastRec, weeklyLoad, timeline }) {
  let raw = t('education_content')
  if (!raw || typeof raw !== 'string') return null

  if (current) {
    const ctl = Math.round(current.fitness)
    const atl = Math.round(current.fatigue)
    const tsb = Math.round(current.fitness - current.fatigue)
    const rec = Math.round(current.recovery)

    const ctlA = assessValue(ctl, [[0, '#ff5370', 'Beginner'], [40, '#ffc777', 'Intermediate'], [70, '#c3e88d', 'Advanced']])
    const atlA = assessValue(atl, [[0, '#c3e88d', 'Low fatigue'], [80, '#ffc777', 'Elevated'], [100, '#ff5370', 'High — recovery needed']])
    const tsbA = assessValue(tsb, [[-30, '#ff5370', 'Overreaching'], [-10, '#ff966c', 'Building fitness'], [0, '#ffc777', 'Balanced'], [10, '#c3e88d', 'Fresh — race ready']])
    const recA = assessValue(rec, [[0, '#ff5370', 'Depleted'], [25, '#ff966c', 'Fatigued'], [50, '#ffc777', 'Moderate'], [75, '#c3e88d', 'Fresh']])

    raw = raw.replace(/\{ctl\}/g, coloredVal(ctl, '', ctlA))
      .replace(/\{atl\}/g, coloredVal(atl, '', atlA))
      .replace(/\{tsb\}/g, coloredVal(tsb > 0 ? `+${tsb}` : tsb, '', tsbA))
      .replace(/\{recovery\}/g, coloredVal(rec, '%', recA))
  }
  if (lastRec) {
    if (lastRec.resting_hr) {
      const rhr = Math.round(lastRec.resting_hr)
      const rhrA = assessValue(rhr, [[0, '#c3e88d', 'Excellent'], [50, '#ffc777', 'Good'], [60, '#ff5370', 'Elevated']])
      raw = raw.replace(/\{rhr\}/g, coloredVal(rhr, ' bpm', rhrA))
    }
    if (lastRec.hrv_ms) {
      const hrv = Math.round(lastRec.hrv_ms)
      const hrvA = assessValue(hrv, [[0, '#ff5370', 'Low'], [40, '#ffc777', 'Moderate'], [55, '#c3e88d', 'Good']])
      raw = raw.replace(/\{hrv\}/g, coloredVal(hrv, ' ms', hrvA))
    }
    if (lastRec.sleep_total) {
      const sleepH = lastRec.sleep_total / 60
      const h = Math.floor(sleepH)
      const m = Math.round(lastRec.sleep_total % 60)
      const slA = assessValue(sleepH, [[0, '#ff5370', 'Insufficient'], [6, '#ffc777', 'Adequate'], [7, '#c3e88d', 'Optimal']])
      raw = raw.replace(/\{sleep\}/g, coloredVal(`${h}h ${m}m`, '', slA))
    }
  }
  if (weeklyLoad && weeklyLoad.change_pct !== undefined) {
    const pct = Math.abs(weeklyLoad.change_pct)
    const sign = weeklyLoad.change_pct > 0 ? '+' : ''
    const wlA = assessValue(pct, [[0, '#c3e88d', 'Safe'], [10, '#ffc777', 'Moderate increase'], [20, '#ff5370', 'Risky — injury prone']])
    raw = raw.replace(/\{weekly_change\}/g, coloredVal(`${sign}${weeklyLoad.change_pct}%`, '', wlA))
  }
  if (timeline && timeline.length > 0) {
    const latestTrimp = timeline.findLast(e => e.day_trimp > 0)
    if (latestTrimp) {
      const tv = Math.round(latestTrimp.day_trimp)
      const tA = assessValue(tv, [[0, '#c3e88d', 'Easy'], [50, '#ffc777', 'Moderate'], [100, '#ff966c', 'Hard'], [150, '#ff5370', 'Very Hard']])
      raw = raw.replace(/\{trimp\}/g, coloredVal(tv, '', tA))
    }
    const latestHrtss = timeline.findLast(e => (e.day_hrtss ?? 0) > 0)
    if (latestHrtss) {
      const hv = Math.round(latestHrtss.day_hrtss)
      const hA = assessValue(hv, [[0, '#82aaff', 'Easy'], [40, '#ffc777', 'Moderate'], [80, '#ff966c', 'Threshold'], [120, '#ff5370', 'Hard']])
      raw = raw.replace(/\{hrtss\}/g, coloredVal(hv, '', hA))
    }
  }
  raw = raw.replace(/\{[a-z_]+\}/g, '--')

  const lines = raw.split('\n')

  const sections = []
  let cur = []
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim()
    if (trimmed.startsWith('**') && trimmed.endsWith('**') && cur.length > 0) {
      sections.push(cur)
      cur = []
    }
    cur.push({ line: lines[i], idx: i })
  }
  if (cur.length > 0) sections.push(cur)

  return (
    <div className="card mb-20 education-card">
      <h4 className="education-card-title">{String(t('education_title'))}</h4>
      <div className="education-content" dir="auto">
        {sections.map((section, si) => (
          <div key={si} className="education-section">
            {section.map(({ line, idx }) => renderEduLine(line, idx))}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function RecoveryPage() {
  const { t } = useI18n()
  const { dateFrom, dateTo } = useApp()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [raceEvents, setRaceEvents] = useState([])
  const [showEducation, setShowEducation] = useState(false)

  const load = useCallback(async () => {
    try {
      const d = await api(`/api/recovery?from_date=${dateFrom}&to_date=${dateTo}`)
      setData(d)
    } catch (e) {
      console.error('Recovery load error:', e)
    } finally {
      setLoading(false)
    }
  }, [dateFrom, dateTo])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    api('/api/events')
      .then(events => {
        if (Array.isArray(events)) {
          const valid = events.filter(e => e.event_date).sort((a, b) => new Date(a.event_date) - new Date(b.event_date))
          setRaceEvents(valid)
        }
      })
      .catch(err => console.error('Failed to load events:', err))
  }, [])

  if (loading) return <LoadingSpinner />
  if (!data || !data.current) return <p className="text-dim">{t('no_recovery_data')}</p>

  const { current, timeline, recovery_data, vo2max = [], weekly_load = null } = data

  const INFO = getRecoveryInfoTexts(t)

  // TSB chart data
  const dates = timeline.map(e => e.date)
  const fitness = timeline.map(e => e.fitness)
  const fatigue = timeline.map(e => e.fatigue)
  const recovery = timeline.map(e => e.recovery)
  const trimp = timeline.map(e => e.day_trimp)
  const hrtss = timeline.map(e => e.day_hrtss ?? 0)
  const hasHrtss = hrtss.some(v => v > 0)

  // Recovery data (sleep, RHR, HRV)
  const recDates = recovery_data.map(r => r.date)
  const rhr = recovery_data.map(r => r.resting_hr ?? null)
  const hrv = recovery_data.map(r => r.hrv_ms ?? null)
  const sleepTotal = recovery_data.map(r => r.sleep_total ? r.sleep_total / 60 : null)
  const sleepDeep = recovery_data.map(r => r.sleep_deep ? r.sleep_deep / 60 : null)
  const sleepRem = recovery_data.map(r => r.sleep_rem ? r.sleep_rem / 60 : null)
  const sleepCore = recovery_data.map(r => r.sleep_core ? r.sleep_core / 60 : null)

  // Latest recovery data
  const lastRec = recovery_data.length > 0 ? recovery_data[recovery_data.length - 1] : null

  const chartLayout = (title, yLabel, extra = {}) => ({
    ...PLOTLY_LAYOUT,
    margin: { ...PLOTLY_LAYOUT.margin, t: 8 },
    xaxis: { ...PLOTLY_LAYOUT.xaxis },
    yaxis: { ...PLOTLY_LAYOUT.yaxis, title: yLabel },
    showlegend: true,
    legend: { ...PLOTLY_LAYOUT.legend, orientation: 'h', y: 1.12 },
    ...extra,
  })

  return (
    <>
      <div className="flex-between mb-20">
        <h1 className="page-title" style={{ margin: 0 }}>{t('recovery_title')}</h1>
        <button type="button" className="education-toggle" onClick={() => setShowEducation(v => !v)}>
          {showEducation ? '\u25B2 ' : '\u25BC '}{t('learn_more')}
        </button>
      </div>

      {/* Education Panel */}
      {showEducation && (
        <EducationPanel t={t} current={current} lastRec={lastRec} weeklyLoad={weekly_load} timeline={timeline} />
      )}

      {/* Race Day Projection */}
      {recovery_data.length > 0 && raceEvents.length > 0 && (
        <div className="card mb-20">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <h4 style={{ margin: 0 }}>{t('race_readiness')}</h4>
            <InfoTip text={t('tsb_explanation') + t('tsb_explanation_colors')} />
          </div>

          {/* TSB current value */}
          {(() => {
            const tsb = Math.round(current.fitness - current.fatigue)
            let tsbColor = '#c3e88d'
            let tsbZoneKey = 'tsb_zone_peaked'
            if (tsb < -20) { tsbColor = '#ff5370'; tsbZoneKey = 'tsb_zone_building' }
            else if (tsb < 0) { tsbColor = '#ff966c'; tsbZoneKey = 'tsb_zone_maintaining' }
            else if (tsb < 15) { tsbColor = '#ffc777'; tsbZoneKey = 'tsb_zone_tapering' }
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span className="text-sm">{t('tsb_current_value')}</span>
                <span className="tsb-value" style={{ color: tsbColor }}>{tsb > 0 ? `+${tsb}` : tsb}</span>
                <span className="text-sm text-dim" dir="auto">{t(tsbZoneKey)}</span>
              </div>
            )
          })()}

          {raceEvents.map(event => (
            <RaceReadinessBar key={event.id} event={event} tsb={Math.round(current.fitness - current.fatigue)} />
          ))}
        </div>
      )}

      {/* TSB / Training Load Chart */}
      <div className="card mt-20">
        <div className="chart-title-row">
          <h4>{t('tsb_chart')}</h4>
          <InfoTip text={INFO.tsb} />
        </div>
        <Plot
          data={[
            { x: dates, y: fitness, type: 'scatter', mode: 'lines', name: t('fitness_ctl'), line: { color: '#c3e88d', width: 2 } },
            { x: dates, y: fatigue, type: 'scatter', mode: 'lines', name: t('fatigue_atl'), line: { color: '#ff966c', width: 2 } },
            { x: dates, y: recovery, type: 'scatter', mode: 'lines', name: t('recovery_pct'), line: { color: '#65bcff', width: 2, dash: 'dot' }, yaxis: 'y2' },
          ]}
          layout={{
            ...chartLayout('', ''),
            yaxis: { ...PLOTLY_LAYOUT.yaxis, title: t('training_load') },
            yaxis2: { overlaying: 'y', side: 'right', title: t('recovery_pct'), range: [0, 100], gridcolor: 'transparent', titlefont: { color: '#65bcff' }, tickfont: { color: '#65bcff' } },
          }}
          config={PLOTLY_CONFIG}
          useResizeHandler style={{ width: '100%', height: 320 }}
        />
      </div>

      {/* Daily Training Load Chart */}
      <div className="card mt-20">
        <div className="chart-title-row">
          <h4>{t('daily_trimp')}</h4>
          <InfoTip text={INFO.trimp} />
        </div>
        <Plot
          data={[
            {
              x: dates, y: trimp, type: 'bar', name: 'TRIMP',
              marker: {
                color: trimp.map(v => v > 150 ? '#ff5370' : v > 80 ? '#ff966c' : v > 0 ? '#c3e88d' : 'transparent'),
              },
            },
            ...(hasHrtss ? [{
              x: dates, y: hrtss, type: 'bar', name: 'hrTSS',
              marker: {
                color: hrtss.map(v => v > 120 ? '#ff5370' : v > 80 ? '#ffc777' : v > 0 ? '#82aaff' : 'transparent'),
              },
            }] : []),
          ]}
          layout={{ ...chartLayout('', ''), barmode: 'group' }}
          config={PLOTLY_CONFIG}
          useResizeHandler style={{ width: '100%', height: 220 }}
        />
      </div>

      {/* Sleep & Recovery Biomarkers */}
      {recovery_data.length > 0 && (
        <>
          <h3 style={{ margin: '24px 0 12px' }}>{t('recovery_biomarkers')}</h3>
          <div className="chart-grid-2col">
            {/* Resting HR */}
            {rhr.some(v => v !== null) && (
              <div className="card">
                <div className="chart-title-row">
                  <h4>{t('resting_heart_rate')}</h4>
                  <InfoTip text={INFO.rhr} />
                </div>
                <Plot
                  data={[{ x: recDates, y: rhr, type: 'scatter', mode: 'lines+markers', name: 'RHR', line: { color: '#ff5370' }, marker: { size: 4 }, connectgaps: false }]}
                  layout={chartLayout('', 'bpm')}
                  config={PLOTLY_CONFIG}
                  useResizeHandler style={{ width: '100%', height: 250 }}
                />
              </div>
            )}

            {/* HRV */}
            {hrv.some(v => v !== null) && (
              <div className="card">
                <div className="chart-title-row">
                  <h4>{t('heart_rate_variability')}</h4>
                  <InfoTip text={INFO.hrv} />
                </div>
                <Plot
                  data={[{ x: recDates, y: hrv, type: 'scatter', mode: 'lines+markers', name: 'SDNN', line: { color: '#c099ff' }, marker: { size: 4 }, connectgaps: false }]}
                  layout={chartLayout('', 'ms')}
                  config={PLOTLY_CONFIG}
                  useResizeHandler style={{ width: '100%', height: 250 }}
                />
              </div>
            )}

            {/* VO2Max */}
            {vo2max.length > 1 && (
              <div className="card">
                <div className="chart-title-row">
                  <h4>{t('vo2max_trend')}</h4>
                  <InfoTip text={t('info_vo2max')} />
                </div>
                <Plot
                  data={[{
                    x: vo2max.map(v => v.date),
                    y: vo2max.map(v => v.value),
                    type: 'scatter',
                    mode: 'lines+markers',
                    name: 'VO\u2082max',
                    line: { color: '#82aaff' },
                    marker: { size: 5 },
                    text: vo2max.map(v => `#${v.workout_num} ${v.workout_type}`),
                    hovertemplate: '%{text}<br>%{x}<br>%{y:.1f} mL/min\u00B7kg<extra></extra>',
                  }]}
                  layout={chartLayout('', 'mL/min\u00B7kg')}
                  config={PLOTLY_CONFIG}
                  useResizeHandler style={{ width: '100%', height: 250 }}
                />
              </div>
            )}

            {/* Sleep Duration */}
            {sleepTotal.some(v => v !== null) && (
              <div className="card">
                <div className="chart-title-row">
                  <h4>{t('sleep_duration')}</h4>
                  <InfoTip text={INFO.sleep} />
                </div>
                <Plot
                  data={[
                    { x: recDates, y: sleepDeep, type: 'bar', name: t('deep'), marker: { color: '#3d59a1' } },
                    { x: recDates, y: sleepCore, type: 'bar', name: t('core'), marker: { color: '#65bcff' } },
                    { x: recDates, y: sleepRem, type: 'bar', name: t('rem'), marker: { color: '#c099ff' } },
                  ]}
                  layout={{
                    ...chartLayout('', 'hours'),
                    barmode: 'stack',
                    shapes: [{
                      type: 'line', y0: 7, y1: 7, x0: 0, x1: 1, xref: 'paper',
                      line: { color: '#c3e88d', width: 1, dash: 'dot' },
                    }, {
                      type: 'line', y0: 9, y1: 9, x0: 0, x1: 1, xref: 'paper',
                      line: { color: '#c3e88d', width: 1, dash: 'dot' },
                    }],
                  }}
                  config={PLOTLY_CONFIG}
                  useResizeHandler style={{ width: '100%', height: 250 }}
                />
              </div>
            )}

            {/* Sleep Stages Avg */}
            {sleepTotal.some(v => v !== null) && (() => {
              const validSleep = recovery_data.filter(r => r.sleep_total)
              if (validSleep.length === 0) return null
              const avgTotal = validSleep.reduce((s, r) => s + r.sleep_total, 0) / validSleep.length
              const avgDeep = validSleep.reduce((s, r) => s + (r.sleep_deep || 0), 0) / validSleep.length
              const avgCore = validSleep.reduce((s, r) => s + (r.sleep_core || 0), 0) / validSleep.length
              const avgRem = validSleep.reduce((s, r) => s + (r.sleep_rem || 0), 0) / validSleep.length
              const avgAwake = validSleep.reduce((s, r) => s + (r.sleep_awake || 0), 0) / validSleep.length
              return (
                <div className="card">
                  <h4 style={{ marginBottom: 12 }}>{t('avg_sleep')}</h4>
                  <div className="sleep-avg-grid">
                    <div className="sleep-avg-item">
                      <div className="sleep-avg-value">{fmtSleepHours(avgTotal)}</div>
                      <div className="sleep-avg-label">{t('total')}</div>
                    </div>
                    <div className="sleep-avg-item">
                      <div className="sleep-avg-value" style={{ color: '#3d59a1' }}>{fmtSleepHours(avgDeep)}</div>
                      <div className="sleep-avg-label">{t('deep')}</div>
                    </div>
                    <div className="sleep-avg-item">
                      <div className="sleep-avg-value" style={{ color: '#65bcff' }}>{fmtSleepHours(avgCore)}</div>
                      <div className="sleep-avg-label">{t('core')}</div>
                    </div>
                    <div className="sleep-avg-item">
                      <div className="sleep-avg-value" style={{ color: '#c099ff' }}>{fmtSleepHours(avgRem)}</div>
                      <div className="sleep-avg-label">{t('rem')}</div>
                    </div>
                    <div className="sleep-avg-item">
                      <div className="sleep-avg-value" style={{ color: 'var(--text-dim)' }}>{fmtSleepHours(avgAwake)}</div>
                      <div className="sleep-avg-label">{t('awake')}</div>
                    </div>
                  </div>
                  <p className="text-sm text-dim mt-12">
                    {t('sleep_based_on_pre')} {validSleep.length} {t('sleep_based_on_post')}
                  </p>
                </div>
              )
            })()}
          </div>
        </>
      )}
    </>
  )
}
