import { useState, useEffect, useCallback, useMemo } from 'react'
import Plot from 'react-plotly.js'
import { api } from '../api'
import { PLOTLY_LAYOUT, PLOTLY_CONFIG } from '../constants'
import KpiCard from '../components/common/KpiCard'
import InfoTip from '../components/common/InfoTip'
import { useI18n } from '../i18n/I18nContext'
import { useApp } from '../context/AppContext'

function ProfileSection({ onBmrUpdate, onHeightUpdate }) {
  const [open, setOpen] = useState(false)
  const [profile, setProfile] = useState(null)
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const { t } = useI18n()

  useEffect(() => {
    api('/api/auth/profile').then(p => {
      setProfile(p)
      if (onBmrUpdate) onBmrUpdate(computeBmr(p))
      if (onHeightUpdate && p.height_cm > 0) onHeightUpdate(p.height_cm / 100)
    }).catch(err => console.error('Failed to load:', err))
  }, [])

  function computeBmr(p) {
    if (!p || !p.height_cm || !p.birth_date) return 0
    const age = (Date.now() - new Date(p.birth_date).getTime()) / (365.25 * 24 * 60 * 60 * 1000)
    const weight = p.weight_kg || 0
    if (!weight || !p.height_cm) return 0
    const offset = p.sex === 'female' ? -161 : 5
    return Math.round(10 * weight + 6.25 * p.height_cm - 5 * age + offset)
  }

  if (!profile) return null

  const hasProfile = profile.height_cm > 0 && profile.birth_date
  const age = profile.birth_date
    ? Math.floor((Date.now() - new Date(profile.birth_date).getTime()) / (365.25 * 24 * 60 * 60 * 1000))
    : null

  const chips = []
  if (profile.sex) chips.push(profile.sex === 'female' ? 'Female' : 'Male')
  if (age) chips.push(`${age}y`)
  if (profile.height_cm > 0) chips.push(`${profile.height_cm} cm`)

  async function handleSave() {
    setMsg('')
    setSaving(true)
    try {
      await api('/api/auth/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          height_cm: parseFloat(profile.height_cm) || 0,
          weight_kg: parseFloat(profile.weight_kg) || 0,
          birth_date: profile.birth_date,
          sex: profile.sex,
        }),
      })
      if (onBmrUpdate) onBmrUpdate(computeBmr(profile))
      const h = parseFloat(profile.height_cm) || 0
      if (onHeightUpdate && h > 0) onHeightUpdate(h / 100)
      setMsg('Saved')
      setTimeout(() => { setMsg(''); setOpen(false) }, 1200)
    } catch {
      setMsg('Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="profile-section" style={{ marginBottom: 18 }}>
      <button
        className="profile-toggle"
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
          background: hasProfile ? 'var(--bg-2)' : 'rgba(255,150,108,0.08)',
          border: `1px solid ${hasProfile ? 'var(--border)' : 'rgba(255,150,108,0.25)'}`,
          borderRadius: 10, color: 'var(--text)', cursor: 'pointer', fontSize: 13, width: '100%',
          transition: 'background 0.15s',
        }}
      >
        <span style={{ fontSize: 16 }}>{'\uD83C\uDFC3'}</span>
        <span style={{ fontWeight: 600, fontSize: 13 }}>Athlete Profile</span>
        {hasProfile ? (
          <div style={{ display: 'flex', gap: 6, marginInlineStart: 6 }}>
            {chips.map(c => (
              <span key={c} style={{
                padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 500,
                background: 'rgba(101,188,255,0.1)', color: 'var(--accent)',
              }}>{c}</span>
            ))}
          </div>
        ) : (
          <span style={{ opacity: 0.5, fontSize: 12, marginInlineStart: 4 }}>Not configured — click to set up</span>
        )}
        <span style={{ marginInlineStart: 'auto', opacity: 0.4, fontSize: 11 }}>{open ? '\u25B2' : '\u25BC'}</span>
      </button>
      {open && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-end',
          padding: '14px 16px', marginTop: 4,
          background: 'var(--bg-2)', border: '1px solid var(--border)', borderRadius: 10,
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="text-dim" style={{ fontSize: 11, fontWeight: 500 }}>Height (cm)</label>
            <input type="number" className="input-sm" style={{ width: 80 }}
              value={profile.height_cm > 0 ? profile.height_cm : ''}
              onChange={e => setProfile(p => ({ ...p, height_cm: e.target.value }))}
              placeholder="e.g. 180" />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="text-dim" style={{ fontSize: 11, fontWeight: 500 }}>Birth Date</label>
            <input type="date" className="input-sm" style={{ width: 145 }}
              value={profile.birth_date || ''}
              onChange={e => setProfile(p => ({ ...p, birth_date: e.target.value }))} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label className="text-dim" style={{ fontSize: 11, fontWeight: 500 }}>Sex</label>
            <select className="input-sm" style={{ width: 95 }}
              value={profile.sex || 'male'}
              onChange={e => setProfile(p => ({ ...p, sex: e.target.value }))}>
              <option value="male">Male</option>
              <option value="female">Female</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginInlineStart: 8 }}>
            <button className="btn btn-accent btn-xs" onClick={handleSave} disabled={saving}>{saving ? t('saving') : t('save')}</button>
            <button className="btn btn-xs" onClick={() => setOpen(false)}>Cancel</button>
            {msg && <span className={msg === 'Saved' ? 'text-green text-xs' : 'text-red text-xs'}>{msg}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

function formatDate(d) {
  const dt = new Date(d)
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' })
}

function daysSince(dateStr) {
  const d = new Date(dateStr)
  const now = new Date()
  return Math.floor((now - d) / (1000 * 60 * 60 * 24))
}

const numStyle = { textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

// Reference ranges based on: Male, 180cm, ~37yo, endurance athlete
// Reference ranges computed from athlete profile (male, 180cm, ~37yo)
//
// VERIFIED SOURCES:
//   Weight:      WHO BMI "Normal" = 18.5-25 × height²  (WHO, confirmed via calculator.net)
//   Body Fat:    ACE body fat categories for males      (ACE, confirmed via calculator.net)
//                Essential 2-5%, Athletes 6-13%, Fitness 14-17%, Average 18-24%, Obese 25%+
//   BMI:         WHO classification                     (WHO, confirmed via calculator.net)
//                Normal 18.5-25, Overweight 25-30, Obese 30+
//   Lean Mass:   Derived mathematically: Weight × (1 − BF%). Uses ACE athlete BF range.
//   Muscle Mass: BIA scale "Standard" range cross-referenced with athlete's LeaOne data:
//                At 66.3kg, 35.0kg muscle mass (52.8%) rated "Standard" by scale.
//                Male BIA norms: low <49%, standard 49-59%, high >59%
//   Muscle Rate: Same BIA norms as above, expressed as percentage.
const ATHLETE_HEIGHT_M = 1.80
const DEFAULT_WEIGHT_KG = 66
const WHO_BMI_LOW = 18.5
const WHO_BMI_HIGH = 25

function getRanges(latestWeight, heightM) {
  const h = heightM || ATHLETE_HEIGHT_M
  const w = latestWeight || DEFAULT_WEIGHT_KG
  return {
    weight: {
      low: Math.round(WHO_BMI_LOW * h * h * 10) / 10,
      high: Math.round(WHO_BMI_HIGH * h * h * 10) / 10,
      label: `WHO Normal BMI (${WHO_BMI_LOW}-${WHO_BMI_HIGH}) at ${Math.round(h * 100)}cm`,
    },
    bodyFat: {
      low: 6,
      high: 13,
      label: 'ACE Athletes (male): 6-13%',
    },
    bmi: {
      low: WHO_BMI_LOW,
      high: WHO_BMI_HIGH,
      label: `WHO Normal: ${WHO_BMI_LOW}-${WHO_BMI_HIGH}`,
    },
    leanMass: {
      // Derived: Weight × (1 - BF%) at ACE athlete range (6-13%)
      low: Math.round(w * (1 - 0.13) * 10) / 10,
      high: Math.round(w * (1 - 0.06) * 10) / 10,
      label: `At 6-13% body fat (${w}kg)`,
    },
    muscleMass: {
      // BIA scale norms for males: "Standard" = 49-59% of body weight
      // Cross-ref: athlete's 35.0kg/66.3kg = 52.8% rated "Standard" by LeaOne
      low: Math.round(w * 0.49 * 10) / 10,
      high: Math.round(w * 0.59 * 10) / 10,
      label: `BIA Standard: 49-59% of BW (${w}kg)`,
    },
    muscleRate: {
      // Same BIA norms as percentage
      low: 49,
      high: 59,
      label: 'BIA Standard (male): 49-59%',
    },
  }
}

function rangeBand(low, high, color = 'rgba(76, 175, 80, 0.08)') {
  return {
    type: 'rect', xref: 'paper', yref: 'y',
    x0: 0, x1: 1, y0: low, y1: high,
    fillcolor: color, line: { width: 0 },
    layer: 'below',
  }
}

function rangeLine(val, color, dash = 'dot') {
  return {
    type: 'line', xref: 'paper', yref: 'y',
    x0: 0, x1: 1, y0: val, y1: val,
    line: { color, width: 1.5, dash },
    layer: 'below',
  }
}

function rangeShapes(range) {
  return [
    rangeBand(range.low, range.high),
    rangeLine(range.low, '#4caf50'),
    rangeLine(range.high, '#4caf50'),
  ]
}

// Info text definitions — includes source citations
function getInfo(t) {
  return {
    weight: t('info_weight'),
    bodyFat: t('info_body_fat'),
    bmi: t('info_bmi'),
    leanMass: t('info_lean_mass'),
    muscleMass: t('info_muscle_mass'),
    muscleRate: t('info_muscle_rate'),
  }
}

export default function BodyMetricsPage() {
  const { t } = useI18n()
  const { dateFrom, dateTo } = useApp()
  const [allData, setAllData] = useState([])
  const [loading, setLoading] = useState(true)
  const [sortCol, setSortCol] = useState('date')
  const [sortDir, setSortDir] = useState('desc')
  const [bmr, setBmr] = useState(0)
  const [profileHeight, setProfileHeight] = useState(null)

  const loadData = useCallback(() => {
    api('/api/body-metrics')
      .then(setAllData)
      .catch(() => setAllData([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadData() }, [loadData])

  useEffect(() => {
    window.addEventListener('coach-data-update', loadData)
    return () => window.removeEventListener('coach-data-update', loadData)
  }, [loadData])

  const data = useMemo(() => {
    if (!dateFrom && !dateTo) return allData
    return allData.filter(d => {
      const date = d.date
      if (dateFrom && date < dateFrom) return false
      if (dateTo && date > dateTo) return false
      return true
    })
  }, [allData, dateFrom, dateTo])

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir(prev => prev === 'asc' ? 'desc' : 'asc')
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
  }

  const sortArrow = (col) => {
    if (sortCol !== col) return null
    return <span className="sort-arrow">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>
  }

  const sortedData = useMemo(() => {
    const arr = [...data]
    arr.sort((a, b) => {
      let va, vb
      switch (sortCol) {
        case 'date': va = a.date; vb = b.date; break
        case 'weight': va = a.BodyMass ?? -1; vb = b.BodyMass ?? -1; break
        case 'body_fat': va = a.BodyFatPercentage ?? -1; vb = b.BodyFatPercentage ?? -1; break
        case 'muscle_mass': va = a.MuscleMass ?? -1; vb = b.MuscleMass ?? -1; break
        case 'muscle_rate': va = a.MuscleRate ?? -1; vb = b.MuscleRate ?? -1; break
        case 'lean_mass': va = a.LeanBodyMass ?? -1; vb = b.LeanBodyMass ?? -1; break
        case 'bmi': va = a.BodyMassIndex ?? -1; vb = b.BodyMassIndex ?? -1; break
        case 'source': va = a.source || ''; vb = b.source || ''; break
        default: va = a.date; vb = b.date
      }
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return arr
  }, [data, sortCol, sortDir])

  if (loading) return <div className="page-loading">{t('loading_body_metrics')}</div>
  if (!allData.length || !data.length) return (
    <div className="empty-state">
      <ProfileSection onBmrUpdate={setBmr} onHeightUpdate={setProfileHeight} />
      <p>{t('no_body_metrics')}</p>
      <p className="text-dim text-sm">{t('import_body_metrics_hint')}</p>
    </div>
  )

  const dates = data.map(d => d.date)
  const weight = data.map(d => d.BodyMass ?? null)
  const fat = data.map(d => d.BodyFatPercentage ?? null)
  const bmi = data.map(d => d.BodyMassIndex ?? null)
  const lean = data.map(d => d.LeanBodyMass ?? null)
  const muscle = data.map(d => d.MuscleMass ?? null)
  const muscleRate = data.map(d => d.MuscleRate ?? null)

  const hasMuscleMass = data.some(d => d.MuscleMass != null)
  const hasMuscleRate = data.some(d => d.MuscleRate != null)

  const latest = data[data.length - 1]
  const earliest = data.find(d => d.BodyMass) || data[0]
  const latestWeight = latest.BodyMass || 66
  const weightChange = latestWeight && earliest.BodyMass
    ? (latestWeight - earliest.BodyMass).toFixed(1)
    : null
  const days = daysSince(latest.date)
  const lastDateLabel = days === 0 ? t('today') : days === 1 ? t('yesterday') : `${days} ${t('days_ago')}`

  const ranges = getRanges(latestWeight, profileHeight)
  const INFO = getInfo(t)

  function rangeColor(value, range) {
    if (!value || !range) return 'var(--text)'
    if (value >= range.low && value <= range.high) return '#c3e88d'
    const dist = value < range.low ? range.low - value : value - range.high
    const span = range.high - range.low
    return dist > span * 0.5 ? '#ff5370' : '#ffc777'
  }
  const wColor = rangeColor(latestWeight, ranges.weight)
  const bfColor = rangeColor(latest.BodyFatPercentage, ranges.bodyFat)
  const mmColor = rangeColor(latest.MuscleMass, ranges.muscleMass)
  const lmColor = rangeColor(latest.LeanBodyMass, ranges.leanMass)
  const bmiColor = rangeColor(latest.BodyMassIndex, ranges.bmi)

  function tightYRange(values) {
    const valid = values.filter(v => v != null)
    if (!valid.length) return undefined
    const lo = Math.min(...valid)
    const hi = Math.max(...valid)
    const span = hi - lo
    const pad = Math.max(span * 0.15, 0.5)
    return [Math.floor((lo - pad) * 2) / 2, Math.ceil((hi + pad) * 2) / 2]
  }

  const chartLayout = (title, yTitle, shapes = [], yRange) => ({
    ...PLOTLY_LAYOUT,
    title: { text: title, font: { size: 14, color: '#c8d3f5' } },
    yaxis: { ...PLOTLY_LAYOUT.yaxis, title: yTitle, dtick: 0.5, ...(yRange ? { range: yRange } : {}) },
    xaxis: { ...PLOTLY_LAYOUT.xaxis },
    margin: { ...PLOTLY_LAYOUT.margin, t: 40 },
    shapes,
  })

  const trace = (y, name, color) => ({
    x: dates, y, type: 'scatter', mode: 'lines+markers',
    name, line: { color, width: 2 },
    marker: { size: 6, color },
    connectgaps: false,
  })

  const weightShapes = rangeShapes(ranges.weight)
  const fatShapes = rangeShapes(ranges.bodyFat)
  const bmiShapes = rangeShapes(ranges.bmi)
  const leanShapes = rangeShapes(ranges.leanMass)
  const muscleShapes = rangeShapes(ranges.muscleMass)
  const muscleRateShapes = rangeShapes(ranges.muscleRate)

  return (
    <div className="page-body-metrics">
      <ProfileSection onBmrUpdate={setBmr} onHeightUpdate={setProfileHeight} />
      <div className="flex-between mb-20">
        <div>
          <h2 style={{ margin: 0 }}>{t('page_body')}</h2>
          <p className="text-dim text-sm" style={{ margin: '4px 0 0' }}>
            {data.length} {t('measurements')} &middot; {t('last')}: {formatDate(latest.date)} ({lastDateLabel})
            {latest.source && <> &middot; {t('source')}: {latest.source}</>}
          </p>
        </div>
      </div>

      <div className="card-grid">
        <KpiCard
          value={latestWeight ? `${latestWeight} kg` : '--'}
          label={t('weight')}
          sublabel={weightChange ? `${weightChange > 0 ? '+' : ''}${weightChange} kg ${t('since_first')}` : ''}
          info={INFO.weight}
          style={{ color: wColor }}
        />
        <KpiCard
          value={latest.BodyFatPercentage ? `${latest.BodyFatPercentage}%` : '--'}
          label={t('body_fat')}
          sublabel={formatDate(latest.date)}
          info={INFO.bodyFat}
          style={{ color: bfColor }}
        />
        {hasMuscleMass && (
          <KpiCard
            value={latest.MuscleMass ? `${latest.MuscleMass} kg` : '--'}
            label={t('muscle_mass')}
            sublabel={hasMuscleRate && latest.MuscleRate ? `${latest.MuscleRate}% ${t('of_body_weight')}` : formatDate(latest.date)}
            info={INFO.muscleMass}
            style={{ color: mmColor }}
          />
        )}
        <KpiCard
          value={latest.LeanBodyMass ? `${latest.LeanBodyMass} kg` : '--'}
          label={t('lean_body_mass')}
          sublabel={formatDate(latest.date)}
          info={INFO.leanMass}
          style={{ color: lmColor }}
        />
        <KpiCard
          value={latest.BodyMassIndex ? `${latest.BodyMassIndex}` : '--'}
          label={t('bmi')}
          sublabel={formatDate(latest.date)}
          info={INFO.bmi}
          style={{ color: bmiColor }}
        />
        {(() => {
          const scaleBmr = latest.BMR ? Math.round(latest.BMR) : 0
          const formulaBmr = bmr
          const displayBmr = scaleBmr || formulaBmr
          if (!displayBmr) return null
          const isScale = scaleBmr > 0
          return (
            <KpiCard
              value={`${displayBmr} kcal`}
              label="BMR"
              sublabel={isScale ? `Scale (${formatDate(latest.date)})` : 'Mifflin-St Jeor *'}
              info={isScale
                ? `Basal Metabolic Rate measured by your smart scale. More accurate than formula-based estimates as it uses your actual body composition data (muscle mass, body fat %).${formulaBmr ? `\n\nFormula estimate: ${formulaBmr} kcal (Mifflin-St Jeor)` : ''}`
                : 'Basal Metabolic Rate — estimated using the Mifflin-St Jeor formula from your profile (height, weight, age, sex).\n\n* Estimate only. For a more accurate reading, use a smart scale that measures body composition.'}
            />
          )
        })()}
      </div>

      <div className="chart-grid-2col">
        <div className="card">
          <div className="chart-title-row">
            <h4>{t('weight')}</h4>
            <InfoTip text={INFO.weight + `\n\n**${t('range')}**: ${ranges.weight.low}-${ranges.weight.high} kg\n${ranges.weight.label}`} />
          </div>
          <Plot
            data={[trace(weight, 'Weight', '#ff966c')]}
            layout={chartLayout('', 'kg', weightShapes, tightYRange(weight))}
            config={PLOTLY_CONFIG}
            useResizeHandler style={{ width: '100%', height: 280 }}
          />
        </div>
        <div className="card">
          <div className="chart-title-row">
            <h4>{t('body_fat_pct')}</h4>
            <InfoTip text={INFO.bodyFat + `\n\n**${t('range')}**: ${ranges.bodyFat.low}-${ranges.bodyFat.high}%\n${ranges.bodyFat.label}`} />
          </div>
          <Plot
            data={[trace(fat, 'Body Fat %', '#c099ff')]}
            layout={chartLayout('', '%', fatShapes, tightYRange(fat))}
            config={PLOTLY_CONFIG}
            useResizeHandler style={{ width: '100%', height: 280 }}
          />
        </div>
        {hasMuscleMass && (
          <div className="card">
            <div className="chart-title-row">
              <h4>{t('muscle_mass')}</h4>
              <InfoTip text={INFO.muscleMass + `\n\n**${t('range')}**: ${ranges.muscleMass.low}-${ranges.muscleMass.high} kg\n${ranges.muscleMass.label}`} />
            </div>
            <Plot
              data={[trace(muscle, 'Muscle Mass', '#c3e88d')]}
              layout={chartLayout('', 'kg', muscleShapes, tightYRange(muscle))}
              config={PLOTLY_CONFIG}
              useResizeHandler style={{ width: '100%', height: 280 }}
            />
          </div>
        )}
        {hasMuscleRate && (
          <div className="card">
            <div className="chart-title-row">
              <h4>{t('muscle_rate')}</h4>
              <InfoTip text={INFO.muscleRate + `\n\n**${t('range')}**: ${ranges.muscleRate.low}-${ranges.muscleRate.high}%\n${ranges.muscleRate.label}`} />
            </div>
            <Plot
              data={[trace(muscleRate, 'Muscle Rate', '#ffc777')]}
              layout={chartLayout('', '%', muscleRateShapes, tightYRange(muscleRate))}
              config={PLOTLY_CONFIG}
              useResizeHandler style={{ width: '100%', height: 280 }}
            />
          </div>
        )}
        <div className="card">
          <div className="chart-title-row">
            <h4>{t('bmi')}</h4>
            <InfoTip text={INFO.bmi + `\n\n**${t('range')}**: ${ranges.bmi.low}-${ranges.bmi.high}\n${ranges.bmi.label}`} />
          </div>
          <Plot
            data={[trace(bmi, 'BMI', '#65bcff')]}
            layout={chartLayout('', '', bmiShapes, tightYRange(bmi))}
            config={PLOTLY_CONFIG}
            useResizeHandler style={{ width: '100%', height: 280 }}
          />
        </div>
        <div className="card">
          <div className="chart-title-row">
            <h4>{t('lean_body_mass')}</h4>
            <InfoTip text={INFO.leanMass + `\n\n**${t('range')}**: ${ranges.leanMass.low}-${ranges.leanMass.high} kg\n${ranges.leanMass.label}`} />
          </div>
          <Plot
            data={[trace(lean, 'Lean Mass', '#c3e88d')]}
            layout={chartLayout('', 'kg', leanShapes, tightYRange(lean))}
            config={PLOTLY_CONFIG}
            useResizeHandler style={{ width: '100%', height: 280 }}
          />
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginBottom: 12 }}>{t('measurement_history')}</h3>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleSort('date')}>{t('date')}{sortArrow('date')}</th>
                <th className="sortable-th" onClick={() => handleSort('weight')} style={{ textAlign: 'right' }}>{t('weight')}{sortArrow('weight')}</th>
                <th className="sortable-th" onClick={() => handleSort('body_fat')} style={{ textAlign: 'right' }}>{t('body_fat')}{sortArrow('body_fat')}</th>
                {hasMuscleMass && <th className="sortable-th" onClick={() => handleSort('muscle_mass')} style={{ textAlign: 'right' }}>{t('muscle_mass')}{sortArrow('muscle_mass')}</th>}
                {hasMuscleRate && <th className="sortable-th" onClick={() => handleSort('muscle_rate')} style={{ textAlign: 'right' }}>{t('muscle_rate_pct')}{sortArrow('muscle_rate')}</th>}
                <th className="sortable-th" onClick={() => handleSort('lean_mass')} style={{ textAlign: 'right' }}>{t('lean_body_mass')}{sortArrow('lean_mass')}</th>
                <th className="sortable-th" onClick={() => handleSort('bmi')} style={{ textAlign: 'right' }}>{t('bmi')}{sortArrow('bmi')}</th>
                <th className="sortable-th" onClick={() => handleSort('source')}>{t('source')}{sortArrow('source')}</th>
              </tr>
            </thead>
            <tbody>
              {sortedData.map(d => (
                <tr key={d.date}>
                  <td style={{ whiteSpace: 'nowrap' }}>{formatDate(d.date)}</td>
                  <td style={numStyle}>{d.BodyMass ? `${d.BodyMass} kg` : '--'}</td>
                  <td style={numStyle}>{d.BodyFatPercentage ? `${d.BodyFatPercentage}%` : '--'}</td>
                  {hasMuscleMass && <td style={numStyle}>{d.MuscleMass ? `${d.MuscleMass} kg` : '--'}</td>}
                  {hasMuscleRate && <td style={numStyle}>{d.MuscleRate ? `${d.MuscleRate}%` : '--'}</td>}
                  <td style={numStyle}>{d.LeanBodyMass ? `${d.LeanBodyMass} kg` : '--'}</td>
                  <td style={numStyle}>{d.BodyMassIndex ?? '--'}</td>
                  <td className="text-dim">{d.source || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
