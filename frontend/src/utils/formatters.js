import { marked } from 'marked'
import DOMPurify from 'dompurify'

export function md(text) {
  return { __html: DOMPurify.sanitize(marked.parse(text || '')) }
}

export function fmtDur(m) {
  const h = Math.floor(m / 60)
  const mm = Math.floor(m % 60)
  const ss = Math.floor((m * 60) % 60)
  return h ? `${h}h ${mm}m ${ss}s` : `${mm}m ${ss}s`
}

export function fmtDist(km) {
  return (Math.floor(km * 100) / 100).toFixed(2)
}

export function fmtDate(d) {
  return new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function fmtDateShort(d) {
  try {
    const dt = new Date(d.replace(' +', '+'))
    return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  } catch {
    return d?.slice(0, 10) || ''
  }
}

export function fmtTime(d, tz) {
  if (!d) return ''
  try {
    const normalized = String(d).replace(/^(\d{4}-\d{2}-\d{2}) /, '$1T').replace(' +', '+').replace(' -', '-')
    const dt = new Date(normalized)
    if (isNaN(dt.getTime())) return ''
    const opts = { hour: '2-digit', minute: '2-digit' }
    if (tz) opts.timeZone = tz
    return dt.toLocaleTimeString('en-GB', opts)
  } catch {
    return ''
  }
}

export function fmtPace(spdKmh) {
  if (!spdKmh || spdKmh <= 0) return '-'
  const totalSeconds = Math.round(3600 / spdKmh)
  const mins = Math.floor(totalSeconds / 60)
  const secs = totalSeconds % 60
  return `${mins}:${String(secs).padStart(2, '0')}/km`
}

export function fmtPaceFromDist(distKm, durMin) {
  if (!distKm || distKm <= 0 || !durMin || durMin <= 0) return '-'
  const totalSeconds = Math.round((durMin * 60) / distKm)
  const mins = Math.floor(totalSeconds / 60)
  const secs = totalSeconds % 60
  return `${mins}:${String(secs).padStart(2, '0')}/km`
}

export function fmtSize(bytes) {
  if (bytes == null) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export function fmtDateNice(d) {
  if (!d) return '-'
  try {
    const dt = new Date(d)
    return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) +
      ' ' + dt.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return d
  }
}

export function localDateStr(dt) {
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`
}

export function safef(v, d = 0) {
  const n = parseFloat(v)
  return isNaN(n) ? d : n
}

export function formatCost(usd) {
  if (!usd) return '$0.00'
  if (usd >= 0.01) return `$${usd.toFixed(2)}`
  return `$${usd.toFixed(3)}`
}

export function formatTokens(n) {
  if (!n) return '0'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export function shortModel(m) {
  return (m || 'unknown').replace(/^us\.anthropic\./, '').replace(/-v\d+.*$/, '').replace(/-\d{8}$/, '')
}

// Detect text direction: returns 'rtl' if first strong char is Hebrew/Arabic, 'ltr' otherwise.
// When text is empty, returns null (caller should use UI language or 'auto').
const RTL_RE = /[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F]/
const LTR_RE = /[A-Za-z\u00C0-\u024F]/
export function detectDir(text) {
  if (!text) return 'auto'
  for (const ch of text) {
    if (RTL_RE.test(ch)) return 'rtl'
    if (LTR_RE.test(ch)) return 'ltr'
  }
  return 'auto'
}

export function hasHebrew(text) {
  return /[\u0590-\u05FF]/.test(text)
}

export function fmtSleepHours(min) {
  if (!min) return '--'
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return `${h}h ${m}m`
}

export function getRecoveryInfoTexts(t) {
  return {
    recovery: t('info_recovery'),
    fitness: t('info_fitness'),
    fatigue: t('info_fatigue'),
    tsb: t('info_tsb'),
    trimp: t('info_trimp'),
    rhr: t('info_rhr'),
    hrv: t('info_hrv'),
    sleep: t('info_sleep'),
  }
}

/**
 * Auto-grow textarea on input. Resets height then sets to scrollHeight.
 * Use as onInput handler: onInput={autoGrow}
 */
export function autoGrow(e) {
  const el = e.target || e
  el.style.height = 'auto'
  el.style.height = el.scrollHeight + 'px'
}

export async function uploadFileToServer(file) {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch('/api/chat/upload', { method: 'POST', body: form })
  return await r.json()
}

export async function uploadFilesToServer(files, setAttachedFiles) {
  for (const file of files) {
    try {
      const data = await uploadFileToServer(file)
      setAttachedFiles(prev => [...prev, { file_path: data.file_path, filename: data.filename }])
    } catch (er) {
      console.error('Upload failed:', er)
    }
  }
}

export function handleFilePaste(e, uploadFn) {
  const items = e.clipboardData?.items
  if (!items) return
  const files = []
  for (const item of items) {
    if (item.kind === 'file') {
      const file = item.getAsFile()
      if (file) files.push(file)
    }
  }
  if (files.length > 0) {
    e.preventDefault()
    uploadFn(files)
  }
}

export function computeRaceTsbData(tsbVal, daysToRace) {
  const tsbPct = Math.max(0, Math.min(100, ((tsbVal + 40) / 70) * 100))
  let timePhase = 'taper'
  if (daysToRace > 56) timePhase = 'early'
  else if (daysToRace > 28) timePhase = 'mid'
  else if (daysToRace > 14) timePhase = 'late'

  let tsbZone = 'peaked'
  if (tsbVal < -20) tsbZone = 'building'
  else if (tsbVal < 0) tsbZone = 'maintaining'
  else if (tsbVal < 15) tsbZone = 'tapering'
  const recKey = `tsb_rec_${tsbZone}_${timePhase}`
  const isAligned = (
    (timePhase === 'early' && (tsbZone === 'building' || tsbZone === 'maintaining')) ||
    (timePhase === 'mid' && (tsbZone === 'maintaining' || tsbZone === 'building')) ||
    (timePhase === 'late' && (tsbZone === 'tapering' || tsbZone === 'maintaining')) ||
    (timePhase === 'taper' && (tsbZone === 'peaked' || tsbZone === 'tapering'))
  )
  let recColor = '#ffc777'
  if (isAligned) {
    recColor = '#c3e88d'
  } else if ((timePhase === 'taper' && tsbZone === 'building') || (timePhase === 'early' && tsbZone === 'peaked')) {
    recColor = '#ff5370'
  }
  return { tsbPct, timePhase, tsbZone, recKey, isAligned, recColor }
}
