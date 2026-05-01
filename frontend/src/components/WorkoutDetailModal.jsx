import { useState, useEffect, useMemo, useRef } from 'react'
import Plot from 'react-plotly.js'
import { MapContainer, TileLayer, Polyline, Marker, CircleMarker, Tooltip, useMap } from 'react-leaflet'
import L from 'leaflet'
import { api } from '../api'
import { COLORS, PLOTLY_LAYOUT, PLOTLY_CONFIG, HR_ZONE_COLORS, HR_ZONE_LABELS } from '../constants'
import { md, safef, fmtDur, fmtDist, fmtDate, fmtTime, hasHebrew, autoGrow, uploadFileToServer } from '../utils/formatters'
import { hrZone, classifyType } from '../utils/classifiers'
import { notifyLlmStart, notifyLlmEnd } from './NotificationBell'
import { useApp } from '../context/AppContext'
import { useChat } from '../context/ChatContext'
import { useI18n } from '../i18n/I18nContext'
import Modal from './common/Modal'
import LoadingSpinner from './common/LoadingSpinner'
import InfoTip from './common/InfoTip'


// Metric-based cell coloring: green=good, yellow=ok, red=poor
// Lower-is-better: value <= good → green, <= ok → yellow, else red
// Higher-is-better: value >= good → green, >= ok → yellow, else red
function metricColor(val, good, ok, lowerIsBetter = true) {
  if (val == null || val === 0) return undefined
  if (lowerIsBetter) {
    if (val <= good) return 'var(--green)'
    if (val <= ok) return 'var(--yellow)'
    return 'var(--red)'
  }
  // higher is better
  if (val >= good) return 'var(--green)'
  if (val >= ok) return 'var(--yellow)'
  return 'var(--red)'
}

const COMPASS_DIRS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

export default function WorkoutDetailModal({ workoutNum: initialWorkoutNum, open, onClose }) {
  const { t } = useI18n()
  const [currentNum, setCurrentNum] = useState(initialWorkoutNum)
  const [navHistory, setNavHistory] = useState([])
  const [data, setData] = useState(null)
  const [sections, setSections] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('overview')
  const [insight, setInsight] = useState(null)
  const [insightLoading, setInsightLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const generateAbortRef = useRef(null)
  const [brickInfo, setBrickInfo] = useState(null)
  const [insightNote, setInsightNote] = useState('')
  const [insightFiles, setInsightFiles] = useState([]) // [{file_path, filename}]
  const insightFileRef = useRef(null)
  const { allWorkouts, refreshWorkouts, aiEnabled } = useApp()
  const { setChatOpen, setPendingInput, newSession } = useChat()
  const allWorkoutsRef = useRef(allWorkouts)
  allWorkoutsRef.current = allWorkouts

  // Sync with prop when modal opens with a new workout
  useEffect(() => {
    if (open) {
      setCurrentNum(initialWorkoutNum)
      setNavHistory([])
    }
  }, [initialWorkoutNum, open])

  const workoutNum = currentNum

  function navigateToWorkout(num) {
    setNavHistory(prev => [...prev, currentNum])
    setCurrentNum(num)
  }

  function navigateBack() {
    setNavHistory(prev => {
      const next = [...prev]
      const backNum = next.pop()
      if (backNum != null) setCurrentNum(backNum)
      return next
    })
  }

  useEffect(() => {
    if (!open || workoutNum == null) return
    setLoading(true)
    setData(null)
    setSections(null)
    setActiveTab('overview')
    setInsight(null)
    setInsightLoading(true)
    setBrickInfo(null)

    // Use ref to read latest allWorkouts without depending on it
    const curWorkouts = allWorkoutsRef.current
    // Check if this is a merged workout — pass all constituent nums
    const wo = curWorkouts?.find(w => String(w.workout_num) === String(workoutNum))
    const mergedNums = wo?.merged_nums
    const mergeParam = mergedNums ? `?merge_with=${mergedNums.join(',')}` : ''

    Promise.all([
      api(`/api/workout/${workoutNum}${mergeParam}`),
      api(`/api/workout/${workoutNum}/sections`).catch(() => null),
    ])
      .then(([d, s]) => { setData(d); setSections(s); setLoading(false) })
      .catch((e) => { setError(e.message); setLoading(false) })

    // Load brick info scoped to just this workout's date (not full date range)
    const woDate = wo?.startDate?.slice(0, 10) || ''
    const bricksPromise = woDate
      ? api(`/api/bricks?from_date=${woDate}&to_date=${woDate}`).catch(() => [])
      : Promise.resolve([])

    // Check if this workout is part of a brick
    bricksPromise.then(bricks => {
      for (const b of bricks) {
        const nums = b.workouts.map(w => w.workout_num)
        if (nums.includes(Number(workoutNum))) {
          setBrickInfo(b)
          break
        }
      }
    })

    // Load insight (brick workouts share the same insight, stored under each workout_num)
    api(`/api/insights/workout/${workoutNum}`)
      .then(ins => {
        if (ins?.insight) setInsight(ins)
        setInsightLoading(false)
      })
      .catch(() => setInsightLoading(false))
  }, [workoutNum, open])

  function generateInsight() {
    // Language priority: 1) detected from user note, 2) insightLang setting, 3) 'en'
    const noteLang = insightNote.trim() ? (hasHebrew(insightNote) ? 'he' : 'en') : null
    const body = {
      lang: noteLang || localStorage.getItem('insightLang') || 'en',
      include_raw_data: true,
    }
    if (insightNote.trim()) body.user_note = insightNote.trim()
    if (insightFiles.length > 0) body.user_files = insightFiles.map(f => f.file_path)

    // Show generating state in modal; runs in background if user closes
    setGenerating(true)
    const taskId = `insight-w${workoutNum}`
    const capturedNum = workoutNum
    notifyLlmStart(taskId, `${t('detail_insight')} #${capturedNum}`, `workout:${capturedNum}`)

    let genErr = null
    api(`/api/insights/generate/${capturedNum}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(result => {
      if (result?.insight) {
        setInsight(result)
        // Only clear context on success
        setInsightNote('')
        setInsightFiles([])
      }
    }).catch(e => {
      genErr = e.message
      console.error('Insight generation failed:', e)
    }).finally(() => {
      setGenerating(false)
      notifyLlmEnd(taskId, genErr)
    })
  }

  async function uploadInsightFile(file) {
    try {
      const data = await uploadFileToServer(file)
      setInsightFiles(prev => [...prev, { file_path: data.file_path, filename: data.filename }])
    } catch (er) {
      console.error('Upload failed:', er)
    }
  }

  function stopGenerating() {
    generateAbortRef.current?.abort()
  }

  const meta = data?.metadata || []
  const pts = data?.data || []
  const info = useMemo(() => {
    const o = {}
    meta.forEach((l) => {
      const m = l.match(/^## (\w+): (.+)/)
      if (m) o[m[1]] = m[2]
      const tzm = l.match(/^## Meta: TimeZone = (.+)/)
      if (tzm) o.TimeZone = tzm[1].trim().split(',')[0]
    })
    return o
  }, [meta])

  async function handleMerge(numA, numB) {
    try {
      await api('/api/merges', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pairs: [[numA, numB]] })
      })
      refreshWorkouts()
      // Reload this workout with merged data
      setLoading(true)
      const [d, s] = await Promise.all([
        api(`/api/workout/${numA}?merge_with=${numA},${numB}`),
        api(`/api/workout/${numA}/sections`).catch(() => null),
      ])
      setData(d)
      setSections(s)
      setLoading(false)
      // Try to regenerate insights for merged workouts (best-effort)
      try {
        await api('/api/insights/generate-batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ workout_nums: [numA, numB] })
        })
      } catch { /* insight regen is best-effort — works without Claude */ }
    } catch (e) {
      setError(e.message)
    }
  }

  function discussWithCoach() {
    const snippet = (insight?.insight || '').slice(0, 500)
    const wType = info.Type || 'Workout'
    const wDate = info.Start ? fmtDate(info.Start) : ''
    // Determine specialist agent based on workout discipline
    const disc = classifyType(wType)
    const agentMap = { run: 'run-coach', swim: 'swim-coach', bike: 'bike-coach' }
    const agent = agentMap[disc] || 'main-coach'
    const msg = `Let's discuss workout #${workoutNum} (${wType}, ${wDate}). Here's the current insight: ${snippet}... `
    // Create a new specialist session
    newSession(agent)
    setPendingInput(msg)
    setChatOpen(true)
  }

  const hasSections = sections?.sections?.length > 0
  const hasDetailedData = !!(sections?.intervals || sections?.hr_profile || sections?.elevation_profile)
  const startTime = fmtTime(info.Start, info.TimeZone)
  const endTime = fmtTime(info.End, info.TimeZone)
  const timeRange = startTime && endTime ? ` · ${startTime}–${endTime}` : ''
  const title = `#${workoutNum} ${info.Type || 'Workout'} — ${info.Start ? fmtDate(info.Start) : ''}${timeRange}`

  return (
    <Modal open={open} onClose={onClose} title={title} wide onBack={navHistory.length > 0 ? navigateBack : undefined}>
      {loading && <LoadingSpinner />}
      {!loading && error && <div className="loading-msg">Error: {error}</div>}
      {!loading && !error && (
        <>
          <div className="detail-tabs">
            <button className={`detail-tab${activeTab === 'overview' ? ' active' : ''}`} onClick={() => setActiveTab('overview')}>{t('detail_overview')}</button>
            {hasSections && <button className={`detail-tab${activeTab === 'splits' ? ' active' : ''}`} onClick={() => setActiveTab('splits')}>{t('detail_splits_zones')}</button>}
            {hasSections && <button className={`detail-tab${activeTab === 'analysis' ? ' active' : ''}`} onClick={() => setActiveTab('analysis')}>{t('detail_analysis')}</button>}
            {hasDetailedData && <button className={`detail-tab${activeTab === 'detailed' ? ' active' : ''}`} onClick={() => setActiveTab('detailed')}>{t('detail_detailed_data')}</button>}
          </div>

          {activeTab === 'overview' && (
            <OverviewTab
              meta={meta}
              info={info}
              pts={pts}
              sections={sections}
              workoutNum={workoutNum}
              workouts={allWorkouts}
              insight={insight}
              insightLoading={insightLoading}
              generating={generating}
              onGenerate={generateInsight}
              onStop={stopGenerating}
              onDiscuss={discussWithCoach}
              brickInfo={brickInfo}
              onWorkoutNav={navigateToWorkout}
              onMerge={handleMerge}
              gpsCorrections={data?.gps_corrections}
              externalWeather={data?.external_weather}
              vo2max={data?.vo2max}
              trimp={data?.trimp}
              hrtss={data?.hrtss}
              insightNote={insightNote}
              setInsightNote={setInsightNote}
              insightFiles={insightFiles}
              setInsightFiles={setInsightFiles}
              insightFileRef={insightFileRef}
              uploadInsightFile={uploadInsightFile}
              aiEnabled={aiEnabled}
            />
          )}
          {activeTab === 'splits' && hasSections && <SplitsTab sections={sections} />}
          {activeTab === 'analysis' && hasSections && <AnalysisTab pts={pts} sections={sections} />}
          {activeTab === 'detailed' && hasDetailedData && <DetailedDataTab sections={sections} />}
        </>
      )}
    </Modal>
  )
}

/* ── Insight context input (note + photo) ── */
function InsightContextInput({ t, insightNote, setInsightNote, insightFiles, setInsightFiles, insightFileRef, uploadInsightFile, generating, onGenerate, onStop, aiEnabled }) {
  const [contextOpen, setContextOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const hasContext = !!(insightNote.trim() || insightFiles.length > 0)

  function handlePaste(e) {
    const items = e.clipboardData?.items
    if (!items) return
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault()
        const file = item.getAsFile()
        if (file) uploadInsightFile(file)
        return
      }
    }
  }

  // Collapsed: Generate button + "add context" toggle
  if (!contextOpen) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {generating ? (
          <button className="btn btn-sm btn-red" onClick={onStop}>{t('stop')}</button>
        ) : (
          <button className="btn btn-sm btn-accent" onClick={onGenerate} disabled={!aiEnabled}>{aiEnabled ? t('generate') : t('ai_disabled_btn')}</button>
        )}
        <button className="btn btn-sm btn-outline" onClick={() => setContextOpen(true)} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          {t('add_context')}
          {hasContext && <span style={{ color: 'var(--green)' }}> *</span>}
        </button>
      </div>
    )
  }

  // Expanded: textarea + file attachments + buttons
  return (
    <div style={{ position: 'relative' }}>
      {expanded && <div className="expand-backdrop" onClick={() => setExpanded(false)} />}
      <div className={expanded ? 'note-expand-area expanded' : ''}>
        <textarea
          className="input-full"
          placeholder={t('insight_note_placeholder')}
          value={insightNote}
          onChange={e => setInsightNote(e.target.value)}
          onInput={autoGrow}
          onPaste={handlePaste}
          onKeyDown={e => { if (e.key === 'Escape') { if (expanded) setExpanded(false); else setContextOpen(false) } }}
          dir="auto"
          rows={expanded ? 10 : 4}
          style={{ overflow: 'hidden', resize: 'none', width: '100%' }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
          <input type="file" accept="image/*" multiple style={{ display: 'none' }}
            ref={insightFileRef}
            onChange={e => { for (const f of e.target.files) uploadInsightFile(f); e.target.value = '' }} />
          <button
            className="btn btn-sm btn-icon btn-outline"
            style={{ padding: '4px 6px' }}
            onClick={() => insightFileRef.current?.click()}
            title={t('attach_photo')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          </button>
          <button
            className="btn btn-sm btn-icon btn-outline"
            style={{ padding: '4px 6px' }}
            onClick={() => setExpanded(v => !v)}
            title={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? '\u2193' : '\u2191'}
          </button>
          {insightFiles.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {insightFiles.map((af, i) => (
                <span key={i} className="attached-file-tag" style={{ fontSize: 11 }}>
                  {af.filename}
                  <button onClick={() => setInsightFiles(prev => prev.filter((_, j) => j !== i))}>&times;</button>
                </span>
              ))}
            </div>
          )}
          <div style={{ marginInlineStart: 'auto', display: 'flex', gap: 6 }}>
            <button className="btn btn-sm btn-outline" onClick={() => { setExpanded(false); setContextOpen(false) }}>&times;</button>
            {generating ? (
              <button className="btn btn-sm btn-red" onClick={onStop}>{t('stop')}</button>
            ) : (
              <button className="btn btn-sm btn-accent" onClick={onGenerate} disabled={!aiEnabled}>{aiEnabled ? t('generate') : t('ai_disabled_btn')}</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Overview Tab ── */
function OverviewTab({ meta, info, pts, sections, workoutNum, workouts, insight, insightLoading, generating, onGenerate, onStop, onDiscuss, brickInfo, onWorkoutNav, gpsCorrections, externalWeather, vo2max, trimp, hrtss, onMerge, insightNote, setInsightNote, insightFiles, setInsightFiles, insightFileRef, uploadInsightFile, aiEnabled }) {
  const { t } = useI18n()
  const wSummary = workouts.find((w) => String(w.workout_num) === String(workoutNum))

  // Parse metadata
  const distMeta = meta.find((l) => l.includes('Distance'))
  const distMatch = distMeta?.match(/sum=([\d.]+)/)
  const hrMeta = meta.find((l) => l.includes('HeartRate'))
  const avgHR = hrMeta?.match(/average=([\d.]+)/)
  const maxHR = hrMeta?.match(/maximum=([\d.]+)/)
  const calMeta = meta.find((l) => l.includes('ActiveEnergyBurned'))
  const calMatch = calMeta?.match(/sum=([\d.]+)/)

  // Weather: try this workout first, then try brick partner
  let tempC = null, humidity = null, waterTempC = null, isIndoor = false
  const weatherSource = wSummary?.meta_WeatherTemperature
    ? wSummary
    : brickInfo?.workouts?.map(bw => workouts.find(w => String(w.workout_num) === String(bw.workout_num))).find(w => w?.meta_WeatherTemperature)
  if (weatherSource?.meta_WeatherTemperature) {
    const f = parseFloat(weatherSource.meta_WeatherTemperature)
    if (!isNaN(f)) tempC = ((f - 32) * 5 / 9).toFixed(0)
  }
  if (weatherSource?.meta_WeatherHumidity) {
    let h = parseFloat(weatherSource.meta_WeatherHumidity)
    if (!isNaN(h)) { if (h > 100) h = h / 100; humidity = Math.round(h) }
  }
  // Water temperature (swim)
  const waterRaw = wSummary?.WaterTemperature_average
  if (waterRaw) {
    const wt = parseFloat(waterRaw)
    if (!isNaN(wt) && wt > 0) waterTempC = wt.toFixed(1)
  }
  // Indoor/outdoor
  isIndoor = String(wSummary?.meta_IndoorWorkout || '').trim() === '1'
  // External weather (wind, rain)
  const windKmh = externalWeather?.wind_kmh || 0
  const windDir = externalWeather?.wind_dir || 0
  const rainMm = externalWeather?.rain_mm || 0
  const windCompass = windDir ? COMPASS_DIRS[Math.round(windDir / 45) % 8] : ''
  let elevM = null
  let elevCorrected = false
  if (gpsCorrections?.corrected_elevation_m != null) {
    elevM = Math.round(gpsCorrections.corrected_elevation_m)
    elevCorrected = true
  } else if (wSummary?.meta_ElevationAscended) {
    const raw = parseFloat(wSummary.meta_ElevationAscended)
    if (!isNaN(raw)) elevM = Math.round(raw / 100)
  }

  // Avg pace / speed
  const disc = classifyType(wSummary?.type)
  let paceLabel = null, paceValue = null
  if (disc === 'run') {
    const distKm = safef(wSummary?.distance_km || wSummary?.DistanceWalkingRunning_sum)
    const durMin = safef(wSummary?.duration_min || info.Duration)
    if (distKm > 0 && durMin > 0) {
      const mpk = durMin / distKm
      paceValue = `${Math.floor(mpk)}:${String(Math.round(mpk % 1 * 60)).padStart(2, '0')}/km`
      paceLabel = t('th_avg_pace')
    }
  } else if (disc === 'bike') {
    const distKm = safef(wSummary?.distance_km || wSummary?.DistanceCycling_sum)
    const durMin = safef(wSummary?.duration_min || info.Duration)
    if (distKm > 0 && durMin > 0) {
      paceValue = `${(Math.floor(distKm / (durMin / 60) * 10) / 10).toFixed(1)} km/h`
      paceLabel = t('th_avg_speed')
    }
  } else if (disc === 'swim') {
    // Use sections active time if available, otherwise total duration
    const distM = safef(wSummary?.DistanceSwimming_sum)
    if (distM > 0) {
      const secs = sections?.sections
      let activeTimeSec = 0
      if (secs?.length) {
        activeTimeSec = secs.reduce((sum, s) => sum + (s.duration_sec || 0), 0)
      }
      const totalSec = safef(wSummary?.duration_min || info.Duration) * 60
      // Show active pace (without rest) if we have section data
      const useSec = activeTimeSec > 0 ? activeTimeSec : totalSec
      const per100 = useSec / (distM / 100)
      const mm = Math.floor(per100 / 60)
      const ss = Math.round(per100 % 60)
      paceValue = `${mm}:${String(ss).padStart(2, '0')}/100m`
      paceLabel = activeTimeSec > 0 ? t('detail_active_pace') : t('th_avg_pace')
    }
  }

  // Brick info
  const brickPartners = brickInfo?.workouts?.filter(bw => bw.workout_num !== Number(workoutNum)) || []

  const isSwim = classifyType(wSummary?.type) === 'swim'
  const hasGPS = !isSwim && (
    pts.some((p) => p.lat && p.lon && parseFloat(p.lat)) ||
    sections?.hr_colored_segments?.length > 1
  )

  return (
    <>
      {/* Summary stats — performance */}
      <div className="detail-summary">
        {info.Duration && <StatCard value={fmtDur(parseFloat(info.Duration))} label={t('th_duration')} />}
        {distMatch && <StatCard value={fmtDist(parseFloat(distMatch[1]))} label={`${t('th_distance')} (${disc === 'swim' ? 'm' : 'km'})`} />}
        {paceValue && <StatCard value={paceValue} label={paceLabel} />}
        {avgHR && <StatCard value={Math.round(parseFloat(avgHR[1]))} label={t('detail_avg_hr')} />}
        {maxHR && <StatCard value={Math.round(parseFloat(maxHR[1]))} label={t('detail_max_hr')} />}
        {calMatch && <StatCard value={Math.floor(parseFloat(calMatch[1]))} label={t('calories')} />}
        {elevM != null && <StatCard value={`${elevM}m${elevCorrected ? '*' : ''}`} label={t('detail_elev_gain')} />}
        {vo2max != null && <StatCard value={vo2max} label={t('vo2max')} style={{ color: '#82aaff' }} />}
        {trimp != null && trimp > 0 && (
          <StatCard value={trimp} label={<>{t('trimp_label')} <InfoTip text={t('info_trimp_workout')} /></>} style={{ color: trimp > 150 ? '#ff5370' : trimp > 100 ? '#ff966c' : trimp > 50 ? '#ffc777' : '#c3e88d' }} />
        )}
        {hrtss != null && (
          <StatCard value={hrtss} label={<>{t('hrtss_label')} <InfoTip text={t('info_hrtss_workout')} /></>} style={{ color: hrtss > 120 ? '#ff5370' : hrtss > 80 ? '#ff966c' : hrtss > 40 ? '#ffc777' : '#82aaff' }} />
        )}
      </div>
      {/* Summary stats — conditions */}
      {(tempC != null || waterTempC != null || windKmh > 0 || isIndoor) && (
        <div className="detail-summary detail-summary-weather">
          {isIndoor && <StatCard value={t('detail_indoor')} label={t('detail_location')} small />}
          {waterTempC != null && <StatCard value={`${waterTempC}\u00b0C`} label={t('detail_water_temp')} small />}
          {tempC != null && <StatCard value={`${tempC}\u00b0C`} label={waterTempC ? t('detail_air_temp') : t('detail_temperature')} small />}
          {humidity != null && <StatCard value={`${humidity}%`} label={t('detail_humidity')} small />}
          {windKmh > 0 && <StatCard value={`${Math.round(windKmh)} ${windCompass}`} label={t('detail_wind')} small />}
          {rainMm > 0 && <StatCard value={`${rainMm.toFixed(1)}mm`} label={t('detail_rain')} small />}
        </div>
      )}

      {/* GPS corrections banner — compact single line */}
      {gpsCorrections && (
        <div style={{ borderInlineStart: '3px solid var(--yellow)', padding: '6px 12px', marginBottom: 8, fontSize: '0.8em', display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <strong style={{ color: 'var(--yellow)' }}>⚠ GPS</strong>
          <span className="text-dim">{gpsCorrections.corrected_count} pts corrected</span>
          {gpsCorrections.original_elevation_m != null && (
            <span>Elev: <s className="text-dim">{Math.round(gpsCorrections.original_elevation_m)}m</s> → <strong>{Math.round(gpsCorrections.corrected_elevation_m)}m*</strong></span>
          )}
          {gpsCorrections.original_gps_distance_km != null && (
            <span>Dist: <s className="text-dim">{gpsCorrections.original_gps_distance_km.toFixed(2)}km</s> → <strong>{gpsCorrections.corrected_gps_distance_km.toFixed(2)}km*</strong></span>
          )}
        </div>
      )}

      {/* Brick badge */}
      {brickInfo && brickPartners.length > 0 && (
        <div className="brick-banner">
          <span>🧱 {t('page_bricks')}: {brickInfo.brick_type}</span>
          <span style={{ marginInlineStart: 8 }}>
            {brickPartners.map((bw, i) => (
              <span key={bw.workout_num}>
                {i > 0 && ', '}
                <a
                  href="#"
                  className="brick-workout-link"
                  onClick={(e) => { e.preventDefault(); onWorkoutNav(bw.workout_num) }}
                >
                  #{bw.workout_num} {bw.type}
                </a>
              </span>
            ))}
            {brickInfo.transition_times?.length > 0 && (
              <span className="text-dim"> | T: {brickInfo.transition_times.map(tt => `${Math.round(tt)}m`).join(', ')}</span>
            )}
          </span>
        </div>
      )}

      {/* Merge suggestion — same discipline, adjacent in time, not already merged */}
      {(() => {
        if (!wSummary || wSummary.merged_nums) return null
        const wDisc = classifyType(wSummary.type)
        if (wDisc === 'other' || wDisc === 'strength') return null
        const wStart = new Date(wSummary.startDate)
        const wEnd = new Date(wStart.getTime() + safef(wSummary.duration_min) * 60000)
        // Find adjacent same-discipline workouts within 30 min
        const candidates = workouts.filter(w => {
          if (String(w.workout_num) === String(workoutNum)) return false
          if (classifyType(w.type) !== wDisc) return false
          if (w.merged_nums) return false
          const s = new Date(w.startDate)
          const e = new Date(s.getTime() + safef(w.duration_min) * 60000)
          const gapMs = Math.min(Math.abs(s - wEnd), Math.abs(wStart - e))
          return gapMs < 30 * 60000
        })
        if (!candidates.length) return null
        return (
          <div className="brick-banner" style={{ borderColor: 'var(--accent)' }}>
            <span>{t('merge_nearby')}: </span>
            {candidates.map(c => (
              <span key={c.workout_num} style={{ marginInlineStart: 4 }}>
                #{c.workout_num} {c.type} ({Math.round(safef(c.duration_min))}min)
                <button className="btn btn-sm btn-accent" style={{ marginInlineStart: 6 }}
                  onClick={() => onMerge(workoutNum, c.workout_num)}>
                  {t('merge_btn')}
                </button>
              </span>
            ))}
          </div>
        )
      })()}

      {/* Insight */}
      <div className="insight-card" style={{ marginBottom: 16 }}>
        {insightLoading ? (
          <div className="insight-card-body text-dim">{t('detail_loading_insight')}</div>
        ) : generating ? (
          <div className="insight-card-body" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <LoadingSpinner size={18} />
            <span className="text-dim">{t('detail_generating_insight')}</span>
            <button className="btn btn-sm btn-red" style={{ marginInlineStart: 'auto' }} onClick={onStop}>{t('stop')}</button>
          </div>
        ) : insight?.insight ? (
          <div dir={hasHebrew(insight.insight) ? 'rtl' : undefined}>
            <div className="insight-card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ color: 'var(--accent)' }}>{t('detail_coach_insight')}</h4>
              <button className="btn btn-sm btn-outline" onClick={onDiscuss} disabled={!aiEnabled}>{t('detail_discuss')}</button>
            </div>
            <div className="insight-card-body">
              <div dangerouslySetInnerHTML={md(insight.insight)} />
              {insight.plan_comparison && (
                <div className="insight-plan-cmp" dangerouslySetInnerHTML={md(insight.plan_comparison)} />
              )}
            </div>
          </div>
        ) : (
          <div className="insight-card-body">
            <InsightContextInput t={t} insightNote={insightNote} setInsightNote={setInsightNote} insightFiles={insightFiles} setInsightFiles={setInsightFiles} insightFileRef={insightFileRef} uploadInsightFile={uploadInsightFile} generating={generating} onGenerate={onGenerate} onStop={onStop} aiEnabled={aiEnabled} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
              <span className="text-dim text-xs">{t('detail_no_insight')}</span>
            </div>
          </div>
        )}
      </div>

      {/* GPS Map */}
      {hasGPS && <DetailMap pts={pts} sections={sections} badClusters={gpsCorrections?.bad_clusters} />}
      {hasGPS && gpsCorrections && (() => {
        const total = sections?.sections?.length || 0
        const withGps = sections?.sections?.filter(s => s.start_lat && s.start_lon).length || 0
        if (total > 0 && withGps < total) {
          return <div className="text-dim" style={{ fontSize: '0.85em', textAlign: 'center', marginTop: -10, marginBottom: 8 }}>
            GPS coverage: {withGps}/{total} splits — signal lost after KM {withGps}
          </div>
        }
        return null
      })()}

    </>
  )
}

function StatCard({ value, label, style, small }) {
  return (
    <div className={`detail-stat${small ? ' detail-stat-sm' : ''}`}>
      <div className="detail-stat-val" style={style}>{value}</div>
      <div className="detail-stat-label">{label}</div>
    </div>
  )
}

/* ── Map helper: fly to a point ── */
function FlyToPoint({ lat, lon, zoom = 15 }) {
  const map = useMap()
  useEffect(() => {
    if (lat && lon) map.flyTo([lat, lon], zoom, { duration: 0.8 })
  }, [lat, lon, zoom, map])
  return null
}

function FitBounds({ bounds, expanded }) {
  const map = useMap()
  useEffect(() => {
    // Resize the leaflet container to match its parent
    const el = map.getContainer()
    if (el) el.style.height = '100%'
    setTimeout(() => {
      map.invalidateSize()
      if (bounds?.length > 1) map.fitBounds(bounds, { padding: [30, 30] })
    }, 150)
  }, [bounds, expanded, map])
  return null
}

/* ── GPS Map ── */
function DetailMap({ pts, sections, badClusters }) {
  const { t } = useI18n()
  const gps = useMemo(() => pts.filter((p) => p.lat && p.lon && parseFloat(p.lat)), [pts])
  const mapRef = useRef(null)
  const [expanded, setExpanded] = useState(false)

  const segs = sections?.hr_colored_segments
  const sectionsList = sections?.sections
  const intervals = sections?.intervals

  // Build set of anomaly coordinates for exclusion from bounds
  const badCoordSet = useMemo(() => {
    if (!badClusters?.length) return null
    const s = new Set()
    for (const c of badClusters) s.add(`${c.lat.toFixed(5)},${c.lon.toFixed(5)}`)
    return s
  }, [badClusters])

  // Build polyline segments colored by HR zone
  const polylines = useMemo(() => {
    if (segs && segs.length > 1) {
      const lines = []
      for (let i = 1; i < segs.length; i++) {
        const s = segs[i]
        const prev = segs[i - 1]
        const col = s.zone ? HR_ZONE_COLORS[s.zone] || '#65bcff' : '#65bcff'
        lines.push({ positions: [[prev.lat, prev.lon], [s.lat, s.lon]], color: col, data: s })
      }
      return lines
    }
    // Fallback: color from raw GPS
    const hrVals = gps.map((p) => safef(p.HeartRate))
    if (hrVals.some((h) => h > 0)) {
      const lines = []
      let lastHr = 0
      for (let i = 1; i < gps.length; i++) {
        if (hrVals[i] > 0) lastHr = hrVals[i]
        lines.push({
          positions: [[parseFloat(gps[i - 1].lat), parseFloat(gps[i - 1].lon)], [parseFloat(gps[i].lat), parseFloat(gps[i].lon)]],
          color: HR_ZONE_COLORS[hrZone(lastHr)] || '#65bcff',
          data: null,
        })
      }
      return lines
    }
    return [{ positions: gps.map((p) => [parseFloat(p.lat), parseFloat(p.lon)]), color: COLORS.run, data: null }]
  }, [gps, segs])

  // Bounds: exclude anomaly clusters so map focuses on the real route
  const bounds = useMemo(() => {
    let coords
    if (segs?.length) {
      coords = badCoordSet
        ? segs.filter(s => !badCoordSet.has(`${s.lat.toFixed(5)},${s.lon.toFixed(5)}`)).map(s => [s.lat, s.lon])
        : segs.map(s => [s.lat, s.lon])
    } else {
      coords = badCoordSet
        ? gps.filter(p => !badCoordSet.has(`${parseFloat(p.lat).toFixed(5)},${parseFloat(p.lon).toFixed(5)}`)).map(p => [parseFloat(p.lat), parseFloat(p.lon)])
        : gps.map(p => [parseFloat(p.lat), parseFloat(p.lon)])
    }
    // Fall back to all coords if filtering removed everything
    if (!coords.length) {
      coords = segs?.length ? segs.map(s => [s.lat, s.lon]) : gps.map(p => [parseFloat(p.lat), parseFloat(p.lon)])
    }
    return coords.length ? coords : [[32.8, 35.5]]
  }, [gps, segs, badCoordSet])

  const kmMarkers = useMemo(() => {
    if (!sectionsList) return []
    return sectionsList.filter((s) => s.start_lat && s.start_lon).map((s) => ({
      position: [s.start_lat, s.start_lon],
      label: s.km || s.km_marker || s.segment_m || s.num,
      pace: s.pace_str || (s.avg_speed_kmh ? `${s.avg_speed_kmh}km/h` : ''),
      hr: s.avg_hr,
    }))
  }, [sectionsList])

  const kmIcon = (label) => L.divIcon({
    className: 'km-marker',
    html: `<div class="km-marker-inner">${label}</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  })

  // ESC to close fullscreen
  useEffect(() => {
    if (!expanded) return
    const handler = (e) => { if (e.key === 'Escape') setExpanded(false) }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [expanded])

  const mapHeight = expanded ? '70vh' : 700

  return (
    <div className={`detail-map${expanded ? ' interval-map-expanded' : ''}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <div className="map-hr-legend" style={{ position: 'static' }}>
          <div className="legend-title">{t('detail_hr_zones')}</div>
          <div style={{ display: 'flex', gap: 8 }}>
            {Object.entries(HR_ZONE_LABELS).map(([z, label]) => (
              <span key={z} className="legend-item" style={{ marginBottom: 0 }}>
                <span className="legend-swatch" style={{ background: HR_ZONE_COLORS[z] }} />
                {label}
              </span>
            ))}
          </div>
        </div>
        <button className="btn btn-sm btn-icon" onClick={() => setExpanded(!expanded)} title={expanded ? 'Collapse' : 'Expand'}>
          {expanded ? '↙' : '↗'}
        </button>
      </div>
      <div style={{ height: mapHeight, borderRadius: 'var(--radius)', overflow: 'hidden' }}>
        <MapContainer
          ref={mapRef}
          bounds={bounds}
          boundsOptions={{ padding: [30, 30] }}
          style={{ height: '100%', width: '100%' }}
          scrollWheelZoom={true}
          whenReady={(e) => setTimeout(() => e.target.invalidateSize(), 200)}
        >
          <FitBounds bounds={bounds} expanded={expanded} />
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="OSM" />
          {polylines.map((seg, i) => (
            <Polyline key={i} positions={seg.positions} pathOptions={{ color: seg.color, weight: 3, opacity: 0.9 }}>
              {seg.data && (seg.data.hr || seg.data.pace) && (
                <Tooltip sticky>
                  {seg.data.hr && <span>HR: {Math.round(seg.data.hr)} bpm<br /></span>}
                  {seg.data.pace && seg.data.pace < 20 && (
                    <span>Pace: {Math.floor(seg.data.pace)}:{String(Math.round((seg.data.pace % 1) * 60)).padStart(2, '0')}/km<br /></span>
                  )}
                  {seg.data.elevation && <span>Elev: {seg.data.elevation}m</span>}
                </Tooltip>
              )}
            </Polyline>
          ))}
          {kmMarkers.map((m, i) => (
            <Marker key={i} position={m.position} icon={kmIcon(m.label)}>
              <Tooltip>
                <b>{t('detail_split')} {m.label}</b>
                {m.pace && <><br />Pace: {m.pace}</>}
                {m.hr && <><br />HR: {Math.round(m.hr)} bpm</>}
              </Tooltip>
            </Marker>
          ))}
          {badClusters?.map((c, i) => (
            <CircleMarker key={`bad-${i}`} center={[c.lat, c.lon]} radius={10}
              pathOptions={{ color: '#ff5370', fillColor: '#ff5370', fillOpacity: 0.5, weight: 2 }}>
              <Tooltip>
                <b>GPS Anomaly #{i + 1}</b><br />{c.count} bad points
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
    </div>
  )
}

/* ── Elevation Chart ── */
function ElevationChart({ pts }) {
  const { t } = useI18n()
  const { xVals, yVals, xTitle } = useMemo(() => {
    // Filter to only GPS points with elevation + lat/lon + timestamp
    const gpsPts = pts.filter(p => p.elevation_m && p.lat && p.lon && parseFloat(p.lat) && p.timestamp)

    if (gpsPts.length < 2) return { xVals: [], yVals: [], xTitle: '' }

    // Compute cumulative distance in km using Haversine
    const toRad = d => d * Math.PI / 180
    function haversine(lat1, lon1, lat2, lon2) {
      const dLat = toRad(lat2 - lat1), dLon = toRad(lon2 - lon1)
      const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
      return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
    }

    let startIdx = 0
    for (let i = 1; i < gpsPts.length; i++) {
      const segDist = haversine(
        parseFloat(gpsPts[i - 1].lat), parseFloat(gpsPts[i - 1].lon),
        parseFloat(gpsPts[i].lat), parseFloat(gpsPts[i].lon)
      )
      const t1 = new Date(gpsPts[i - 1].timestamp.replace(' ', 'T').replace(/ ([+-])(\d{2})(\d{2})$/, '$1$2:$3'))
      const t2 = new Date(gpsPts[i].timestamp.replace(' ', 'T').replace(/ ([+-])(\d{2})(\d{2})$/, '$1$2:$3'))
      const dtSec = (t2 - t1) / 1000
      const speedKmh = dtSec > 0 ? (segDist / dtSec) * 3600 : 999
      if (speedKmh < 50 && segDist < 0.1) {
        startIdx = i - 1
        break
      }
    }

    const x = [0]
    const y = [safef(gpsPts[startIdx].elevation_m)]
    let cumDist = 0
    let lastValidIdx = startIdx
    for (let i = startIdx + 1; i < gpsPts.length; i++) {
      const segDist = haversine(
        parseFloat(gpsPts[lastValidIdx].lat), parseFloat(gpsPts[lastValidIdx].lon),
        parseFloat(gpsPts[i].lat), parseFloat(gpsPts[i].lon)
      )
      const t1 = new Date(gpsPts[lastValidIdx].timestamp.replace(' ', 'T').replace(/ ([+-])(\d{2})(\d{2})$/, '$1$2:$3'))
      const t2 = new Date(gpsPts[i].timestamp.replace(' ', 'T').replace(/ ([+-])(\d{2})(\d{2})$/, '$1$2:$3'))
      const dtSec = (t2 - t1) / 1000
      const speedKmh = dtSec > 0 ? (segDist / dtSec) * 3600 : 999
      if (speedKmh > 50) continue
      if (segDist > 0.5) continue
      cumDist += segDist
      lastValidIdx = i
      const elev = safef(gpsPts[i].elevation_m)
      if (y.length > 0 && Math.abs(elev - y[y.length - 1]) > 30) continue
      x.push(+cumDist.toFixed(3))
      y.push(elev)
    }
    return { xVals: x, yVals: y, xTitle: 'Distance (km)' }
  }, [pts])

  if (xVals.length < 2) return null

  return (
    <div className="chart-row single">
      <div className="chart-container">
        <h4>{t('detail_elevation')}</h4>
        <Plot
          data={[{
            x: xVals, y: yVals, type: 'scatter', mode: 'lines',
            fill: 'tozeroy', fillcolor: 'rgba(101,188,255,0.1)',
            line: { color: COLORS.swim, width: 1.5 },
            hovertemplate: 'Altitude: %{y:.0f}m | Distance: %{x:.2f} km<extra></extra>',
          }]}
          layout={{
            ...PLOTLY_LAYOUT,
            xaxis: {
              ...PLOTLY_LAYOUT.xaxis,
              title: { text: `${t('th_distance')} (km)`, font: { size: 12, color: '#8899aa' }, standoff: 8 },
              type: 'linear',
            },
            yaxis: {
              ...PLOTLY_LAYOUT.yaxis,
              title: { text: `${t('th_elevation')} (m)`, font: { size: 12, color: '#8899aa' }, standoff: 8 },
              autorange: true,
            },
            margin: { ...PLOTLY_LAYOUT.margin, l: 55, b: 45 },
          }}
          config={PLOTLY_CONFIG}
          useResizeHandler
          style={{ width: '100%', height: 220 }}
        />
      </div>
    </div>
  )
}

/* ── Splits & Zones Tab ── */
function SplitsTab({ sections: sectionsData }) {
  const { t } = useI18n()
  const disc = sectionsData.discipline
  const secs = sectionsData.sections
  const zones = sectionsData.hr_zones
  const swimSets = sectionsData.swim_sets || []
  const swimLaps = sectionsData.swim_laps || []

  // Find fastest/slowest
  let paceVals = []
  if (disc === 'run') paceVals = secs.map((s) => s.avg_pace_min_km || 999)
  else if (disc === 'swim') paceVals = secs.map((s) => s.pace_per_100m_sec || 999)
  else if (disc === 'bike') paceVals = secs.map((s) => s.avg_speed_kmh || 0)

  const fastestIdx = disc === 'bike'
    ? paceVals.indexOf(Math.max(...paceVals.filter((v) => v > 0)))
    : paceVals.indexOf(Math.min(...paceVals.filter((v) => v < 999)))
  const slowestIdx = disc === 'bike'
    ? paceVals.indexOf(Math.min(...paceVals.filter((v) => v > 0)))
    : paceVals.indexOf(Math.max(...paceVals.filter((v) => v < 999)))

  // HR Zone donut data
  const zoneLabels = [], zoneValues = [], zoneColors = []
  for (const z of ['Z1', 'Z2', 'Z3', 'Z4', 'Z5']) {
    const zd = zones?.[z]
    if (zd && zd.seconds > 0) {
      zoneLabels.push(z)
      zoneValues.push(zd.seconds)
      zoneColors.push(zd.color)
    }
  }

  // Pace bar chart data
  const barX = [], barY = [], barColors = [], barText = []
  secs.forEach((s) => {
    if (disc === 'run') {
      barX.push(`km ${s.km}`)
      barY.push(s.avg_pace_min_km || 0)
      barText.push(s.pace_str || '')
    } else if (disc === 'swim') {
      barX.push(`${s.segment_m}m`)
      barY.push(s.pace_per_100m_sec || 0)
      barText.push(s.pace_str || '')
    } else {
      barX.push(`${s.km_marker}km`)
      barY.push(s.avg_speed_kmh || 0)
      barText.push(`${s.avg_speed_kmh || 0} km/h`)
    }
    barColors.push(s.avg_hr ? HR_ZONE_COLORS[hrZone(s.avg_hr)] : '#7a88b8')
  })
  const yTitle = disc === 'run' ? t('detail_pace_min_km') : disc === 'swim' ? t('detail_pace_sec_100m') : t('detail_speed_kmh')

  return (
    <>
      {/* Splits table */}
      <div className="table-scroll" style={{ maxHeight: 350, marginBottom: 20 }}>
        {disc === 'run' && (() => {
          const hasElev = secs.some(s => s.elev_gain_m != null)
          return (
          <table className="data-table">
            <thead><tr><th>KM</th><th>{t('th_time')}</th><th>{t('detail_pace')}</th><th>HR</th><th>{t('detail_zone')}</th><th>{t('th_cadence')} <InfoTip text={t('info_cadence')} /></th><th>{t('th_avg_power')} <InfoTip text={t('info_power_run')} /></th><th>{t('detail_gct_ms')} <InfoTip text={t('info_gct')} /></th><th>{t('detail_stride')} <InfoTip text={t('info_stride')} /></th>{hasElev && <th>{t('th_elev_gain')}</th>}</tr></thead>
            <tbody>
              {secs.map((s, i) => {
                const cls = i === fastestIdx ? { color: 'var(--green)' } : i === slowestIdx ? { color: 'var(--red)' } : {}
                const hrCol = s.avg_hr ? { color: HR_ZONE_COLORS[hrZone(s.avg_hr)] } : {}
                return (
                  <tr key={i} style={cls}>
                    <td>{s.km}</td><td>{s.time_str || '-'}</td><td>{s.pace_str || '-'}</td>
                    <td style={hrCol}>{s.avg_hr ? Math.round(s.avg_hr) : '-'}</td>
                    <td>{s.hr_zone || '-'}</td>
                    <td style={{ color: metricColor(s.avg_cadence, 170, 160, false) }}>{s.avg_cadence || '-'}</td>
                    <td>{s.avg_power ? Math.round(s.avg_power) + 'W' : '-'}</td>
                    <td>{s.avg_gct ? Math.round(s.avg_gct) : '-'}</td>
                    <td>{s.avg_stride ? s.avg_stride.toFixed(2) + 'm' : '-'}</td>
                    {hasElev && <td>{s.elev_gain_m != null ? Math.round(s.elev_gain_m) + 'm' : '-'}{s.elev_gain_m_original != null ? <span className="text-dim" style={{ fontSize: '0.8em' }}> (*{Math.round(s.elev_gain_m_original)}m)</span> : ''}</td>}
                  </tr>
                )
              })}
            </tbody>
          </table>
          )
        })()}
        {disc === 'swim' && <SwimSplitsView secs={secs} swimSets={swimSets} swimLaps={swimLaps} fastestIdx={fastestIdx} slowestIdx={slowestIdx} />}
        {disc === 'bike' && <BikeSplitsView secs={secs} fastestIdx={fastestIdx} slowestIdx={slowestIdx} t={t} />}
      </div>

      {/* Charts: HR Zone Donut + Pace Bars */}
      <div className="chart-row">
        <div className="chart-container">
          <h4>{t('detail_hr_zone_dist')}</h4>
          <Plot
            data={[{
              labels: zoneLabels, values: zoneValues, type: 'pie', hole: 0.5,
              marker: { colors: zoneColors },
              textinfo: 'label+percent', textfont: { size: 11, color: '#c8d3f5' },
              hovertemplate: '%{label}<br>%{customdata}<br>%{percent}<extra></extra>',
              customdata: zoneValues.map(s => {
                if (s >= 3600) return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`
                if (s >= 60) return `${Math.floor(s/60)}m ${Math.round(s%60)}s`
                return `${Math.round(s)}s`
              }),
            }]}
            layout={{ ...PLOTLY_LAYOUT, margin: { l: 10, r: 10, t: 30, b: 30 }, showlegend: false }}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: 280 }}
          />
        </div>
        <div className="chart-container">
          <h4>{disc === 'bike' ? t('detail_speed_per_split') : t('detail_pace_per_split')}</h4>
          <Plot
            data={[{
              x: barX, y: barY, type: 'bar',
              marker: { color: barColors },
              text: barText, textposition: 'outside', textfont: { size: 10, color: '#c8d3f5' },
              hovertemplate: '%{x}: %{text}<extra></extra>',
            }]}
            layout={{
              ...PLOTLY_LAYOUT,
              xaxis: { ...PLOTLY_LAYOUT.xaxis, type: 'category' },
              yaxis: { ...PLOTLY_LAYOUT.yaxis, title: yTitle },
              margin: { ...PLOTLY_LAYOUT.margin, t: 40 },
            }}
            config={PLOTLY_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: 280 }}
          />
        </div>
      </div>
    </>
  )
}

/* ── Bike Splits View (per-1km + per-5km toggle) ── */
function BikeSplitsView({ secs, fastestIdx, slowestIdx, t }) {
  const [groupSize, setGroupSize] = useState(1)
  const hasElev = secs.some(s => s.elev_gain_m != null)

  const displayRows = useMemo(() => {
    if (groupSize === 1) return secs
    const groups = []
    for (let i = 0; i < secs.length; i += groupSize) {
      const chunk = secs.slice(i, i + groupSize)
      const hrVals = chunk.filter(s => s.avg_hr).map(s => s.avg_hr)
      const spdVals = chunk.filter(s => s.avg_speed_kmh).map(s => s.avg_speed_kmh)
      const pwrVals = chunk.filter(s => s.avg_power).map(s => s.avg_power)
      const cadVals = chunk.filter(s => s.avg_cadence).map(s => s.avg_cadence)
      const elevSum = chunk.reduce((sum, s) => sum + (s.elev_gain_m || 0), 0)
      const durSum = chunk.reduce((sum, s) => sum + (s.duration_sec || 0), 0)
      const fromKm = chunk[0].km_marker
      const toKm = chunk[chunk.length - 1].km_marker
      const label = fromKm === toKm ? `${fromKm}` : `${fromKm}-${toKm}`
      // Speed = total distance / total time (not average of per-km speeds)
      const avgSpd = durSum > 0 ? +(chunk.length / (durSum / 3600)).toFixed(1) : null
      const avgHr = hrVals.length ? +(hrVals.reduce((a, b) => a + b, 0) / hrVals.length).toFixed(1) : null
      groups.push({
        km_marker: label,
        avg_speed_kmh: avgSpd,
        avg_hr: avgHr,
        avg_power: pwrVals.length ? Math.round(pwrVals.reduce((a, b) => a + b, 0) / pwrVals.length) : null,
        avg_cadence: cadVals.length ? Math.round(cadVals.reduce((a, b) => a + b, 0) / cadVals.length) : null,
        elev_gain_m: hasElev ? Math.round(elevSum) : null,
        duration_sec: durSum,
      })
    }
    return groups
  }, [secs, groupSize, hasElev])

  // Fastest/slowest for current grouping
  const speedVals = displayRows.map(s => s.avg_speed_kmh || 0)
  const grpFastest = speedVals.indexOf(Math.max(...speedVals))
  const grpSlowest = speedVals.indexOf(Math.min(...speedVals.filter(v => v > 0)))

  return (
    <>
      {secs.length >= 10 && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          {[1, 5, 10].map(n => (
            <button key={n} className={`btn btn-sm${groupSize === n ? ' btn-primary' : ''}`}
              onClick={() => setGroupSize(n)}>{n === 1 ? 'Per 1km' : `Per ${n}km`}</button>
          ))}
        </div>
      )}
      <table className="data-table">
        <thead><tr><th>KM</th><th>{t('th_time')}</th><th>{t('detail_speed')}</th><th>HR</th><th>{t('detail_zone')}</th><th>{t('th_avg_power')} <InfoTip text={t('info_power_bike')} /></th><th>{t('th_cadence')} <InfoTip text={t('info_cadence_bike')} /></th>{hasElev && <th>{t('th_elev_gain')}</th>}</tr></thead>
        <tbody>
          {displayRows.map((s, i) => {
            const cls = i === grpFastest ? { color: 'var(--green)' } : i === grpSlowest ? { color: 'var(--red)' } : {}
            const hrCol = s.avg_hr ? { color: HR_ZONE_COLORS[hrZone(s.avg_hr)] } : {}
            const durStr = s.duration_sec ? `${Math.floor(s.duration_sec / 60)}:${String(Math.round(s.duration_sec % 60)).padStart(2, '0')}` : '-'
            return (
              <tr key={i} style={cls}>
                <td>{s.km_marker}km</td><td>{durStr}</td><td>{s.avg_speed_kmh} km/h</td>
                <td style={hrCol}>{s.avg_hr ? Math.round(s.avg_hr) : '-'}</td>
                <td>{s.avg_hr ? hrZone(s.avg_hr) : '-'}</td>
                <td>{s.avg_power ? Math.round(s.avg_power) + 'W' : '-'}</td>
                <td style={{ color: metricColor(s.avg_cadence, 80, 70, false) }}>{s.avg_cadence ? Math.round(s.avg_cadence) : '-'}</td>
                {hasElev && <td>{s.elev_gain_m != null ? Math.round(s.elev_gain_m) + 'm' : '-'}</td>}
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

/* ── Swim Splits View (Auto Sets + Splits /100M & /25M) ── */
function SwimSplitsView({ secs, swimSets, swimLaps, fastestIdx, slowestIdx }) {
  const { t } = useI18n()
  const [view, setView] = useState('sets')
  const [splitUnit, setSplitUnit] = useState('100m')

  function fmtSwimTime(sec) {
    if (!sec || sec <= 0) return '-'
    const m = Math.floor(sec / 60)
    const s = Math.round(sec % 60)
    return `${m}:${String(s).padStart(2, '0')}`
  }

  // Find fastest/slowest for /25M laps
  const lapPaces = (swimLaps || []).map(l => l.pace_per_100m_sec || 999)
  const fastestLapIdx = lapPaces.length > 0 ? lapPaces.indexOf(Math.min(...lapPaces)) : -1
  const slowestLapIdx = lapPaces.length > 0 ? lapPaces.indexOf(Math.max(...lapPaces.filter(p => p < 999))) : -1

  return (
    <>
      <div className="swim-view-tabs">
        <button className={`swim-view-tab${view === 'sets' ? ' active' : ''}`} onClick={() => setView('sets')}>{t('swim_sets')}</button>
        <button className={`swim-view-tab${view === 'splits' ? ' active' : ''}`} onClick={() => setView('splits')}>{t('swim_splits')}</button>
      </div>

      {view === 'sets' && swimSets.length > 0 && (
        <div className="table-scroll">
          <table className="data-table swim-table">
            <thead>
              <tr>
                <th>#</th>
                <th>{t('detail_stroke_style')}</th>
                <th style={{ textAlign: 'right' }}>{t('th_distance')}</th>
                <th style={{ textAlign: 'right' }}>/100m</th>
                <th style={{ textAlign: 'right' }}>{t('swim_time')}</th>
                <th style={{ textAlign: 'right' }}>{t('detail_rest')}</th>
                <th style={{ textAlign: 'right' }}>HR</th>
                <th style={{ textAlign: 'right' }}>SWOLF <InfoTip text={t('info_swolf')} /></th>
                <th style={{ textAlign: 'right' }}>/25 <InfoTip text={t('info_strokes')} /></th>
                <th style={{ textAlign: 'right' }}>/100 <InfoTip text={t('info_strokes_100')} /></th>
              </tr>
            </thead>
            <tbody>
              {swimSets.map((s) => {
                const hrCol = s.avg_hr ? { color: HR_ZONE_COLORS[hrZone(s.avg_hr)] } : {}
                const str100 = s.strokes_per_25 != null ? s.strokes_per_25 * 4 : null
                return (
                  <tr key={s.set_num}>
                    <td className="text-dim">{s.set_num}</td>
                    <td style={{ color: 'var(--swim)', fontWeight: 600 }}>{s.stroke_style || '-'}</td>
                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{s.distance_m}m</td>
                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{s.pace_str || '-'}</td>
                    <td style={{ textAlign: 'right' }}>{fmtSwimTime(s.swim_sec)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtSwimTime(s.rest_after_sec)}</td>
                    <td style={{ textAlign: 'right', ...hrCol }}>{s.avg_hr ? <>{Math.round(s.avg_hr)} <span className="swim-hr-zone">{hrZone(s.avg_hr)}</span></> : '-'}</td>
                    <td style={{ textAlign: 'right', color: metricColor(s.swolf, 40, 50) }}>{s.swolf ?? '-'}</td>
                    <td style={{ textAlign: 'right', color: metricColor(s.strokes_per_25, 16, 20) }}>{s.strokes_per_25 ?? '-'}</td>
                    <td style={{ textAlign: 'right', color: metricColor(str100, 64, 80) }}>{str100 ?? '-'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {view === 'splits' && (
        <>
          <div className="swim-split-unit-toggle">
            <button className={splitUnit === '100m' ? 'active' : ''} onClick={() => setSplitUnit('100m')}>/100M</button>
            <button className={splitUnit === '25m' ? 'active' : ''} onClick={() => setSplitUnit('25m')}>/25M</button>
          </div>

          <div className="table-scroll">
            <table className="data-table swim-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t('detail_stroke_style')}</th>
                  <th style={{ textAlign: 'right' }}>{t('th_distance')}</th>
                  <th style={{ textAlign: 'right' }}>{t('detail_active_pace')}</th>
                  <th style={{ textAlign: 'right' }}>HR</th>
                  <th style={{ textAlign: 'right' }}>{splitUnit === '100m' ? t('th_strokes') : t('th_strokes')} <InfoTip text={splitUnit === '100m' ? t('info_strokes_100') : t('info_strokes')} /></th>
                  <th style={{ textAlign: 'right' }}>SWOLF <InfoTip text={splitUnit === '100m' ? t('info_swolf_100') : t('info_swolf')} /></th>

                </tr>
              </thead>
              <tbody>
                {splitUnit === '100m' && secs.map((s, i) => {
                  const cls = i === fastestIdx ? 'fastest' : i === slowestIdx ? 'slowest' : ''
                  const hrCol = s.avg_hr ? { color: HR_ZONE_COLORS[hrZone(s.avg_hr)] } : {}
                  return (
                    <tr key={i} className={cls}>
                      <td className="text-dim">{s.num}</td>
                      <td style={{ color: 'var(--swim)' }}>{s.stroke_style || '-'}</td>
                      <td style={{ textAlign: 'right', color: 'var(--swim)', fontWeight: 600 }}>{s.segment_m}m</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{s.pace_str || '-'}</td>
                      <td style={{ textAlign: 'right', ...hrCol }}>{s.avg_hr ? <>{Math.round(s.avg_hr)} <span className="swim-hr-zone">{s.hr_zone || ''}</span></> : '-'}</td>
                      <td style={{ textAlign: 'right', color: metricColor(s.stroke_count, 64, 80) }}>{s.stroke_count || '-'}</td>
                      <td style={{ textAlign: 'right', color: metricColor(s.swolf_100, 160, 200) }}>{s.swolf_100 || '-'}</td>
                    </tr>
                  )
                })}
                {splitUnit === '25m' && swimLaps.map((l, i) => {
                  const cls = i === fastestLapIdx ? 'fastest' : i === slowestLapIdx ? 'slowest' : ''
                  const hrCol = l.avg_hr ? { color: HR_ZONE_COLORS[hrZone(l.avg_hr)] } : {}
                  return (
                    <tr key={i} className={cls}>
                      <td className="text-dim">{l.lap_num}</td>
                      <td style={{ color: 'var(--swim)' }}>{l.stroke_style || '-'}</td>
                      <td style={{ textAlign: 'right' }}>25m</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{l.pace_str || '-'}</td>
                      <td style={{ textAlign: 'right', ...hrCol }}>{l.avg_hr ? <>{Math.round(l.avg_hr)} <span className="swim-hr-zone">{l.hr_zone || ''}</span></> : '-'}</td>
                      <td style={{ textAlign: 'right', color: metricColor(l.strokes, 16, 20) }}>{l.strokes || '-'}</td>
                      <td style={{ textAlign: 'right', color: metricColor(l.swolf, 40, 50) }}>{l.swolf || '-'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}

/* ── Analysis Tab ── */
function AnalysisTab({ pts, sections: sectionsData }) {
  const { t } = useI18n()
  const disc = sectionsData?.discipline || 'run'
  const secs = sectionsData?.sections || []

  const splitLabels = secs.map((s) => {
    if (disc === 'run') return `km ${s.km}`
    if (disc === 'swim') return `${s.segment_m}m`
    return `${s.km_marker}km`
  })
  const secHR = secs.map((s) => s.avg_hr || null)

  let secSecondary = null
  let secondaryLabel = ''
  if (disc === 'run' && secs.some((s) => s.avg_cadence)) {
    secSecondary = secs.map((s) => s.avg_cadence || null)
    secondaryLabel = t('detail_cadence_spm')
  } else if (disc === 'swim' && secs.some((s) => s.stroke_count)) {
    secSecondary = secs.map((s) => s.stroke_count || null)
    secondaryLabel = t('detail_strokes_100m')
  }

  const dualTraces = []
  if (secHR.some((v) => v)) {
    dualTraces.push({
      x: splitLabels, y: secHR, name: t('detail_avg_hr_bpm'), yaxis: 'y',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#ff757f', width: 2 }, marker: { size: 6, color: '#ff757f' },
    })
  }
  if (secSecondary) {
    dualTraces.push({
      x: splitLabels, y: secSecondary, name: secondaryLabel, yaxis: 'y2',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#c099ff', width: 2, dash: 'dot' }, marker: { size: 6, color: '#c099ff', symbol: 'diamond' },
    })
  }

  // Pace vs HR scatter
  const scatter = useMemo(() => {
    const sHR = [], sPace = [], sCol = [], sLabels = []
    secs.forEach((s, i) => {
      const hr = s.avg_hr
      let pace = 0
      if (disc === 'run' && s.avg_pace_min_km) pace = s.avg_pace_min_km
      else if (disc === 'bike' && s.avg_speed_kmh) pace = s.avg_speed_kmh
      else if (disc === 'swim' && s.pace_per_100m_sec) pace = s.pace_per_100m_sec / 60
      if (hr > 0 && pace > 0) {
        sHR.push(hr)
        sPace.push(pace)
        sCol.push(HR_ZONE_COLORS[hrZone(hr)])
        sLabels.push(splitLabels[i])
      }
    })
    return { hr: sHR, pace: sPace, col: sCol, labels: sLabels }
  }, [secs, disc, splitLabels])

  // Cardiac drift — prefer pre-computed from raw time-series (more accurate than per-km averages)
  const drift = useMemo(() => {
    const hrSum = sectionsData?.hr_summary
    // Use pre-computed values from raw 30s-sampled time-series when available
    const avgHR1 = hrSum?.first_half_avg || 0
    const avgHR2 = hrSum?.second_half_avg || 0
    const d = hrSum?.drift_pct ?? (avgHR1 > 0 ? ((avgHR2 - avgHR1) / avgHR1 * 100) : null)

    if (d == null && secs.length < 4) return null

    // Fall back to per-split calculation if no pre-computed data
    let finalHR1 = avgHR1, finalHR2 = avgHR2, finalD = d
    if (!hrSum) {
      const mid = Math.floor(secs.length / 2)
      const first = secs.slice(0, mid)
      const second = secs.slice(mid)
      finalHR1 = first.filter((s) => s.avg_hr).reduce((a, s) => a + s.avg_hr, 0) / first.filter((s) => s.avg_hr).length || 0
      finalHR2 = second.filter((s) => s.avg_hr).reduce((a, s) => a + s.avg_hr, 0) / second.filter((s) => s.avg_hr).length || 0
      finalD = finalHR1 > 0 ? ((finalHR2 - finalHR1) / finalHR1 * 100) : 0
    }

    // Pace labels from per-km splits (still useful context even with pre-computed drift)
    const mid = Math.floor(secs.length / 2)
    const first = secs.slice(0, mid)
    const second = secs.slice(mid)
    let paceLabel = ''
    let pace1Label = '', pace2Label = ''
    if (disc === 'run') {
      const p1 = first.filter((s) => s.avg_pace_min_km).reduce((a, s) => a + s.avg_pace_min_km, 0) / first.filter((s) => s.avg_pace_min_km).length || 0
      const p2 = second.filter((s) => s.avg_pace_min_km).reduce((a, s) => a + s.avg_pace_min_km, 0) / second.filter((s) => s.avg_pace_min_km).length || 0
      const fmt = (v) => `${Math.floor(v)}:${String(Math.round((v % 1) * 60)).padStart(2, '0')}/km`
      pace1Label = fmt(p1); pace2Label = fmt(p2)
      paceLabel = `Pace: ${pace1Label} \u2192 ${pace2Label}`
    } else if (disc === 'bike') {
      const s1 = first.filter((s) => s.avg_speed_kmh).reduce((a, s) => a + s.avg_speed_kmh, 0) / first.filter((s) => s.avg_speed_kmh).length || 0
      const s2 = second.filter((s) => s.avg_speed_kmh).reduce((a, s) => a + s.avg_speed_kmh, 0) / second.filter((s) => s.avg_speed_kmh).length || 0
      pace1Label = `${s1.toFixed(1)} km/h`; pace2Label = `${s2.toFixed(1)} km/h`
      paceLabel = `${t('detail_speed')}: ${s1.toFixed(1)} \u2192 ${s2.toFixed(1)} km/h`
    } else if (disc === 'swim') {
      const p1 = first.filter((s) => s.pace_per_100m_sec).reduce((a, s) => a + s.pace_per_100m_sec, 0) / first.filter((s) => s.pace_per_100m_sec).length || 0
      const p2 = second.filter((s) => s.pace_per_100m_sec).reduce((a, s) => a + s.pace_per_100m_sec, 0) / second.filter((s) => s.pace_per_100m_sec).length || 0
      const fmt = (v) => `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, '0')}/100m`
      pace1Label = fmt(p1); pace2Label = fmt(p2)
      paceLabel = `Pace: ${pace1Label} \u2192 ${pace2Label}`
    }

    const color = Math.abs(finalD) <= 5 ? 'var(--green)' : finalD > 5 ? 'var(--red)' : 'var(--yellow)'
    const verdict = Math.abs(finalD) <= 3 ? t('drift_minimal')
      : finalD <= 5 ? t('drift_mild')
      : finalD <= 8 ? t('drift_moderate')
      : t('drift_significant')

    return { avgHR1: finalHR1, avgHR2: finalHR2, d: finalD, color, verdict, paceLabel, pace1Label, pace2Label }
  }, [secs, disc, sectionsData, t])

  const paceYTitle = disc === 'run' ? t('detail_pace_min_km') : disc === 'swim' ? t('detail_pace_min_100m') : t('detail_speed_kmh')

  return (
    <>
      {/* HR + Secondary metric per split */}
      {dualTraces.length > 0 && (
        <div className="chart-row">
          <div className="chart-container">
            <h4>{secSecondary ? `${t('detail_hr')} + ${secondaryLabel}` : t('detail_hr_per_split')}</h4>
            <Plot
              data={dualTraces}
              layout={{
                ...PLOTLY_LAYOUT,
                margin: { l: 48, r: secSecondary ? 48 : 16, t: 40, b: 40 },
                legend: { ...PLOTLY_LAYOUT.legend, orientation: 'h', x: 0, y: 1.15 },
                xaxis: { ...PLOTLY_LAYOUT.xaxis, type: 'category', autorange: true },
                yaxis: { ...PLOTLY_LAYOUT.yaxis, tickfont: { color: '#ff757f' }, autorange: true },
                ...(secSecondary ? { yaxis2: { overlaying: 'y', side: 'right', gridcolor: 'transparent', tickfont: { color: '#c099ff' }, autorange: true } } : {}),
              }}
              config={PLOTLY_CONFIG}
              useResizeHandler
              style={{ width: '100%', height: 300 }}
            />
          </div>

          {/* Pace vs HR scatter */}
          {scatter.hr.length > 0 && (
            <div className="chart-container">
              <h4>{t('detail_pace_vs_hr')}</h4>
              <Plot
                data={[{
                  x: scatter.pace, y: scatter.hr, type: 'scatter', mode: 'markers+text',
                  marker: { color: scatter.col, size: 10 },
                  text: scatter.labels, textposition: 'top center', textfont: { size: 9, color: '#7a88b8' },
                  hovertemplate: `%{text}<br>${paceYTitle}: %{x:.2f}<br>HR: %{y:.0f} bpm<extra></extra>`,
                }]}
                layout={{
                  ...PLOTLY_LAYOUT,
                  margin: { l: 48, r: 16, t: 32, b: 48 },
                  xaxis: { ...PLOTLY_LAYOUT.xaxis, title: paceYTitle, type: 'linear', autorange: true },
                  yaxis: { ...PLOTLY_LAYOUT.yaxis, title: 'Heart Rate (bpm)', autorange: true },
                }}
                config={PLOTLY_CONFIG}
                useResizeHandler
                style={{ width: '100%', height: 300 }}
              />
            </div>
          )}
        </div>
      )}

      {/* Cardiac Drift */}
      <div className="chart-row single">
        <div className="chart-container">
          <h4>
              {t('detail_cardiac_drift')}
              <InfoTip text={t('detail_cardiac_drift_info')} />
            </h4>
          <div style={{ padding: 16 }}>
            {drift ? (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 16 }}>
                  <div className="detail-stat" style={{ background: 'var(--bg-3)' }}>
                    <div className="detail-stat-val" style={{ fontSize: 16 }}>{t('detail_first_half')}</div>
                    <div className="detail-stat-label">
                      HR: {drift.avgHR1.toFixed(0)} bpm
                      {drift.pace1Label && <><br />{drift.pace1Label}</>}
                    </div>
                  </div>
                  <div className="detail-stat" style={{ background: 'var(--bg-3)' }}>
                    <div className="detail-stat-val" style={{ fontSize: 16 }}>{t('detail_second_half')}</div>
                    <div className="detail-stat-label">
                      HR: {drift.avgHR2.toFixed(0)} bpm
                      {drift.pace2Label && <><br />{drift.pace2Label}</>}
                    </div>
                  </div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 24, fontWeight: 700, color: drift.color }}>
                    {drift.d >= 0 ? '+' : ''}{drift.d.toFixed(1)}% {t('detail_hr_drift')}
                  </div>
                  <div className="text-dim" style={{ marginTop: 4 }}>{drift.verdict}</div>
                  {drift.paceLabel && <div className="text-dim" style={{ marginTop: 2 }}>{drift.paceLabel}</div>}
                </div>
              </>
            ) : (
              <div className="text-dim" style={{ textAlign: 'center' }}>{t('detail_need_splits')}</div>
            )}
          </div>
        </div>
      </div>

      {/* Elevation */}
      {pts.some((p) => p.elevation_m) && <ElevationChart pts={pts} />}
    </>
  )
}

/* ── Interval Map ── */
function IntervalMapPopup({ gpsSegs, intervals, selectedIdx, disc, onClose, onSelect, t }) {
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!expanded) return
    const handler = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setExpanded(false)
      }
    }
    window.addEventListener('keydown', handler, true)
    return () => window.removeEventListener('keydown', handler, true)
  }, [expanded])

  // Map GPS segments to intervals by time proportion
  const totalDur = useMemo(() => {
    if (!intervals?.length) return 0
    const last = intervals[intervals.length - 1]
    return last.start_offset_sec + last.duration_sec
  }, [intervals])

  // Always color route by work/rest. Selected interval gets a highlight marker.
  const { polylines, bounds } = useMemo(() => {
    if (!gpsSegs?.length || !intervals?.length || !totalDur) return { polylines: [], bounds: [[32.5, 35]] }

    const segCount = gpsSegs.length
    const lines = []
    for (let i = 1; i < segCount; i++) {
      const s = gpsSegs[i]
      const prev = gpsSegs[i - 1]
      if (!s.lat || !prev.lat) continue
      // Map GPS index to time
      const tSec = (i / (segCount - 1)) * totalDur
      let iv = null
      for (let j = 0; j < intervals.length; j++) {
        const candidate = intervals[j]
        if (tSec >= candidate.start_offset_sec && tSec <= candidate.start_offset_sec + candidate.duration_sec) {
          iv = candidate
          break
        }
      }
      const isWork = iv?.type === 'work'
      const isRest = iv?.type === 'rest'
      lines.push({
        positions: [[prev.lat, prev.lon], [s.lat, s.lon]],
        color: isRest ? '#FF5722' : '#2196F3',
        weight: 3,
        opacity: isRest ? 0.9 : 0.7,
      })
    }
    const coords = gpsSegs.filter(s => s.lat).map(s => [s.lat, s.lon])
    return { polylines: lines, bounds: coords.length ? coords : [[32.5, 35]] }
  }, [gpsSegs, intervals, totalDur])

  const selIv = selectedIdx != null ? intervals[selectedIdx] : null
  const mapHeight = expanded ? '70vh' : 400

  const mapContent = (
    <>
      <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="OSM" />
      {polylines.map((seg, i) => (
        <Polyline key={i} positions={seg.positions} pathOptions={{ color: seg.color, weight: seg.weight, opacity: seg.opacity }} />
      ))}
      {selIv?.start_lat && <FlyToPoint lat={selIv.start_lat} lon={selIv.start_lon} zoom={14} />}
      {selIv?.start_lat && (
        <CircleMarker center={[selIv.start_lat, selIv.start_lon]} radius={9}
          pathOptions={{ color: '#fff', fillColor: selIv.type === 'work' ? '#2196F3' : '#FF5722', fillOpacity: 1, weight: 3 }}>
          <Tooltip permanent>#{selectedIdx + 1} {selIv.type === 'work' ? t('detail_work') : t('detail_rest')}</Tooltip>
        </CircleMarker>
      )}
    </>
  )

  return (
    <div className={`interval-map-container${expanded ? ' interval-map-expanded' : ''}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <div style={{ display: 'flex', gap: 10, fontSize: 12, alignItems: 'center' }}>
          <span><span style={{ display: 'inline-block', width: 12, height: 3, background: '#2196F3', marginInlineEnd: 4, verticalAlign: 'middle' }} />{t('detail_work')}</span>
          <span><span style={{ display: 'inline-block', width: 12, height: 3, background: '#FF5722', marginInlineEnd: 4, verticalAlign: 'middle' }} />{t('detail_rest')}</span>
          {selIv && (
            <span className="text-dim" style={{ marginInlineStart: 4 }}>
              #{selectedIdx + 1} {selIv.type === 'work' ? t('detail_work') : t('detail_rest')}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          <button className="btn btn-sm btn-icon" onClick={() => setExpanded(!expanded)} title={expanded ? 'Collapse' : 'Expand'}>
            {expanded ? '↙' : '↗'}
          </button>
          {selIv && <button className="btn btn-sm btn-icon" onClick={onClose} title="Clear">✕</button>}
        </div>
      </div>
      <div style={{ height: mapHeight, borderRadius: 'var(--radius)', overflow: 'hidden' }}>
        <MapContainer bounds={bounds} style={{ height: '100%', width: '100%' }} scrollWheelZoom={true}
          whenReady={(e) => setTimeout(() => e.target.invalidateSize(), 200)}>
          {mapContent}
        </MapContainer>
      </div>
    </div>
  )
}

/* ── Detailed Data Tab (intervals, HR/elevation profiles) ── */
function DetailedDataTab({ sections }) {
  const { t } = useI18n()
  const intervals = sections?.intervals
  const hrProfile = sections?.hr_profile
  const elevProfile = sections?.elevation_profile
  const hrSummary = sections?.hr_summary
  const elevSummary = sections?.elevation_summary
  const disc = sections?.discipline
  const gpsSegs = sections?.hr_colored_segments
  const hasGpsSegs = gpsSegs?.length > 1
  const [mapInterval, setMapInterval] = useState(null) // index of selected interval or null

  function fmtOffset(sec) {
    const m = Math.floor(sec / 60)
    const s = Math.round(sec % 60)
    return `${m}:${String(s).padStart(2, '0')}`
  }

  return (
    <>
      {/* Summaries row */}
      {(hrSummary || elevSummary) && (
        <div className="detail-summary" style={{ marginBottom: 20 }}>
          {hrSummary && (
            <>
              <StatCard value={`${hrSummary.min} bpm`} label="Min HR" />
              <StatCard value={`${hrSummary.max} bpm`} label={t('detail_max_hr')} />
            </>
          )}
          {elevSummary && (
            <>
              <StatCard value={`${Math.round(elevSummary.total_ascent_m)}m`} label={t('detail_total_ascent')} />
              <StatCard value={`${Math.round(elevSummary.total_descent_m)}m`} label={t('detail_total_descent')} />
              <StatCard value={`${Math.round(elevSummary.min_m)}m`} label={t('detail_min_elev')} />
              <StatCard value={`${Math.round(elevSummary.max_m)}m`} label={t('detail_max_elev')} />
            </>
          )}
        </div>
      )}

      {/* Intervals table */}
      {intervals && intervals.length > 0 && (() => {
        // Compute cumulative distance to derive km marker per interval
        let cumDist = 0
        const ivWithKm = intervals.map(iv => {
          const kmStart = (cumDist / 1000).toFixed(1)
          cumDist += iv.distance_m || 0
          return { ...iv, km: kmStart }
        })
        return (
        <div style={{ marginBottom: 24 }}>
          <h4 style={{ marginBottom: 8 }}>{t('detail_intervals')} <InfoTip text={t('info_intervals')} /></h4>
          <div className="table-scroll" style={{ maxHeight: 350 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t('type')}</th>
                  <th>KM</th>
                  <th>{t('th_time')}</th>
                  <th>{t('th_duration')}</th>
                  <th>{disc === 'bike' ? t('detail_speed') : t('detail_pace')}</th>
                  <th>{t('th_distance')}</th>
                  <th>HR</th>
                  <th>{t('th_avg_power')}</th>
                  {hasGpsSegs && <th></th>}
                </tr>
              </thead>
              <tbody>
                {ivWithKm.map((iv, i) => {
                  const isWork = iv.type === 'work'
                  const isSelected = mapInterval === i
                  const rowStyle = isWork ? {} : { opacity: 0.6 }
                  const typeColor = isWork ? 'var(--green)' : 'var(--text-dim)'
                  const hrCol = iv.avg_hr ? { color: HR_ZONE_COLORS[hrZone(iv.avg_hr)] } : {}
                  const hasPos = iv.start_lat && iv.start_lon
                  return (
                    <tr key={i} style={{ ...rowStyle, ...(isSelected ? { background: 'rgba(130,170,255,0.15)' } : {}) }}>
                      <td className="text-dim">{i + 1}</td>
                      <td style={{ color: typeColor, fontWeight: 600 }}>{isWork ? t('detail_work') : t('detail_rest')}</td>
                      <td>{iv.km}</td>
                      <td className="text-dim">{fmtOffset(iv.start_offset_sec)}</td>
                      <td>{fmtOffset(iv.duration_sec)}</td>
                      <td style={{ fontWeight: 600 }}>
                        {iv.pace_str || (iv.avg_speed_kmh ? `${iv.avg_speed_kmh} km/h` : '-')}
                      </td>
                      <td>{iv.distance_m ? `${iv.distance_m}m` : '-'}</td>
                      <td style={hrCol}>
                        {iv.avg_hr ? `${Math.round(iv.avg_hr)}` : '-'}
                        {iv.hr_min && iv.hr_max ? <span className="text-dim" style={{ fontSize: '0.8em' }}> ({iv.hr_min}-{iv.hr_max})</span> : ''}
                      </td>
                      <td>{iv.avg_power ? `${iv.avg_power}W` : '-'}</td>
                      {hasGpsSegs && (
                        <td>
                          {hasPos && (
                            <button className={`btn btn-sm btn-icon${isSelected ? ' btn-accent' : ''}`} style={{ padding: '2px 5px' }} title={t('locate_on_map')}
                              onClick={() => setMapInterval(isSelected ? null : i)}>
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
        )
      })()}

      {/* Interval map */}
      {hasGpsSegs && intervals?.length > 0 && (
        <IntervalMapPopup
          gpsSegs={gpsSegs}
          intervals={intervals}
          selectedIdx={mapInterval}
          disc={disc}
          onClose={() => setMapInterval(null)}
          onSelect={setMapInterval}
          t={t}
        />
      )}

      {/* HR Profile chart */}
      {hrProfile && hrProfile.length > 1 && (
        <div className="chart-row single" style={{ marginBottom: 20 }}>
          <div className="chart-container">
            <h4>{t('detail_hr_over_time')}</h4>
            <Plot
              data={[{
                x: hrProfile.map(p => p.t / 60),
                y: hrProfile.map(p => p.hr),
                type: 'scatter', mode: 'lines',
                line: { color: '#ff757f', width: 1.5 },
                hovertemplate: 'HR: %{y:.0f} bpm | Time: %{x:.1f} min<extra></extra>',
              }]}
              layout={{
                ...PLOTLY_LAYOUT,
                xaxis: {
                  ...PLOTLY_LAYOUT.xaxis,
                  type: 'linear',
                  title: { text: 'Time (min)', font: { size: 12, color: '#8899aa' }, standoff: 8 },
                },
                yaxis: {
                  ...PLOTLY_LAYOUT.yaxis,
                  title: { text: 'HR (bpm)', font: { size: 12, color: '#8899aa' }, standoff: 8 },
                  autorange: true,
                },
                margin: { ...PLOTLY_LAYOUT.margin, l: 50, b: 45 },
              }}
              config={PLOTLY_CONFIG}
              useResizeHandler
              style={{ width: '100%', height: 250 }}
            />
          </div>
        </div>
      )}

      {/* Elevation Profile chart */}
      {elevProfile && elevProfile.length > 1 && (
        <div className="chart-row single" style={{ marginBottom: 20 }}>
          <div className="chart-container">
            <h4>{t('detail_elev_over_time')}</h4>
            <Plot
              data={[{
                x: elevProfile.map(p => p.t / 60),
                y: elevProfile.map(p => p.elev_m),
                type: 'scatter', mode: 'lines',
                fill: 'tozeroy', fillcolor: 'rgba(101,188,255,0.1)',
                line: { color: COLORS.swim, width: 1.5 },
                hovertemplate: 'Elevation: %{y:.0f}m | Time: %{x:.1f} min<extra></extra>',
              }]}
              layout={{
                ...PLOTLY_LAYOUT,
                xaxis: {
                  ...PLOTLY_LAYOUT.xaxis,
                  type: 'linear',
                  title: { text: 'Time (min)', font: { size: 12, color: '#8899aa' }, standoff: 8 },
                },
                yaxis: {
                  ...PLOTLY_LAYOUT.yaxis,
                  title: { text: 'Elevation (m)', font: { size: 12, color: '#8899aa' }, standoff: 8 },
                  autorange: true,
                },
                margin: { ...PLOTLY_LAYOUT.margin, l: 55, b: 45 },
              }}
              config={PLOTLY_CONFIG}
              useResizeHandler
              style={{ width: '100%', height: 250 }}
            />
          </div>
        </div>
      )}

      {!intervals && !hrProfile && !elevProfile && (
        <div className="text-dim" style={{ textAlign: 'center', padding: 40 }}>{t('detail_no_detailed_data')}</div>
      )}
    </>
  )
}
