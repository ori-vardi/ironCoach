import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import Plot from 'react-plotly.js'
import { api } from '../api'
import { PLOTLY_LAYOUT, PLOTLY_CONFIG } from '../constants'
import { localDateStr, safef, detectDir, autoGrow, uploadFilesToServer, handleFilePaste } from '../utils/formatters'
import ProgressRing from '../components/common/ProgressRing'
import Modal from '../components/common/Modal'
import ConfirmDialog from '../components/common/ConfirmDialog'
import { notifyLlmStart, notifyLlmEnd } from '../components/NotificationBell'
import { useI18n } from '../i18n/I18nContext'
import { useApp } from '../context/AppContext'
import { useAuth } from '../context/AuthContext'
import InfoTip from '../components/common/InfoTip'

function calcTEF(protein_g, carbs_g, fat_g) {
  return Math.round(protein_g * 4 * 0.25 + carbs_g * 4 * 0.08 + fat_g * 9 * 0.03)
}

const MEAL_TYPES = ['breakfast', 'lunch', 'dinner', 'snack', 'pre_workout', 'during_workout', 'post_workout']

const EMPTY_MEAL = {
  meal_type: 'lunch', meal_time: '', description: '', calories: 0,
  protein_g: 0, carbs_g: 0, fat_g: 0, hydration_ml: 0,
}

export default function NutritionPage() {
  const { t, lang } = useI18n()
  const { dateFrom, dateTo, aiEnabled } = useApp()
  const { user } = useAuth()
  const suggestKey = `nutrition_suggestion_${user?.id || 0}`
  const draftKey = `nutrition-draft_${user?.id || 0}`
  const filesKey = `nutrition-files_${user?.id || 0}`
  const [searchParams] = useSearchParams()
  const [date, setDate] = useState(() => searchParams.get('date') || localDateStr(new Date()))
  const [meals, setMeals] = useState([])
  const [trendData, setTrendData] = useState({ dates: [], values: [] })
  const [netData, setNetData] = useState([])
  const [analyzing, setAnalyzing] = useState(false)
  const [mealText, _setMealText] = useState(() => sessionStorage.getItem(draftKey) || '')
  const setMealText = (v) => { _setMealText(v); sessionStorage.setItem(draftKey, v) }
  const [mealExpanded, setMealExpanded] = useState(false)
  const [energy, setEnergy] = useState(null)

  const [expandedMeals, setExpandedMeals] = useState(new Set())
  const [expandedGroups, setExpandedGroups] = useState(new Set(MEAL_TYPES))

  // Recent items for autocomplete
  const [recentItems, setRecentItems] = useState([])
  const [showRecent, setShowRecent] = useState(false)
  const [selectedRecent, setSelectedRecent] = useState([])
  const recentRef = useRef(null)
  const recentTimerRef = useRef(null)

  // File attachments for meal analysis (persisted in sessionStorage)
  const [attachedFiles, _setAttachedFiles] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem(filesKey) || '[]') } catch { return [] }
  })
  const setAttachedFiles = (v) => {
    _setAttachedFiles(prev => {
      const next = typeof v === 'function' ? v(prev) : v
      sessionStorage.setItem(filesKey, JSON.stringify(next))
      return next
    })
  }
  const fileInputRef = useRef(null)
  const analyzeAreaRef = useRef(null)
  const analyzeAbortRef = useRef(null)
  const regenPollRef = useRef(null)

  // Form state
  const [formOpen, setFormOpen] = useState(false)
  const [formData, setFormData] = useState(EMPTY_MEAL)
  const [formItems, setFormItems] = useState([])
  const [editingId, setEditingId] = useState(null)

  // Confirm dialog state
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [weeklyModalOpen, setWeeklyModalOpen] = useState(false)
  const [confirmTarget, setConfirmTarget] = useState(null)

  // Feedback banner after meal submission
  const [mealFeedback, setMealFeedback] = useState(null)

  // Nutrition targets
  const [targets, setTargets] = useState({ calories: 2500, protein_g: 150, carbs_g: 300, fat_g: 80, water_ml: 2500 })
  const [targetsOpen, setTargetsOpen] = useState(false)
  const [targetsForm, setTargetsForm] = useState(targets)
  const [suggesting, setSuggesting] = useState(false)
  const [suggestReason, setSuggestReason] = useState('')
  const [autoSuggest, setAutoSuggest] = useState(false)

  // Load saved AI suggestion into the modal form
  function openTargetsWithSuggestion() {
    const saved = localStorage.getItem(suggestKey)
    if (saved) {
      try {
        const s = JSON.parse(saved)
        setTargetsForm({ calories: s.calories, protein_g: s.protein_g, carbs_g: s.carbs_g, fat_g: s.fat_g, water_ml: s.water_ml })
        if (s.reasoning) setSuggestReason(s.reasoning)
      } catch { setTargetsForm({ ...targets }) }
    } else {
      setTargetsForm({ ...targets })
    }
    setTargetsOpen(true)
  }

  // Open targets modal from URL param (e.g. notification click)
  useEffect(() => {
    if (searchParams.get('openTargets') === '1') openTargetsWithSuggestion()
  }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

  // Listen for custom event (same-page notification click)
  useEffect(() => {
    const handler = () => openTargetsWithSuggestion()
    window.addEventListener('open-nutrition-targets', handler)
    return () => window.removeEventListener('open-nutrition-targets', handler)
  }, [targets]) // eslint-disable-line react-hooks/exhaustive-deps

  const loadDay = useCallback(async (d) => {
    try {
      const data = await api(`/api/nutrition?date=${d}`)
      setMeals(data)
    } catch {
      setMeals([])
    }
  }, [])

  const loadTrend = useCallback(async () => {
    try {
      const fromStr = dateFrom || localDateStr((() => { const d = new Date(); d.setDate(d.getDate() - 28); return d })())
      const toStr = dateTo || localDateStr(new Date())
      const [range, netRange] = await Promise.all([
        api(`/api/nutrition/range?from_date=${fromStr}&to_date=${toStr}`),
        api(`/api/energy-balance/range?from_date=${fromStr}&to_date=${toStr}`).catch(() => []),
      ])
      const byDate = {}
      range.forEach(m => {
        if (!byDate[m.date]) byDate[m.date] = 0
        byDate[m.date] += safef(m.calories)
      })
      const dates = Object.keys(byDate).sort()
      setTrendData({ dates, values: dates.map(d => byDate[d]) })
      setNetData(netRange || [])
    } catch (err) { console.error('Failed to load nutrition:', err) }
  }, [dateFrom, dateTo])

  const loadEnergy = useCallback(async (d) => {
    try {
      const data = await api(`/api/energy-balance?date=${d}`)
      setEnergy(data)
    } catch { setEnergy(null) }
  }, [])

  const loadRecent = useCallback(async () => {
    try {
      const data = await api('/api/nutrition/recent')
      setRecentItems(data)
    } catch { setRecentItems([]) }
  }, [])

  useEffect(() => { loadDay(date); loadEnergy(date) }, [date, loadDay, loadEnergy])
  useEffect(() => { loadTrend() }, [loadTrend])
  useEffect(() => { loadRecent() }, [loadRecent])
  useEffect(() => {
    api('/api/nutrition/targets').then(t => { setTargets(t); setTargetsForm(t) }).catch(() => {})
    api('/api/settings/nutrition_auto_suggest').then(r => setAutoSuggest(r.value === '1')).catch(() => {})
  }, [])

  // Close recent dropdown on click outside
  useEffect(() => {
    const handler = (e) => {
      if (recentRef.current && !recentRef.current.contains(e.target)) setShowRecent(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Warn before unload if meal analysis is in progress
  useEffect(() => {
    const warn = (e) => { if (analyzing) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [analyzing])

  // Refresh when coach stores new data or tab regains focus
  useEffect(() => {
    const refresh = () => { loadDay(date); loadTrend(); loadEnergy(date) }
    const onVisible = () => { if (document.visibilityState === 'visible') refresh() }
    window.addEventListener('coach-data-update', refresh)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('coach-data-update', refresh)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [date, loadDay, loadTrend, loadEnergy])

  // Cleanup polling interval on unmount
  useEffect(() => {
    return () => {
      if (regenPollRef.current) clearInterval(regenPollRef.current)
      clearTimeout(recentTimerRef.current)
    }
  }, [])

  const totals = useMemo(() => meals.reduce((t, m) => ({
    cal: t.cal + safef(m.calories),
    prot: t.prot + safef(m.protein_g),
    carb: t.carb + safef(m.carbs_g),
    fat: t.fat + safef(m.fat_g),
    water: t.water + safef(m.hydration_ml),
  }), { cal: 0, prot: 0, carb: 0, fat: 0, water: 0 }), [meals])

  function changeDay(delta) {
    const d = new Date(date)
    d.setDate(d.getDate() + delta)
    setDate(localDateStr(d))
  }

  function openAddForm() {
    setEditingId(null)
    setFormData({ ...EMPTY_MEAL, date })
    setFormItems([])
    setFormOpen(true)
  }

  function openEditForm(meal) {
    setEditingId(meal.id)
    let items = []
    try { items = meal.notes ? JSON.parse(meal.notes) : [] } catch { /* not JSON */ }
    setFormData({
      meal_type: meal.meal_type || 'lunch',
      meal_time: meal.meal_time || '',
      description: meal.description || '',
      calories: meal.calories || 0,
      protein_g: meal.protein_g || 0,
      carbs_g: meal.carbs_g || 0,
      fat_g: meal.fat_g || 0,
      hydration_ml: meal.hydration_ml || 0,
      date: meal.date || date,
    })
    setFormItems(items.map(it => ({ ...it })))
    setFormOpen(true)
  }

  function sumItemMacros(items) {
    return items.reduce((t, it) => ({
      calories: t.calories + (parseFloat(it.calories) || 0),
      protein_g: t.protein_g + (parseFloat(it.protein_g) || 0),
      carbs_g: t.carbs_g + (parseFloat(it.carbs_g) || 0),
      fat_g: t.fat_g + (parseFloat(it.fat_g) || 0),
    }), { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 })
  }

  function updateItem(idx, field, value) {
    setFormItems(prev => {
      const next = prev.map((it, i) => i === idx ? { ...it, [field]: value } : it)
      setFormData(f => ({ ...f, ...sumItemMacros(next) }))
      return next
    })
  }

  function removeItem(idx) {
    setFormItems(prev => {
      const next = prev.filter((_, i) => i !== idx)
      setFormData(f => ({ ...f, ...sumItemMacros(next) }))
      return next
    })
  }

  function addItem() {
    setFormItems(prev => [...prev, { name: '', calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 }])
  }

  function openFromRecent(items) {
    // Build form items with total macros (quantity * unit macros)
    const formItemsList = items.map(it => {
      const qty = it.quantity || 1
      const uCal = it.unit_calories || 0
      const uP = it.unit_protein_g || 0
      const uC = it.unit_carbs_g || 0
      const uF = it.unit_fat_g || 0
      return {
        name: qty > 1 ? `${qty} x ${it.base_name || it.name}` : (it.base_name || it.name),
        calories: Math.round(uCal * qty),
        protein_g: Math.round(uP * qty * 10) / 10,
        carbs_g: Math.round(uC * qty * 10) / 10,
        fat_g: Math.round(uF * qty * 10) / 10,
      }
    })
    const totals = sumItemMacros(formItemsList)
    const desc = items.map(it => {
      const qty = it.quantity || 1
      return qty > 1 ? `${qty} x ${it.base_name || it.name}` : (it.base_name || it.name)
    }).join(' + ')
    setEditingId(null)
    setFormData({ ...EMPTY_MEAL, date, description: desc, ...totals })
    setFormItems(formItemsList)
    setFormOpen(true)
    setSelectedRecent([])
    setShowRecent(false)
    setMealText('')
  }

  function adjustRecentQty(idx, delta) {
    setSelectedRecent(prev => prev.map((it, i) => {
      if (i !== idx) return it
      const newQty = Math.max(0.5, (it.quantity || 1) + delta)
      return { ...it, quantity: newQty }
    }))
  }

  const filteredRecent = useMemo(() => {
    const trimmed = mealText.trim()
    if (trimmed.length < 2) return recentItems
    const q = trimmed.toLowerCase()
    return recentItems.filter(it =>
      (it.base_name || '').toLowerCase().includes(q) ||
      (it.name || '').toLowerCase().includes(q)
    )
  }, [mealText, recentItems])

  async function saveMeal() {
    const mealDate = formData.date || date
    const data = {
      date: mealDate,
      meal_time: formData.meal_time || '',
      meal_type: formData.meal_type,
      description: formData.description,
      calories: parseFloat(formData.calories) || 0,
      protein_g: parseFloat(formData.protein_g) || 0,
      carbs_g: parseFloat(formData.carbs_g) || 0,
      fat_g: parseFloat(formData.fat_g) || 0,
      hydration_ml: parseFloat(formData.hydration_ml) || 0,
      notes: formItems.length > 0 ? JSON.stringify(formItems) : '',
    }
    let regenWorkouts = []
    if (editingId) {
      const res = await api(`/api/nutrition/${editingId}`, { method: 'PUT', body: JSON.stringify(data) })
      regenWorkouts = res.regenerating || []
    } else {
      const res = await api('/api/nutrition', { method: 'POST', body: JSON.stringify(data) })
      regenWorkouts = res.regenerating || []
    }
    if (regenWorkouts.length) {
      regenWorkouts.forEach(rw => {
        notifyLlmStart(`insight-regen-${rw.workout_num}`, `Regenerating insight #${rw.workout_num}`, `/insights#workout-${rw.workout_num}`)
      })
      const nums = regenWorkouts.map(rw => `#${rw.workout_num}`).join(', ')
      setMealFeedback({ type: 'info', text: t('nutrition_insights_updating').replace('{nums}', nums) })
    } else {
      setMealFeedback({ type: 'ok', text: t('nutrition_no_insights_affected') })
    }
    setTimeout(() => setMealFeedback(null), 8000)
    setFormOpen(false)
    setDate(mealDate)
    loadDay(mealDate)
    loadTrend()
    loadRecent()
  }

  function requestDelete(id) {
    setConfirmTarget(id)
    setConfirmOpen(true)
  }

  async function confirmDelete() {
    if (!confirmTarget) return
    await api(`/api/nutrition/${confirmTarget}`, { method: 'DELETE' })
    setConfirmOpen(false)
    setConfirmTarget(null)
    loadDay(date)
    loadTrend()
  }

  function uploadFiles(files) {
    uploadFilesToServer(files, setAttachedFiles)
  }

  function handleDrop(e) {
    e.preventDefault()
    analyzeAreaRef.current?.classList.remove('drag-over')
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files)
  }

  async function analyzeMeal() {
    const text = mealText.trim()
    if (!text && !attachedFiles.length) return
    const controller = new AbortController()
    analyzeAbortRef.current = controller
    setAnalyzing(true)
    notifyLlmStart('meal-analyze', 'Meal Analysis', `/nutrition?date=${date}`)
    let mealErr = null
    try {
      let results
      if (attachedFiles.length) {
        const form = new FormData()
        form.append('text', text)
        for (const af of attachedFiles) {
          form.append('file_paths', af.file_path)
        }
        const r = await fetch('/api/nutrition/analyze', { method: 'POST', body: JSON.stringify({ text, file_paths: attachedFiles.map(f => f.file_path) }), headers: { 'Content-Type': 'application/json' }, signal: controller.signal })
        if (!r.ok) throw new Error(await r.text())
        results = await r.json()
      } else {
        results = await api('/api/nutrition/analyze', { method: 'POST', body: JSON.stringify({ text }), signal: controller.signal })
      }
      // results is an array of meals — save each one directly
      const mealsArr = Array.isArray(results) ? results : [results]
      const seenRegen = new Set()
      for (const meal of mealsArr) {
        const res = await api('/api/nutrition', {
          method: 'POST',
          body: JSON.stringify({
            date,
            meal_time: meal.meal_time || '',
            meal_type: meal.meal_type || 'snack',
            description: meal.description || text,
            calories: parseFloat(meal.calories) || 0,
            protein_g: parseFloat(meal.protein_g) || 0,
            carbs_g: parseFloat(meal.carbs_g) || 0,
            fat_g: parseFloat(meal.fat_g) || 0,
            hydration_ml: parseFloat(meal.hydration_ml) || 0,
            notes: meal.items ? JSON.stringify(meal.items) : '',
          }),
        })
        // Show notification for insight regeneration (only once per workout)
        if (res.regenerating) {
          for (const rw of res.regenerating) {
            if (!seenRegen.has(rw.workout_num)) {
              seenRegen.add(rw.workout_num)
              notifyLlmStart(`insight-regen-${rw.workout_num}`, `Regenerating insight #${rw.workout_num}`, `/insights#workout-${rw.workout_num}`)
            }
          }
        }
      }
      setMealText('')
      setAttachedFiles([])
      loadDay(date)
      loadTrend()
      loadRecent()
      // Show feedback banner
      if (seenRegen.size > 0) {
        const nums = [...seenRegen]
        setMealFeedback({ type: 'info', text: t('nutrition_insights_updating').replace('{nums}', nums.map(n => `#${n}`).join(', ')) })
      } else {
        setMealFeedback({ type: 'ok', text: t('nutrition_no_insights_affected') })
      }
      setTimeout(() => setMealFeedback(null), 8000)
      // Poll for insight regeneration completion
      if (seenRegen.size > 0) {
        const regenNums = [...seenRegen]
        if (regenPollRef.current) clearInterval(regenPollRef.current)
        const pollRegen = setInterval(async () => {
          try {
            const status = await api('/api/insights/status')
            // Check if new history entries appeared for our workouts
            const done = regenNums.filter(num =>
              (status.history || []).some(h => h.label?.includes(`#${num}`) && h.status === 'done')
            )
            done.forEach(num => notifyLlmEnd(`insight-regen-${num}`))
            if (done.length === regenNums.length) {
              clearInterval(pollRegen)
              regenPollRef.current = null
            }
          } catch { /* ignore */ }
        }, 4000)
        regenPollRef.current = pollRegen
        // Safety timeout: stop polling after 3 minutes
        setTimeout(() => {
          clearInterval(pollRegen)
          if (regenPollRef.current === pollRegen) regenPollRef.current = null
          regenNums.forEach(num => notifyLlmEnd(`insight-regen-${num}`))
        }, 180000)
      }
    } catch (e) {
      if (e.name === 'AbortError') { /* user stopped */ }
      else { mealErr = e.message; setMealFeedback({ type: 'error', text: 'Analysis failed: ' + e.message }) }
    } finally {
      analyzeAbortRef.current = null
      setAnalyzing(false)
      notifyLlmEnd('meal-analyze', mealErr)
    }
  }

  return (
    <>
      <div className="flex-between mb-20">
        <h1 className="page-title" style={{ margin: 0 }}>{t('page_nutrition')}</h1>
        <div className="form-inline">
          <button className="btn btn-sm" onClick={() => changeDay(-1)} title={t('prev_day')}>&lt;</button>
          <label>{t('date')}:</label>
          <input type="date" className="input-full" value={date}
            style={{ width: 160 }}
            onChange={e => setDate(e.target.value)} />
          <button className="btn btn-sm" onClick={() => changeDay(1)} title={t('next_day')}>&gt;</button>
        </div>
      </div>

      <div className="nutrition-summary">
        <ProgressRing value={totals.cal} target={targets.calories} label={t('calories_in')} color="#ff6ebb" size={140} thickness={11} />
        <ProgressRing value={totals.prot} target={targets.protein_g} label={t('protein')} unit="g" color="#65bcff" size={140} thickness={11} />
        <ProgressRing value={totals.carb} target={targets.carbs_g} label={t('carbs')} unit="g" color="#c3e88d" size={130} thickness={10} />
        <ProgressRing value={totals.fat} target={targets.fat_g} label={t('fat')} unit="g" color="#ff966c" size={130} thickness={10} />
        <ProgressRing value={totals.water} target={targets.water_ml} label={t('water')} unit="ml" color="#ff757f" size={130} thickness={10} />
        <button
          className="btn btn-sm"
          style={{ alignSelf: 'center', padding: '6px 8px', opacity: 0.6 }}
          onClick={() => openTargetsWithSuggestion()}
          title={t('nutrition_targets') || 'Set daily targets'}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
        </button>
      </div>

      {energy && (() => {
        // Macro-specific thermic effect: protein ~25%, carbs ~8%, fat ~3%
        const tef = calcTEF(totals.prot, totals.carb, totals.fat)
        const neatCal = energy.neat_calories || 0
        const totalOut = energy.bmr + energy.workout_calories + neatCal + tef
        const net = Math.round(totals.cal) - totalOut
        const netColor = net > 0 ? 'var(--green)' : net < -500 ? 'var(--red)' : 'var(--yellow)'
        const stepsStr = energy.steps ? energy.steps.toLocaleString() : '0'
        return (
          <div className="energy-balance-bar">
            <div className="energy-balance-items">
              <div className="energy-item">
                <span className="energy-label">{t('energy_bmr')} <InfoTip text={energy.bmr_source === 'measured' ? t('info_bmr_measured') : t('info_bmr_formula')} /></span>
                <span className="energy-value">{energy.bmr}</span>
              </div>
              <span className="energy-op">+</span>
              <div className="energy-item">
                <span className="energy-label">{t('energy_workout')}{energy.workout_count > 0 ? ` (${energy.workout_count})` : ''}</span>
                <span className="energy-value">{energy.workout_calories}</span>
              </div>
              <span className="energy-op">+</span>
              <div className="energy-item">
                <span className="energy-label">{t('energy_neat')} ({stepsStr} {t('steps_label')}) <InfoTip text={t('info_neat')} /></span>
                <span className="energy-value">{neatCal}</span>
              </div>
              <span className="energy-op">+</span>
              <div className="energy-item">
                <span className="energy-label">{t('energy_tef')} <InfoTip text={t('info_tef')} /></span>
                <span className="energy-value">{tef}</span>
              </div>
              <span className="energy-op">=</span>
              <div className="energy-item">
                <span className="energy-label">{t('energy_total_out')}</span>
                <span className="energy-value energy-total">{totalOut}</span>
              </div>
              <span className="energy-op">|</span>
              <div className="energy-item">
                <span className="energy-label">{t('energy_net')}</span>
                <span className="energy-value" style={{ color: netColor, fontWeight: 700 }}>
                  {net > 0 ? '+' : ''}{net}
                </span>
              </div>
            </div>
            <div className="energy-footnote">
              kcal | {t('weight')}: {energy.weight_kg}kg | {t('energy_age')}: {Math.floor(energy.age)}
            </div>
          </div>
        )
      })()}

      {mealFeedback && (
        <div
          style={{
            padding: '8px 14px', marginBottom: 12, borderRadius: 'var(--radius)',
            fontSize: 13, display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            background: mealFeedback.type === 'error' ? 'rgba(255,98,98,0.15)' : mealFeedback.type === 'info' ? 'rgba(130,170,255,0.15)' : 'rgba(195,232,141,0.15)',
            color: mealFeedback.type === 'error' ? 'var(--red)' : mealFeedback.type === 'info' ? 'var(--accent)' : 'var(--green)',
          }}
        >
          <span dir="auto">{mealFeedback.text}</span>
          <button className="btn btn-sm" style={{ padding: '2px 6px', minWidth: 0 }} onClick={() => setMealFeedback(null)}>&times;</button>
        </div>
      )}

      {/* Weekly net calories summary */}
      {netData.length > 0 && (() => {
        const weeks = {}
        netData.forEach(d => {
          if (d.calories_in <= 0 && d.calories_out <= 0) return
          const dt = new Date(d.date + 'T00:00:00')
          const day = dt.getDay()
          const sun = new Date(dt)
          sun.setDate(sun.getDate() - day)
          const key = localDateStr(sun)
          if (!weeks[key]) weeks[key] = { from: d.date, to: d.date, net: 0, in: 0, out: 0, days: 0 }
          weeks[key].to = d.date
          weeks[key].net += d.net
          weeks[key].in += d.calories_in
          weeks[key].out += d.calories_out
          weeks[key].days++
        })
        const sorted = Object.entries(weeks).sort((a, b) => b[0].localeCompare(a[0]))
        const renderCard = (w) => {
          const netColor = w.net > 0 ? 'var(--green)' : w.net < -2000 ? 'var(--red)' : 'var(--yellow)'
          const fromShort = w.from.slice(8) + '/' + w.from.slice(5, 7)
          const toShort = w.to.slice(8) + '/' + w.to.slice(5, 7)
          return (
            <div key={w.from} className="weekly-net-card card">
              <div className="weekly-net-dates">{fromShort} — {toShort}</div>
              <div className="weekly-net-value" style={{ color: netColor }}>
                {w.net > 0 ? '+' : ''}{Math.round(w.net).toLocaleString()}
              </div>
              <div className="weekly-net-detail">
                <span style={{ color: 'var(--green)' }}>{t('energy_in_short')}: {Math.round(w.in).toLocaleString()}</span>
                <span style={{ color: 'var(--red)' }}>{t('energy_out_short')}: {Math.round(w.out).toLocaleString()}</span>
              </div>
              <div className="weekly-net-avg text-dim">{t('avg')}: {w.days ? Math.round(w.net / w.days).toLocaleString() : 0} / {t('day')}</div>
            </div>
          )
        }
        const visible = sorted.slice(0, 6)
        const hasMore = sorted.length > 6
        return (
          <>
          <div className="weekly-net-summary mt-20">
            <h4>{t('weekly_net_total')}</h4>
            <div className="weekly-net-cards">
              {visible.map(([, w]) => renderCard(w))}
              {hasMore && (
                <button className="weekly-net-card card" style={{ cursor: 'pointer', opacity: 0.7, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 90 }} onClick={() => setWeeklyModalOpen(true)}>
                  <span className="text-dim">{t('show_all')} ({sorted.length})</span>
                </button>
              )}
            </div>
          </div>
          {weeklyModalOpen && (
            <Modal title={t('weekly_net_total')} onClose={() => setWeeklyModalOpen(false)}>
              <div className="weekly-net-cards" style={{ flexWrap: 'wrap' }}>
                {sorted.map(([, w]) => renderCard(w))}
              </div>
            </Modal>
          )}
          </>
        )
      })()}

      <div className="chart-row mt-20 single">
        <div className="chart-container">
          <h4>{t('daily_net_calories')}</h4>
          {netData.length > 0 ? (() => {
            const chartData = netData.filter(d => d.calories_in > 0)
            return chartData.length > 0 ? (
            <Plot
              data={[
                {
                  x: chartData.map(d => d.date),
                  y: chartData.map(d => d.calories_in),
                  text: chartData.map(d => d.calories_in ? Math.round(d.calories_in).toLocaleString() : ''),
                  textposition: 'outside',
                  textfont: { size: 9, color: 'rgba(130,170,255,0.9)' },
                  name: t('calories_in'),
                  type: 'bar',
                  marker: { color: 'rgba(130,170,255,0.6)' },
                },
                {
                  x: chartData.map(d => d.date),
                  y: chartData.map(d => d.calories_out),
                  text: chartData.map(d => d.calories_out ? Math.round(d.calories_out).toLocaleString() : ''),
                  textposition: 'outside',
                  textfont: { size: 9, color: 'rgba(199,146,234,0.9)' },
                  name: t('energy_total_out'),
                  type: 'bar',
                  marker: { color: 'rgba(199,146,234,0.6)' },
                },
                {
                  x: chartData.map(d => d.date),
                  y: chartData.map(d => d.net),
                  text: chartData.map(d => {
                    const n = Math.round(d.net)
                    return n ? (n > 0 ? '+' : '') + n.toLocaleString() : ''
                  }),
                  textposition: chartData.map(d => d.net >= 0 ? 'top center' : 'bottom center'),
                  textfont: { size: 10, color: chartData.map(d => d.net >= 0 ? 'rgba(195,232,141,0.9)' : 'rgba(255,117,127,0.9)') },
                  name: t('energy_net'),
                  type: 'scatter',
                  mode: 'lines+markers+text',
                  line: { color: '#c3e88d', width: 2 },
                  marker: { size: 6, color: chartData.map(d => d.net >= 0 ? '#c3e88d' : '#ff757f') },
                },
              ]}
              layout={{
                ...PLOTLY_LAYOUT,
                barmode: 'group',
                legend: { ...PLOTLY_LAYOUT.legend, orientation: 'h', y: 1.12 },
                shapes: [{
                  type: 'line', x0: 0, x1: 1, xref: 'paper',
                  y0: 0, y1: 0, line: { color: 'rgba(200,211,245,0.3)', width: 1, dash: 'dot' },
                }],
              }}
              config={PLOTLY_CONFIG}
              useResizeHandler
              style={{ width: '100%', height: 300 }}
            />
          ) : <p className="text-dim text-sm">{t('no_nutrition_data')}</p>
            })() : <p className="text-dim text-sm">{t('no_nutrition_data')}</p>}
        </div>
      </div>

      {mealExpanded && <div className="expand-backdrop" onClick={() => setMealExpanded(false)} />}
      <div
        className={`meal-analyze-area${mealExpanded ? ' expanded' : ''}`}
        ref={analyzeAreaRef}
        onDragOver={e => { e.preventDefault(); analyzeAreaRef.current?.classList.add('drag-over') }}
        onDragLeave={e => { e.preventDefault(); analyzeAreaRef.current?.classList.remove('drag-over') }}
        onDrop={handleDrop}
      >
        <h4 style={{ marginBottom: 8 }}>{t('describe_meal')}</h4>
        <p className="text-sm text-dim mb-12">{t('describe_meal_hint')}</p>
        {selectedRecent.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
              {selectedRecent.map((it, i) => {
                const qty = it.quantity || 1
                const totalCal = Math.round((it.unit_calories || 0) * qty)
                const key = `${it.base_name || it.name}-${i}`
                return (
                  <span key={key} className="recent-selected-chip">
                    <span dir="auto">{it.base_name || it.name}</span>
                    <span className="recent-qty-controls">
                      <button onClick={() => adjustRecentQty(i, -0.5)}>-</button>
                      <span className="recent-qty-value">{qty % 1 === 0 ? qty : qty.toFixed(1)}</span>
                      <button onClick={() => adjustRecentQty(i, 0.5)}>+</button>
                    </span>
                    <span className="text-dim text-sm">({totalCal} cal)</span>
                    <button className="recent-chip-remove" onClick={() => setSelectedRecent(prev => prev.filter((_, j) => j !== i))}>&times;</button>
                  </span>
                )
              })}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button className="btn btn-accent btn-sm" onClick={() => openFromRecent(selectedRecent)}>
                {t('save_selected')}
              </button>
              <span className="text-dim text-sm">
                {Math.round(selectedRecent.reduce((s, it) => s + (it.unit_calories || 0) * (it.quantity || 1), 0))} cal total
              </span>
            </div>
          </div>
        )}
        <div style={{ position: 'relative' }} ref={recentRef}>
          <textarea
            rows={mealExpanded ? 10 : 2}
            value={mealText}
            onChange={e => { setMealText(e.target.value); clearTimeout(recentTimerRef.current); if (e.target.value.trim().length >= 2) recentTimerRef.current = setTimeout(() => setShowRecent(true), 150); else setShowRecent(false) }}
            onInput={autoGrow}
            onFocus={() => { if (recentItems.length > 0) setShowRecent(true) }}
            onPaste={e => handleFilePaste(e, uploadFiles)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (mealText.trim() || attachedFiles.length) { analyzeMeal(); setMealExpanded(false) } } else if (e.key === 'Escape') { if (showRecent) { setShowRecent(false); e.stopPropagation() } else if (mealExpanded) setMealExpanded(false) } }}
            placeholder={t('meal_placeholder')}
            dir={detectDir(mealText) || (lang === 'he' ? 'rtl' : 'ltr')}
            style={{ overflow: 'hidden' }}
          />
          {showRecent && filteredRecent.length > 0 && (
            <div className="recent-items-dropdown">
              <div className="recent-items-header text-dim text-sm" style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)' }}>
                {t('recent_items')} — {t('recent_items_hint')}
              </div>
              <div style={{ maxHeight: 200, overflowY: 'auto' }}>
                {filteredRecent.slice(0, 15).map((it, i) => {
                  const uCal = it.unit_calories || 0
                  return (
                    <div key={i} className="recent-item-row"
                      onMouseDown={e => e.preventDefault()}
                      onClick={() => {
                        setSelectedRecent(prev => {
                          const key = (it.base_name || it.name || '').toLowerCase()
                          if (prev.some(p => (p.base_name || p.name || '').toLowerCase() === key)) return prev
                          return [...prev, { ...it, quantity: 1 }]
                        })
                      }}>
                      <span dir="auto" style={{ flex: 1 }}>{it.base_name || it.name}</span>
                      <span className="text-dim text-sm" style={{ whiteSpace: 'nowrap' }}>
                        {Math.round(uCal)} cal/unit | P:{Math.round(it.unit_protein_g || 0)}g C:{Math.round(it.unit_carbs_g || 0)}g F:{Math.round(it.unit_fat_g || 0)}g
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
        {attachedFiles.length > 0 && (
          <div className="attached-files">
            {attachedFiles.map((f, i) => (
              <span key={f.file_path || i} className="attached-file-tag">
                {f.filename}
                <button onClick={() => setAttachedFiles(prev => prev.filter((_, j) => j !== i))}>&times;</button>
              </span>
            ))}
          </div>
        )}
        <div className="form-inline" style={{ gap: 8 }}>
          <input type="file" ref={fileInputRef} style={{ display: 'none' }} multiple
            accept="image/*" onChange={e => { if (e.target.files.length) uploadFiles(e.target.files); e.target.value = '' }} />
          <button className="btn btn-sm" onClick={() => fileInputRef.current?.click()} title={t('attach_photo')} style={{ fontSize: 16, padding: '4px 8px' }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          </button>
          <button className="btn btn-sm btn-icon" onClick={() => { setMealExpanded(x => { if (!x) setTimeout(() => analyzeAreaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50); return !x }); }} title={mealExpanded ? 'Collapse' : 'Expand'} style={{ fontSize: 16, padding: '4px 8px' }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              {mealExpanded
                ? <><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></>
                : <><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></>
              }
            </svg>
          </button>
          {analyzing ? (
            <button className="btn btn-red btn-sm" onClick={() => analyzeAbortRef.current?.abort()}>{t('stop')}</button>
          ) : (
            <button className="btn btn-accent btn-sm" onClick={() => { analyzeMeal(); setMealExpanded(false) }} disabled={(!mealText.trim() && !attachedFiles.length) || !aiEnabled}>
              {aiEnabled ? t('analyze') : t('ai_disabled_btn')}
            </button>
          )}
        </div>
      </div>

      <div className="flex-between mb-12">
        <h4>{t('meals')}</h4>
        <button className="btn btn-accent btn-sm" onClick={openAddForm}>{t('add_meal')}</button>
      </div>

      <div className="meal-list">
        {meals.length ? (() => {
          // Group meals by meal_type
          const groups = {}
          meals.forEach(m => {
            const type = m.meal_type || 'snack'
            if (!groups[type]) groups[type] = []
            groups[type].push(m)
          })
          // Order groups by MEAL_TYPES order
          const orderedTypes = MEAL_TYPES.filter(mt => groups[mt])
          return orderedTypes.map(mtype => {
            const groupMeals = groups[mtype]
            const groupCal = groupMeals.reduce((s, m) => s + safef(m.calories), 0)
            const groupP = groupMeals.reduce((s, m) => s + safef(m.protein_g), 0)
            const groupC = groupMeals.reduce((s, m) => s + safef(m.carbs_g), 0)
            const groupF = groupMeals.reduce((s, m) => s + safef(m.fat_g), 0)
            const isExpanded = expandedGroups.has(mtype)
            return (
              <div key={mtype} className="meal-group">
                <div className="meal-group-header" onClick={() => setExpandedGroups(prev => {
                  const next = new Set(prev)
                  next.has(mtype) ? next.delete(mtype) : next.add(mtype)
                  return next
                })}>
                  <span className="meal-expand-icon">{isExpanded ? '\u25BC' : '\u25B6'}</span>
                  <span className="meal-group-type">{mtype.replace('_', ' ')}</span>
                  <span className="meal-group-macros text-dim text-sm">
                    {Math.round(groupCal)} cal | P:{Math.round(groupP)}g C:{Math.round(groupC)}g F:{Math.round(groupF)}g
                  </span>
                </div>
                {isExpanded && groupMeals.map(m => {
                  let items = []
                  try { items = m.notes ? JSON.parse(m.notes) : [] } catch { /* not JSON */ }
                  const hasHeb = items.some(it => /[\u0590-\u05FF]/.test(it.name || ''))
                  const numCell = hasHeb ? { direction: 'ltr', unicodeBidi: 'isolate', textAlign: 'left' } : undefined
                  return (
                    <div key={m.id} className="meal-item">
                      <div className="meal-item-info">
                        <div className="meal-item-header" onClick={() => items.length > 0 && setExpandedMeals(prev => {
                          const next = new Set(prev)
                          next.has(m.id) ? next.delete(m.id) : next.add(m.id)
                          return next
                        })} style={items.length > 0 ? { cursor: 'pointer' } : undefined}>
                          <div className="meal-item-type">
                            {items.length > 0 && <span className="meal-expand-icon">{expandedMeals.has(m.id) ? '\u25BC' : '\u25B6'}</span>}
                            {m.meal_time && <span className="text-dim text-sm">{m.meal_time}</span>}
                          </div>
                          <div dir="auto">{m.description || `(${t('no_description')})`}</div>
                          <div className="meal-item-macros">
                            <span>{Math.round(safef(m.calories))} cal</span>
                            <span>P: {Math.round(safef(m.protein_g))}g</span>
                            <span>C: {Math.round(safef(m.carbs_g))}g</span>
                            <span>F: {Math.round(safef(m.fat_g))}g</span>
                          </div>
                        </div>
                        {items.length > 0 && expandedMeals.has(m.id) && (
                          <table className="meal-items-table" dir={hasHeb ? 'rtl' : undefined}>
                            <thead>
                              <tr><th>{t('item')}</th><th>{t('calories')}</th><th>Net cal</th><th>{t('protein')}</th><th>{t('carbs')}</th><th>{t('fat')}</th></tr>
                            </thead>
                            <tbody>
                              {items.map((it, idx) => {
                                const cal = safef(it.calories)
                                const tefItem = calcTEF(safef(it.protein_g), safef(it.carbs_g), safef(it.fat_g))
                                return (
                                  <tr key={idx}>
                                    <td dir="auto">{it.name}</td>
                                    <td style={numCell}>{Math.round(cal)}</td>
                                    <td style={numCell} title={`TEF: -${tefItem} kcal`}>{Math.round(cal - tefItem)}</td>
                                    <td style={numCell}>{Math.round(safef(it.protein_g))}g</td>
                                    <td style={numCell}>{Math.round(safef(it.carbs_g))}g</td>
                                    <td style={numCell}>{Math.round(safef(it.fat_g))}g</td>
                                  </tr>
                                )
                              })}
                              {(() => {
                                const totalCal = safef(m.calories)
                                const totalTef = calcTEF(safef(m.protein_g), safef(m.carbs_g), safef(m.fat_g))
                                return (
                                  <tr className="meal-items-total">
                                    <td>{t('total')}</td>
                                    <td style={numCell}>{Math.round(totalCal)}</td>
                                    <td style={numCell} title={`TEF: -${totalTef} kcal`}>{Math.round(totalCal - totalTef)}</td>
                                    <td style={numCell}>{Math.round(safef(m.protein_g))}g</td>
                                    <td style={numCell}>{Math.round(safef(m.carbs_g))}g</td>
                                    <td style={numCell}>{Math.round(safef(m.fat_g))}g</td>
                                  </tr>
                                )
                              })()}
                            </tbody>
                          </table>
                        )}
                      </div>
                      <div className="meal-actions">
                        <button className="btn btn-sm" onClick={() => openEditForm(m)}>{t('edit')}</button>
                        <button className="btn btn-sm btn-red" onClick={() => requestDelete(m.id)}>{t('del')}</button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          })
        })() : <p className="text-dim text-sm">{t('no_meals')}</p>}
      </div>

      {/* Add/Edit Meal Modal */}
      <Modal open={formOpen} onClose={() => setFormOpen(false)} title={editingId ? t('edit_meal') : t('add_meal_title')}>
        <div className="form-row mt-12">
          <div className="form-group">
            <label>{t('date')}</label>
            <input type="date" className="input-full" value={formData.date || date}
              onChange={e => setFormData(f => ({ ...f, date: e.target.value }))} />
          </div>
          <div className="form-group" style={{ maxWidth: 110 }}>
            <label>{t('time')}</label>
            <input type="time" className="input-full" value={formData.meal_time || ''}
              onChange={e => setFormData(f => ({ ...f, meal_time: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>{t('meal_type')}</label>
            <select className="input-full" value={formData.meal_type}
              onChange={e => setFormData(f => ({ ...f, meal_type: e.target.value }))}>
              {MEAL_TYPES.map(mt => (
                <option key={mt} value={mt}>{mt.replace('_', ' ')}</option>
              ))}
            </select>
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label>{t('description')}</label>
            <input type="text" className="input-full" dir="auto" value={formData.description}
              placeholder={t('describe_what_you_ate')}
              onChange={e => setFormData(f => ({ ...f, description: e.target.value }))} />
          </div>
        </div>

        {formItems.length > 0 ? (
          <>
            <h5 style={{ margin: '12px 0 8px', color: 'var(--text-dim)' }}>{t('items')}</h5>
            <div className="edit-items-table-wrap">
              <table className="edit-items-table">
                <thead>
                  <tr>
                    <th>{t('item')}</th>
                    <th>{t('cal_short')}</th>
                    <th>{t('protein')}</th>
                    <th>{t('carbs')}</th>
                    <th>{t('fat')}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {formItems.map((it, idx) => (
                    <tr key={idx}>
                      <td>
                        <input type="text" dir="auto" value={it.name || ''} className="input-full"
                          onChange={e => updateItem(idx, 'name', e.target.value)} />
                      </td>
                      <td>
                        <input type="number" value={it.calories || 0} className="input-full"
                          onChange={e => updateItem(idx, 'calories', e.target.value)} />
                      </td>
                      <td>
                        <input type="number" value={it.protein_g || 0} className="input-full"
                          onChange={e => updateItem(idx, 'protein_g', e.target.value)} />
                      </td>
                      <td>
                        <input type="number" value={it.carbs_g || 0} className="input-full"
                          onChange={e => updateItem(idx, 'carbs_g', e.target.value)} />
                      </td>
                      <td>
                        <input type="number" value={it.fat_g || 0} className="input-full"
                          onChange={e => updateItem(idx, 'fat_g', e.target.value)} />
                      </td>
                      <td>
                        <button className="btn btn-sm btn-red" onClick={() => removeItem(idx)} title={t('remove_item')}>&times;</button>
                      </td>
                    </tr>
                  ))}
                  <tr className="meal-items-total">
                    <td>{t('total')}</td>
                    <td>{Math.round(parseFloat(formData.calories) || 0)}</td>
                    <td>{Math.round(parseFloat(formData.protein_g) || 0)}g</td>
                    <td>{Math.round(parseFloat(formData.carbs_g) || 0)}g</td>
                    <td>{Math.round(parseFloat(formData.fat_g) || 0)}g</td>
                    <td></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <button className="btn btn-sm mt-8" onClick={addItem}>{t('add_item')}</button>
          </>
        ) : (
          <>
            <div className="form-row-4">
              <div className="form-group">
                <label>{t('calories')}</label>
                <input type="number" className="input-full" value={formData.calories}
                  onChange={e => setFormData(f => ({ ...f, calories: e.target.value }))}
                  onKeyDown={e => { if (e.key === 'Enter') saveMeal() }} />
              </div>
              <div className="form-group">
                <label>{t('protein')} (g)</label>
                <input type="number" className="input-full" value={formData.protein_g}
                  onChange={e => setFormData(f => ({ ...f, protein_g: e.target.value }))}
                  onKeyDown={e => { if (e.key === 'Enter') saveMeal() }} />
              </div>
              <div className="form-group">
                <label>{t('carbs')} (g)</label>
                <input type="number" className="input-full" value={formData.carbs_g}
                  onChange={e => setFormData(f => ({ ...f, carbs_g: e.target.value }))}
                  onKeyDown={e => { if (e.key === 'Enter') saveMeal() }} />
              </div>
              <div className="form-group">
                <label>{t('fat')} (g)</label>
                <input type="number" className="input-full" value={formData.fat_g}
                  onChange={e => setFormData(f => ({ ...f, fat_g: e.target.value }))}
                  onKeyDown={e => { if (e.key === 'Enter') saveMeal() }} />
              </div>
            </div>
            <button className="btn btn-sm mt-8" onClick={addItem}>{t('add_items_breakdown')}</button>
          </>
        )}

        <div className="form-group mt-12">
          <label>{t('hydration')} (ml)</label>
          <input type="number" className="input-full" value={formData.hydration_ml}
            style={{ maxWidth: 200 }}
            onChange={e => setFormData(f => ({ ...f, hydration_ml: e.target.value }))}
            onKeyDown={e => { if (e.key === 'Enter') saveMeal() }} />
        </div>
        <div className="form-actions">
          <button className="btn btn-accent" onClick={saveMeal}>{editingId ? t('update') : t('add')}</button>
          <button className="btn" onClick={() => setFormOpen(false)}>{t('cancel')}</button>
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmOpen}
        title={t('delete_meal')}
        message={t('delete_meal_confirm')}
        onConfirm={confirmDelete}
        onCancel={() => { setConfirmOpen(false); setConfirmTarget(null) }}
      />

      {/* Nutrition Targets Modal */}
      <Modal open={targetsOpen} onClose={() => {
        if (suggesting) {
          setSuggestReason(t('smart_suggest_background'))
        }
        setTargetsOpen(false)
      }} title={t('nutrition_targets')}>
        <p className="text-sm text-dim mb-12">{t('nutrition_targets_hint')}</p>
        <div className="form-row-4" style={{ gap: 12 }}>
          {[
            { key: 'calories', label: t('calories'), unit: 'kcal', info: t('info_target_calories') },
            { key: 'protein_g', label: t('protein'), unit: 'g', info: t('info_target_protein') },
            { key: 'carbs_g', label: t('carbs'), unit: 'g', info: t('info_target_carbs') },
            { key: 'fat_g', label: t('fat'), unit: 'g', info: t('info_target_fat') },
            { key: 'water_ml', label: t('water'), unit: 'ml', info: t('info_target_water') },
          ].map(({ key, label, unit, info }) => (
            <div className="form-group" key={key}>
              <label>{label} ({unit}) <InfoTip text={info} /></label>
              <input type="number" className="input-full" value={targetsForm[key] || 0}
                onChange={e => setTargetsForm(f => ({ ...f, [key]: parseInt(e.target.value) || 0 }))} />
            </div>
          ))}
        </div>
        {suggestReason && (
          <div style={{ padding: '8px 10px', marginTop: 12, borderRadius: 'var(--radius)', background: 'rgba(130,170,255,0.1)', fontSize: 12, color: 'var(--accent)', lineHeight: 1.6 }} dir="auto">
            {suggestReason.split(/(?<=\.)\s+/).map((s, i) => <div key={i}>{s}</div>)}
          </div>
        )}
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer', marginTop: 12, fontSize: 13 }}>
          <input type="checkbox" checked={autoSuggest} onChange={async e => {
            const val = e.target.checked
            setAutoSuggest(val)
            try { await api('/api/settings/nutrition_auto_suggest', { method: 'PUT', body: JSON.stringify({ value: val ? '1' : '0' }) }) } catch {}
          }} />
          {t('auto_suggest_weekly')}
          <InfoTip text={t('auto_suggest_weekly_tip')} />
        </label>
        <div className="form-actions">
          <button className="btn btn-accent" onClick={async () => {
            try {
              const saved = await api('/api/nutrition/targets', { method: 'PUT', body: JSON.stringify(targetsForm) })
              setTargets(saved)
              setTargetsOpen(false)
            } catch (e) { console.error('Failed to save targets:', e) }
          }}>{t('save')}</button>
          <button
            className={suggesting ? 'btn btn-accent' : 'btn'}
            disabled={!suggesting && !aiEnabled}
            onClick={async () => {
              setSuggesting(true)
              setSuggestReason('')
              let suggestErr = null
              notifyLlmStart('suggest-targets', t('ai_suggest_targets'), '/nutrition?openTargets=1')
              try {
                const res = await api('/api/nutrition/targets/suggest', { method: 'POST' })
                const newTargets = { calories: res.calories, protein_g: res.protein_g, carbs_g: res.carbs_g, fat_g: res.fat_g, water_ml: res.water_ml }
                setTargetsForm(newTargets)
                if (res.reasoning) setSuggestReason(res.reasoning)
                localStorage.setItem(suggestKey, JSON.stringify({ ...newTargets, reasoning: res.reasoning || '' }))
              } catch (e) {
                suggestErr = e.message
                console.error('AI suggest failed:', e)
                setSuggestReason('Failed: ' + e.message)
              } finally {
                setSuggesting(false)
                notifyLlmEnd('suggest-targets', suggestErr)
              }
            }}
          >
            {suggesting ? <><span className="spinner-sm" /> {t('analyzing')}</> : t('ai_suggest_targets')}
          </button>
          <button className="btn" onClick={() => {
            if (suggesting) {
              setSuggestReason(t('smart_suggest_background'))
            }
            setTargetsOpen(false)
          }}>{t('close')}</button>
        </div>
      </Modal>
    </>
  )
}
